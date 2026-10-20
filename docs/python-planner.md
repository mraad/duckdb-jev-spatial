# Python planner and Python backend

The Python planner compiles a fixed English grammar into parameterized SQL.
It validates fields, units, and aliases against the project metadata.
The native extension has no English parser.
The [terms](../README.md#terms) in the README apply.

## Dataset directory files

The planner reads these files in the dataset directory:

- `NorthSea.ddb`
- `NorthSea.ossie.yaml`
- `NorthSea.ontology.yaml`
- `knowledge/datasets/Wells.md`
- `knowledge/datasets/Pipelines.md`

If `knowledge/datasets/` is absent, the planner reads `okf/Wells.md` and `okf/Pipelines.md`.
See [Metadata rules](#metadata-rules).

## Set up

Before you start, [extract the sample dataset](../README.md#extract-the-sample-dataset).
Then do [setup steps 1, 2, and 4](../README.md#set-up-the-native-extension) in the README.
For the native backend, also do setup steps 5 to 7.

1. Set offline mode for model loading.

   ```sh
   export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
   ```

## Print and run a query plan

1. Print the query plan.

   ```sh
   .venv/bin/jev-spatial \
     'Show all wells with depth > 350 meters and are within 5 km of an in-service pipeline' \
     --database data/NorthSea/NorthSea.ddb
   ```

   The output contains the typed plan, the SQL, the bound values, the interpretation notes, and the source hashes.

2. Run the query.

   ```sh
   .venv/bin/jev-spatial \
     'Show all wells with depth > 350 meters and are within 5 km of an in-service pipeline' \
     --database data/NorthSea/NorthSea.ddb --run
   ```

   The command returns the matching attribute rows as JSON.
   The JSON output excludes geometry. This exact query loads no model.

## Grammar

The planner accepts Wells requests with one or both of these conditions:

- A depth comparison.
- A within-distance relation to pipelines.

`depth`, `water depth`, and `well water depth` all identify `Wells.water_depth`.
In NorthSea, `depth` is water depth. It is not total borehole depth.

| Item | Accepted values |
|---|---|
| Units | `meters`, `metres`, `m`, `kilometers`, `kilometres`, `km` |
| Comparisons | `>`, `>=`, `<`, `<=`, `=`, `greater than`, `deeper than`, `shallower than`, `at least`, `at most`, `no deeper than` |
| Negation | `not within` compiles to `NOT EXISTS` |
| Pipeline phase | `in-service` selects `current_phase = 'IN SERVICE'`; without it, all phases qualify |

The planner must parse the full request.
These items cause an error: unsupported clauses, unsupported units, OR expressions, gas or oil qualifiers, and unparsed text.
The planner does not send an unknown spatial condition to the model.
[Validation](validation.md#current-results) gives the measured English coverage.

## Add a semantic filter with the CLI

These options add a semantic filter:

| Option | Purpose |
|---|---|
| `--semantic 'your text question'` | Sets the model question. |
| `--fields status` | Selects the text field. |
| `--model models/laya` | Selects the PyTorch checkpoint for the Python backend. |
| `--extension native/dist/jev_spatial.duckdb_extension` | Selects the native backend. Then `--model` must select the ONNX model. |

The CLI uses the Python backend if `--extension` is absent.
The native backend uses CPU and a fixed budget of 1,000 model evaluations.
Thus the CLI rejects other `--device` and `--max-evaluations` values with `--extension`.

1. Run the query with a semantic filter.

   ```sh
   HF_HUB_OFFLINE=1 .venv/bin/jev-spatial \
     'Show all wells with depth > 350 meters and are within 5 km of an in-service pipeline' \
     --database data/NorthSea/NorthSea.ddb --run \
     --semantic 'Does the well status explicitly say that it is operating?' \
     --fields status --model models/laya
   ```

The executor runs two statements.
The first statement stores the candidate rows in the temporary table `_jev_candidates`.
The second statement applies `WHERE jev(struct_pack("status" := c."status"), ?)` to that table.
`Compiled.semantic_where` holds that generated expression.
Thus exact filtering completes before the first model evaluation.

The model returns two false positives for this question.
Use an exact `Filter` comparison for a known operating-status code.

## Typed query API

This typed query gives the exact filter of the example question:

```python
from jev_spatial import Catalog, Filter, Relation, Query, compile_query

catalog = Catalog('data/NorthSea')
query = Query(
    filters=(Filter('water_depth', '>', 350),),
    relation=Relation(
        target='Pipelines', distance_m=5000,
        filters=(Filter('current_phase', '=', 'IN SERVICE'),),
    ),
)
compiled = compile_query(query, catalog)
# con.execute(compiled.sql, compiled.parameters)
```

The typed API supports one relation for each query.
The relation predicate must be in the ontology.
The compiler accepts these predicates: `within_distance`, `within`, `intersects`, `contains`, `touches`, and `disjoint`.
Only `within_distance` takes a distance.

The compiler accepts the operators `=`, `!=`, `>`, `>=`, `<`, `<=`, and `ILIKE`.
`ILIKE` applies only to text fields.
The compiler checks each filter value against the Ossie datatype.
Numeric values must be finite.
Semantic fields must be text fields. They cannot be geometry.

A relation requires at least one matching target.
With `negate=True`, a relation requires no matching target.
`disjoint` requires at least one disjoint pair.
A negated `intersects` requires no intersecting pair.

## Query meaning

This table shows how the planner grounds the example question:

| Phrase | Evidence | Compiled meaning |
|---|---|---|
| wells | Ossie dataset `Wells`, source `NorthSea.main.Wells` | `main."Wells"` |
| depth | Grammar alias for the Ossie synonym `well water depth` | `Wells.water_depth` |
| > 350 meters | Ossie numeric datatype and ARCGIS unit `meters` | `water_depth > CAST(? AS DOUBLE PRECISION)`, value 350 |
| in-service pipeline | Ossie and OKF `current_phase` values | `Pipelines.current_phase = ?`, value `IN SERVICE` |
| within 5 km | Ontology `Feature.within_distance`; project CRS instructions | `ST_DWithin` in EPSG:3035, value 5000 |
| a pipeline | Existential quantification | Correlated `EXISTS`; no duplicate wells |

The compiler uses placeholders for all literal values.
[artifacts/northsea.sql](../artifacts/northsea.sql) contains the full SQL.

```sql
WHERE w."water_depth" > CAST(? AS DOUBLE PRECISION)
  AND w."geometry" IS NOT NULL
  AND NOT ST_IsEmpty(w."geometry")
  AND EXISTS (
    SELECT 1 FROM main."Pipelines" p
    WHERE p."current_phase" = ?
      AND p."geometry" IS NOT NULL
      AND NOT ST_IsEmpty(p."geometry")
      AND ST_DWithin(
        ST_Transform(w."geometry", 'EPSG:4326', 'EPSG:3035', always_xy := TRUE),
        ST_Transform(p."geometry", 'EPSG:4326', 'EPSG:3035', always_xy := TRUE),
        CAST(? AS DOUBLE PRECISION)
      )
  )
-- Parameters in order: [350.0, 'IN SERVICE', 5000.0]
```

The distance goes from a well point to the nearest part of the pipeline geometry.
It does not use the pipeline centroid or endpoints.
"Within" includes the distance boundary.
`depth > 350` excludes a depth of exactly 350 meters.
A NULL depth cannot satisfy the comparison.
NULL and empty geometry cannot make a spatial match.
The query does not repair invalid geometry. A spatial error causes a query error.
The query uses no buffer approximation.

`not within` requires no matching target near the well.
A distant target alone does not satisfy that condition.
The source geometry must be present and not empty.
If no target has usable geometry, the negated condition is true.
The result describes the records. It does not prove that no pipeline exists.

`always_xy := TRUE` keeps the longitude and latitude axis order of the project.
EPSG:3035 is the distance policy of the project ontology.
EPSG:3035 does not keep all distances exact.
This project does not show geodesic accuracy at a 5 km boundary.
Survey or geodesic work needs a different distance policy and boundary validation.
A spherical point-to-point distance does not measure the distance from a point to a pipeline.

The ontology concept `WellNearGasLine` has a different meaning: **500 m**, **gas only**, **in service**.
Its link table would exclude valid results of the 5 km question.
Thus the compiler calculates the relation from the base tables.
It does not relate wells and pipelines through matching names or an assumed foreign key.

## Metadata rules

The loader always reads `NorthSea.ossie.yaml` and `NorthSea.ontology.yaml`.
It then selects one document layout:

| Condition | Documents |
|---|---|
| `knowledge/datasets/` exists | `knowledge/datasets/Wells.md` and `knowledge/datasets/Pipelines.md` |
| `knowledge/datasets/` is absent | `okf/Wells.md` and `okf/Pipelines.md` |

The loader rejects an incomplete or invalid knowledge layout.
It does not combine the layouts. It does not try the legacy files after an error.
Thus an old `okf/` document cannot hide an invalid knowledge export.
The loader calculates SHA-256 hashes from the bytes of all four selected files.
The CLI reports the file paths and the hashes.

For the knowledge layout, each Markdown document must have YAML front matter.
The front matter must contain `type: Ossie Dataset`, the correct `title`, and the Ossie `source`.
The loader reads the query meaning from structured Ossie fields:

- `Wells.water_depth` must have a supported depth synonym. The current export uses `well water depth`.
- No other Wells field can use `depth`, `water depth`, or `well water depth` as a name or synonym.
- `water_depth` must be numeric. Its ARCGIS unit must be `meters`.
- `Pipelines.current_phase` must be a string field. Its sample values must contain `IN SERVICE`.
- The project instructions must specify EPSG:3035 for metric distances.
- Wells, Pipelines, and Discoveries must use the expected sources and EPSG:4326 geometry.

For the legacy layout, the loader checks the depth and status text in the field headings.
Both layouts keep the unit, datatype, source, and CRS checks.
Before execution, the CLI and the tev1 runner check the required database columns.
They also check GEOMETRY storage and the `sp_ref` value 4326.

Ossie defines the schema.
The loader does not execute Markdown query hints or ontology `derived_by` expressions.
It does not infer aliases from prose.
The file hashes include the body text of the knowledge documents.
The loader does not use that body text as a second schema.

## Python backend

The Python backend runs the PyTorch checkpoint in the Python process.
It needs no change to the Laya source.

1. Run this example to show pipeline answers.

   ```python
   from jev_spatial.engine import connect, load_agent, register_jev

   con = connect('data/NorthSea/NorthSea.ddb')
   runtime = register_jev(con, load_agent('models/laya'), max_evaluations=1000)
   print(con.execute("""
       SELECT current_phase,
              jev({'current_phase': current_phase},
                  'Is this pipeline currently in service?') AS accepted,
              jev_prob({'current_phase': current_phase},
                       'Is this pipeline currently in service?') AS probability
       FROM (SELECT DISTINCT current_phase FROM Pipelines)
   """).fetchall())
   con.close()
   ```

| SQL function | Result |
|---|---|
| `jev(row, question)` | BOOLEAN; true if the probability of true is at least the threshold (default 0.5) |
| `jev_prob(row, question)` | DOUBLE; probability of true |
| `jev_choice(row, question, ['a', 'b'])` | VARCHAR; one supplied label |
| `jev_score(row, question, ['low', 'high'])` | DOUBLE; expected zero-based level index |

`engine.connect()` opens the existing database in read-only mode.
It loads the installed spatial extension.
`engine.load_agent()` needs a local directory with `rl_agent_config.json` and `model.safetensors`.
It sets `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` before it imports Laya.
A missing file causes an error. The Python backend does not download a model.

`register_jev()` adds three internal Arrow UDFs and four temporary SQL macros.
The UDFs call `Agent.predict_batch` from the `../laya` checkout.
The macros serialize the row through `to_json`.
A structured row must be a STRUCT of selected text fields.
The Python backend treats a serialized JSON string as text.

The runtime groups each Arrow chunk by kind, question, and option list.
It removes duplicate states. It sends the other states in batches of up to 16.
State JSON uses sorted keys. Choice options keep their order.
The cache stores answers across chunks. `jev` and `jev_prob` share one answer.
The cache belongs to one runtime and one connection. It is not persistent.
Do not change the model files or the calibration while the runtime is active.

The budget counts model evaluations, not scanned or returned rows.
The default `max_evaluations` is 1,000 until `runtime.reset()`.
The executor resets the runtime before each query with a semantic filter.
For raw SQL, call `runtime.reset()` before each new evaluation period.
A lock permits one Laya call at a time.
The UDFs declare side effects because they change counters and the cache.
That declaration prevents constant folding.

SQL NULL returns NULL. Choice and score labels must be distinct and not empty.
These conditions cause a query error: model errors, truncated states, collapsed options, and an exhausted budget.

The executor reserves the temporary table name `_jev_candidates`.
Run one query at a time on each connection.
Use one Jev backend on each connection. Two backends can override each other's function names.
The Python API returns geometry. The CLI excludes geometry from its JSON output.
