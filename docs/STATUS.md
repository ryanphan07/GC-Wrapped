Last session: 2026-11-03 — Airflow DAG orchestrating ingest and dbt build
Brief completed: yes once merged (CI green on branch airflow-dag, run 41595574540; merge hashes below)
Done:
- airflow/Dockerfile (apache/airflow:3.3.2-python3.12): gcwrapped + dbt in /opt/gcw-venv from uv.lock (uv export; editable install at /opt/gcw so migrations/ resolves); dbt project and migrations copied in; `runtime` and `test` stages. .dockerignore keeps data/, .env and fixtures out of build contexts.
- airflow/dags/gcw_pipeline.py: @daily, catchup=False, max_active_runs=1, retries=1; check_landing >> migrate >> ingest >> dbt_deps_and_build >> summarize, all @task.bash calling the venv's gcw/dbt. DB from env, no Airflow connections.
- New CLI: `gcw check-landing DIR`, `gcw ingest --landing DIR`, `gcw last-run` (src/gcwrapped/landing.py, runs.py); 6 unit tests + 1 integration test (idempotent landing ingest, last-run counts).
- docker-compose `airflow` service (standalone, landing mount read-only, DATABASE_URL/POSTGRES_* env, depends on db healthy); `make airflow-up`.
- DAG tests (4) inside the test image: imports clean, task order, schedule/catchup/max_active_runs, retries.
- CI job `airflow`: build both targets, DAG tests, synthetic landing dir, `airflow db migrate && airflow dags test gcw_pipeline` against the CI Postgres, then asserts dbt PASS line with ERROR=0, raw.messages and analytics.fct_messages non-empty, latest load run succeeded.
- README: running the DAG, why separate environments. decisions.md: 2 entries from the brief + 2 more.
Deviations from the brief (what + why):
- Added `gcw check-landing`, `ingest --landing`, `last-run` rather than logic in the DAG: keeps the DAG free of project imports, testable without Airflow.
- `airflow db migrate` and `dags test` run in one container invocation (the SQLite file lives in the container).
- DAG tests use dagbag.dags and dag.schedule: DagBag.get_dag queries the metadata DB, and CronTriggerTimetable has no .summary.
- Not run locally: the image, compose service and `make airflow-up` (no Docker/make here). The image and DAG are verified by CI only.
In progress / broken:
- compose service and `make airflow-up` are unexercised anywhere (CI does not use compose).
- airflow/ is outside mypy's files; the DAG file is linted by ruff only.
- Server-side error scrubbing against a real server log still unverified (unchanged).
Next up:
- Dashboard, one-command e2e (remaining Phase 1 items).
- Ryan: try `make airflow-up` with his landing dir; inspector on real export, reconcile format module.
Open questions for planning:
- None blocking.
Measured this session (exact value + source):
- CI run 41595574540, branch airflow-dag (2026-11-03 UTC): all 5 DAG tasks succeeded; export roots 2; ingest rows parsed 140, inserted 140; dbt Done. PASS=124 WARN=0 ERROR=0 TOTAL=124; DAG tests 4 passed.
- Local: ruff, ruff format --check, mypy --strict clean; pytest -m "not integration" 79 passed, 18 deselected.
Commits: (branch commits and merge hash filled in after merge)
Phase 1 checklist: parser [x] (provisional format) loader [x] dbt staging [x] marts [x] Airflow DAG [x] dashboard [ ] one-command e2e [ ]
