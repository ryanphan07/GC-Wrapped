Last session: 2026-11-03 — Local synthetic run in WSL2 and README screenshot
Brief completed: yes
Done:
- Created .env from .env.example with random passwords (openssl rand -hex 16); GCW_DB_NAME=gcw_dev; .env confirmed gitignored (.gitignore line 3).
- Followed the README Quickstart as written on a fresh WSL2 Docker setup (uv sync, synthetic generator, make e2e): worked with no code changes.
- Second `make e2e`: rows inserted 0 (load id 2). `make test-integration`: 18 passed. `make test-warehouse`: 18 passed (after a fresh e2e, see below).
- Dashboard screenshot via Playwright Docker image (mcr.microsoft.com/playwright/python:v1.49.0-noble, --network host, no sudo): docs/images/dashboard-synthetic.png, embedded in README with a synthetic-data caption.
- Dev stack stopped with `make down`; no containers left running.
Deviations from the brief (what + why):
- README only: added a note that test-integration resets raw in gcw_dev (tests/conftest.py), so test-warehouse needs a fresh `make e2e` first. Running them in the brief's order (integration, then warehouse) failed 2 warehouse tests with `schema "raw" does not exist` until e2e was rerun. CI already orders it this way.
- A first Bash command that also listed data/landing was denied; I did not retry the listing (data/ is off limits).
In progress / broken:
- Nothing. CI on PR #10: lint-test, integration, airflow, e2e all pass.
Next up: real-data run and format reconciliation.
Open questions for planning: should test-warehouse depend on e2e in the Makefile so the ordering trap goes away? (Left as a docs note; small diff.)
Measured this session (exact value + source):
- First make e2e: rows parsed 140, inserted 140, dbt PASS=125 WARN=0 ERROR=0 (gcw last-run, dbt output).
- Second make e2e: rows parsed 140, rows inserted 0 (gcw last-run, load id 2).
- make test-integration: 18 passed, 87 deselected. make test-warehouse: 18 passed, 105 deselected. make lint test: clean, 87 passed, 18 deselected.
Commits: 6efd149 — Verify local synthetic e2e; add dashboard screenshot to README; 79acbaa — Merge pull request #10 (merge commit on main)
Phase 1 checklist: parser [x] (provisional format) loader [x] dbt staging [x] marts [x] Airflow DAG [x] dashboard [x] one-command e2e [x]
