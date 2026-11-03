Last session: 2026-11-03 — Scrub loader errors, dbt sources + staging, dbt build in CI
Brief completed: yes (CI green on branch; merged to main, see Commits)
Done:
- Loader: any exception during COPY or merge becomes LoadError(class, row index, field name). The per-row try sits inside the COPY block so psycopg's cancel message to the server is value-free. raw.load_runs.error stores only that summary (e.g. "RuntimeError at row 3").
- `gcw ingest` on paths with no threads now fails before database access and before any run row; message and exit code 1 unchanged.
- dbt: dbt-postgres >=1.10 (resolved 1.11.0 locally), dbt_utils 1.4.1 via packages.yml (package-lock.yml committed).
- Sources raw.messages and raw.load_runs with tests; stg_instagram__messages and stg_instagram__reactions (views) fully documented and tested. Var gcw_timezone (default America/Los_Angeles) drives sent_at_local.
- CI integration job: reset gcw_dev, gcw migrate, make synth, gcw ingest export_a, make dbt-deps, make dbt-build. Added Makefile targets dbt-deps, dbt-build.
- Tests: 4 new integration (iterator failure, NUL-byte bad row, CLI failure output, no-threads leaves no run row), 1 new CLI unit (no-threads before DB), 1 LoadError unit. Sentinel is SENTINEL_SECRET.
Deviations from the brief (what + why):
- dbt floor is >=1.10, not >=1.8: needed for the `arguments:` test syntax (older syntax is deprecated). Satisfies "≥ 1.8".
- message_type priority is media > share > text > empty (see decisions.md); the brief said "derived from content" only.
- CI resets gcw_dev before the e2e steps, because the integration tests leave it in an arbitrary state.
- LoadError wraps ParseError too, so `gcw ingest` prints "ParseError at row N" instead of the parser's field message. Run `gcw parse` for the detailed message.
- Added dbt/.user.yml to .gitignore.
In progress / broken:
- Field name is only filled when a server error's context names a known column; client-side failures report row only (field unknown). Not exercised in CI.
- Server-side errors that Postgres logs itself (e.g. a NUL byte in text) can still appear in the server log; only client-originated exceptions are scrubbed there. Not verified against a server log.
- Format still PROVISIONAL; real-schema reconciliation not done.
Next up:
- Intermediate and marts (threads, reply times, member activity, activity over time) -- includes write-it-yourself SQL for threads/reply times.
- Ryan: inspector on real export, reconcile format module.
Open questions for planning:
- Is the reactions uniqueness test (message_key, actor, emoji) too strict for real data? It will fail if an export lists the same reaction twice.
Measured this session (exact value + source):
- CI run 41589427550 (2026-11-03 UTC): unit 67 passed, 16 deselected; integration 16 passed, 67 deselected; dbt build PASS=34 WARN=0 ERROR=0 TOTAL=34; synthetic export_a ingest rows parsed 105, inserted 105.
- Local: ruff check, ruff format --check, mypy --strict clean; pytest -m "not integration" 67 passed; dbt parse clean (2026-11-03).
Commits: filled in below after merge
Phase 1 checklist: parser [x] (provisional format) loader [x] dbt staging [x] marts [ ] Airflow DAG [ ] dashboard [ ] one-command e2e [ ]
