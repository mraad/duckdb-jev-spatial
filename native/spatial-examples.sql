-- Run from the project root in a fresh DuckDB 1.5.6 session.
-- duckdb -readonly -unsigned /path/to/NorthSea.ddb < native/spatial-examples.sql
-- These exact spatial queries do not load a Laya model.
LOAD 'native/dist/jev_spatial.duckdb_extension';

-- setup: retain source geometry for topology; project distance geometry once.
CREATE TEMP TABLE wells_m AS
SELECT OBJECTID, water_depth, status, geometry,
       ST_Transform(geometry, 'EPSG:4326', 'EPSG:3035', always_xy := true) AS geom_m
FROM Wells
WHERE geometry IS NOT NULL AND NOT ST_IsEmpty(geometry);

CREATE TEMP TABLE service_pipelines_m AS
SELECT OBJECTID, geometry,
       ST_Transform(geometry, 'EPSG:4326', 'EPSG:3035', always_xy := true) AS geom_m
FROM Pipelines
WHERE current_phase = 'IN SERVICE'
  AND geometry IS NOT NULL AND NOT ST_IsEmpty(geometry);

CREATE TEMP TABLE discovery_areas AS
SELECT OBJECTID, geometry FROM Discoveries
WHERE geometry IS NOT NULL AND NOT ST_IsEmpty(geometry);

-- example: deep_wells_near_pipeline
SELECT w.OBJECTID
FROM wells_m w
WHERE w.water_depth > 350
  AND EXISTS (
    SELECT 1 FROM service_pipelines_m p
    WHERE ST_DWithin(w.geom_m, p.geom_m, 5000)
  )
ORDER BY w.OBJECTID;

-- example: deep_wells_without_near_pipeline
SELECT w.OBJECTID
FROM wells_m w
WHERE w.water_depth > 350
  AND NOT EXISTS (
    SELECT 1 FROM service_pipelines_m p
    WHERE ST_DWithin(w.geom_m, p.geom_m, 5000)
  )
ORDER BY w.OBJECTID;

-- example: wells_within_discovery
SELECT w.OBJECTID
FROM wells_m w
WHERE EXISTS (
  SELECT 1 FROM discovery_areas d
  WHERE ST_Within(w.geometry, d.geometry)
)
ORDER BY w.OBJECTID;

-- example: pipelines_intersecting_discovery
SELECT p.OBJECTID
FROM service_pipelines_m p
WHERE EXISTS (
  SELECT 1 FROM discovery_areas d
  WHERE ST_Intersects(p.geometry, d.geometry)
)
ORDER BY p.OBJECTID;

-- example: discoveries_containing_wells
SELECT d.OBJECTID
FROM discovery_areas d
WHERE EXISTS (
  SELECT 1 FROM wells_m w
  WHERE ST_Contains(d.geometry, w.geometry)
)
ORDER BY d.OBJECTID;

-- example: wells_outside_all_discoveries
SELECT w.OBJECTID
FROM wells_m w
WHERE NOT EXISTS (
  SELECT 1 FROM discovery_areas d
  WHERE ST_Intersects(w.geometry, d.geometry)
)
ORDER BY w.OBJECTID;

-- example: deep_wells_within_discovery_near_pipeline
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

-- example: nearest_pipeline_to_deep_well
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
