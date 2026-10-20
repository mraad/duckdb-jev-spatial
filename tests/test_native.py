"""Optional real-extension checks. Build native/dist first; model checks need ORT_DYLIB_PATH."""
import json
import math
import os
from pathlib import Path

import duckdb
import pytest

from jev_spatial.engine import NativeRuntime, connect

ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / 'native/dist/jev_spatial.duckdb_extension'
MODEL = ROOT / 'models/laya-onnx/laya'
pytestmark = pytest.mark.skipif(not EXTENSION.is_file(), reason='Build the optional Rust extension first')


@pytest.fixture
def native_con():
    with duckdb.connect(config={'allow_unsigned_extensions': 'true'}) as con:
        con.execute("LOAD '" + str(EXTENSION).replace("'", "''") + "'")
        yield con


@pytest.fixture(scope='module')
def model_con():
    if not os.environ.get('ORT_DYLIB_PATH') or not (MODEL / 'model.onnx').is_file():
        pytest.skip('Requires the local ONNX export and explicit ORT_DYLIB_PATH')
    with duckdb.connect(config={'allow_unsigned_extensions': 'true'}) as con:
        con.execute("LOAD '" + str(EXTENSION).replace("'", "''") + "'")
        NativeRuntime(con, MODEL)
        yield con


def test_native_load_is_read_only_and_spatial_is_available(tmp_path):
    database = tmp_path / 'source.ddb'
    with duckdb.connect(str(database)) as con:
        con.execute('CREATE TABLE source AS SELECT 1 id')
    before = database.read_bytes()
    with connect(database, EXTENSION) as con:
        assert con.execute('SELECT ST_Distance(ST_Point(0,0), ST_Point(3,4))').fetchone()[0] == 5
        assert json.loads(con.execute('SELECT jev_laya_stats()').fetchone()[0])['model_directory'] is None
    assert database.read_bytes() == before


def test_native_missing_model_fails_and_nulls_propagate(native_con):
    assert native_con.execute("SELECT jev(NULL,'Q'),jev_prob('x',NULL),jev_choice('x','Q',NULL),jev_score(NULL,'Q',['a'])").fetchone() == (None,) * 4
    with pytest.raises(duckdb.Error, match='jev_laya_load'):
        native_con.execute("SELECT jev({'status':'operating'},'Q')").fetchall()
    assert native_con.execute('SELECT 42').fetchone() == (42,)


@pytest.mark.parametrize('expression', [
    "jev('x','Q',2.0)", "jev('x','Q','NaN'::DOUBLE)",
    "jev_choice('x','Q',[])", "jev_choice('x','Q',['a','a'])",
    "jev_choice('x','Q',['a',NULL])", "jev_score('x','Q',[' '])",
    "jev_prob({'geometry':ST_Point(0,0)},'Q')", "jev_prob('x','')",
    "jev_score_norm('x','Q',[])",
    "jev_eval('x','Q','unknown',[])",
    "jev_eval('x','Q','noul',['unexpected'])",
    "jev_eval('x','Q','choice',['a',NULL])",
    "jev_confidence('x','Q','score',['a','a'])",
])
def test_native_invalid_inputs_fail_before_model_load(native_con, expression):
    with pytest.raises(duckdb.Error) as error:
        native_con.execute('SELECT '+expression).fetchall()
    assert 'Call jev_laya_load' not in str(error.value)
    assert json.loads(native_con.execute('SELECT jev_laya_stats()').fetchone()[0])['evaluations'] == 0


@pytest.mark.parametrize('name', ['jev', 'jev_eval', 'jev_confidence', 'jev_score_norm'])
def test_native_does_not_replace_existing_functions(name):
    with duckdb.connect(config={'allow_unsigned_extensions': 'true'}) as con:
        con.execute(f'CREATE MACRO {name}(x,y) AS true')
        with pytest.raises(duckdb.Error, match='already exist'):
            con.execute("LOAD '"+str(EXTENSION)+"'")
        assert con.execute(f"SELECT {name}('x','Q')").fetchone() == (True,)


