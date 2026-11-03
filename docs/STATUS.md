Last session: 2026-11-02 — Postgres loader with idempotent raw tables
Brief completed: yes (CI green on branch loader-raw-tables; PR/merge hash under Commits)
Done:
- migrations/001_raw_schema.sql: schema raw, raw.messages (message_key PK, load_id FK), raw.load_runs.
- src/gcwrapped/migrate.py + `gcw migrate`: ordered files, one transaction per file, schema_migrations tracking, advisory lock, idempotent.
- src/gcwrapped/load.py + `gcw ingest PATH [PATH ...]`: COPY to temp table, INSERT ... ON CONFLICT DO NOTHING, one transaction; run row written first (autocommit), marked succeeded in the load transaction, marked failed from a fresh connection on any error, then re-raised.
- src/gcwrapped/db.py: database_url() and database_name() (name only is printed, never credentials).
- Tests: 12 integration (all six brief scenarios plus CLI output, missing-migration hint, failed-migration rollback, failed load leaves earlier data intact), unit tests for row conversion, reactions JSON, migration discovery, db name.
- README documents migrate and ingest; decisions.md has the two entries from the brief.
Deviations from the brief (what + why):
- `gcw ingest` previously took one `file`; now takes `paths` (nargs +), as the brief says.
- Added tiny extras: advisory lock in migrate, idempotent-failure tests, `clean_db` fixture refuses to reset any DB other than gcw_dev.
In progress / broken:
- Privacy gap: when a load fails mid-COPY, psycopg sends the exception text to Postgres, which writes it to the server log (seen in CI with a synthetic sentinel). ParseError text is value-free by design, but other exceptions could echo data. Not fixed (scope); fix would wrap iterator errors in a text-free exception inside the COPY block.
- `gcw ingest` on a path with no threads records a succeeded run with 0 rows, then exits 1.
- Migrations dir is resolved from the source tree (repo root); fine for editable installs, would need packaging for a wheel.
- Format still PROVISIONAL (see previous session): real-schema reconciliation not done yet.
Next up:
- Ryan: inspector on real export, reconcile format module; run `gcw migrate` + `gcw ingest` against his own DB.
- dbt sources + staging models.
Open questions for planning:
- Should failed-load exception text be scrubbed before it reaches the server log (see above)?
Measured this session (exact value + source):
- CI run 41588321575 (2026-11-03 UTC): unit 65 passed, 12 deselected; integration 12 passed, 65 deselected.
- Local: ruff check, ruff format --check, mypy --strict clean; pytest -m "not integration" 65 passed (2026-11-02).
Commits: loader-raw-tables branch — Add SQL migrations and idempotent Postgres loader (gcw migrate, gcw ingest); merge commit: see PR
Phase 1 checklist: parser [x] (provisional format) loader [x] dbt staging [ ] marts [ ] Airflow DAG [ ] dashboard [ ] one-command e2e [ ]
