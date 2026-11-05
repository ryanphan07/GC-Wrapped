Last session: 2026-11-05 — Real-ingest hardening and CI verification
Brief completed: yes. PR #21: every check green on its commits (lint-test, integration, airflow, e2e, real-ingest-synthetic with all three runs, on both the push and pull_request runs).
Done:
- ENV_FILE rule (confirmed bug): `config._HostSettings` is the base of all four settings classes and reads `ENV_FILE` (else `.env`) plus the process env; no class names `.env` itself. Makefile `export ENV_FILE`; real_ingest sets `ENV_FILE=<env file>` in every step's env. Tests: named file beats cwd `.env`; a key only in `.env` is not read; `make check-landing ENV_FILE=<temp>` uses the temp allowlist.
- load_runs duration (root cause): `now()` is the transaction START, and the lazy parse runs inside the load transaction, so finished_at = started_at + ~0. Now `clock_timestamp()` for finished_at (succeeded and failed). Integration test: parse slowed 0.2 s gives duration >= 0.2 and last-run prints >= 0.2.
- real-ingest: guard for GCW_DB_NAME (set, not gcw_dev, message names the variable only); fixed step headers `== step: check-landing ==` / `== step: e2e ==` from code; `_port` uses ASCII-digit regex ("5²" tested); module docstring corrected; `gcw_ci` added to the target-database pattern (exactly `gcw_dev|gcw_real|gcw_ci`), leak tests extended.
- GCW_LOG_DIR removed from docker-compose.yml, README and .env.example text now say it is host-side only.
- .claude/settings.json deny rules: `Bash(*make real-ingest*)`, `Bash(*5433*)`, `Bash(*8502*)`; `tests/test_claude_settings.py` fails if any of these or the gcw-private/real entries go missing.
- CI job `real-ingest-synthetic` (sentinel landing, project gc-wrapped-ci-real, ports 5544/8599, db gcw_ci, logs under $RUNNER_TEMP): run 1 ok, run 2 ok and rows inserted 0, run 3 adds an allowlisted folder with a malformed message file and must exit nonzero with "real-ingest: failed at". All three must show no "sentinel" (case-insensitive). Private log uploaded on failure.
- CLAUDE.md rules replaced as briefed; two decisions appended verbatim; METRICS.md rows and the load_runs note added verbatim.
Deviations from the brief (what + why):
- `docker/postgres/init/01-create-databases.sql` now also creates `gcw_ci` (+ vector). Needed: the CI stack is a fresh volume and nothing else creates that database.
- The Makefile already exported ENV_FILE implicitly (bare `export`); I added an explicit `export ENV_FILE` so the intent is visible. The "make check-landing reads .env" suspicion was only half right: make's `-include` already put the named file's keys into the environment, so only keys MISSING from the named file fell back to `.env`.
- CI allowlist includes 99999999999999 (the folder run 3 adds) from run 1, because there is no bypass; runs 1-2 therefore show "allowlisted IDs not found: 1" (check-landing only fails when none are found).
- Deny rule is `Bash(*make real-ingest*)` (leading `*`, like the existing entries) so chained commands match too. `uv run gcw real-ingest` is NOT denied; say if you want that added.
- Side effect of the new `*gcw_real*` / `*5433*` / `*8502*` rules: I could not run some of my own greps in this session; used the Read tool instead.
In progress / broken: nothing known. make test-warehouse and Airflow-image DAG tests not run locally (CI runs them). Run 3 (failure path) took 323.16 s of pipeline time in CI (printed by the runner); I did not investigate why a failing DAG run is that slow (possibly task retries).
Process slip: I committed once with one failing test (test_consent.py allowed only the dev IDs in ci.yml) because my command chain ran past the failure; fixed in the second commit before CI finished.
Next up: Ryan re-runs real-ingest after merge (block below); then Session C (make metrics with the dbt model/test split, shared parse loop).
Open questions for planning:
- Remaining duplicate lines in real-ingest output are expected (README line added): check-landing runs twice by design (host step before the stack starts, and the DAG's first task); last-run lines appear twice (the DAG's final task and `gcw last-run` at the end of `make e2e`). Not deduped, per brief.
Measured this session (exact value + source, this session's terminal):
- make lint test: 220 passed, 20 deselected; ruff and mypy clean. make test-integration: 20 passed.
- make e2e x2, default .env: both exit 0; second run `rows inserted: 0`; last-run elapsed 0.04 s vs ingest step 0.06 s (previously last-run showed ~0.00).
Commits: 7703baa fix: real-ingest hardening, ENV_FILE-only settings, load_runs duration, CI verification; 3446402 test: consent test accepts the real-ingest-synthetic job's allowlist
Phase 1 checklist: parser [x] loader [x] dbt staging [x] marts [x] Airflow DAG [x] dashboard [x] one-command e2e [x] real-data run [x] (two runs, aggregates in METRICS.md)
