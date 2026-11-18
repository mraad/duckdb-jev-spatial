# Validation

This document is the only place for current check results.
Other documents link here. They do not repeat these numbers.
The [terms](../README.md#terms) in the README apply.

## Current results

The checks ran on 2026-11-18 on macOS arm64.
They used DuckDB 2.0.0, Python 3.12, and the development build of the native extension.
All NorthSea results are equal to the DuckDB 1.5.6 results.

| Check | Result | Record |
|---|---|---|
| Python tests, with native model checks | 95 passed; 0 skipped | [duckdb2-validation.json](../artifacts/duckdb2-validation.json) |
| Native Rust unit tests | 4 passed | [duckdb2-validation.json](../artifacts/duckdb2-validation.json) |
| Clippy with warnings denied | Passed | [duckdb2-validation.json](../artifacts/duckdb2-validation.json) |
| rustfmt check | Passed | [duckdb2-validation.json](../artifacts/duckdb2-validation.json) |
| NorthSea smoke test | 106 IDs; equal to the independent baseline | [northsea-smoke.json](../artifacts/duckdb2/northsea-smoke.json) |
| Exact spatial examples in the DuckDB shell | 8 executed; counts unchanged | [duckdb2-validation.json](../artifacts/duckdb2-validation.json) |
| Mixed spatial and semantic examples | 106, 52, 4, and 600 rows; 3 false positives | [spatial-jev.json](../artifacts/duckdb2/spatial-jev.json) |
| Mixed examples in the DuckDB shell, with no Python process | Result sets equal to the previous run | [spatial-jev-shell.json](../artifacts/duckdb2/spatial-jev-shell.json) |
| Capital selection example, Laya | Threshold: 14/20 rows correct. Top city for each country: 9/10 correct | [capitals-example-validation.json](../artifacts/capitals-example-validation.json) |
| Capital question, larger models | Local Qwen3-8B: 20/20 and 18/20. gpt-5.6-sol through the Azure gateway: 20/20 and 20/20 | [larger-models.json](../artifacts/capitals-example/larger-models.json) |
| Cache benchmark, 30,000 rows from the cache | 0.295 s, 0.420 s, and 0.666 s; answers unchanged; about 36% slower than on DuckDB 1.5.6 | [benchmark.json](../artifacts/duckdb2/benchmark.json) |
| tev1 outputs | 256/256 valid; 250/256 correct | [tev1 report](../artifacts/duckdb2/tev1/report.json) |
| tev1 spatial membership cases | 212/212 correct | [tev1 report](../artifacts/duckdb2/tev1/report.json) |
| tev1 semantic cases | 38/44 correct | [tev1 report](../artifacts/duckdb2/tev1/report.json) |
| Model evaluations in the tev1 run | 25 | [tev1 report](../artifacts/duckdb2/tev1/report.json) |
| Sample English coverage | 0/3,000 sample corpus questions accepted by the planner | [metadata-update-validation.json](../artifacts/metadata-update-validation.json) |
| Source database SHA-256 | Unchanged | [duckdb2-validation.json](../artifacts/duckdb2-validation.json) |

The database SHA-256 is `37262552dc78051fcceb074e8b02f23de8783fd084f8cd3d7862a43f241b596f`.
The ONNX model weights SHA-256 is `afcc11e72b59193fbec6e5dc8b693c9baa81cf66bec53279cefd687b318c3199`.
A successful check can contain incorrect model answers.

## Model errors

### Semantic failures in tev1

The six tev1 failures are three well statuses.
Each status fails under both yes and no answer-letter mappings.

| Well status | P(explicitly operating) | Expected |
|---|---:|---|
| BLOWOUT | 0.6077 | no |
| COMPLETED (SHUT IN) | 0.6025 | no |
| DRILLING | 0.7270 | no |

### False positives in the examples

The 106 candidate rows of the README procedure contain seven distinct status states.
The model accepts wells **4918 and 5026**. Both have `DRILLING` status and probability 0.727.
Neither has the coded status `COMPLETED (OPERATING)`.
Thus both are false positives. The exact spatial result is not affected.

The candidate rows contain only `DRILLING` of the three failing statuses.
tev1 tests all 16 distinct NorthSea well statuses.
Thus tev1 also finds the `BLOWOUT` and `COMPLETED (SHUT IN)` errors.

The containment example returns areas **687, 1197, and 1341** as false positives.

### Pipeline answers

The smoke test asks three questions about each pipeline phase:

| Phase | P(in service) | `jev` | `jev_choice` | Activity score (0 to 1) |
|---|---:|---|---|---:|
| IN SERVICE | 0.8996 | true | in service | 0.9815 |
| DECOMMISSIONED | 0.2223 | false | decommissioned | 0.8982 |
| ABANDONED IN PLACE | 0.3833 | false | abandoned | 0.6886 |

The boolean and choice answers agree with the status meanings.
The activity scores are **not useful operating-status indicators**.
Inactive states also get high scores.
A valid typed number does not show that the number is useful.

Laya clamps the temperature to 0.5 for choices with 11 or more options.
No check uses 11 or more options.
This result does not show calibration for any NorthSea classification.
An operational threshold needs a separate evaluation of the model probabilities.

### Capital answers

`native/capitals-example.sql` asks "Is this city the capital of its country?" for 20 cities.
The ground truth is in the validation record only. The SQL file does not contain it.
The first three queries use the first wording. That wording was written before any result was known.

| City | Country | P(capital) | Expected | Result |
|---|---|---:|---|---|
| Istanbul | Turkey | 0.805 | no | false positive |
| Rio de Janeiro | Brazil | 0.801 | no | false positive |
| Ottawa | Canada | 0.420 | yes | false negative |
| Oslo | Norway | 0.439 | yes | false negative |
| Canberra | Australia | 0.207 | yes | false negative |
| Ankara | Turkey | 0.132 | yes | false negative |

The other 14 rows are correct.
The model tends to accept the largest or best-known city of a country.
Thus Oslo (about 1,340 km from Paris) is missing from the 1,500 km result.

#### Other wordings of the question

The check asked 10 wordings of the yes or no question.
It used two sets of cities: the 20 example cities, and 20 other cities in 10 other countries.
In each country of the second set, the largest city is not the capital.
The second set is the check for a wording that was selected on the first set.

"Top city" counts the countries where the capital has a higher probability than the other city.

| Question | Example cities, threshold 0.5 | Example cities, top city | Other cities, threshold 0.5 | Other cities, top city |
|---|---:|---:|---:|---:|
| Is this city the capital of its country? (first wording) | 14/20 | 8/10 | 14/20 | 8/10 |
| Is this city the capital city of the country? | 10/20 | 9/10 | 12/20 | 6/10 |
| Is this city the official capital of this country, where the national government has its seat? | 13/20 | 9/10 | 17/20 | 9/10 |
| Is the seat of the national government of this country in this city? | 12/20 | 8/10 | 10/20 | 5/10 |
| Is the national government of the country located in this city? | 10/20 | 6/10 | 10/20 | 6/10 |
| Is this city the political capital of the country? The largest city is not always the capital. | 12/20 | 7/10 | 8/20 | 4/10 |
| Does the national parliament of this country meet in this city? | 11/20 | 7/10 | 11/20 | 7/10 |
| Is this the capital? | 11/20 | 7/10 | 14/20 | 9/10 |
| Is {city} the capital of {country}? (names written in the question) | 13/20 | 6/10 | 14/20 | 9/10 |
| Is the capital of this country this city, and not another city? | 14/20 | 9/10 | 16/20 | 9/10 |

No wording is better than the first wording at the 0.5 threshold on the example cities.
The results change much between wordings that have the same meaning.
Thus Laya does not have reliable knowledge of capitals. A new wording does not correct this.

The fourth query in the SQL file uses the last wording and keeps the top city of each country.
It gives 9 of 10 correct countries. It selects Istanbul for Turkey.
On the other cities, it also gives 9 of 10. It selects Auckland for New Zealand.
This query assumes that the table contains the capital of each country.

A `jev_choice` question between the two cities of a country was also tested.
The answer follows the option order: 10/10 with the capital as the first option, and 5/10 with the capital as the second option.
With "What is the capital city of this country?" on the other cities, the results are 9/10 and 3/10.
Do not use `jev_choice` for this task.

The run of the SQL file used 40 model evaluations and 40 cache hits on DuckDB 2.0.0.

#### Larger models

`scripts/try_larger_models.py` asks a general language model the first wording, one city for each request.
The script is a trial. It is not a Jev backend, and SQL cannot call it.

| Model | Example cities | Other cities |
|---|---:|---:|
| Laya (421M parameters), threshold 0.5 | 14/20 | 14/20 |
| Qwen3-8B, Q4_K_M, local `llama-server` 1.0.0, no network | 20/20 | 18/20 |
| gpt-5.6-sol (deployment `gpt-5.6-sol-2026-07-09`), IST AzureAI API Gateway DEV tier | 20/20 | 20/20 |

The gpt-5.6-sol run is not local. It sends the city and country names to the gateway, and it needs a subscription key.
The gateway DEV tier is for research and development only.
Qwen3-8B answers "no" for Bern and Pretoria. gpt-5.6-sol answers "yes" for both.
Switzerland has no capital in law, and South Africa has three capitals. Thus these two answers are not clear errors.
The Claude run did not start, because the shell has no `ANTHROPIC_API_KEY`. The `--claude` option of the script is not tested.
The duckdb shell was not run, because the shell on `PATH` is version 2.0.0.

## Run the checks

Before you start, [extract the sample dataset](../README.md#extract-the-sample-dataset).
Then do the [native setup](../README.md#set-up-the-native-extension).
The unit tests do not need the sample dataset.

1. Set the ONNX Runtime library path.

   ```sh
   export ORT_DYLIB_PATH="$PWD/.venv/lib/python3.12/site-packages/onnxruntime/capi/libonnxruntime.1.31.0.dylib"
   ```

2. Set offline mode for model loading.

   ```sh
   export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
   ```

### Unit tests

The native tests load `native/dist/jev_spatial.duckdb_extension`. They do not build it.
Build the native extension again after each change to `native/src/lib.rs`.
The native tests skip if the native extension is missing.
The native model tests skip if `ORT_DYLIB_PATH` or the ONNX model is missing.
The tev1 tests skip if `../tev1` is missing.

1. Run the Python tests.

   ```sh
   .venv/bin/python -m pytest -q
   ```

2. Run the Rust unit tests.

   ```sh
   cargo test --manifest-path native/Cargo.toml --locked --offline
   ```

3. Run Clippy.

   ```sh
   cargo clippy --manifest-path native/Cargo.toml --locked --offline -- -D warnings
   ```

4. Check the Rust formatting.

   ```sh
   cargo fmt --manifest-path native/Cargo.toml --check
   ```

To run one Python test, do this step:

1. Run the test by its file and name.

   ```sh
   .venv/bin/python -m pytest tests/test_planner.py -k test_name
   ```

### NorthSea smoke test

The smoke test runs the example question through the planner.
It compares the 106 IDs with an independent baseline.
The baseline projects the geometry and makes a cross join.
It applies a grouped `MIN(ST_Distance(...)) <= 5000` condition.
The planner uses a correlated `EXISTS` with `ST_DWithin`.
Both queries use the EPSG:3035 distance policy.
Their agreement validates the compiled query. It does not show geodesic or survey accuracy.

With a model, the smoke test also runs the pipeline status probe and the semantic filter.
It checks that the semantic filter returns a subset of the candidate rows.
It checks the database hash before and after.

Note: The default output is `artifacts/northsea-smoke.json`.
Use `--output` to keep that record unchanged.

1. Run the smoke test with the native backend.

   ```sh
   .venv/bin/python scripts/smoke_northsea.py \
     --database data/NorthSea/NorthSea.ddb \
     --extension native/dist/jev_spatial.duckdb_extension \
     --model models/laya-onnx/laya --output artifacts/northsea-smoke-new.json
   ```

To use the Python backend, do this step:

1. Run the smoke test with the PyTorch checkpoint.

   ```sh
   .venv/bin/python scripts/smoke_northsea.py \
     --database data/NorthSea/NorthSea.ddb \
     --model models/laya --output artifacts/northsea-smoke-python-new.json
   ```

### Exact spatial examples

1. Run the exact examples in the DuckDB shell.

   ```sh
   duckdb -readonly -unsigned \
     data/NorthSea/NorthSea.ddb \
     < native/spatial-examples.sql
   ```

Compare the row counts with the [README table](../README.md#exact-spatial-examples).
The [first record](../artifacts/spatial-examples.json) also contains 13 cross-checks.
For example, the near and far results together contain all 600 deep wells.
A synthetic boundary point tests `ST_Within`, `ST_Contains`, `ST_Intersects`, and `ST_Touches`.

### Mixed spatial and semantic examples

The check runs the four queries in `native/spatial-jev-examples.sql`.
It compares the full result sets with exact status filters.
The distance reference uses `ST_Distance <= 5000`.
The containment reference uses `ST_Within` with reversed arguments.
The nearest-pipeline reference compares pipeline IDs and distances within one micrometer.
The check also checks unique primary IDs and the source database hash.

A semantic mismatch goes into the report. It does not cause an error exit.
A query failure or a failed integrity check stops the check.
This check uses known status codes. It is not a held-out model evaluation.

1. Run the mixed example check.

   ```sh
   .venv/bin/python scripts/smoke_spatial_jev.py \
     --database data/NorthSea/NorthSea.ddb \
     --output artifacts/spatial-jev-examples-new.json
   ```

To check the DuckDB shell without Python, do these steps:

1. Run the mixed examples with JSON output.

   ```sh
   duckdb -readonly -unsigned -json \
     data/NorthSea/NorthSea.ddb \
     < native/spatial-jev-examples.sql > artifacts/spatial-jev-shell-new.json
   ```

2. Compare the output with the current record.

   ```sh
   cmp artifacts/spatial-jev-cleanup-shell.json artifacts/spatial-jev-shell-new.json
   ```

### tev1 evaluation

The runner loads the `payload`, `select`, and `summarize` functions from `../tev1`.
These functions are in `examples/decide.py` and `scripts/evaluate.py` of the tev1 checkout.
`payload` removes the reference answers and their provenance.
Only the state, the question, and the options go to the local adapter.
Spatial cases use the planner, the compiler, and DuckDB.
Semantic cases use the Jev functions.
The adapter maps each answer to an answer letter. tev1 validates and scores that letter.
The runner never uses the hosted tev1 runner, an endpoint, or tev1 weights.
`status=200` identifies a local success.
The report keeps the tev1 field `http_errors`. The runner makes no HTTP request.

The runner blocks Python socket connections during the run.
This guard is not an operating-system firewall. It does not control native libraries.
The native extension disables Laya downloads separately.
The runner checks the database hash before and after.
It also compares the full spatial ID set with the independent baseline.

Caution: The output directory must not exist. The runner stops if it exists.

1. Run the tev1 evaluation with the native backend.

   ```sh
   .venv/bin/python scripts/evaluate_tev1.py \
     --tev1 ../tev1 --database data/NorthSea/NorthSea.ddb \
     --extension native/dist/jev_spatial.duckdb_extension \
     --model models/laya-onnx/laya --output artifacts/tev1-native-new
   ```

2. Read `correct`, `accuracy`, and `failures` in `artifacts/tev1-native-new/report.json`.

To use the Python backend, do this step:

1. Run the tev1 evaluation with the PyTorch checkpoint.

   ```sh
   HF_HOME="$PWD/.hf-cache" .venv/bin/python scripts/evaluate_tev1.py \
     --tev1 ../tev1 --database data/NorthSea/NorthSea.ddb \
     --model models/laya --output artifacts/tev1-local-repeat
   ```

| Source | Correct | Cases |
|---|---:|---|
| Spatial SQL membership | 212/212 | 106 matches, 53 deep wells that do not match, and 53 shallow or missing-depth wells |
| Pipeline `noul` | 6/6 | Three phases, two answer-letter mappings |
| Pipeline choice | 6/6 | Three phases, normal and reversed option order |
| Well status `noul` | 26/32 | All 16 distinct well statuses, two answer-letter mappings |
| Semantic subtotal | 38/44 | Pipeline and well model answers only |
| Total | 250/256 | All 256 valid; no execution errors |

`noul` does not receive ordered options.
Thus reversed answer letters test the adapter mapping, not the model.
The six choice cases change the option order for the model.
Repeated labels and states are not independent statistical trials.
The cache reduces the run to 25 model evaluations.
This is a development check on a small project vocabulary.
It is not an estimate of general model accuracy.

### Cache benchmark

The benchmark uses an in-memory database. It does not read NorthSea.

1. Run the cache benchmark.

   ```sh
   .venv/bin/python scripts/benchmark_native.py \
     --extension native/dist/jev_spatial.duckdb_extension \
     --model models/laya-onnx/laya --output artifacts/cache-benchmark-new.json
   ```

The output contains query times, answer hashes, and runtime counters.

## Not evaluated

- GPU execution.
- Platforms other than macOS arm64.
- Signed distribution.
- Optimized release builds.
- Throughput on large data.
- Cold inference speed.
- Concurrent queries.
- Memory use of the cache.
- Spatial accuracy outside the projected-distance policy, including geodesic accuracy.
- Calibrated semantic accuracy.

No training or threshold tuning used the diagnostic cases.
The checks create only temporary tables and temporary macros.
They do not change the database or the NorthSea metadata.

## Change history

| Date (UTC) | Change | Python tests | Rust tests | Result | Record |
|---|---|---:|---|---|---|
| 2026-10-19 | First Python validation | 54 | None | 106 IDs; tev1 212/212 spatial, 38/44 semantic | [tev1-local](../artifacts/tev1-local/report.json), [smoke](../artifacts/northsea-smoke.json) |
| 2026-10-19 | Native extension | 68 | 2; 17 Laya Rust library tests | All 256 tev1 predictions and probabilities equal to Python | [native-validation.json](../artifacts/native-validation.json) |
| 2026-10-19 | Exact spatial examples | Not run | Not run | 8 examples; 13 cross-checks passed | [spatial-examples.json](../artifacts/spatial-examples.json) |
| 2026-10-20 | Upstream review and diagnostic functions | 82, with 28 native | 4 | tev1 results unchanged | [upstream-review-validation.json](../artifacts/upstream-review-validation.json) |
| 2026-10-20 | Knowledge metadata layout | 100 | Not run | Planner restored on the new layout; tev1 256 valid, 250 correct | [metadata-update-validation.json](../artifacts/metadata-update-validation.json) |
| 2026-10-20 | Cache optimization | 105 | 4 | Benchmark below; answers unchanged | [optimization-validation.json](../artifacts/optimization-validation.json) |
| 2026-10-20 | Mixed spatial and semantic examples | Not recorded | Not recorded | 4 examples; tev1 256 valid, 250 correct | [spatial-jev-examples.json](../artifacts/spatial-jev-examples.json), [tev1](../artifacts/tev1-spatial-jev/report.json) |
| 2026-10-20 | Removal of the reference-plan validation tool | 93 | 4 | All results unchanged | [cleanup-validation.json](../artifacts/cleanup-validation.json) |
| 2026-10-20 | Documentation consolidation | Not run | Not run | 10 files merged into 5; no broken links | [readme-consolidation-validation.json](../artifacts/readme-consolidation-validation.json) |
| 2026-10-20 | Local sample dataset archive | Not run | Not run | Archive files equal to the source; examples unchanged | [local-data-validation.json](../artifacts/local-data-validation.json) |
| 2026-11-18 | Capital selection example, with a rewording study | Not run | Not run | Threshold 14/20 rows; top city 9/10 countries; no source change | [capitals-example-validation.json](../artifacts/capitals-example-validation.json) |
| 2026-11-18 | DuckDB 2.0.0 and a larger-model trial | 95 | 4 | All NorthSea results equal to DuckDB 1.5.6; Qwen3-8B 20/20 and 18/20, gpt-5.6-sol 20/20 and 20/20 on the capital question | [duckdb2-validation.json](../artifacts/duckdb2-validation.json) |
| 2026-11-18 | Audit fixes: option rule, abstain branch, shared baseline query, CLI metadata errors | 95 | Not run | 106 IDs; tev1 256 valid, 250 correct; tev1 records unchanged | [audit-fixes-validation.json](../artifacts/audit-fixes-validation.json) |

Older records can name artifacts that were later pruned.
Commit `fafa195` contains those files.

The first Python validation had 54 tests. The later tool removal deleted 12 of them.
The other 42 tests cover these items:
parsing equivalence, unsupported clauses, units, strict numeric boundaries, inclusive distance boundaries,
missing geometry and depths, spatial duplicates, negated existence, literal parameterization, schema and CRS checks,
staging from SQL to Laya, all four functions, NULL values, deduplication across DuckDB chunks,
microbatching, evaluation limits, malformed options, truncation, and threshold behavior.
The tev1 adapter tests check real framework use, no leak of reference answers, independent spatial labels, and kept semantic errors.
`compileall` passed on all Python files.

### First Python validation

The first run used Python 3.12.12, DuckDB 1.5.6, and Laya 0.4.12.
Laya came from `../laya` at commit `ffc142012af63d015fa19f05f29f9c5ff329a2cc`.
The PyTorch checkpoint revision is `1c5edc17a7acd8701df6fc341c0d179f1c62c982`.
Its weights SHA-256 is `891102d372688fc2a094dac56a384bc537b87c63f21f9f3dac0be2b7cbc8d86c`.

| Measurement | Result |
|---|---:|
| Wells | 9,299 |
| Wells with water depth > 350 m | 600 |
| Pipelines | 73 |
| In-service pipelines | 63 |
| Matching wells | 106 |
| Independent baseline | 106; same OBJECTID set |
| Duplicate result wells | 0 |
| Model evaluations for the exact query | 0 |
| Query execution and row retrieval | 0.181 seconds, one local measurement |

The status probe made nine model evaluations. `jev` used the `jev_prob` answers again.
The probe took approximately 0.506 seconds after the model load.
The semantic filter sent seven distinct status states to the model in one batch.

### Native extension

The native extension gave the same tev1 predictions and probabilities as the Python backend.
The four status probe outputs were also equal.
The ONNX export check compared two fixtures with PyTorch outputs.
It also ran 36 shape and session checks. These checks did not compare each shape with PyTorch.
Output agreement applies to this model and these cases only.
See the [export log](../artifacts/onnx-export.txt) and the [shell output](../artifacts/native-cli.txt).

### Upstream review

The review rebuilt the native extension with locked, cached dependencies.
The DuckDB shell returned 106 candidate rows and the two known false positives.
All eight exact example counts were unchanged.

During the review, `scripts/evaluate_tev1.py` stopped before evaluation.
NorthSea no longer had the `okf/` documents.
The new `knowledge/datasets/Wells.md` did not have the depth-alias text that the old loader needed.
Thus the review replayed the compiled SQL from the native smoke test.
This replay tested the native SQL and the model. It did not test the planner.
The metadata update later fixed the loader.

The review tests cover repeated rows across chunks, shared connections, model reloads, failed loads, delimiters in options, row contents, malformed distributions, NULL inputs, and truncation.
Two failed truncation attempts make two model evaluations, two errors, and no cache entry.

### Knowledge metadata layout

The update added the `knowledge/datasets/` layout to the planner.
The tests cover both layouts, layout precedence, incomplete exports, and dataset identity.
They also cover malformed or ambiguous synonyms, numeric types, units, status values, sources, CRS, and file hashes.
The update did not expand the grammar. It did not change the native extension.

### Cache optimization

Both builds used the development profile on one macOS arm64 machine.
The benchmark used DuckDB 1.5.6 and one SQL thread.
Each case used 30,000 rows and four distinct states.
Four first model evaluations filled the cache.
The times exclude the model load and those four model evaluations.
The times include query execution and row retrieval.
Each value is the median of five runs.

| Query | Before | After | Speedup |
|---|---:|---:|---:|
| Probability from STRUCT | 0.512 s | 0.217 s | 2.36× |
| Choice from STRUCT | 0.817 s | 0.309 s | 2.65× |
| Raw score JSON from JSON input | 0.693 s | 0.487 s | 1.42× |

Each row equaled its first answer. The answer hashes were equal in both builds.
Each case made four model evaluations, 150,000 cache hits, and zero errors.
These results apply only to answers that are already in the cache.
See the [before](../artifacts/optimization-before.json) and [after](../artifacts/optimization-after.json) measurements.

The benchmark ran again on DuckDB 2.0.0 on 2026-11-18, with the same machine, profile, rows, and method.

| Query | DuckDB 1.5.6 | DuckDB 2.0.0 | Change |
|---|---:|---:|---:|
| Probability from STRUCT | 0.217 s | 0.295 s | 1.36× slower |
| Choice from STRUCT | 0.309 s | 0.420 s | 1.36× slower |
| Raw score JSON from JSON input | 0.487 s | 0.666 s | 1.37× slower |

The answer hashes are equal to the DuckDB 1.5.6 hashes.
A second run gave the same times within 1%.
The cause of the slower times is not known. It was not examined.
See the [DuckDB 2.0.0 measurement](../artifacts/duckdb2/benchmark.json).

## Backups

Before a change starts, an archive stores the project files that the change can affect.
Each `project.tar.gz` has a `manifest.json` with the SHA-256 hash of each archived file.
Each `project.tar.gz` passed a readback hash check.
The archives exclude the models, dependency caches, build caches, and the NorthSea database.
Source control excludes `backups/`.

| Directory | Content |
|---|---|
| `backups/before-spatial-jev-docs-20261020T043439/` | Plain copy of the README and two documents; no archive |
| `backups/before-knowledge-metadata-20261020T074748Z/` | 72 files under the prefix `duckdb-jev-spatial/` |
| `backups/before-optimization-20261020T075758Z/` | 85 files |
| `backups/before-cleanup-20261020T085227Z/` | 111 files |
| `backups/before-readme-consolidation-20261020T092502Z/` | 12 documentation files |
| `backups/before-data-archive-20261020T101005Z/` | 7 documentation and configuration files |

Caution: Do not extract an archive into the working project.
That action replaces the current files with the earlier files.

To inspect a backup, do these steps:

1. Create an empty recovery directory.

   ```sh
   mkdir backups/restore-before-optimization
   ```

2. Extract the archive into that directory.

   ```sh
   tar -xzf backups/before-optimization-20261020T075758Z/project.tar.gz \
     -C backups/restore-before-optimization
   ```

3. Compare the recovered files with the working project.

   ```sh
   diff -r backups/restore-before-optimization/docs docs
   ```
