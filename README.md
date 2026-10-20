# DuckDB Jev Spatial

This project runs spatial SQL and local Laya answers inside DuckDB.
The native extension adds seven Jev functions to DuckDB.
Laya Rust and ONNX Runtime run the model inside the DuckDB process.
The project uses no hosted model service and no API credentials.

DuckDB evaluates geometry, distance, and coded attributes.
Laya evaluates selected text fields.
The optional Python planner compiles a fixed set of English questions into SQL.
The local tev1 harness scores the answers.

| Document | Content |
|---|---|
| [SQL reference](docs/sql-reference.md) | Jev functions, inputs, errors, answers, cache, and connections |
| [Python planner](docs/python-planner.md) | English grammar, CLI, typed query API, metadata rules, and Python backend |
| [Design](docs/design.md) | Execution boundary, extension design, upstream review, and Laya correction |
| [Validation](docs/validation.md) | Current results, check procedures, model errors, change history, and backups |

## Terms

The documentation uses each term with one meaning only.

| Term | Meaning |
|---|---|
| Native extension | The Rust DuckDB extension `native/dist/jev_spatial.duckdb_extension`. |
| Native backend | The Jev functions that the native extension registers. |
| Python backend | The Jev functions that `register_jev()` adds to one Python connection. |
| Jev function | A SQL function that returns a model answer, for example `jev` or `jev_prob`. |
| Model | The local Laya model. |
| ONNX model | The exported model directory `models/laya-onnx/laya`. The native backend uses it. |
| PyTorch checkpoint | The downloaded model directory `models/laya`. The Python backend uses it. |
| Exact filter | A SQL condition on stored attributes or geometry. |
| Semantic filter | A condition that uses a Jev function on selected text fields. |
| Candidate rows | The rows that pass the exact filter. |
| State | The row data that a Jev function sends to the model. |
| Answer | The validated result of one model evaluation. |
| Model evaluation | One uncached model run for one state, question, kind, and option list. |
| Dataset directory | The directory that contains `NorthSea.ddb` and its metadata files. The sample dataset directory is `data/NorthSea`. It comes from the archive `data/NorthSea.tar.gz`. |

## Requirements

The tested build is for DuckDB 1.5.6 on macOS arm64.
The native extension is an unsigned development build.
Source control excludes the native extension and the model files.

Run all commands from the project root.
The archive `data/NorthSea.tar.gz` contains the NorthSea sample dataset.
The examples and the checks use the extracted dataset directory `data/NorthSea`.
To use a different dataset, replace `data/NorthSea` with its dataset directory.

Setup requires these items:

- The DuckDB 1.5.6 shell.
- A Rust toolchain.
- `uv` and Python 3.12.
- The Laya checkout at `../laya`.

The native extension targets DuckDB 1.5.6 exactly.
Its Rust bindings use the unstable DuckDB C extension API.
Other platforms, GPU providers, and signed distribution are not tested.

## Extract the sample dataset

The archive contains these files:

- `NorthSea/NorthSea.ddb`: the DuckDB database.
- `NorthSea/NorthSea.ossie.yaml`: the Ossie schema.
- `NorthSea/NorthSea.ontology.yaml`: the ontology.
- `NorthSea/knowledge/datasets/Wells.md` and `NorthSea/knowledge/datasets/Pipelines.md`: the knowledge documents.

The SQL examples need only the database.
The Python planner, the smoke test, and tev1 also need the three metadata items.
In the `data` directory, source control tracks only `data/NorthSea.tar.gz`.

Caution: Extraction replaces files in `data/NorthSea` that have the same names.

1. Extract the archive into the `data` directory.

   ```sh
   tar -xzf data/NorthSea.tar.gz -C data
   ```

