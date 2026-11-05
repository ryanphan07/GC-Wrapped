# Status
Last session: 2026-11-05 — Workflow follow-ups (CLAUDE.md fixes, guard covers database URLs)

## Phase & state
Phase 1 (Pipeline) complete. This session changed workflow, the test guard and docs only.
Checklist: parser [x] loader [x] dbt staging [x] marts [x] Airflow DAG [x] dashboard [x] e2e [x] real-data run [x] metrics [x]

## Done this session
- CLAUDE.md: stop trigger 6 (brief conflicts with decisions/STATUS or expands scope); STATUS carry-forward rule; "Claude Code never merges PRs" replaces the merge-if-green line; "never merge" dropped from Real-data steps.
- tests/isolation_guard.py: DATABASE_URL and DASHBOARD_DATABASE_URL added to GUARDED_VARS; check_database_url rejects port 5433, database gcw_real, and unparsable URLs or invalid ports (fail closed). Messages name the variable only.
- tests/test_isolation_guard.py: URL tests (port, database, unparsable, invalid port, dev passes, sentinel password hidden, env file vs environ).
- METRICS.md: header table name raw.ingestion_runs -> raw.load_runs (migrations/001_raw_schema.sql); header line only.
- docs/decisions.md: D66 appended.
- Resolved: PR 26's "CI has not run on this branch" (merged with all checks green).

## Deviations from brief
- none

## Checks
- make lint test: ruff, format, mypy --strict pass; 295 passed, 22 deselected.
- grep "never merge" CLAUDE.md: no output; grep "never merges PRs": one line (11).
- dbt tests, integration, warehouse tests: not run (no pipeline change).

## Not verified
- CI on this branch. No real-data make target needed.
- An empty DATABASE_URL (as in .env.example) is treated as unset and passes.

## Open issues and questions
- tests/conftest.py clean_db: make test-integration empties gcw_dev; consider a separate gcw_test database. Run make e2e before make metrics afterward.
- docs/decisions.md and METRICS.md carry old counts; decisions.md is append-only, so these stay.
- Open question: real-ingest-synthetic run 3 takes ~323 s (likely Airflow retries on the failure path); parked.

## Suggested next session
Session C2 (shared parse loop).
