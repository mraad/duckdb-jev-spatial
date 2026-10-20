-- Generated from the typed plan in northsea-plan.json.
-- Run using duckdb -readonly /path/to/NorthSea.ddb < artifacts/northsea.sql
LOAD spatial;
SELECT w.* FROM main."Wells" w WHERE w."water_depth" > CAST(350.0 AS DOUBLE PRECISION) AND w."geometry" IS NOT NULL AND NOT ST_IsEmpty(w."geometry") AND EXISTS (SELECT 1 FROM main."Pipelines" p WHERE p."current_phase" = 'IN SERVICE' AND p."geometry" IS NOT NULL AND NOT ST_IsEmpty(p."geometry") AND ST_DWithin(ST_Transform(w."geometry", 'EPSG:4326', 'EPSG:3035', always_xy := TRUE), ST_Transform(p."geometry", 'EPSG:4326', 'EPSG:3035', always_xy := TRUE), CAST(5000.0 AS DOUBLE PRECISION)));