def test_native_real_model_types_deduplication_and_chunks(model_con):
    model_con.execute('SELECT jev_laya_reset()')
    rows = model_con.execute("""SELECT jev_prob({'current_phase':'IN SERVICE'},'Is this pipeline currently in service?'),
        jev({'current_phase':'IN SERVICE'},'Is this pipeline currently in service?') FROM range(4097)""").fetchall()
    assert len(rows) == 4097 and all(r == (0.8996, True) for r in rows)
    assert json.loads(model_con.execute('SELECT jev_laya_stats()').fetchone()[0])['evaluations'] == 1
    assert model_con.execute("SELECT jev({'current_phase':'IN SERVICE'},'Is this pipeline currently in service?',0.95)").fetchone() == (False,)
    choice, score = model_con.execute("""SELECT
        jev_choice({'current_phase':'IN SERVICE'},'What is the pipeline lifecycle state?', ['in service','decommissioned','abandoned']),
        jev_score({'current_phase':'IN SERVICE'},'How active is the pipeline?',['inactive','active'])""").fetchone()
    assert choice == 'in service' and 0 <= score <= 1


def test_native_json_struct_parity_and_failed_load_preserves_model(model_con):
    expected = model_con.execute("SELECT jev_prob({'current_phase':'DECOMMISSIONED'},'Is this pipeline currently in service?')").fetchone()
    assert model_con.execute("SELECT jev_prob(CAST(? AS JSON),'Is this pipeline currently in service?')",
                             [json.dumps({'current_phase': 'DECOMMISSIONED'})]).fetchone() == expected
    with pytest.raises(duckdb.Error):
        model_con.execute("SELECT jev_laya_load('/does/not/exist')").fetchone()
    assert model_con.execute("SELECT jev_prob({'current_phase':'DECOMMISSIONED'},'Is this pipeline currently in service?')").fetchone() == expected


def test_native_truncation_fails_closed(model_con):
    model_con.execute('SELECT jev_laya_reset()')
    for _ in range(2):
        with pytest.raises(duckdb.Error, match='truncated state'):
            model_con.execute("SELECT jev_eval(?, 'Is this pipeline in service?', 'noul', [])",
                              ['pipeline ' * 2000]).fetchone()
    stats = json.loads(model_con.execute('SELECT jev_laya_stats()').fetchone()[0])
    assert (stats['evaluations'], stats['errors'], stats['cache_entries']) == (2, 2, 0)
    stats = json.loads(model_con.execute('SELECT jev_laya_reset()').fetchone()[0])
    assert (stats['evaluations'], stats['errors'], stats['cache_entries']) == (0, 0, 0)
    assert stats['model_directory'] == str(MODEL)


def test_native_diagnostic_nulls_without_inference(native_con):
    assert native_con.execute("""SELECT jev_eval(NULL,'Q','noul',NULL),
        jev_eval('x',NULL,'noul',NULL), jev_eval('x','Q',NULL,[]),
        jev_eval('x','Q','choice',NULL), jev_confidence('x','Q','score',NULL),
        jev_score_norm('x','Q',NULL)""").fetchone() == (None,) * 6
    # Noul uses an empty list; NULL options propagate just like other NULL arguments.
    with pytest.raises(duckdb.Error, match='jev_laya_load'):
        native_con.execute("SELECT jev_eval('x','Q','noul',[])").fetchall()
    stats = json.loads(native_con.execute('SELECT jev_laya_stats()').fetchone()[0])
    assert stats['evaluations'] == stats['errors'] == 0


