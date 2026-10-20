"""Use tev1's real payload/select/summarize functions to evaluate local SQL and Laya."""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jev_spatial import Catalog, compile_query, parse_question, register_jev
from jev_spatial.engine import NativeRuntime, connect, execute, load_agent
from smoke_northsea import QUESTION, sha256


def load_framework(root):
    root = root.resolve()
    sys.path.insert(0, str(root))
    spec = importlib.util.spec_from_file_location('tev1_evaluator', root / 'scripts/evaluate.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def record(identifier, source, group, state, question, choices, answer_key):
    options = [{'label': chr(65+i), 'key': key, 'description': description}
               for i, (key, description) in enumerate(choices)]
    answer = next(o['label'] for o in options if o['key'] == answer_key)
    return {'id': identifier, 'source': source, 'group_id': group, 'state': state,
            'question': question, 'options': options, 'answer': answer, 'answer_key': answer_key}


def build_records(con):
    # Independent of parse_question/compile_query: exhaustive projected distance baseline.
    ids = {r[0] for r in con.execute('''
        WITH w AS MATERIALIZED (
            SELECT OBJECTID, ST_Transform(geometry,'EPSG:4326','EPSG:3035',always_xy:=true) g
            FROM Wells WHERE water_depth > 350 AND geometry IS NOT NULL AND NOT ST_IsEmpty(geometry)
        ), p AS MATERIALIZED (
            SELECT ST_Transform(geometry,'EPSG:4326','EPSG:3035',always_xy:=true) g
            FROM Pipelines WHERE current_phase='IN SERVICE'
                AND geometry IS NOT NULL AND NOT ST_IsEmpty(geometry)
        )
        SELECT w.OBJECTID FROM w CROSS JOIN p GROUP BY w.OBJECTID
        HAVING min(ST_Distance(w.g,p.g)) <= 5000
    ''').fetchall()}
    all_wells = con.execute('SELECT OBJECTID, water_depth FROM Wells ORDER BY OBJECTID').fetchall()
    # Balance positives with both deep/far and shallow negatives; selection precedes inference.
    deep = [oid for oid, depth in all_wells if oid not in ids and depth is not None and depth > 350]
    shallow = [oid for oid, depth in all_wells if oid not in ids and (depth is None or depth <= 350)]
    selected = sorted(ids) + deep[:len(ids)//2] + shallow[:len(ids)-len(ids)//2]
    records = []
    for oid in selected:
        records.append(record(f'sql-{oid}', 'spatial_sql_membership', f'well-{oid}',
                              {'mode': 'sql', 'request': QUESTION, 'OBJECTID': oid},
                              'Does this well belong in the result of the requested spatial filter?',
                              [('yes', 'Matches the requested filter'), ('no', 'Does not match')],
                              'yes' if oid in ids else 'no'))
    cases = []
    phases = [r[0] for r in con.execute('SELECT DISTINCT current_phase FROM Pipelines ORDER BY 1').fetchall()]
    for i, phase in enumerate(phases):
        cases.append((f'pipeline-{i}', 'laya_pipeline_noul', {'current_phase': phase},
                      'Is this pipeline currently in service?', phase == 'IN SERVICE'))
    statuses = [r[0] for r in con.execute('SELECT DISTINCT status FROM Wells ORDER BY status').fetchall()]
    for i, status in enumerate(statuses):
        cases.append((f'well-status-{i}', 'laya_well_noul', {'status': status},
                      'Does the well status explicitly say that it is operating?', status == 'COMPLETED (OPERATING)'))
    for name, source, state, question, truth in cases:
        # Reversed labels test the harness mapping, not a noul model's option-order stability.
        for order in range(2):
            choices = [('yes', 'Yes'), ('no', 'No')][::1 if order == 0 else -1]
            records.append(record(f'{name}-order{order}', source, name,
                                  {'mode': 'noul', 'model_state': state}, question,
                                  choices, 'yes' if truth else 'no'))
    choices = [('in_service', 'in service'), ('decommissioned', 'decommissioned'), ('abandoned', 'abandoned')]
    phase_keys = {'IN SERVICE': 'in_service', 'DECOMMISSIONED': 'decommissioned', 'ABANDONED IN PLACE': 'abandoned'}
    for i, phase in enumerate(phases):
        for order in range(2):
            records.append(record(f'choice-{i}-order{order}', 'laya_pipeline_choice', f'pipeline-{i}',
                                  {'mode': 'choice', 'model_state': {'current_phase': phase}},
                                  'What is the pipeline lifecycle state?', choices[::1 if order == 0 else -1], phase_keys[phase]))
    return records, ids


def local_decision(decision, con, catalog, sql_cache):
    """Only the framework-sanitized decision enters the adapter; gold is unavailable."""
    state, question, options = (decision[k] for k in ('state', 'question', 'options'))
    details = {}
    if state['mode'] == 'sql':
        request = state['request']
        if request not in sql_cache:
            compiled = compile_query(parse_question(request, catalog), catalog)
            cursor = execute(con, compiled)
            id_index = [c[0] for c in cursor.description].index('OBJECTID')
            sql_cache[request] = {r[id_index] for r in cursor.fetchall()}
        key = 'yes' if state['OBJECTID'] in sql_cache[request] else 'no'
    elif state['mode'] == 'noul':
        probability = con.execute('SELECT jev_prob(CAST(? AS JSON), ?)',
                                  [json.dumps(state['model_state']), question]).fetchone()[0]
        key = 'yes' if probability >= 0.5 else 'no'
        details['probability'] = probability
    elif state['mode'] == 'choice':
        chosen = con.execute('SELECT jev_choice(CAST(? AS JSON), ?, ?)',
                             [json.dumps(state['model_state']), question, [o['description'] for o in options]]).fetchone()[0]
        key = next(o['key'] for o in options if o['description'] == chosen)
    else:
        raise ValueError('Unknown local evaluation mode')
    label = next(o['label'] for o in options if o['key'] == key)
    return {'choices': [{'message': {'content': label}}]}, details


def score(framework, rows):
    summary = framework.summarize(rows)
    summary['by_expected_key'] = {key: framework.summarize([r for r in rows if r['expected_key'] == key])
                                  for key in sorted({r['expected_key'] for r in rows})}
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tev1', type=Path, default=Path('../tev1'))
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--model', type=Path, default=Path('models/laya'))
    parser.add_argument('--extension', type=Path, help='Use the local Rust extension and an ONNX --model directory')
    parser.add_argument('--output', type=Path, required=True, help='New output directory')
    args = parser.parse_args()
    framework = load_framework(args.tev1)
    before = sha256(args.database)
    catalog = Catalog(args.database.parent)
    args.output.mkdir(parents=True, exist_ok=False)
    model_name = 'local-rust-laya-and-duckdb' if args.extension else 'local-laya-and-duckdb'
    # Reject outbound Python socket connections even if a dependency attempts a download.
    with patch('socket.socket.connect', side_effect=RuntimeError('Network disabled for local evaluation')), \
         patch('socket.create_connection', side_effect=RuntimeError('Network disabled for local evaluation')), \
         connect(args.database, args.extension) as con:
        catalog.validate_database(con)
        records, baseline_ids = build_records(con)
        assert records and len({r['id'] for r in records}) == len(records)
        data = ''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in records)
        (args.output / 'records.jsonl').write_text(data)
        runtime = NativeRuntime(con, args.model) if args.extension else register_jev(con, load_agent(args.model), max_evaluations=1000)
        sql_cache, results = {}, []
        for row in records:
            body = framework.payload(row, model_name)
            decision = json.loads(body['messages'][1]['content'])
            assert set(decision) == {'state', 'question', 'options'}, 'Gold leakage'
            gold = next(o for o in row['options'] if o['label'] == row['answer'])
            assert gold['key'] == row['answer_key']
            out = {k: row[k] for k in ['id', 'source', 'group_id']}
            out.update(expected=row['answer'], expected_key=row['answer_key'], status=0,
                       valid=False, correct=False, predicted_key=None)
            start = time.perf_counter()
            try:
                response, details = local_decision(decision, con, catalog, sql_cache)
                chosen = framework.select(response, row['options'])
                out.update(status=200, valid=True, correct=chosen['label'] == row['answer'],
                           predicted_key=chosen['key'], **details)
            except Exception as exc:
                out.update(error=type(exc).__name__, message=str(exc))
            out['latency_ms'] = round((time.perf_counter()-start)*1000, 3)
            results.append(out)
        report = {'provider': 'local_in_process', 'model': str(args.model.resolve()),
                  'framework': str(args.tev1.resolve()), 'finished_utc': datetime.now(timezone.utc).isoformat(),
                  'framework_sha256': {str(p): sha256(args.tev1 / p) for p in map(Path, ['examples/decide.py', 'scripts/evaluate.py'])},
                  'records_sha256': hashlib.sha256(data.encode()).hexdigest(),
                  'backend': model_name,
                  'extension_sha256': sha256(args.extension) if args.extension else None,
                  'weights_format': 'onnx_external_data' if args.extension else 'safetensors',
                  'weights_sha256': sha256(args.model / ('model.onnx.data' if args.extension else 'model.safetensors')),
                  'onnx_graph_sha256': sha256(args.model / 'model.onnx') if args.extension else None,
                  'database_sha256': before, 'database_unchanged': before == sha256(args.database),
                  'offline': True, 'python_socket_connections_blocked': True,
                  'transport_note': 'status=200 denotes local success; http_errors is tev1 compatibility terminology, no HTTP requests were made',
                  'scope': 'Balanced SQL membership sample and all distinct NorthSea well/pipeline statuses; development checks, not a held-out accuracy benchmark',
                  'sql_full_id_set_matches_baseline': sql_cache.get(QUESTION) == baseline_ids,
                  'sql_full_result_count': len(sql_cache.get(QUESTION, [])),
                  'laya_unique_evaluations': runtime.evaluations,
                  'latency_note': 'Includes query/cache effects; not a throughput benchmark',
                  **score(framework, results),
                  'by_source': {s: score(framework, [r for r in results if r['source'] == s])
                                for s in sorted({r['source'] for r in results})},
                  'failures': [r for r in results if not r['correct']]}
        (args.output / 'results.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in results))
        (args.output / 'report.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report, indent=2))
        if not report['database_unchanged'] or not report['sql_full_id_set_matches_baseline'] or report['valid'] != report['requests']:
            raise SystemExit(1)


if __name__ == '__main__':
    main()
