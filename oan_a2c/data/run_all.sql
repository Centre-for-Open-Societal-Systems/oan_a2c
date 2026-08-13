-- Load all Ethiopia admin boundary tables (requires psql)
-- Usage: psql -d your_database -f run_all.sql

\i 00_setup.sql
\i eth_regions.sql
\i eth_zones.sql
\i eth_woredas.sql
