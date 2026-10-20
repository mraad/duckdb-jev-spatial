-- Run from the project root in a fresh DuckDB 1.5.6 session.
-- Set ORT_DYLIB_PATH to the local ONNX Runtime library before starting DuckDB.
-- duckdb -readonly -unsigned /path/to/NorthSea.ddb < native/spatial-jev-examples.sql
LOAD 'native/dist/jev_spatial.duckdb_extension';
SELECT jev_laya_load('models/laya-onnx/laya');

-- Materialize attribute and geometry filters before the mixed queries.
CREATE TEMP TABLE deep_wells_m AS
SELECT OBJECTID, status, geometry,
       ST_Transform(geometry, 'EPSG:4326', 'EPSG:3035', always_xy := true) AS geom_m
FROM Wells
WHERE water_depth > 350
  AND geometry IS NOT NULL AND NOT ST_IsEmpty(geometry);

CREATE TEMP TABLE pipelines_m AS
SELECT OBJECTID, current_phase, geometry,
       ST_Transform(geometry, 'EPSG:4326', 'EPSG:3035', always_xy := true) AS geom_m
FROM Pipelines
WHERE geometry IS NOT NULL AND NOT ST_IsEmpty(geometry);

CREATE TEMP TABLE discovery_areas AS
SELECT OBJECTID, geometry FROM Discoveries
WHERE geometry IS NOT NULL AND NOT ST_IsEmpty(geometry);

-- Predicate order below does not control inference order.
-- Jev receives only status text. DuckDB evaluates all geometry.

-- example: deep_wells_near_jev_pipeline
SELECT w.OBJECTID
FROM deep_wells_m w
WHERE EXISTS (
  SELECT 1 FROM pipelines_m p
  WHERE ST_DWithin(w.geom_m, p.geom_m, 5000)
    AND jev({'current_phase': p.current_phase},
            'Is this pipeline currently in service?')
)
ORDER BY w.OBJECTID;

-- example: jev_pipelines_intersecting_discovery
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

-- example: discoveries_containing_jev_deep_wells
-- Diagnostic: DRILLING can pass this model threshold incorrectly.
SELECT d.OBJECTID
FROM discovery_areas d
WHERE EXISTS (
  SELECT 1 FROM deep_wells_m w
  WHERE ST_Contains(d.geometry, w.geometry)
    AND jev_prob({'status': w.status},
                 'Does the well status explicitly say that it is operating?') >= 0.5
)
ORDER BY d.OBJECTID;

-- example: nearest_jev_pipeline_to_deep_well
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