2. Calculate the SHA-256 hash of the database.

   ```sh
   shasum -a 256 data/NorthSea/NorthSea.ddb
   ```

   The hash must be equal to the database SHA-256 in [Validation](docs/validation.md#current-results).

## Set up the native extension

Before you start, [extract the sample dataset](#extract-the-sample-dataset).

Note: The local `../laya` checkout already has the
[Laya CPU policy correction](docs/design.md#laya-cpu-policy-correction).
Apply that patch only to a fresh checkout at the documented base commit.

1. Install the locked Python dependencies.

   ```sh
   uv sync --locked --extra dev --extra onnx-export --python 3.12
   ```

2. Install the DuckDB spatial extension.

   ```sh
   duckdb -c 'INSTALL spatial;'
   ```

   The DuckDB shell and the Python `duckdb` package use the same installed extension.

3. Fetch the locked Rust dependencies.

   ```sh
   cargo fetch --manifest-path native/Cargo.toml --locked
   ```

4. Download the PyTorch checkpoint. Skip this step if `models/laya` exists.

   ```sh
   HF_HUB_OFFLINE=0 HF_HOME="$PWD/.hf-cache" .venv/bin/python - <<'PY'
   from huggingface_hub import snapshot_download
   snapshot_download(
       'convaiinnovations/laya',
       revision='1c5edc17a7acd8701df6fc341c0d179f1c62c982',
       local_dir='models/laya',
       allow_patterns=['rl_agent_config.json', 'model.safetensors', 'tokenizer/*', 'encoder/*'],
   )
   PY
   ```

   This step uses the network. Setup steps 1 to 3 also use the network.
   All later steps and all queries read only local files.

5. Export the ONNX model. Skip this step if `models/laya-onnx/laya` exists.

   ```sh
   HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 .venv/bin/python \
     ../laya/laya-rust/tools/export_onnx.py models/laya \
     --out models/laya-onnx --verify
   ```

   The exporter does not overwrite an existing export.
   The graph is approximately 2.7 MB. The external weights are approximately 1.69 GB.

6. Build the native extension.

   ```sh
   .venv/bin/python scripts/build_extension.py
   ```

   The build uses the development profile and cached dependencies.
   The `--release` option selects an optimized build. The optimized build is not tested.

7. Set the ONNX Runtime library path.

   ```sh
   export ORT_DYLIB_PATH="$PWD/.venv/lib/python3.12/site-packages/onnxruntime/capi/libonnxruntime.1.31.0.dylib"
   ```

   This path is correct for the tested Mac.
   A separate ONNX Runtime installation can supply the library.
   The native extension does not use Python at run time.

8. Run the complete exact and semantic example.

   ```sh
   duckdb -readonly -unsigned \
     data/NorthSea/NorthSea.ddb \
     < native/northsea.sql
   ```

## Open a session

1. Open the database in read-only mode.

   ```sh
   duckdb -readonly -unsigned data/NorthSea/NorthSea.ddb
   ```

2. Load the native extension.

   ```sql
   LOAD 'native/dist/jev_spatial.duckdb_extension';
   ```

The `LOAD` statement also loads the installed spatial extension.
Exact filters need no model.
Semantic filters need the ONNX model and `ORT_DYLIB_PATH` from setup step 7.

## Exact spatial examples

These examples use only exact filters. They load no model.
Their English titles describe the queries.
The titles are not the grammar of the Python planner.
Direct SQL supports several relations and nearest-feature queries.
The typed query API supports one relation for each query.

In NorthSea, `depth` is **water depth**.
Deep means water depth greater than 350 meters.
The dataset stores geometry in EPSG:4326.
Distance queries transform both geometries to EPSG:3035 with `always_xy := true`.
Distances are projected meters. They are not geodesic distances.
Topological queries use the stored EPSG:4326 geometry.

To run all eight examples, do this step:

1. Run the SQL file in a new session.

   ```sh
   duckdb -readonly -unsigned \
     data/NorthSea/NorthSea.ddb \
     < native/spatial-examples.sql
   ```

To run one example, [open a session](#open-a-session). Then do these steps:

1. Create the projected wells table.

   ```sql
   CREATE TEMP TABLE wells_m AS
   SELECT OBJECTID, water_depth, status, geometry,
          ST_Transform(geometry, 'EPSG:4326', 'EPSG:3035', always_xy := true) AS geom_m
   FROM Wells
   WHERE geometry IS NOT NULL AND NOT ST_IsEmpty(geometry);
   ```

2. Create the projected in-service pipelines table.

   ```sql
   CREATE TEMP TABLE service_pipelines_m AS
   SELECT OBJECTID, geometry,
          ST_Transform(geometry, 'EPSG:4326', 'EPSG:3035', always_xy := true) AS geom_m
   FROM Pipelines
   WHERE current_phase = 'IN SERVICE'
     AND geometry IS NOT NULL AND NOT ST_IsEmpty(geometry);
   ```

3. Create the discovery areas table.

   ```sql
   CREATE TEMP TABLE discovery_areas AS
   SELECT OBJECTID, geometry FROM Discoveries
   WHERE geometry IS NOT NULL AND NOT ST_IsEmpty(geometry);
   ```

4. Run the SQL of one example below.

Temporary tables do not change the source tables.
The setup excludes NULL and empty geometry.
The setup does not repair invalid geometry.
All NorthSea geometry is valid. Another dataset can contain invalid geometry.

| Example | SQL relation | NorthSea result |
|---|---|---:|
| 1. Deep wells within 5 km of an in-service pipeline | `EXISTS` with `ST_DWithin` | 106 wells |
| 2. Deep wells with no in-service pipeline within 5 km | `NOT EXISTS` with `ST_DWithin` | 494 wells |
| 3. Wells inside a discovery area | `ST_Within(well, area)` | 4,426 wells |
| 4. In-service pipelines that intersect a discovery area | `ST_Intersects(pipeline, area)` | 52 pipelines |
| 5. Discovery areas that contain a well | `ST_Contains(area, well)` | 1,375 areas |
| 6. Wells outside all discovery areas | `NOT EXISTS` with `ST_Intersects` | 4,873 wells |
| 7. Deep wells inside a discovery area and near an in-service pipeline | Two `EXISTS` conditions | 72 wells |
| 8. Nearest in-service pipeline for each deep well | Lateral query ordered by `ST_Distance` | 600 well and pipeline pairs |

`EXISTS` returns each primary feature one time.
Several matching targets do not make duplicate rows.
The [DuckDB spatial function reference](https://duckdb.org/docs/current/core_extensions/spatial/functions)
defines each spatial predicate.

### 1. Deep wells within 5 km of an in-service pipeline

“Show all wells with water depth greater than 350 meters and an in-service pipeline within 5 km.”

`ST_DWithin` includes the 5,000-meter boundary.

```sql
SELECT w.OBJECTID
FROM wells_m w
WHERE w.water_depth > 350
  AND EXISTS (
    SELECT 1 FROM service_pipelines_m p
    WHERE ST_DWithin(w.geom_m, p.geom_m, 5000)
  )
ORDER BY w.OBJECTID;
```

### 2. Deep wells with no in-service pipeline within 5 km

“Show deep wells without an in-service pipeline within 5 km.”

`NOT EXISTS` requires no matching pipeline.
This result and example 1 together contain all 600 deep wells with usable geometry.
If the target table is empty, every eligible well passes this condition.
The result describes the database records.
It does not prove that no pipeline exists in the real world.

```sql
SELECT w.OBJECTID
FROM wells_m w
WHERE w.water_depth > 350
  AND NOT EXISTS (
    SELECT 1 FROM service_pipelines_m p
    WHERE ST_DWithin(w.geom_m, p.geom_m, 5000)
  )
ORDER BY w.OBJECTID;
```

### 3. Wells inside a discovery area

“Show wells inside at least one discovery area.”

For a point well, `ST_Within` excludes a point on the area boundary.
Use `ST_Intersects` if boundary points must qualify.

```sql
SELECT w.OBJECTID
FROM wells_m w
WHERE EXISTS (
  SELECT 1 FROM discovery_areas d
  WHERE ST_Within(w.geometry, d.geometry)
)
ORDER BY w.OBJECTID;
```

### 4. In-service pipelines that intersect a discovery area

“Show in-service pipelines that intersect a discovery area.”

Intersection includes boundary contact, passage through an area, and containment.
The pipeline does not have to cross the area boundary.

```sql
SELECT p.OBJECTID
FROM service_pipelines_m p
WHERE EXISTS (
  SELECT 1 FROM discovery_areas d
  WHERE ST_Intersects(p.geometry, d.geometry)
)
ORDER BY p.OBJECTID;
```

### 5. Discovery areas that contain a well

“Show discovery areas that contain at least one well.”

The area is the primary result.
`ST_Contains(area, well)` reverses the arguments of `ST_Within(well, area)`.

```sql
SELECT d.OBJECTID
FROM discovery_areas d
WHERE EXISTS (
  SELECT 1 FROM wells_m w
  WHERE ST_Contains(d.geometry, w.geometry)
)
ORDER BY d.OBJECTID;
```

### 6. Wells outside all discovery areas

“Show wells that do not intersect any discovery area.”

Use `NOT EXISTS` with `ST_Intersects`. A well on a boundary is excluded.
Do not use `EXISTS` with `ST_Disjoint`.
That condition requires only one unrelated area. It answers a different question.

```sql
SELECT w.OBJECTID
FROM wells_m w
WHERE NOT EXISTS (
  SELECT 1 FROM discovery_areas d
  WHERE ST_Intersects(w.geometry, d.geometry)
)
ORDER BY w.OBJECTID;
```

### 7. Deep wells inside a discovery area and near a pipeline

“Show deep wells inside a discovery area and within 5 km of an in-service pipeline.”

Each `EXISTS` adds one relation to the well. Each result satisfies both relations.
The pipeline does not have to intersect the discovery area.
That condition needs a relation between the pipeline and the area.

```sql
SELECT w.OBJECTID
FROM wells_m w
WHERE w.water_depth > 350
  AND EXISTS (
    SELECT 1 FROM discovery_areas d
    WHERE ST_Within(w.geometry, d.geometry)
  )
  AND EXISTS (
    SELECT 1 FROM service_pipelines_m p
    WHERE ST_DWithin(w.geom_m, p.geom_m, 5000)
  )
ORDER BY w.OBJECTID;
```

### 8. Nearest in-service pipeline for each deep well

“For each deep well, show the nearest in-service pipeline and its distance.”

The lateral query returns one pipeline for each well.
If two distances are equal, the query selects the pipeline with the lowest ID.
There is no 5 km limit.
If no eligible pipeline exists, the query returns no row for that well.
The distance is the shortest distance between the two geometries.
It is not a distance along a pipeline.

```sql
SELECT w.OBJECTID AS well_id, nearest.pipeline_id, nearest.distance_m
FROM wells_m w
CROSS JOIN LATERAL (
  SELECT p.OBJECTID AS pipeline_id,
         ST_Distance(w.geom_m, p.geom_m) AS distance_m
  FROM service_pipelines_m p
  ORDER BY distance_m, p.OBJECTID
  LIMIT 1
) nearest
WHERE w.water_depth > 350
ORDER BY w.OBJECTID;
```

## Semantic filters inside spatial queries

A semantic filter can select the related feature inside a spatial `EXISTS` query.
A semantic filter can also select the eligible pipelines before a nearest-pipeline search.
DuckDB calculates geometry and distance.
Jev functions read only the selected text fields. They return answers, not geometry.

Do not send geometry to a Jev function.
Do not use a model score as a distance or a radius.
Each query sets the 5,000-meter limit explicitly.

Caution: Predicate order in one `WHERE` clause does not control evaluation order.
DuckDB can send a row to the model before that row passes the spatial condition.
If the model must receive only candidate rows, use the
[candidate-table procedure](#add-a-semantic-filter-to-candidate-rows).

To run all four examples, do these steps:

1. Do [setup step 7](#set-up-the-native-extension).
2. Run the SQL file in a new session.

   ```sh
   duckdb -readonly -unsigned \
     data/NorthSea/NorthSea.ddb \
     < native/spatial-jev-examples.sql
   ```

To run one example, [open a session](#open-a-session). Then do these steps:

1. Load the ONNX model.

   ```sql
   SELECT jev_laya_load('models/laya-onnx/laya');
   ```

2. Create the temporary table for deep wells.

   ```sql
   CREATE TEMP TABLE deep_wells_m AS
   SELECT OBJECTID, status, geometry,
          ST_Transform(geometry, 'EPSG:4326', 'EPSG:3035', always_xy := true) AS geom_m
   FROM Wells
   WHERE water_depth > 350
     AND geometry IS NOT NULL AND NOT ST_IsEmpty(geometry);
   ```

3. Create the temporary table for pipelines.

   ```sql
   CREATE TEMP TABLE pipelines_m AS
   SELECT OBJECTID, current_phase, geometry,
          ST_Transform(geometry, 'EPSG:4326', 'EPSG:3035', always_xy := true) AS geom_m
   FROM Pipelines
   WHERE geometry IS NOT NULL AND NOT ST_IsEmpty(geometry);
   ```

4. Create the temporary table for discovery areas.

   ```sql
   CREATE TEMP TABLE discovery_areas AS
   SELECT OBJECTID, geometry FROM Discoveries
   WHERE geometry IS NOT NULL AND NOT ST_IsEmpty(geometry);
   ```

5. Run the SQL of one example below.

The pipeline table keeps all lifecycle states.
Thus the model can accept or reject each pipeline state.
The setup statements apply the depth and geometry filters first.
Repeated states use the native answer cache.

| Spatial operation | Semantic filter | NorthSea result |
|---|---|---|
| `ST_DWithin` inside `EXISTS` | `jev` accepts the related pipeline state | 106 wells; IDs match the exact filter |
| `ST_Intersects` inside `EXISTS` | `jev_choice` selects the pipeline lifecycle | 52 pipelines; IDs match the exact filter |
| `ST_Contains` inside `EXISTS` | `jev_prob` accepts the contained well status | 4 areas; 1 correct and 3 false positives |
| Lateral query ordered by `ST_Distance` | `jev` selects the eligible pipelines | 600 pairs; pipeline IDs and distances match the exact filter |

The exact filter for comparison is the known status code.
[Validation](docs/validation.md#mixed-spatial-and-semantic-examples) shows how to check these results.

### Deep wells near a model-accepted pipeline

“Show deep wells within 5 km of a pipeline that Jev classifies as in service.”

The same pipeline must satisfy `ST_DWithin` and `jev`.
Two separate `EXISTS` clauses can match two different pipelines.
`EXISTS` returns each well one time. The 5,000-meter boundary is included.

```sql
SELECT w.OBJECTID
FROM deep_wells_m w
WHERE EXISTS (
  SELECT 1 FROM pipelines_m p
  WHERE ST_DWithin(w.geom_m, p.geom_m, 5000)
    AND jev({'current_phase': p.current_phase},
            'Is this pipeline currently in service?')
)
ORDER BY w.OBJECTID;
```

`jev` uses a probability threshold of 0.5.
An optional third argument sets a different threshold.
The threshold does not change the distance limit.

### Intersecting pipelines selected by lifecycle

“Show pipelines that intersect a discovery area and that Jev classifies as in service.”

`jev_choice` returns one of the supplied labels.
The equality condition accepts only `in service`.
`ST_Intersects` includes boundary contact.

```sql
SELECT p.OBJECTID
FROM pipelines_m p
WHERE EXISTS (
  SELECT 1 FROM discovery_areas d
  WHERE ST_Intersects(p.geometry, d.geometry)
    AND jev_choice({'current_phase': p.current_phase},
                   'What is the pipeline lifecycle state?',
                   ['in service', 'decommissioned', 'abandoned']) = 'in service'
)
ORDER BY p.OBJECTID;
```

The model must select one of the supplied labels.
A selected label does not show calibrated confidence.

### Discovery areas containing model-accepted deep wells

“Show discovery areas that contain a deep well which Jev classifies as operating.”

`jev_prob` supplies the operating-status probability.
The query compares that probability with 0.5.
`ST_Contains` requires the same well to be inside the area.
For point wells, the area boundary is excluded.

```sql
SELECT d.OBJECTID
FROM discovery_areas d
WHERE EXISTS (
  SELECT 1 FROM deep_wells_m w
  WHERE ST_Contains(d.geometry, w.geometry)
    AND jev_prob({'status': w.status},
                 'Does the well status explicitly say that it is operating?') >= 0.5
)
ORDER BY d.OBJECTID;
```

This example shows model error. Areas **687, 1197, and 1341** are false positives.
No correct area is missing.
Use `w.status = 'COMPLETED (OPERATING)'` if the task needs this known status code.
A higher threshold needs its own validation.
A higher threshold does not make the remaining answers correct.

### Nearest model-accepted pipeline

“For each deep well, show the nearest pipeline that Jev classifies as in service.”

The semantic filter selects the eligible pipelines before the distance ranking.
Do not select the nearest pipeline first and reject it after.
That order can discard a well that has another eligible pipeline.

```sql
SELECT w.OBJECTID AS well_id, nearest.pipeline_id, nearest.distance_m
FROM deep_wells_m w
CROSS JOIN LATERAL (
  SELECT p.OBJECTID AS pipeline_id,
         ST_Distance(w.geom_m, p.geom_m) AS distance_m
  FROM pipelines_m p
  WHERE jev({'current_phase': p.current_phase},
            'Is this pipeline currently in service?')
  ORDER BY distance_m, p.OBJECTID
  LIMIT 1
) nearest
ORDER BY w.OBJECTID;
```

The lowest pipeline ID breaks an equal-distance tie. There is no 5 km limit.
A well has no result row if no pipeline passes the semantic filter.
The distance is between geometries. It is not a distance along a pipeline.

## Add a semantic filter to candidate rows

Use this procedure when the model must receive only candidate rows.
A separate temporary table makes sure of that order.
Two predicates in one `WHERE` clause do not.

Before you start, do these steps:

- Create the temporary tables from the [exact spatial examples](#exact-spatial-examples).
- Do [setup step 7](#set-up-the-native-extension).

Do these steps:

1. Load the ONNX model.

   ```sql
   SELECT jev_laya_load('models/laya-onnx/laya');
   ```

2. Store the candidate rows in a temporary table.

   ```sql
   CREATE TEMP TABLE exact_wells AS
   SELECT w.OBJECTID, w.status
   FROM wells_m w
   WHERE w.water_depth > 350
     AND EXISTS (
       SELECT 1 FROM service_pipelines_m p
       WHERE ST_DWithin(w.geom_m, p.geom_m, 5000)
     );
   ```

3. Apply the semantic filter to the candidate rows.

   ```sql
   SELECT OBJECTID, status,
          jev_prob({'status': status},
                   'Does the well status explicitly say that it is operating?') AS probability
   FROM exact_wells
   WHERE jev({'status': status},
             'Does the well status explicitly say that it is operating?')
   ORDER BY OBJECTID;
   ```

This example tests model behavior.
The model accepts wells 4918 and 5026. Both wells have `DRILLING` status.
These results are false positives.
Thus the model is not a reliable operating-status filter.
Use exact SQL comparisons for known status codes.
Model probabilities and scores are not calibrated for NorthSea.

[native/northsea.sql](native/northsea.sql) contains this procedure as one file.
To inspect the full answers, see [Inspect model answers](docs/sql-reference.md#inspect-model-answers).

## Ask English questions

The optional Python planner compiles a fixed English grammar into parameterized SQL.
The native extension has no English parser.

1. Print the query plan.

   ```sh
   .venv/bin/jev-spatial \
     'Show all wells with depth > 350 meters and are within 5 km of an in-service pipeline' \
     --database data/NorthSea/NorthSea.ddb
   ```

The [Python planner](docs/python-planner.md) document gives the grammar, CLI options, and typed query API.

No procedure in this documentation changes the NorthSea data or metadata.

## Acknowledgments

Thank you to [Geodata](https://www.geodata.no/) for the NorthSea sample data.