def test_native_full_answers_reuse_the_decision_cache(model_con):
    model_con.execute('SELECT jev_laya_reset()')
    state = json.dumps({'current_phase': 'IN SERVICE'})
    cases = [
        ('noul', 'Is this pipeline currently in service?', []),
        ('choice', 'What is the pipeline lifecycle state?', ['in service', 'decommissioned', 'abandoned']),
        ('score', 'How active is the pipeline?', ['inactive', 'partly active', 'active']),
    ]
    for kind, question, options in cases:
        raw, dtype = model_con.execute("""SELECT jev_eval(CAST(? AS JSON), ?, ?, ?),
            typeof(jev_eval(CAST(? AS JSON), ?, ?, ?))""",
            [state, question, kind, options] * 2).fetchone()
        answer = json.loads(raw)
        assert dtype == 'JSON' and answer['type'] == kind
        confidence = model_con.execute('SELECT jev_confidence(CAST(? AS JSON), ?, ?, ?)',
                                       [state, question, kind, options]).fetchone()[0]
        assert confidence == answer['confidence']
        assert 0 <= answer['action']['act_probability'] <= 1
        if kind == 'noul':
            assert answer['noul'] == model_con.execute('SELECT jev_prob(CAST(? AS JSON), ?)',
                                                       [state, question]).fetchone()[0] == 0.8996
            assert confidence == answer['answer_confidence'] == 0.8996
        else:
            probabilities = answer['probabilities']
            if kind == 'choice':
                assert list(probabilities) == options
                assert answer['choice'] == model_con.execute('SELECT jev_choice(CAST(? AS JSON), ?, ?)',
                                                             [state, question, options]).fetchone()[0]
                probabilities = list(probabilities.values())
            else:
                assert answer['legend'] == options
                score, norm = model_con.execute("""SELECT jev_score(CAST(? AS JSON), ?, ?),
                    jev_score_norm(CAST(? AS JSON), ?, ?)""", [state, question, options] * 2).fetchone()
                assert score == answer['score'] and norm == score / (len(options) - 1)
                assert score == pytest.approx(sum(i * p for i, p in enumerate(probabilities)), abs=0.0005)
            assert sum(probabilities) == pytest.approx(1, abs=0.0002)
            assert all(0 <= p <= 1 for p in probabilities)
            assert answer['answer_confidence'] == max(probabilities)
            entropy_confidence = 1 + sum(p * math.log(p) for p in probabilities if p) / math.log(len(options))
            assert confidence == pytest.approx(entropy_confidence, abs=0.001)
    stats = json.loads(model_con.execute('SELECT jev_laya_stats()').fetchone()[0])
    assert (stats['evaluations'], stats['cache_entries'], stats['errors']) == (3, 3, 0)


def test_native_single_level_normalized_score(model_con):
    assert model_con.execute("SELECT jev_score_norm('pipeline','Rate the state',['only level'])").fetchone() == (0.0,)


def test_native_dynamic_kinds_and_nullable_list_entries(model_con):
    rows = model_con.execute("""SELECT id, jev_eval({'current_phase':'IN SERVICE'}, 'State?', kind, options)
        FROM (VALUES
            (0, 'noul', []::VARCHAR[]),
            (1, 'choice', ['in service', 'abandoned']),
            (2, 'score', ['inactive', 'active']),
            (3, 'noul', []),
            (4, 'choice', NULL),
            (5, NULL, ['a']),
            (6, 'invalid', ['a'])
        ) t(id,kind,options) WHERE id < 6 ORDER BY id""").fetchall()
    assert [json.loads(raw)['type'] if raw else None for _, raw in rows] == [
        'noul', 'choice', 'score', 'noul', None, None]
    assert rows[0][1] == rows[3][1]


def test_native_cache_separates_delimiters_options_and_rows(model_con):
    model_con.execute('SELECT jev_laya_reset()')
    # These option lists collide if concatenated with upstream's record-separator delimiter.
    lists = [['in\x1eservice', 'abandoned'], ['in', 'service\x1eabandoned']]
    for options in lists:
        model_con.execute("SELECT jev_choice('IN SERVICE','State?',?)", [options]).fetchone()
    for i in (60, 68, 60, 68):
        model_con.execute("SELECT jev_prob(to_json({'id':?, 'label':?}), 'Does the label mention 68?')",
                          [i, f'row {i}']).fetchone()
    stats = json.loads(model_con.execute('SELECT jev_laya_stats()').fetchone()[0])
    assert (stats['evaluations'], stats['cache_entries'], stats['cache_hits']) == (4, 4, 2)


