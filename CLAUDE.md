# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A loadable Rust DuckDB extension (`native/`) that registers Jev decision functions (`jev`, `jev_prob`, `jev_choice`, `jev_score`, `jev_score_norm`, `jev_eval`, `jev_confidence`, plus `jev_laya_load/stats/reset`) backed by local Laya ONNX inference, and an optional Python package (`jev_spatial/`) that compiles a fixed English grammar into parameterized spatial SQL over the NorthSea dataset. No hosted model, no credentials, no network at runtime. Not a git repo.

Hard pins: DuckDB **1.5.6 exactly** (unstable C extension API, `C_STRUCT_UNSTABLE` footer), macOS arm64, `.venv` on Python 3.12. Sibling checkouts: `../laya` (path dependency for Python `laya` and Rust `laya-onnx`) and `../tev1` (evaluation harness).

Dataset: `data/NorthSea.tar.gz` holds the NorthSea sample data from Geodata (https://www.geodata.no/): `NorthSea.ddb`, `NorthSea.ossie.yaml`, `NorthSea.ontology.yaml`, `knowledge/datasets/{Wells,Pipelines}.md`, byte-identical to `~/Documents/ArcGIS/Projects/NorthSea` (database SHA-256 `37262552…`). Extract with `tar -xzf data/NorthSea.tar.gz -C data`; `.gitignore` tracks only `data/NorthSea.tar.gz` inside `data/`. SQL examples need only the database; the planner, smoke test and tev1 read the metadata beside it, so keep all five files together. Always open it read-only and never modify it or its metadata.

## Commands

Run from the project root.

```sh
uv sync --locked --extra dev --extra onnx-export --python 3.12
.venv/bin/python scripts/build_extension.py        # cargo build --locked --offline, writes native/dist/jev_spatial.duckdb_extension
.venv/bin/python -m pytest                         # full Python suite
.venv/bin/python -m pytest tests/test_planner.py -k test_name
cargo test --manifest-path native/Cargo.toml
cargo clippy --manifest-path native/Cargo.toml -- -D warnings
cargo fmt --manifest-path native/Cargo.toml --check
```

Rebuild the extension after editing `native/src/lib.rs`; `tests/test_native.py` loads the built `native/dist` binary, not the source. Its tests skip when the binary is missing, and its model tests skip unless `models/laya-onnx/laya/model.onnx` exists and `ORT_DYLIB_PATH` is set:

```sh
export ORT_DYLIB_PATH="$PWD/.venv/lib/python3.12/site-packages/onnxruntime/capi/libonnxruntime.1.31.0.dylib"
```

`tests/test_tev1.py` skips without `../tev1`. Report how many tests ran vs skipped.

End-to-end checks (need native setup; set `HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1`): `scripts/smoke_northsea.py --database …` (106 exact IDs vs independent baseline), `scripts/evaluate_tev1.py` (output dir must not exist), `scripts/benchmark_native.py`, and `duckdb -readonly -unsigned <db> < native/{spatial-examples,spatial-jev-examples,northsea}.sql`. Full invocations are in `docs/validation.md#run-the-checks`.

## Architecture

**Two inference backends, same SQL surface.**
- Native (`native/src/lib.rs`, single file): `Runtime` holds the Laya engine, a cache keyed on `(canonical state, prompt, kind, ordered options)`, and counters behind one mutex per database instance. All seven decision functions are `Decision<MODE>` sharing one validated answer path (`Runtime::evaluate` → `checked_answer`); controls are `Control<MODE>`. Limits: 1,000 uncached attempts per reset, 1 MiB input. Failed answers are never cached. One forward pass per distinct state, no batching. `LOAD` also runs `LOAD spatial`.
- Python (`jev_spatial/jev.py`): `register_jev` adds Arrow UDFs `_laya_noul/_laya_choice/_laya_score` plus TEMP macros for only the original four functions (`jev`, `jev_prob`, `jev_choice`, `jev_score`). It batches through `agent.predict_batch`. Tests use `FakeAgent` in `tests/conftest.py`.

**Planner pipeline** (`jev_spatial/`): `Catalog` (catalog.py) reads and SHA-256-hashes Ossie, ontology and knowledge docs and fails closed on alias, unit, CRS or identity mismatches (legacy `okf/` layout used only when `knowledge/datasets/` is absent). `parse_question` (planner.py) accepts only Wells depth + pipeline-distance English and rejects any unconsumed text. `compile_query` turns typed `Query/Filter/Relation` into parameterized SQL: one relation per query, `EXISTS`/`NOT EXISTS`, identifiers whitelisted via `identifier()`, distances computed by `ST_Transform` to EPSG:3035 with `always_xy := TRUE`, and topology on the original EPSG:4326 geometry. `engine.execute` materializes exact candidates into a TEMP table (`_jev_candidates`) **in a separate statement** before running the semantic `jev(...)` filter, because predicate order in one `WHERE` does not control inference order. `NativeRuntime` adapts the Rust control functions to the Python runtime interface. CLI: `jev-spatial` (cli.py); `--extension` selects the native backend and needs an ONNX dir for `--model`.

## Conventions

- Each change ships proof in `artifacts/`: `<change>-validation.json` (pytest/rust/clippy counts, source and database SHA-256, what was not rerun or measured) plus raw logs. Before a change, the previous state is archived in `backups/before-<change>-<UTC timestamp>/project.tar.gz` with a hash `manifest.json`.
- Keep known bad results visible: the 6/44 tev1 semantic failures (`BLOWOUT`, `COMPLETED (SHUT IN)`, `DRILLING`) and the false positives at well IDs 4918/5026. Model probabilities and confidence are not calibrated, so don't present them as reliable filters. Use exact SQL for known status codes.
- Docs: `README.md` (terms, setup, examples), `docs/sql-reference.md` (SQL interface, cache, errors, NULL handling), `docs/python-planner.md`, `docs/design.md`, `docs/validation.md`. State each fact in one file only; `docs/validation.md` alone holds current check results and the change history, so update it (not the others) after each check. Write in ASD STE100 style: terms from the README table only, active voice, one instruction per sentence, one action per numbered step, notes and cautions before the step they apply to, untested scope stated explicitly.
- `native/laya-cpu-policy.patch` is already applied in `../laya`; apply it only to a fresh Laya checkout at the documented base commit.
