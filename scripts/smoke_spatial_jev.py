"""Run the mixed SQL examples locally and compare them with exact status filters."""
import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jev_spatial.engine import connect
from smoke_northsea import sha256


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    sql_path = Path('native/spatial-jev-examples.sql')
    extension = Path('native/dist/jev_spatial.duckdb_extension')
    parts = re.split(r'^-- example: (\w+)\n', sql_path.read_text(), flags=re.M)
    assert len(parts) == 9, 'Expected four examples'
    before = sha256(args.database)
    report = {
        'checked_at': datetime.now(timezone.utc).isoformat(),
        'database': str(args.database), 'database_sha256_before': before,
        'sql_sha256': sha256(sql_path), 'extension_sha256': sha256(extension),
        'model_weights_sha256': sha256(Path('models/laya-onnx/laya/model.onnx.data')),
        'backend': 'local Rust Laya and DuckDB spatial',
        'scope': 'Example execution and full result comparison with exact status filters',
        'examples': {},
    }
    with patch('socket.socket.connect', side_effect=RuntimeError('Network disabled')), \
         patch('socket.create_connection', side_effect=RuntimeError('Network disabled')), \
         connect(args.database, extension) as con:
        con.execute(parts[0])
        for name, sql in zip(parts[1::2], parts[2::2]):
            actual = con.execute(sql).fetchall()
            # Use coded attributes and an alternative spatial expression for the reference.
            baseline = re.sub(
                r"jev\(\{'current_phase': p.current_phase\},\s*"
                r"'Is this pipeline currently in service\?'\)",
                "p.current_phase = 'IN SERVICE'", sql)
            baseline = re.sub(
                r"jev_choice\(\{'current_phase': p.current_phase\},\s*"
                r"'What is the pipeline lifecycle state\?',\s*"
                r"\['in service', 'decommissioned', 'abandoned'\]\) = 'in service'",
                "p.current_phase = 'IN SERVICE'", baseline)
            baseline = re.sub(
                r"jev_prob\(\{'status': w.status\},\s*"
                r"'Does the well status explicitly say that it is operating\?'\) >= 0.5",
                "w.status = 'COMPLETED (OPERATING)'", baseline)
            baseline = baseline.replace('ST_DWithin(w.geom_m, p.geom_m, 5000)',
                                        'ST_Distance(w.geom_m, p.geom_m) <= 5000')
            baseline = baseline.replace('ST_Contains(d.geometry, w.geometry)',
                                        'ST_Within(w.geometry, d.geometry)')
            baseline = baseline.replace('ST_Intersects(p.geometry, d.geometry)',
                                        'ST_Intersects(d.geometry, p.geometry)')
            assert not re.search(r'\bjev(?:_choice|_prob)?\(', baseline)
            expected = con.execute(baseline).fetchall()
            actual_by_id = {row[0]: row for row in actual}
            expected_by_id = {row[0]: row for row in expected}
            assert len(actual_by_id) == len(actual), 'Duplicate primary IDs'
            wrong_pairs = [
                key for key in actual_by_id.keys() & expected_by_id.keys()
                if len(actual_by_id[key]) == 3
                and (actual_by_id[key][1] != expected_by_id[key][1]
                     or not math.isclose(actual_by_id[key][2], expected_by_id[key][2],
                                         rel_tol=0, abs_tol=1e-6))
            ]
            false_positives = sorted(actual_by_id.keys() - expected_by_id.keys())
            false_negatives = sorted(expected_by_id.keys() - actual_by_id.keys())
            report['examples'][name] = {
                'count': len(actual), 'exact_count': len(expected),
                'matches_exact': not (false_positives or false_negatives or wrong_pairs),
                'false_positive_ids': false_positives, 'false_negative_ids': false_negatives,
                'wrong_nearest_pair_ids': sorted(wrong_pairs),
                'rows': actual, 'sql': sql.strip(), 'reference_sql': baseline.strip(),
            }
        report['well_status_evidence'] = con.execute("""
            SELECT status, count(*) AS wells,
                   jev_prob({'status': status},
                     'Does the well status explicitly say that it is operating?') AS probability
            FROM deep_wells_m GROUP BY status ORDER BY status
        """).fetchall()
        report['runtime'] = json.loads(con.execute('SELECT jev_laya_stats()').fetchone()[0])
    report['database_sha256_after'] = sha256(args.database)
    report['database_unchanged'] = before == report['database_sha256_after']
    assert report['database_unchanged']
    assert report['runtime']['errors'] == 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({name: {k: v for k, v in result.items()
                             if k not in {'rows', 'sql', 'reference_sql'}}
                      for name, result in report['examples'].items()}, indent=2))
    # Semantic mismatches are evidence, not SQL execution failures.


if __name__ == '__main__':
    main()
