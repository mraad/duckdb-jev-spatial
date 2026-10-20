# SQL reference

This document describes the Jev functions of the native backend.
The [terms](../README.md#terms) in the README apply.
The Python backend has only `jev`, `jev_prob`, `jev_choice`, and `jev_score`.
See the [Python backend](python-planner.md#python-backend).

## Jev functions

| Function | Result |
|---|---|
| `jev(row, question)` | BOOLEAN; true if the probability of true is at least 0.5 |
| `jev(row, question, threshold)` | BOOLEAN; uses a finite threshold from 0 to 1 |
| `jev_prob(row, question)` | DOUBLE; probability that the statement is true |
| `jev_choice(row, question, options)` | VARCHAR; one supplied label |
| `jev_score(row, question, levels)` | DOUBLE; expected zero-based level index |
| `jev_score_norm(row, question, levels)` | DOUBLE; score divided by `max(number of levels - 1, 1)` |
| `jev_eval(row, question, kind, options)` | JSON; full answer with probabilities and confidence fields |
| `jev_confidence(row, question, kind, options)` | DOUBLE; the `confidence` field of the same answer |
| `jev_laya_load(path)` | JSON text with runtime data; loads an ONNX model directory |
| `jev_laya_stats()` | JSON text with counters, model path, and calibration warnings |
| `jev_laya_reset()` | Clears the cache and the model evaluation budget; keeps the model |

`jev` compares the probability of true with the threshold.
It does not use the symmetric `confidence` field.
`jev_choice` returns VARCHAR. It does not create a DuckDB ENUM.
`jev_score` can return a fractional value.
It is not a calibrated physical measurement.
A one-level `jev_score_norm` result is zero.

## Inputs

The row input can be a STRUCT of text fields, JSON, or VARCHAR.
VARCHAR contains plain text.
A `JSON` cast identifies serialized text as structured JSON data.
Use `to_json(...)` to convert other attribute types to JSON.
Do not send geometry. Spatial predicates evaluate geometry.

Equivalent STRUCT and JSON inputs make the same state.
Plain VARCHAR text and a JSON object are different states.
The combined state, question, and options are limited to 1 MiB before tokenization.

For `jev_eval` and `jev_confidence`, `kind` is `noul`, `choice`, or `score`.
Use `[]` as the options for `noul`.
Choice and score need a nonempty list of distinct text labels.

## NULL values and errors

SQL NULL in any argument makes each Jev function return NULL.
This rule includes NULL options for `noul`.
The upstream duckdb-jev interface differs on this point.

These conditions cause a query error:

- An empty question.
- An empty option list for choice or score.
- An empty label, a duplicate label, or a NULL label.
- A threshold that is not finite or not from 0 to 1.
- A `kind` other than `noul`, `choice`, or `score`.
- Options for `noul` that are not empty.
- A STRUCT field that is not text, for example geometry.
- A row input that is not a STRUCT, JSON, or VARCHAR.
- An input larger than 1 MiB.
- A truncated state.
- Options that collapse during tokenization.
- An answer that fails validation.
- No loaded model.
- An exhausted model evaluation budget.
- A control function in a statement with more than one row.

The DuckDB Rust binding changes callback errors and panics into query errors.

## Answers

Each answer passes validation before it enters the cache.
Validation checks the kind, the labels, finite bounds, the confidence fields, and complete distributions.
Distribution sums can have a rounding error in the fourth decimal place.

| Kind | Fields in the `jev_eval` answer |
|---|---|
| `noul` | `type`, `noul`, `confidence`, `answer_confidence`, `action.act_probability` |
| `choice` | `type`, `choice`, `probabilities` keyed by label, `confidence`, `answer_confidence`, `action.act_probability` |
| `score` | `type`, `score`, ordered `legend`, `probabilities` in legend order, `confidence`, `answer_confidence`, `action.act_probability` |

- `noul` is the probability that the statement is true.
- For `noul`, `confidence` and `answer_confidence` are the larger of the true and false probabilities.
- For choice and score, `confidence` is normalized entropy: `1 - H(probabilities) / log(number of options)`.
- For choice and score, `answer_confidence` is the largest option probability.
- `action.act_probability` is the probability from the separate Laya action head.

Entropy confidence measures the concentration of the option distribution.
The largest option probability identifies the most likely option.
For a fractional score, the largest option probability does not measure if the score is correct.
A confident false statement can have a low `noul` value and a high `confidence` value.

Caution: No answer field is a calibrated estimate of NorthSea correctness.
Do not use entropy confidence as a probability of correctness.
The action-head probability is not validated for NorthSea.

## Inspect model answers

Before you start, do the [candidate-table procedure](../README.md#add-a-semantic-filter-to-candidate-rows).
That procedure loads the ONNX model and creates `exact_wells`.

1. Show the full answer for each distinct candidate status.

   ```sql
   SELECT status,
          jev_eval({'status': status},
                   'Does the well status explicitly say that it is operating?',
                   'noul', []) AS answer
   FROM (SELECT DISTINCT status FROM exact_wells)
   ORDER BY status;
   ```

2. Show the distribution over pipeline lifecycle labels.

   ```sql
   SELECT current_phase,
          jev_eval({'current_phase': current_phase},
                   'What is the pipeline lifecycle state?', 'choice',
                   ['in service', 'decommissioned', 'abandoned']) AS answer
   FROM (SELECT DISTINCT current_phase FROM Pipelines)
   ORDER BY current_phase;
   ```

3. Compare a score with its normalized score and its confidence.

   ```sql
   SELECT current_phase,
          jev_score({'current_phase': current_phase}, 'How active is the pipeline?',
                    ['inactive', 'partly active', 'active']) AS score,
          jev_score_norm({'current_phase': current_phase}, 'How active is the pipeline?',
                         ['inactive', 'partly active', 'active']) AS normalized_score,
          jev_confidence({'current_phase': current_phase}, 'How active is the pipeline?',
                         'score', ['inactive', 'partly active', 'active']) AS confidence
   FROM (SELECT DISTINCT current_phase FROM Pipelines)
   ORDER BY current_phase;
   ```

The normalized value of a three-level score is the score divided by two.
These fields do not remove the known NorthSea false positives.

## Cache and model evaluation budget

The model, the counters, and the cache belong to one database instance.
A mutex controls access to them.

The cache key contains the canonical state text, the question, the kind, and the ordered options.
The key encodes these four values as a JSON tuple.
Thus separators or quotes in the text cannot join two different keys.
Only validated answers enter the cache. The cache never stores a failed answer.

All seven Jev functions share one cached answer.
A threshold change, a confidence request, or a normalized score uses that answer again.
These requests need no new model evaluation.
The cache stores answers across DuckDB chunks.

The budget permits **1,000 model evaluations** between resets.
Thus the cache holds 1,000 answers at most between resets.
A failure after model evaluation starts uses one model evaluation.
That failure also increments `errors` in `jev_laya_stats()`.
These failures do not increment `errors`:

- Input validation failures.
- A missing model.
- A budget rejection.

`jev_laya_reset()` clears the cache and the counters.
A successful `jev_laya_load()` replaces the model. It also clears the cache and the counters.
A failed `jev_laya_load()` keeps the current model and cache.

The native backend runs one model evaluation for each distinct state.
It has no multi-state batching.

## Control functions and connections

Call each control function in a separate single-row statement.
Coordinate model loads and resets across connections that share the database.
Do not change the ONNX model files while queries run.

The `LOAD` statement runs `LOAD spatial`. The spatial extension must be installed.
The native extension registers in-memory catalog functions.
It creates no persistent SQL macros. It does not change the database.

Use a new connection that has no other Jev backend.
Temporary macros can override native function names in the connection that created them.
The native extension cannot read the temporary catalog of another connection.
Existing database-level Jev function names cause a load error.

`jev_laya_load()` reads only local files. It does not download a model.
It runs ONNX Runtime with two intra-operation threads and one inter-operation thread.

`jev_laya_stats()` returns these fields:

| Field | Meaning |
|---|---|
| `backend` | `rust-laya-onnx` |
| `version` | Native extension version |
| `model_directory` | Loaded ONNX model directory, or NULL |
| `evaluations` | Model evaluations since the last reset or load |
| `cache_hits` | Answers read from the cache |
| `cache_entries` | Answers in the cache |
| `errors` | Failures after model evaluation started |
| `max_evaluations` | Model evaluation budget |
| `clamped_temperatures` | Model temperatures that Laya clamped |
