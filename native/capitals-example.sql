-- Run from the project root in a fresh in-memory DuckDB 2.0.0 session.
-- Set ORT_DYLIB_PATH to the local ONNX Runtime library before starting DuckDB.
-- duckdb -unsigned < native/capitals-example.sql
-- This example does not use the NorthSea database.
LOAD 'native/dist/jev_spatial.duckdb_extension';
SELECT jev_laya_load('models/laya-onnx/laya');

-- Locations are (longitude, latitude). DuckDB 2.0 needs this setting for ST_Distance_Sphere.
SET geometry_always_xy = true;

-- Three columns only. The table has no capital column.
CREATE TEMP TABLE cities (location GEOMETRY, city VARCHAR, country VARCHAR);
INSERT INTO cities VALUES
  (ST_Point(2.3522, 48.8566),    'Paris',          'France'),
  (ST_Point(4.8357, 45.7640),    'Lyon',           'France'),
  (ST_Point(13.4050, 52.5200),   'Berlin',         'Germany'),
  (ST_Point(11.5820, 48.1351),   'Munich',         'Germany'),
  (ST_Point(139.6917, 35.6895),  'Tokyo',          'Japan'),
  (ST_Point(135.5023, 34.6937),  'Osaka',          'Japan'),
  (ST_Point(149.1300, -35.2809), 'Canberra',       'Australia'),
  (ST_Point(151.2093, -33.8688), 'Sydney',         'Australia'),
  (ST_Point(-75.6972, 45.4215),  'Ottawa',         'Canada'),
  (ST_Point(-79.3832, 43.6532),  'Toronto',        'Canada'),
  (ST_Point(32.8597, 39.9334),   'Ankara',         'Turkey'),
  (ST_Point(28.9784, 41.0082),   'Istanbul',       'Turkey'),
  (ST_Point(-47.8825, -15.7942), 'Brasília',       'Brazil'),
  (ST_Point(-43.1729, -22.9068), 'Rio de Janeiro', 'Brazil'),
  (ST_Point(10.7522, 59.9139),   'Oslo',           'Norway'),
  (ST_Point(5.3221, 60.3913),    'Bergen',         'Norway'),
  (ST_Point(31.2357, 30.0444),   'Cairo',          'Egypt'),
  (ST_Point(29.9187, 31.2001),   'Alexandria',     'Egypt'),
  (ST_Point(36.8219, -1.2921),   'Nairobi',        'Kenya'),
  (ST_Point(39.6682, -4.0435),   'Mombasa',        'Kenya');

-- Jev receives only the city and country text. DuckDB evaluates all geometry.
-- The model probability is not calibrated. Known errors are listed in docs/validation.md.

-- example: jev_capital_probability
SELECT city, country,
       round(jev_prob({'city': city, 'country': country},
                      'Is this city the capital of its country?'), 3) AS p_capital
FROM cities
ORDER BY country, city;

-- example: jev_capitals
SELECT city, country, ST_AsText(location) AS location
FROM cities
WHERE jev({'city': city, 'country': country},
          'Is this city the capital of its country?')
ORDER BY country, city;

-- example: jev_capitals_within_1500_km_of_paris
-- Paris itself has distance 0 and is included when the model accepts it.
SELECT city, country, round(meters / 1000) AS km_from_paris
FROM (
  SELECT city, country,
         ST_Distance_Sphere(location, ST_Point(2.3522, 48.8566)) AS meters
  FROM cities
  WHERE jev({'city': city, 'country': country},
            'Is this city the capital of its country?')
)
WHERE meters <= 1500000
ORDER BY meters, city;

-- example: jev_top_city_per_country
-- Rank the cities of each country by probability. Keep the highest one.
-- This query needs no threshold. It assumes that the table has the capital of each country.
-- The question has a different wording. Refer to docs/validation.md#capital-answers.
SELECT city, country, round(p_capital, 3) AS p_capital
FROM (
  SELECT city, country,
         jev_prob({'city': city, 'country': country},
                  'Is the capital of this country this city, and not another city?') AS p_capital
  FROM cities
)
QUALIFY p_capital = max(p_capital) OVER (PARTITION BY country)
ORDER BY country, city;
