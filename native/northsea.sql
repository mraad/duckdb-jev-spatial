-- Run from the project root with DuckDB 1.5.6 and ORT_DYLIB_PATH set.
-- duckdb -readonly -unsigned /path/to/NorthSea.ddb < native/northsea.sql
LOAD 'native/dist/jev_spatial.duckdb_extension';
SELECT jev_laya_load('models/laya-onnx/laya');

CREATE TEMP TABLE exact_wells AS
SELECT w.* FROM Wells w
WHERE w.water_depth > 350
  AND w.geometry IS NOT NULL AND NOT ST_IsEmpty(w.geometry)
  AND EXISTS (
    SELECT 1 FROM Pipelines p
    WHERE p.current_phase = 'IN SERVICE'
      AND p.geometry IS NOT NULL AND NOT ST_IsEmpty(p.geometry)
      AND ST_DWithin(
        ST_Transform(w.geometry, 'EPSG:4326', 'EPSG:3035', always_xy := true),
        ST_Transform(p.geometry, 'EPSG:4326', 'EPSG:3035', always_xy := true), 5000)
  );

SELECT count(*) AS exact_count, list(OBJECTID ORDER BY OBJECTID) AS exact_ids FROM exact_wells;

-- Diagnostic semantic condition: the checkpoint has known false positives for DRILLING.
SELECT OBJECTID, status,
       jev_prob({'status':status}, 'Does the well status explicitly say that it is operating?') AS probability
FROM exact_wells
WHERE jev({'status':status}, 'Does the well status explicitly say that it is operating?')
ORDER BY OBJECTID;

SELECT jev_laya_stats();
