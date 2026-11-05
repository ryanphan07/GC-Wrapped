# Status
Last session: 2026-11-05 — Isolation guard fails closed on database URLs; CLAUDE.md wording fixes

## Phase & state
Phase 1 (Pipeline) complete. This session changed the test guard and CLAUDE.md only.
Checklist: parser [x] loader [x] dbt staging [x] marts [x] Airflow DAG [x] dashboard [x] e2e [x] real-data run [x] metrics [x]

## Done this session
- tests/isolation_guard.py: check_database_url now accepts only postgresql:// and postgres:// (the schemes libpq/psycopg take, which config.py and db.py pass DATABASE_URL to). Other schemes and non-URLs (keyword DSNs) fail with "<VAR> cannot be parsed". The path is percent-decoded before comparison. Query-string `port=5433` and `dbname=gcw_real` are rejected; other parameters (sslmode) pass. Messages name the variable and rule only.
- tests/test_isolation_guard.py: parametrized over both URL vars: query-string port, query-string dbname, percent-encoded path, keyword DSN, unsupported scheme; sslmode=disable passes; sentinel password not leaked from a query-string rejection.
- CLAUDE.md: stop trigger 6 wording, "Done and Deviations" moved out of the list, "hash updates" dropped.
- Resolved: PR 27's "CI on this branch" (passed) and "empty DATABASE_URL passes" (harmless: the guard checks the component variables the code falls back to).
- Makes the code match D66; no new decision.

## Deviations from brief
- none

## Checks
- make lint test: ruff, format, mypy --strict pass; 315 passed, 22 deselected.
- grep "hash updates" CLAUDE.md: no output; grep "^- Done and Deviations": no output; "Done and Deviations cover this session only": one line.
- dbt tests, integration, warehouse tests: not run (no pipeline change).

## Not verified
- CI on this branch. No real-data make target needed.

## Open issues and questions
- tests/conftest.py clean_db: make test-integration empties gcw_dev; consider a separate gcw_test database. Run make e2e before make metrics afterward.
- docs/decisions.md and METRICS.md carry old counts; decisions.md is append-only, so these stay.
- Open question: real-ingest-synthetic run 3 takes ~323 s (likely Airflow retries on the failure path); parked.

## Suggested next session
Session C2 (shared parse loop).
