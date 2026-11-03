Last session: 2026-11-03 — One-command e2e, verified in CI
Brief completed: yes (CI green on branch incl. e2e job with idempotent second run; merge hashes below)
Done:
- Single target DB: GCW_DB_NAME is required by DatabaseSettings and DashboardSettings (config.py). URLs are built from POSTGRES_* / GCW_READER_PASSWORD; an explicit DATABASE_URL / DASHBOARD_DATABASE_URL is accepted only if its dbname equals GCW_DB_NAME, else ValidationError (CLI prints a fixed "database not configured" message). dbt profile reads env_var('GCW_DB_NAME') with no default. DBT_DEV_DBNAME and GCW_DBNAME are gone.
- Tests: 6 new in tests/test_config.py (built URL, matching URL ok, mismatch fails for gcw and dashboard, dashboard and gcw resolve the same name, dbt profile reads GCW_DB_NAME with no default).
- `make e2e ENV_FILE=…` (default .env): compose up --build --wait db dashboard (dashboard got a healthcheck), build airflow, `compose run -T --no-deps airflow` runs `airflow db migrate`, `airflow dags test gcw_pipeline`, `gcw last-run`; prints dashboard URL. `gcw last-run` now prints the target database first.
- CI job `e2e`: writes an env file and synthetic landing dir under RUNNER_TEMP, runs `make e2e` twice; asserts /_stcore/health = 200, raw.messages and analytics.fct_messages non-empty, first run inserted > 0, second run's latest load_runs.rows_inserted = 0.
- clean_text replaces lone surrogates with U+FFFD (after mojibake fix and NUL strip, before hashing); 2 parser tests. The loader's error-path test still uses its hand-built row.
- .env.example lists every variable with comments; README: Quickstart, Running on real data, Privacy (consent, redaction plan, text never leaves staging, reader role, content-free logs, agent isolation).
- decisions.md: the 3 entries from the brief.
Deviations from the brief (what + why):
- GCW_DB_NAME has no default (compose uses ${GCW_DB_NAME:?}), so every compose command, including `make down`, needs it in .env or the env file. Chosen so a real run is always an explicit choice.
- Airflow runs via `compose run`, not `exec` into the long-running `standalone` service: no wait for that service to boot, and no service left running.
- Privacy "redaction plan" says redaction is planned, not built (Phase 1 has no LLM calls). "Consent" wording is mine; edit if it should say more.
In progress / broken:
- Existing Postgres volumes do not rerun init scripts: gcw_reader must be created once (command in README) before dbt grants work on an old volume.
- LANDING_DIR and CHAT_TIMEZONE (config.Settings) are read by nothing yet; noted in .env.example.
- Pydantic ValidationError text includes the input values if it ever reaches a traceback; the CLI catches it and prints a fixed message, but the dashboard does not.
Next up: Phase 1 is complete. Ryan: run the real sequence below in his own terminal and report counts. Then format reconciliation against the real export (spec is still PROVISIONAL).
Open questions for planning: none blocking.
Measured this session (exact value + source):
- CI run 41600157782 (branch e2e): lint-test, integration, airflow, e2e all success. In the e2e job, run 1: dbt PASS=125 WARN=0 ERROR=0 TOTAL=125, load id 1 rows parsed 140 rows inserted 140; run 2: load id 2 rows parsed 140 rows inserted 0.
- Local: ruff, ruff format --check, mypy --strict clean; pytest unit 87 passed, 18 deselected.
Commits: 6a193e4 — One-command e2e (make e2e) verified in CI; single GCW_DB_NAME; replace lone surrogates; aca2edc — Fix e2e job: runner context is unavailable in job env; 8677e9b — Update STATUS for e2e session; 3a66488 — Merge pull request #7 (merge commit on main).
Phase 1 checklist: parser [x] (provisional format) loader [x] dbt staging [x] marts [x] Airflow DAG [x] dashboard [x] one-command e2e [x]
