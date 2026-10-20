"""Measure local native cache-hit queries; model loading and warm-up are not timed."""
import argparse
import hashlib
import json
from pathlib import Path
from statistics import median
from time import perf_counter

import duckdb


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--extension', type=Path, required=True)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--rows', type=int, default=30000)
    parser.add_argument('--repeats', type=int, default=5)
    args = parser.parse_args()
    if args.rows < 4 or args.repeats < 1:
        parser.error('Use at least four rows and one repetition')
    extension = args.extension.resolve()
    report = {'extension_sha256': hashlib.sha256(extension.read_bytes()).hexdigest(),
              'duckdb': duckdb.__version__, 'rows': args.rows, 'repeats': args.repeats,
              'scope': 'warm cache, four distinct states, one SQL thread, fetch all rows',
              'cases': {}}
    cases = {
        'probability_struct': "jev_prob(s,'Is this pipeline currently in service?')",
        'choice_struct': "jev_choice(s,'What is the pipeline lifecycle state?',"
                         "['in service','decommissioned','abandoned'])",
        'raw_score_json': "jev_eval(to_json(s),'How active is the pipeline?',"
                          "'score',['inactive','active'])",
    }
    with duckdb.connect(config={'allow_unsigned_extensions': 'true', 'threads': '1'}) as con:
        con.execute("LOAD '" + str(extension).replace("'", "''") + "'")
        con.execute('SELECT jev_laya_load(?)', [str(args.model.resolve())])
        con.execute("CREATE TEMP TABLE bench AS SELECT "
                    "{'current_phase':'IN SERVICE','id':(i%4)::VARCHAR} s FROM range(?) t(i)",
                    [args.rows])
        for name, expression in cases.items():
            con.execute('SELECT jev_laya_reset()')
            expected = con.execute(f'SELECT {expression} FROM bench LIMIT 4').fetchall()
            timings = []
            for _ in range(args.repeats):
                started = perf_counter()
                rows = con.execute(f'SELECT {expression} FROM bench').fetchall()
                timings.append(perf_counter() - started)
                assert len(rows) == args.rows
                assert all(row == expected[i % 4] for i, row in enumerate(rows))
            stats = json.loads(con.execute('SELECT jev_laya_stats()').fetchone()[0])
            assert stats['evaluations'] == stats['cache_entries'] == 4
            assert stats['errors'] == 0
            assert stats['cache_hits'] == args.rows * args.repeats
            report['cases'][name] = {
                'seconds': timings, 'median_seconds': median(timings),
                'rows_per_second': args.rows / median(timings), 'stats': stats,
                'answers_sha256': hashlib.sha256(json.dumps(expected).encode()).hexdigest()}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({name: case['median_seconds'] for name, case in report['cases'].items()}))


if __name__ == '__main__':
    main()
