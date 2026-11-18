# Consolidate documentation into README.md (ASD STE100 spirit)

- [x] Back up README, CLAUDE.md, docs/ to `backups/before-readme-consolidation-<UTC>/` with hash manifest, readback verified
- [x] Fix one term per concept (terms table); use only these terms across README
- [x] Split by topic (user choice): README (summary, terms, setup, examples, candidate-table procedure) + docs/sql-reference.md, docs/python-planner.md, docs/design.md, docs/validation.md
- [x] Merge every distinct fact from the 8 docs; state each fact once
- [x] Apply STE rules: active voice, one instruction per sentence, one action per step, procedural sentences <= 20 words, descriptive <= 25 words, notes before the step they apply to
- [x] Delete the 8 merged docs and overview.html (diagram moved to Mermaid in docs/design.md)
- [x] Update CLAUDE.md doc pointers
- [x] Prove: no broken links, key facts/numbers from old docs present, sentence-length report, all SQL/shell blocks byte-identical to source or native/*.sql; write `artifacts/readme-consolidation-validation.json`

# DuckDB 2.x migration and larger-model trial (2026-11-18)

Feasibility checked: PyPI `duckdb` 2.0.0 and crate `duckdb` 1.20000.2 exist; the Homebrew DuckDB 2.0.0 shell opens `NorthSea.ddb` read-only with spatial. Laya (ModernBERT-large, 421M) is already the largest Laya checkpoint, so "larger model" means a general LLM.

## A. DuckDB 2.x
- [x] Back up the project to `backups/before-duckdb2-<UTC>/` with a hash manifest
- [x] Bump pins: `pyproject.toml` (`duckdb==2.0.0`), `uv.lock`, `native/Cargo.toml` (`duckdb = "=1.20000.2"`), `Cargo.lock`, entrypoint `min_duckdb_version`, `scripts/build_extension.py` version check and footer
- [x] No Rust change needed: footer now declares stable V1 C API v1.5.6; rebuilt the extension
- [x] Rerun all checks: pytest, cargo test, clippy, rustfmt, NorthSea smoke, exact and mixed SQL examples (now also in the `duckdb` shell), tev1, capitals example
- [x] Compare each result with the 1.5.6 records; results must be equal
- [x] Update `CLAUDE.md`, `README.md`, `docs/` pins and `docs/validation.md`; write `artifacts/duckdb2-validation.json`

## B. Larger model
- [x] (local Qwen3-8B done; Claude blocked: no ANTHROPIC_API_KEY) Run the capitals question on the 20 example cities and the 20 other cities with the chosen larger model
- [x] Record results in `artifacts/`, `docs/validation.md`, and the published page
