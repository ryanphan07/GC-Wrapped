-- Runs once, on first start of an empty data volume (docker-entrypoint-initdb.d).
-- gcw_dev: synthetic data only. gcw_real: Ryan's real data; Claude Code never connects to it.
-- gcw_ci: the real-ingest-synthetic CI job's database (synthetic sentinel data).
CREATE DATABASE gcw_dev;
CREATE DATABASE gcw_ci;
CREATE DATABASE gcw_real;

\connect gcw_dev
CREATE EXTENSION IF NOT EXISTS vector;

\connect gcw_real
CREATE EXTENSION IF NOT EXISTS vector;

\connect gcw_ci
CREATE EXTENSION IF NOT EXISTS vector;
