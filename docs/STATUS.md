Last session: 2026-11-05 — fix real-ingest-synthetic failure on PR 25 (make metrics step)
Brief completed: yes locally (make lint test: 261 passed; make test-integration passed); CI result confirmed on PR 25 (see Commits).
Done:
- Root cause: CI log said "metrics: failed at dbt (CalledProcessError)". With no dbt/dbt_packages (CI starts clean), dbt_counts() runs `dbt deps`, which accepts --log-path but NOT --target-path (checked with `dbt deps --help`; `dbt parse --help` lists both). deps exited with a usage error.
- Fix (src/gcwrapped/metrics.py): deps gets --project-dir, --profiles-dir, --log-path only; parse keeps --target-path and --log-path in the TemporaryDirectory.
- tests/test_metrics.py: new deps-before-parse test (fake subprocess.run, per-command option sets, no SENTINEL); buildable test now uses distinct per-field values and asserts 110111.
- ci.yml metrics step: asserts "dbt buildable nodes: N" equals the dbt build "TOTAL=" from run 1 (out1.txt prints it), fixed-reason failure message.
Deviations from the brief (what + why):
- None. Hypothesis confirmed; deps still redirects its logs via --log-path, so no dbt/logs deviation.
In progress / broken: nothing known.
Open issues:
- make test-integration empties gcw_dev (integration tests share the dev database); candidate: a separate gcw_test database. After it, make e2e is needed before make metrics.
Next up: Ryan's real-data make metrics run; then Session C2 (shared parse loop).
Open questions for planning:
- real-ingest-synthetic run 3 takes ~323 s (likely Airflow retries on the failure path): parked, not Phase 1 work.
Measured this session (exact value + source):
- Local make metrics, default .env (gcw_dev), with and without dbt/dbt_packages: "metrics: ok"; ls dbt shows no target/ or logs/.
- make lint test: 261 passed, 22 deselected.
Commits: pending (see git log after push)
Phase 1 checklist: parser [x] loader [x] dbt staging [x] marts [x] Airflow DAG [x] dashboard [x] one-command e2e [x] real-data run [x] metrics [x]
