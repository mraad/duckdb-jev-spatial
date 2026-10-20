import argparse
from dataclasses import asdict, replace
import json
from pathlib import Path

from .catalog import Catalog
from .engine import NativeRuntime, connect, execute, load_agent, register_jev
from .planner import compile_query, parse_question


def main():
    parser = argparse.ArgumentParser(description="Compile NorthSea spatial English into exact SQL, with optional local Jev filtering")
    parser.add_argument("question")
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--project", type=Path, help="Defaults to database parent")
    parser.add_argument("--run", action="store_true", help="Execute; default prints the plan and SQL")
    parser.add_argument("--semantic", help="Additional text-only Laya condition")
    parser.add_argument("--fields", nargs="+", default=[], help="Text fields to expose to Laya")
    parser.add_argument("--model", default="models/laya")
    parser.add_argument("--extension", type=Path, help="Local Rust .duckdb_extension; --model must then name an ONNX directory")
    parser.add_argument("--device", default="cpu", choices=["cpu", "mps", "cuda"])
    parser.add_argument("--max-evaluations", type=int, default=1000)
    args = parser.parse_args()
    if args.extension and (args.device != 'cpu' or args.max_evaluations != 1000):
        parser.error('The native backend uses CPU and a fixed 1000-evaluation budget')
    try:
        catalog = Catalog(args.project or args.database.parent)
        query = replace(parse_question(args.question, catalog), semantic=args.semantic, semantic_fields=tuple(args.fields))
        compiled = compile_query(query, catalog)
        output = {"plan": asdict(query), "where": compiled.where, "sql": compiled.sql,
                  "parameters": compiled.parameters, "interpretation": catalog.notes, "sources": catalog.sources}
        if compiled.semantic:
            output["semantic_stage"] = {"where": compiled.semantic_where,
                                        "question": compiled.semantic, "fields": compiled.semantic_fields,
                                        "execution": "Materialize exact candidates, then evaluate Laya"}
        if args.run:
            with connect(args.database, args.extension) as con:
                catalog.validate_database(con)
                runtime = None
                if args.semantic:
                    runtime = NativeRuntime(con, args.model) if args.extension else register_jev(
                        con, load_agent(args.model, args.device), max_evaluations=args.max_evaluations)
                output['backend'] = 'rust-laya-onnx' if args.extension else 'python-laya'
                cursor = execute(con, compiled, runtime)
                columns = [d[0] for d in cursor.description]
                rows = cursor.fetchall()
                output["count"] = len(rows)
                output["rows"] = [{k: v for k, v in zip(columns, row) if k != "geometry"} for row in rows]
                if runtime:
                    output["model_evaluations"] = runtime.evaluations
        print(json.dumps(output, indent=2, ensure_ascii=False, default=str))
    except (ValueError, FileNotFoundError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
