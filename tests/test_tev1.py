import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from evaluate_tev1 import build_records, load_framework, local_decision, record, score
from jev_spatial import register_jev
from jev_spatial.engine import load_agent


@pytest.fixture
def tev1():
    root = Path(__file__).resolve().parents[2] / 'tev1'
    if not (root / 'scripts/evaluate.py').is_file():
        pytest.skip('Requires the adjacent tev1 framework')
    return load_framework(root)


def test_real_framework_and_independent_sql_gold(tev1, catalog, con):
    records, ids = build_records(con)
    assert ids == {1, 5}
    cache = {}
    for row in records:
        if row['source'] != 'spatial_sql_membership':
            continue
        body = tev1.payload(dict(row, answer='DO NOT LEAK', provenance={'secret': True}), 'local')
        decision = json.loads(body['messages'][1]['content'])
        assert set(decision) == {'state', 'question', 'options'}
        response, _ = local_decision(decision, con, catalog, cache)
        assert tev1.select(response, row['options'])['key'] == row['answer_key']


def test_framework_preserves_semantic_errors(tev1, catalog, con, agent):
    register_jev(con, agent)
    row = record('one', 'semantic', 'one', {'mode': 'noul', 'model_state': {'status': 'operating'}},
                 'Is this operating?', [('no', 'No'), ('yes', 'Yes')], 'no')
    decision = json.loads(tev1.payload(row, 'local')['messages'][1]['content'])
    response, details = local_decision(decision, con, catalog, {})
    chosen = tev1.select(response, row['options'])
    assert chosen['key'] == 'yes' and chosen['label'] == 'B'
    summary = score(tev1, [{'correct': False, 'valid': True, 'status': 200, 'expected_key': 'no'}])
    assert summary['accuracy'] == 0 and summary['valid'] == 1 and details['probability'] == 0.9


def test_model_requires_local_files(tmp_path):
    with pytest.raises(FileNotFoundError, match='downloaded local Laya checkpoint'):
        load_agent(tmp_path / 'not-downloaded')
