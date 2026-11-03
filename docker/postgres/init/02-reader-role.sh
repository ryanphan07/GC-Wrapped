#!/usr/bin/env bash
# Creates the dashboard's read-only login role. Runs once on first start of an empty data volume
# (docker-entrypoint-initdb.d), and CI runs it too with PGHOST/PGUSER/PGPASSWORD set.
# The role gets no table privileges here: dbt grants SELECT on the mart tables and USAGE on the
# analytics schema (see dbt_project.yml). raw and staging stay unreadable to it.
set -euo pipefail

: "${GCW_READER_PASSWORD:?GCW_READER_PASSWORD must be set}"

# The password goes in as a psql variable and is quoted with :'pw', never spliced into the shell.
psql -v ON_ERROR_STOP=1 -v pw="$GCW_READER_PASSWORD" \
  --username "${POSTGRES_USER:-${PGUSER:-postgres}}" --dbname postgres <<'SQL'
SELECT 'CREATE ROLE gcw_reader LOGIN'
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'gcw_reader') \gexec
ALTER ROLE gcw_reader WITH LOGIN PASSWORD :'pw' NOSUPERUSER NOCREATEDB NOCREATEROLE;
SQL
