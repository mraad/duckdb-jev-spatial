"""Read-only NorthSea check: exact IDs vs independent baseline, plus optional real Laya."""
import argparse
from dataclasses import asdict, replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import duckdb
from jev_spatial import Catalog, compile_query, parse_question, register_jev
from jev_spatial.engine import NativeRuntime, connect, execute, load_agent

QUESTION = 'Show all wells with depth > 350 meters and are within 5 km of an in-service pipeline'


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--model', help='Local checkpoint: also exercise real Laya')
    parser.add_argument('--device', default='cpu')
    parser.add_argument('--extension', type=Path, help='Local Rust extension; --model must then be an ONNX directory')
    parser.add_argument('--output', type=Path, default=Path('artifacts/northsea-smoke.json'))
    args = parser.parse_args()
    if args.extension and args.device != 'cpu':
        parser.error('Native validation uses CPU')
    before = sha256(args.database)
    catalog = Catalog(args.database.parent)
    query = parse_question(QUESTION, catalog)
    compiled = compile_query(query, catalog)
    report = {'tested_at': datetime.now(timezone.utc).isoformat(),
              'question': QUESTION, 'database': str(args.database), 'database_sha256': before,
              'python': platform.python_version(), 'duckdb': duckdb.__version__,
              'plan': asdict(query), 'sql': compiled.sql, 'parameters': compiled.parameters,
              'sources': catalog.sources, 'interpretation': catalog.notes}
    report['backend'] = 'rust-laya-onnx' if args.extension else 'python-laya'
    report['extension_sha256'] = sha256(args.extension) if args.extension else None
    with connect(args.database, args.extension) as con:
        catalog.validate_database(con)
        report['wells'], report['depth_candidates'] = con.execute('SELECT count(*), count(*) FILTER(WHERE water_depth > 350) FROM Wells').fetchone()
        report['pipelines'], report['in_service_pipelines'] = con.execute("SELECT count(*), count(*) FILTER(WHERE current_phase='IN SERVICE') FROM Pipelines").fetchone()
        start = time.perf_counter()
        cursor = execute(con, compiled)
        columns = [d[0] for d in cursor.description]
        rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
        report['exact_seconds'] = time.perf_counter() - start
        actual = {row['OBJECTID'] for row in rows}
        # Different relational plan and distance predicate; do not call the compiler here.
        baseline = con.execute('''
            WITH w AS MATERIALIZED (
                SELECT OBJECTID, water_depth,
                    ST_Transform(geometry, 'EPSG:4326', 'EPSG:3035', always_xy:=true) g
                FROM Wells WHERE geometry IS NOT NULL AND NOT ST_IsEmpty(geometry)
            ), p AS MATERIALIZED (
                SELECT ST_Transform(geometry, 'EPSG:4326', 'EPSG:3035', always_xy:=true) g
                FROM Pipelines WHERE current_phase='IN SERVICE'
                    AND geometry IS NOT NULL AND NOT ST_IsEmpty(geometry)
            )
            SELECT w.OBJECTID, min(ST_Distance(w.g,p.g)) nearest_m
            FROM w CROSS JOIN p WHERE w.water_depth > 350
            GROUP BY w.OBJECTID HAVING nearest_m <= 5000 ORDER BY w.OBJECTID
        ''').fetchall()
        expected = {row[0] for row in baseline}
        assert actual == expected, f'ID mismatch: {actual ^ expected}'
        assert len(actual) == len(rows), 'Duplicate wells from spatial relation'
        report.update(result_count=len(rows), baseline_count=len(expected), exact_ids_match=True,
                      result_ids=sorted(actual), deterministic_model_calls=0,
                      examples=[{'OBJECTID': row[0], 'nearest_pipeline_m': row[1]} for row in baseline[:5]])
        report['explain'] = con.execute('EXPLAIN ' + compiled.sql, compiled.parameters).fetchone()[1]
        if args.model:
            start = time.perf_counter()
            if args.extension:
                runtime = NativeRuntime(con, args.model)
            else:
                agent = load_agent(args.model, args.device)
                runtime = register_jev(con, agent)
            report['model_load_seconds'] = time.perf_counter() - start
            report['model'] = {'path': args.model, 'device': 'cpu' if args.extension else str(agent.device),
                               'weights_sha256': sha256(Path(args.model) / ('model.onnx.data' if args.extension else 'model.safetensors'))}
            start = time.perf_counter()
            statuses = con.execute('''
                SELECT current_phase,
                    jev_prob({'current_phase': current_phase}, 'Is this pipeline currently in service?') probability,
                    jev({'current_phase': current_phase}, 'Is this pipeline currently in service?') accepted,
                    jev_choice({'current_phase': current_phase}, 'What is the pipeline lifecycle state?',
                               ['in service', 'decommissioned', 'abandoned']) AS lifecycle,
                    jev_score({'current_phase': current_phase}, 'How active is this pipeline?',
                              ['inactive', 'active']) activity_score
                FROM (SELECT DISTINCT current_phase FROM Pipelines) ORDER BY current_phase
            ''').fetchall()
            report['laya_status_probe'] = [dict(zip(['phase','probability','accepted','choice','score'], r)) for r in statuses]
            assert all(r[2] == (r[0] == 'IN SERVICE') for r in statuses), 'Status predicate smoke failed'
            report['laya_probe_evaluations'] = runtime.evaluations
            report['laya_probe_seconds'] = time.perf_counter() - start
            hybrid = compile_query(replace(query, semantic='Does the well status explicitly say that it is operating?',
                                           semantic_fields=('status',)), catalog)
            start = time.perf_counter()
            hybrid_rows = execute(con, hybrid, runtime).fetchall()
            hybrid_ids = {r[0] for r in hybrid_rows}
            assert hybrid_ids <= actual
            # Report semantic quality separately: a plumbing test is not a domain evaluation.
            known_operating = {r['OBJECTID'] for r in rows if r['status'] == 'COMPLETED (OPERATING)'}
            report['hybrid'] = {'where': hybrid.semantic_where, 'question': hybrid.semantic,
                                'count': len(hybrid_rows), 'unique_states_evaluated': runtime.evaluations,
                                'batches': getattr(runtime, 'batches', None), 'seconds': time.perf_counter() - start,
                                'known_operating_count': len(known_operating),
                                'false_positive_ids': sorted(hybrid_ids - known_operating),
                                'false_negative_ids': sorted(known_operating - hybrid_ids)}
            evidence = con.execute('''
                SELECT status, n, jev_prob({'status': status}, ?) probability
                FROM (SELECT status, count(*) n FROM _jev_candidates GROUP BY status)
                ORDER BY status
            ''', [hybrid.semantic]).fetchall()
            report['hybrid']['status_evidence'] = [dict(zip(['status', 'rows', 'probability'], r)) for r in evidence]
    report['database_unchanged'] = before == sha256(args.database)
    assert report['database_unchanged']
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
    print(json.dumps({k: v for k, v in report.items() if k not in {'explain','result_ids','sources','sql','plan'}}, indent=2))


if __name__ == '__main__':
    main()
