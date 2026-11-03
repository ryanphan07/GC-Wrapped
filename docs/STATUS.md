Last session: 2026-11-03 — Rename project to "GC Wrapped"
Brief completed: yes
Done:
- origin now https://github.com/ryanphan07/GC-Wrapped.git (was .../GC-Wrapped-Group-Chat-AI-Platform.git).
- Only stale repo reference in tracked files was the README clone line; now `git clone https://github.com/ryanphan07/GC-Wrapped.git && cd GC-Wrapped`. Titles (README, CLAUDE.md, dashboard, package docstring, CLI) already read "GC Wrapped" with no subtitle; body sentences "Group chat analytics platform ..." (README, CLAUDE.md, pyproject description) left as plain descriptions.
- docker-compose.yml: top-level `name: gc-wrapped`.
- decisions.md: new entry for the rename and compose name.
- Folder-independence check: Makefile, scripts, CI use no folder-derived names (CI uses github.workspace/RUNNER_TEMP). Image names are fixed (gcw-airflow:runtime, gcw-dashboard).
Deviations from the brief (what + why):
- Nothing to change in METRICS.md, .env.example, DAG tags, Dockerfiles, CI names, pyproject URLs: none contained the old slug or a subtitle.
In progress / broken:
- Compose volume names change from the old folder-derived project name to gc-wrapped_*. An existing local dev DB volume will look empty after this; re-create with make e2e (synthetic data) or migrate the volume. The real-data DB (gcw_real) volume, if it lives in the same compose project, needs the migration below before the first `up`.
- tests/test_dashboard_warehouse.py needs GCW_DB_NAME/GCW_READER_PASSWORD and a running DB; locally without them it errors (13) with or without this change. CI passes it.
Next up: Ryan renames the local folder (commands in session summary), then real-data run and format reconciliation.
Open questions for planning: none.
Measured this session (exact value + source):
- Local: ruff check, ruff format --check, mypy --strict clean; pytest -m "not integration" --ignore=tests/test_dashboard_warehouse.py: 87 passed, 18 deselected.
- CI on PR #8: lint-test, integration, airflow, e2e all pass.
Commits: 71c154f — Rename to GC Wrapped: README clone URL, pin compose project name; 38c274e — Merge pull request #8 (merge commit on main).
Phase 1 checklist: parser [x] (provisional format) loader [x] dbt staging [x] marts [x] Airflow DAG [x] dashboard [x] one-command e2e [x]
