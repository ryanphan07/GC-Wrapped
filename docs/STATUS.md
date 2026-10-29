Last session: 2026-10-29 — Repo scaffold
Brief completed: partial. Not verifiable on this machine (no Docker, no make): `make up` healthy, `make psql-dev` listing gcw_dev/gcw_real, `make test-integration`, `make dbt-debug` connection check, CI run.
Done:
- uv project (dev + dbt groups, `gcw` script), ruff + mypy strict + pytest `integration` marker
- docker-compose (pgvector/pgvector:pg17) + init SQL for gcw_dev / gcw_real with vector extension
- src/gcwrapped: config, log (key=value), provisional models, Parser protocol, CLI `ingest` stub (exit 1)
- Tests: config, models, DB integration; dbt project + env_var-only profiles; Makefile; CI; README; METRICS.md; decisions.md
- Source platform recorded as Instagram DMs (JSON); code kept platform-agnostic (no parser, generator, raw SQL, loader)
Deviations from the brief (what + why):
- profiles.yml dbname uses env_var('DBT_DEV_DBNAME', 'gcw_dev') rather than hardcoded; default is gcw_dev.
- METRICS.md has only headings implied by CLAUDE.md; the brief's metric list was not available.
- Settings requires POSTGRES_USER/PASSWORD/DATABASE_URL, so even `gcw ingest` fails without env.
In progress / broken:
- Docker-dependent checks above are unrun. dbt parses profile/project fine; connection fails only because no Postgres is running.
- Git warns LF->CRLF (autocrlf); consider .gitattributes `* text=auto eol=lf`.
Next up:
- Ryan: start Docker, run the unverified acceptance checks.
- Parser session (Ryan writes it): fix mojibake in Meta JSON, derive dedup key (no per-message IDs). Finalize ParsedMessage fields; build synthetic export generator.
Open questions for planning:
- Send the project brief's metric list for METRICS.md.
- Add .gitattributes?
Measured this session (exact value + source):
- `pytest -m "not integration"`: 7 passed, 1 deselected, 2026-10-29
- ruff check / ruff format --check / mypy --strict: no issues (10 source files)
Commits: 9184ad3 — Add tooling and package skeleton; aba5426 — Add Docker Postgres, dbt project, Makefile and CI; d195fc9 — Add README, METRICS, STATUS and decisions docs
Phase 1 checklist: parser [ ] loader [ ] dbt staging [ ] marts [ ] Airflow DAG [ ] dashboard [ ] one-command e2e [ ]
