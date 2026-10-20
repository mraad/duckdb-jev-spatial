import duckdb
import pytest
import yaml

from jev_spatial import Catalog


@pytest.fixture
def catalog_project(tmp_path):
    datasets = []
    for table, extra in {
        'Wells': [('water_depth', 'Float'), ('status', 'String')],
        'Pipelines': [('current_phase', 'String')],
        'Discoveries': [],
    }.items():
        fields = [{'name': n, 'datatype': t, 'description': n} for n, t in [('OBJECTID', 'Integer'), *extra]]
        if table == 'Wells':
            fields[1]['custom_extensions'] = [{'vendor_name': 'ARCGIS', 'data': '{"unit":"meters"}'}]
            fields[1]['ai_context'] = {'synonyms': ['well water depth']}
        if table == 'Pipelines':
            fields[1]['ai_context'] = {'sample_values': ['IN SERVICE', 'DECOMMISSIONED']}
        fields.append({'name': 'geometry', 'datatype': 'String', 'custom_extensions': [
            {'vendor_name': 'ARCGIS', 'data': '{"wkid":4326}'}]})
        datasets.append({'name': table, 'source': f'NorthSea.main.{table}', 'fields': fields})
    (tmp_path / 'NorthSea.ossie.yaml').write_text(yaml.safe_dump({
        'ai_context': {'instructions': 'Project to EPSG:3035'}, 'datasets': datasets}))
    (tmp_path / 'NorthSea.ontology.yaml').write_text(yaml.safe_dump({'ontology': [
        {'concept': 'Feature', 'relationships': [{'name': n} for n in ['within_distance', 'within', 'intersects']]}]}))
    (tmp_path / 'knowledge/datasets').mkdir(parents=True)
    for table in ('Wells', 'Pipelines'):
        (tmp_path / f'knowledge/datasets/{table}.md').write_text(
            f'---\ntype: Ossie Dataset\ntitle: {table}\nsource: NorthSea.main.{table}\n---\n')
    return tmp_path


@pytest.fixture
def catalog(catalog_project):
    return Catalog(catalog_project)


@pytest.fixture
def con():
    con = duckdb.connect()
    con.execute('LOAD spatial')
    con.execute('CREATE TABLE sp_ref AS SELECT 4326 wkid')
    con.execute('CREATE TABLE Wells (OBJECTID INTEGER, water_depth DOUBLE, status VARCHAR, geometry GEOMETRY)')
    con.execute('CREATE TABLE Pipelines (OBJECTID INTEGER, current_phase VARCHAR, geometry GEOMETRY)')
    # Coordinates are constructed in the measurement CRS, then stored in the source CRS.
    for oid, depth, x, status in [(1, 351, 4004999, 'operating'), (2, 350, 4000000, 'operating'),
                                   (3, 351, 4005001, 'operating'), (4, None, 4000000, 'operating'),
                                   (5, 400, 4000000, 'retired')]:
        con.execute("INSERT INTO Wells VALUES (?, ?, ?, ST_Transform(ST_Point(?, 4000000), 'EPSG:3035', 'EPSG:4326', always_xy:=true))",
                    [oid, depth, status, x])
    con.execute("INSERT INTO Wells VALUES (6, 400, 'operating', NULL), (7, 400, 'operating', ST_GeomFromText('POINT EMPTY'))")
    for oid in [1, 2]:
        con.execute("INSERT INTO Pipelines VALUES (?, 'IN SERVICE', ST_Transform(ST_GeomFromText('LINESTRING(4000000 3999999,4000000 4000001)'), 'EPSG:3035', 'EPSG:4326', always_xy:=true))", [oid])
    con.execute("INSERT INTO Pipelines VALUES (3, 'DECOMMISSIONED', ST_Transform(ST_Point(4005001,4000000), 'EPSG:3035', 'EPSG:4326', always_xy:=true))")
    yield con
    con.close()


class FakeAgent:
    def __init__(self):
        self.states = []
        self.calls = 0
        self.truncated = False

    def predict_batch(self, states, questions, **kwargs):
        self.states += states
        self.calls += 1
        q = questions['answer']
        kind = q['type']
        result = []
        for state in states:
            p = 0.9 if state.get('status') == 'operating' else 0.1
            value = p if kind == 'noul' else (next(iter(q['criteria'])) if kind == 'choice' else 0.7)
            result.append({'answers': {'answer': {kind: value}}, 'usage': {'truncated': self.truncated}})
        return result


@pytest.fixture
def agent():
    return FakeAgent()
