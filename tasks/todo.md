# Consolidate documentation into README.md (ASD STE100 spirit)

- [x] Back up README, CLAUDE.md, docs/ to `backups/before-readme-consolidation-<UTC>/` with hash manifest, readback verified
- [x] Fix one term per concept (terms table); use only these terms across README
- [x] Split by topic (user choice): README (summary, terms, setup, examples, candidate-table procedure) + docs/sql-reference.md, docs/python-planner.md, docs/design.md, docs/validation.md
- [x] Merge every distinct fact from the 8 docs; state each fact once
- [x] Apply STE rules: active voice, one instruction per sentence, one action per step, procedural sentences <= 20 words, descriptive <= 25 words, notes before the step they apply to
- [x] Delete the 8 merged docs and overview.html (diagram moved to Mermaid in docs/design.md)
- [x] Update CLAUDE.md doc pointers
- [x] Prove: no broken links, key facts/numbers from old docs present, sentence-length report, all SQL/shell blocks byte-identical to source or native/*.sql; write `artifacts/readme-consolidation-validation.json`
