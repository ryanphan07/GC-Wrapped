Last session: 2026-10-29 — Repo scaffold
Done:
- pyproject (uv, dev + dbt groups, gcw console script), uv.lock, ruff + mypy strict + pytest config
- .gitignore, .env.example, .claude/settings.json (deny Read/Edit on data/**)
- docker-compose (pgvector/pgvector:pg17) + init SQL creating gcw_dev and gcw_real with vector extension
- src/gcwrapped: config, log, provisional models, Parser protocol, CLI with ingest stub (exit 1)
- Tests: config, models, DB integration (marked integration)
- dbt project + env_var-only profiles.yml; empty model folders
- Makefile, GitHub Actions CI (lint-test + integration), README, METRICS.md, decisions.md
In progress / broken:
- Not verified on this machine: `make up`, `make psql-dev`, `make test-integration`, a passing `dbt debug` connection check, and CI. Docker and make are not installed here.
- dbt debug parses the project and profile fine; the connection check fails only because no Postgres is running.
Next up:
- Ryan: install/start Docker, then run the acceptance checks (make up, psql-dev, test-integration, dbt-debug).
- Choose source platform; finalize ParsedMessage fields.
Open questions for planning:
- METRICS.md needs the full metric list from the project brief (only CLAUDE.md-implied headings exist).
- Ingest CLI requires POSTGRES_USER/PASSWORD/DATABASE_URL set (even the stub), by design of Settings.
Measured this session (exact value + source):
- Unit tests: 7 passed, 1 deselected (integration), `pytest -m "not integration"`, 2026-10-29
- ruff check, ruff format --check, mypy --strict: no issues (10 source files)
