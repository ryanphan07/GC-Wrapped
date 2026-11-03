Last session: 2026-11-03 — Streamlit dashboard on marts via read-only role
Brief completed: yes (CI green on branch; PR merged with a merge commit, hashes below)
Done:
- docker/postgres/init/02-reader-role.sh: idempotent gcw_reader LOGIN role from GCW_READER_PASSWORD (password passed as a psql variable). CI runs the same script in the integration and airflow jobs.
- dbt: marts get `+grants: select: [gcw_reader]`; on-run-end hook grants USAGE on the target schema. Staging and intermediate get nothing.
- src/gcwrapped/dashboard/queries.py: typed, parameterized queries (threads, daily activity, member leaderboard, sessions, hour of day, median reply); connects with DASHBOARD_DATABASE_URL (config.py DashboardSettings).
- dashboard/app.py: thread selector, 4 KPIs, messages-per-day line, leaderboard, hour-of-day bar, privacy note. dashboard/Dockerfile + compose `dashboard` service (8501); `make dashboard-up`.
- tests/test_dashboard_warehouse.py (13 tests, marker `warehouse`): column/non-empty checks per query, permission denied on raw.messages, stg_instagram__messages and int_messages_sessionized, SELECT on fct_messages ok, no `content` column visible, Streamlit AppTest smoke.
- CI integration job: `uv sync --group dbt --group dashboard`, role setup before dbt, `make test-warehouse` after dbt build.
- README dashboard section; decisions.md: 2 entries from the brief + 1 (warehouse marker).
Deviations from the brief (what + why):
- New pytest marker `warehouse` and `make test-warehouse`: integration tests run before dbt build and reset schemas, so mart-reading tests cannot share that marker.
- Makefile ignores tests/test_dashboard_warehouse.py in `test` and `test-integration` (it imports streamlit/pandas, absent from the lint-test install). mypy ignores missing imports for pandas/streamlit.
- The airflow CI job also creates the role: dbt's grants fail if gcw_reader does not exist.
- Staging and intermediate views share the `analytics` schema with marts (no custom schemas), so isolation is per-relation grants, not per-schema.
In progress / broken:
- Compose `dashboard` service and the Dockerfile are unexercised (no Docker here, CI does not build them).
- Existing Postgres volumes will not rerun init scripts; README gives the one-off command.
- dbt now fails if gcw_reader is missing (e.g. on a real DB that was never given the role).
Next up: one-command e2e (last Phase 1 item). Ryan: create the role on gcw_real if the dashboard should read it.
Open questions for planning: none blocking.
Measured this session (exact value + source):
- CI run 41598617032 (branch dashboard): all 3 jobs success; integration `18 passed` then `13 passed` (warehouse); dbt PASS=125 WARN=0 ERROR=0 TOTAL=125; airflow DAG tests 4 passed.
- Local: ruff, ruff format --check, mypy --strict clean; pytest unit 79 passed, 18 deselected.
Commits: see git log below (branch commits and merge hash filled in after merge).
Phase 1 checklist: parser [x] (provisional format) loader [x] dbt staging [x] marts [x] Airflow DAG [x] dashboard [x] one-command e2e [ ]
