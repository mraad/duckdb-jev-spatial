"""Replay native SQL/Laya through tev1 without invoking the legacy metadata loader. Run from the project root."""
import argparse, json, sys, time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
root = Path.cwd()
sys.path.insert(0, str(root/'scripts'))
from evaluate_tev1 import load_framework, build_records, local_decision, score
from smoke_northsea import sha256, QUESTION
from jev_spatial.engine import connect, NativeRuntime

database=Path('data/NorthSea/NorthSea.ddb')
extension=Path('native/dist/jev_spatial.duckdb_extension')
model=Path('models/laya-onnx/laya')
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('output',type=Path,help='New output directory')
output=parser.parse_args().output
output.mkdir(exist_ok=False)
framework=load_framework(Path('../tev1'))
plan_path=Path('artifacts/northsea-native-smoke.json')
plan=json.loads(plan_path.read_text())
before=sha256(database)
with patch('socket.socket.connect',side_effect=RuntimeError('Network disabled')), patch('socket.create_connection',side_effect=RuntimeError('Network disabled')), connect(database,extension) as con:
    records,baseline=build_records(con)
    cursor=con.execute(plan['sql'],plan['parameters'])
    index=[c[0] for c in cursor.description].index('OBJECTID')
    actual={row[index] for row in cursor.fetchall()}
    assert actual==baseline
    sql_cache={QUESTION:actual}
    runtime=NativeRuntime(con,model)
    results=[]
    for row in records:
        decision=json.loads(framework.payload(row,'local-rust-laya-and-duckdb')['messages'][1]['content'])
        assert set(decision)=={'state','question','options'}
        out={k:row[k] for k in ['id','source','group_id']}
        out.update(expected=row['answer'],expected_key=row['answer_key'],status=0,valid=False,correct=False,predicted_key=None)
        start=time.perf_counter()
        try:
            response,details=local_decision(decision,con,None,sql_cache)
            chosen=framework.select(response,row['options'])
            out.update(status=200,valid=True,correct=chosen['label']==row['answer'],predicted_key=chosen['key'],**details)
        except Exception as error:
            out.update(error=type(error).__name__,message=str(error))
        out['latency_ms']=round((time.perf_counter()-start)*1000,3)
        results.append(out)
    report={'finished_utc':datetime.now(timezone.utc).isoformat(),'provider':'local_in_process','backend':'local-rust-laya-and-duckdb',
        'framework':str(Path('../tev1').resolve()),'framework_sha256':{str(p):sha256(Path('../tev1')/p) for p in map(Path,['examples/decide.py','scripts/evaluate.py'])},
        'extension_sha256':sha256(extension),'model':str(model.resolve()),'weights_sha256':sha256(model/'model.onnx.data'),
        'database_sha256':before,'database_unchanged':before==sha256(database),'python_socket_connections_blocked':True,
        'sql_full_id_set_matches_baseline':actual==baseline,'sql_full_result_count':len(actual),
        'query_source':str(plan_path),'query_source_sha256':sha256(plan_path),'english_planner_revalidated':False,
        'planner_limitation':'Current NorthSea no longer contains okf/Wells.md and okf/Pipelines.md; knowledge/datasets also changes the required depth alias. Replayed previously compiled SQL; no metadata validation bypass in production code.',
        'scope':'Real tev1 payload/select/summarize with current NorthSea reference answers, stored compiled spatial SQL, and local native Laya decisions; no held-out accuracy claim.',
        'transport_note':'status 200 denotes local success; no HTTP requests were made; Python socket guard is not a native-library firewall.',
        'laya_unique_evaluations':runtime.evaluations,**score(framework,results),
        'by_source':{s:score(framework,[r for r in results if r['source']==s]) for s in sorted({r['source'] for r in results})},
        'failures':[r for r in results if not r['correct']]}
    previous={r['id']:r for r in map(json.loads,Path('artifacts/tev1-native/results.jsonl').read_text().splitlines())}
    report['matches_previous_predictions_and_probabilities']=all((r['predicted_key'],r.get('probability'))==(previous[r['id']]['predicted_key'],previous[r['id']].get('probability')) for r in results)
    (output/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    for filename,rows in [('records.jsonl',records),('results.jsonl',results)]:
        (output/filename).write_text(''.join(json.dumps(r)+'\n' for r in rows))
    assert report['valid']==report['requests']==256 and report['database_unchanged'] and report['matches_previous_predictions_and_probabilities']
    print(json.dumps({k:report[k] for k in ['requests','correct','valid','sql_full_result_count','matches_previous_predictions_and_probabilities','database_unchanged','english_planner_revalidated']},indent=2))
