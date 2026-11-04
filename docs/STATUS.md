Last session: 2026-11-03 — Separate Docker stack for real-data runs
Brief completed: yes (CI on PR #12: lint-test, integration, airflow, e2e all pass, including the volume-name assertion)
Done:
- Every Makefile compose call (up, down, logs, psql-dev, airflow-up, dashboard-up, e2e) goes through COMPOSE = docker compose --env-file "$(ENV_FILE)".
- Verified with a throwaway synthetic env file: COMPOSE_PROJECT_NAME in --env-file DOES override the pinned `name: gc-wrapped` (no -p needed). The e2e run created gc-wrapped-probe_pgdata and left gc-wrapped_pgdata alone; probe stack, volume and file removed afterward.
- Host ports: POSTGRES_HOST_PORT (falls back to POSTGRES_PORT, then 5432) and DASHBOARD_PORT (8501). Checked with 5433/8502: published ports and dashboard health matched.
- CI e2e env file sets COMPOSE_PROJECT_NAME=gc-wrapped-ci; new step asserts volume gc-wrapped-ci_pgdata exists and no gc-wrapped_* volume does.
- .claude/settings.json: deny Bash(*gc-wrapped-real*) and Bash(*real.env*).
- .env.example and README "Running on real data" updated; decisions.md has the brief's entry plus one more (below).
Deviations from the brief (what + why):
- Found a leak the brief did not mention: the Makefile did `-include .env` + `export`, and compose gives shell vars precedence over --env-file (verified). A run with ENV_FILE=<other> would have used .env's passwords and DB name. Fixed by `-include $(ENV_FILE)`. Consequence: ENV_FILE must be plain KEY=value (no quotes/spaces). Logged in decisions.md.
- README says GCW_LANDING_HOST_DIR (absolute path), not "GCW_LANDING_DIR": the host-side variable is _HOST_DIR; GCW_LANDING_DIR is the in-container path set by compose.
- psql-dev kept its name (brief said "psql") and still targets gcw_dev only.
- Airflow UI port 8080 is not configurable (not in the brief); only `make airflow-up` publishes it, so two stacks can't both run that target.
In progress / broken: nothing known. Local: make e2e on default .env ran twice, both inserted 0 (dev volume already held the data); make lint test clean.
Next up: real-data run by Ryan in his own terminal with the new env file, then format reconciliation.
Open questions for planning: none.
Measured this session (exact value + source):
- Probe e2e (fresh volume): rows parsed 140, inserted 140 (gcw last-run). Default-.env e2e x2: parsed 140, inserted 0 each. make lint test: 87 passed, 18 deselected.
Commits: 9ee61c9 — Separate compose stack for real-data runs; aa69fbd — Update STATUS for separate real-data stack; 1e2da55 — Merge pull request #12 (merge commit on main)
Phase 1 checklist: parser [x] (provisional format) loader [x] dbt staging [x] marts [x] Airflow DAG [x] dashboard [x] one-command e2e [x]
