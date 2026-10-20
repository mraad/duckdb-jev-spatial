from dataclasses import replace

import pytest

from jev_spatial import Query, Filter, Relation, compile_query, parse_question, register_jev
from jev_spatial.engine import execute

QUESTION = 'Show all wells with depth > 350 meters and are within 5 km of an in-service pipeline'


def test_exact_result_and_duplicates(catalog, con):
    catalog.validate_database(con)
    query = parse_question(QUESTION, catalog)
    compiled = compile_query(query, catalog)
    assert compiled.parameters == [350.0, 'IN SERVICE', 5000.0]
    assert 'always_xy := TRUE' in compiled.where
    assert 'EXISTS' in compiled.where
    assert sorted(r[0] for r in execute(con, compiled).fetchall()) == [1, 5]


@pytest.mark.parametrize('question', [
    QUESTION.replace('350 meters', '.35 kilometers').replace('5 km', '5000 metres'),
    QUESTION.replace('depth >', 'water depth deeper than'),
    QUESTION.replace('Show all', 'Find'),
])
def test_equivalent_wording(catalog, question):
    assert parse_question(question, catalog) == parse_question(QUESTION, catalog)


@pytest.mark.parametrize('question', [
    QUESTION + ' or country is Norway', QUESTION + '; DROP TABLE Wells',
    QUESTION.replace('meters', 'feet'), QUESTION.replace('350', '-1'),
    QUESTION.replace('depth', 'total drilled depth'), QUESTION.replace('in-service', 'gas'),
    QUESTION.replace('5 km', 'five km'), QUESTION.replace('5 km', '5'),
    QUESTION.replace('in-service', 'not in-service'),
    'Show all wells with depth > 350 meters and',
])
def test_no_partial_translation(catalog, question):
    with pytest.raises(ValueError):
        parse_question(question, catalog)


def test_negation(catalog, con):
    query = parse_question(QUESTION.replace('are within', 'are not within'), catalog)
    assert [r[0] for r in execute(con, compile_query(query, catalog)).fetchall()] == [3]


def test_depth_only_and_distance_only(catalog, con):
    depth = parse_question('Show all wells with depth >= 350 meters', catalog)
    assert len(execute(con, compile_query(depth, catalog)).fetchall()) == 6
    near = parse_question('Show all wells within 0 m of an in-service pipeline', catalog)
    assert near.relation.distance_m == 0


def test_sql_boundary_is_inclusive(con):
    assert con.execute('SELECT ST_DWithin(ST_Point(0,0), ST_Point(5000,0),5000)').fetchone()[0]
    assert not con.execute('SELECT ST_DWithin(ST_Point(0,0), ST_Point(5000.001,0),5000)').fetchone()[0]


@pytest.mark.parametrize('query', [
    Query(entity='Wells; DROP TABLE Wells'), Query(filters=(Filter('bad', '=', 3),)),
    Query(filters=(Filter('water_depth', 'OR TRUE', 3),)),
    Query(filters=(Filter('water_depth', '>', float('nan')),)),
    Query(relation=Relation('Pipelines', distance_m=-1)),
    Query(relation=Relation('Pipelines', predicate='execute_sql', distance_m=1)),
    Query(semantic='near a pipeline', semantic_fields=('geometry',)),
])
def test_invalid_plan(catalog, query):
    with pytest.raises(ValueError):
        compile_query(query, catalog)


def test_literals_are_parameters(catalog):
    value = "x' OR TRUE --"
    compiled = compile_query(Query(filters=(Filter('status', '=', value),)), catalog)
    assert value not in compiled.sql and compiled.parameters == [value]


def test_schema_drift_and_crs(catalog, con):
    con.execute('UPDATE sp_ref SET wkid=3857')
    with pytest.raises(ValueError, match='CRS'):
        catalog.validate_database(con)


def test_hybrid_materializes_candidates(catalog, con, agent):
    query = replace(parse_question(QUESTION, catalog), semantic='Is the well operating?', semantic_fields=('status',))
    runtime = register_jev(con, agent, max_evaluations=2)
    assert [r[0] for r in execute(con, compile_query(query, catalog), runtime).fetchall()] == [1]
    assert runtime.evaluations == 2
    assert sorted(s['status'] for s in agent.states) == ['operating', 'retired']
    assert con.execute('SELECT count(*) FROM _jev_candidates').fetchone()[0] == 2


def test_topological_plan(catalog, con):
    con.execute("CREATE TABLE Discoveries AS SELECT ST_MakeEnvelope(-180,-90,180,90) geometry")
    query = Query(relation=Relation('Discoveries', predicate='within'))
    compiled = compile_query(query, catalog)
    assert 'ST_Within' in compiled.where
    assert len(execute(con, compiled).fetchall()) == 5