def test_native_shared_cache_and_successful_model_reload(model_con):
    model_con.execute('SELECT jev_laya_reset()')
    sql = "SELECT jev_eval({'current_phase':'IN SERVICE'},'Is this pipeline currently in service?','noul',[])"
    expected = model_con.execute(sql).fetchone()
    with model_con.cursor() as other:
        assert other.execute(sql).fetchone() == expected
        stats = json.loads(other.execute('SELECT jev_laya_stats()').fetchone()[0])
        assert (stats['evaluations'], stats['cache_hits']) == (1, 1)
        NativeRuntime(other, MODEL)
    stats = json.loads(model_con.execute('SELECT jev_laya_stats()').fetchone()[0])
    assert (stats['evaluations'], stats['cache_entries'], stats['errors']) == (0, 0, 0)
    assert model_con.execute(sql).fetchone() == expected
    assert json.loads(model_con.execute('SELECT jev_laya_stats()').fetchone()[0])['evaluations'] == 1


@pytest.mark.parametrize('part,overhead', [('state', 5), ('prompt', 5), ('option', 8)])
def test_native_input_byte_limit_before_model_load(native_con, part, overhead):
    for extra in (0, 1):
        value = 'x' * (1_048_576 - overhead + extra)
        state, prompt, kind, options = 'x', 'Q', 'noul', []
        if part == 'state':
            state = value
        elif part == 'prompt':
            prompt = value
        else:
            kind, options = 'choice', [value]
        with pytest.raises(duckdb.Error, match='1 MiB' if extra else 'jev_laya_load'):
            native_con.execute('SELECT jev_eval(?,?,?,?)', [state, prompt, kind, options]).fetchall()
    stats = json.loads(native_con.execute('SELECT jev_laya_stats()').fetchone()[0])
    assert stats['evaluations'] == stats['cache_entries'] == stats['errors'] == 0


def test_native_cache_preserves_state_types_and_json_normalization(model_con):
    model_con.execute('SELECT jev_laya_reset()')
    prompt = 'Is this pipeline currently in service?'
    literal = '{"current_phase":"IN SERVICE"}'
    structured = model_con.execute('SELECT jev_prob(CAST(? AS JSON),?)', [literal, prompt]).fetchone()
    assert model_con.execute('SELECT jev_prob(CAST(? AS JSON),?)',
                             ['{ "current_phase" : "IN SERVICE" }', prompt]).fetchone() == structured
    assert model_con.execute("SELECT jev_prob({'current_phase':'IN SERVICE'},?)",
                             [prompt]).fetchone() == structured
    text = model_con.execute('SELECT jev_prob(?,?)', [literal, prompt]).fetchone()
    assert model_con.execute('SELECT jev_prob(CAST(? AS JSON),?)',
                             [json.dumps(literal), prompt]).fetchone() == text
    stats = json.loads(model_con.execute('SELECT jev_laya_stats()').fetchone()[0])
    assert (stats['evaluations'], stats['cache_entries'], stats['cache_hits']) == (2, 2, 3)


def test_native_cached_answer_does_not_accept_invalid_requests(model_con):
    model_con.execute('SELECT jev_laya_reset()')
    model_con.execute("SELECT jev_prob('IN SERVICE','State?')").fetchone()
    for sql in (
        "SELECT jev_eval('IN SERVICE','State?','noul',['unexpected'])",
        "SELECT jev_eval('IN SERVICE','State?','unknown',[])",
        "SELECT jev('IN SERVICE','State?',2.0)",
        "SELECT jev_prob('IN SERVICE','')",
    ):
        with pytest.raises(duckdb.Error):
            model_con.execute(sql).fetchall()
    model_con.execute("SELECT jev_prob('IN SERVICE','State?')").fetchone()
    stats = json.loads(model_con.execute('SELECT jev_laya_stats()').fetchone()[0])
    assert (stats['evaluations'], stats['cache_entries'], stats['cache_hits'], stats['errors']) == (1, 1, 1, 0)
