# Status
Last session: 2026-11-05 — Workflow hardening (session rules, STATUS format, test-isolation guard)

## Phase & state
Phase 1 (Pipeline) complete; PR 25 merged (6b36f8f). This session changed workflow and docs only.
Checklist: parser [x] loader [x] dbt staging [x] marts [x] Airflow DAG [x] dashboard [x] e2e [x] real-data run [x] metrics [x]

## Done this session
- CLAUDE.md: added "Session rules"; removed overlapping old wording (see Deviations).
- tests/isolation_guard.py (new): check function; resolves ~ and symlinks, never opens a private path.
- tests/conftest.py: session-scoped autouse fixture runs the guard, `pytest.exit(code 3)` on failure.
- tests/test_isolation_guard.py (new): 17 tests (negative per real-stack value, wiring subprocess test).
- docs/decisions.md: headers renumbered D1-D64 (text untouched); D65 appended.
- METRICS.md: header note only. docs/STATUS.md: rewritten in the new format.

## Deviations from brief
- Top-level conftest is tests/conftest.py (no repo-root one); guard function is in tests/isolation_guard.py so tests can import it. <GUARD_TEST_PATH> became those three paths.
- Removed from CLAUDE.md: "Every session" step 3 and the conflict-says-so clause in step 2, the STATUS.md handoff paragraph, old STATUS.md and decisions.md format sections, the STATUS-describes-latest-session bullet, the commit-hash recording rule, the "stop and ask Ryan; do not guess" sentence, the "never invent or round metrics" bullet, and the old Session end bullet. Kept: the `&&` commit-chaining rule, "never merge", and the "Run in your terminal" block (moved into a Real-data steps bullet).
- Guard also checks POSTGRES_HOST_PORT, GCW_LANDING_DIR and GCW_LOG_DIR (my reading of "any configured data path").
- decisions.md: every header already had a date, so no git log lookup; "###" headers became "##".
- README.md: no hardcoded counts existed, so no edit.

## Checks
- make lint test: ruff, format, mypy --strict pass; 278 passed, 22 deselected.
- Guard demo (unit-suite command with ENV_FILE set to a scratchpad file containing a real-stack Postgres port): stopped at the guard, "test-isolation guard: POSTGRES_PORT is the real stack's Postgres port", 0 tests ran. A normal `make test` right after: 278 passed.
- grep for hardcoded counts in README.md and CLAUDE.md: no matches. git diff METRICS.md: header note only.
- dbt tests, integration and warehouse tests: not run (no pipeline change).

## Not verified
- CI has not run on this branch; the guard has not been exercised under the CI jobs' env.
- No real-data make target needed.

## Open issues and questions
- tests/conftest.py clean_db: make test-integration empties gcw_dev; consider a separate gcw_test database. Run make e2e before make metrics afterward.
- docs/decisions.md and METRICS.md carry old counts; decisions.md is append-only, so these stay.
- Open question: real-ingest-synthetic run 3 takes ~323 s (likely Airflow retries on the failure path); parked.

## Suggested next session
Session C2 (shared parse loop).
