-include .env
export

# That module imports streamlit/pandas (dashboard group), which lint-test and dev installs lack.
IGNORE_WAREHOUSE = --ignore=tests/test_dashboard_warehouse.py

# `make e2e ENV_FILE=...` points compose at another env file (real runs keep theirs outside the repo).
ENV_FILE ?= .env
COMPOSE = docker compose --env-file $(ENV_FILE)
DASHBOARD_URL = http://localhost:8501

.PHONY: up down logs psql-dev test test-integration lint fmt synth dbt-debug dbt-deps dbt-build ingest airflow-up test-warehouse dashboard-up e2e

up:
	mkdir -p data/landing data/archive
	docker compose up -d --wait

down:
	docker compose down

# Airflow UI on http://localhost:8080. Mounts GCW_LANDING_HOST_DIR (default data/landing) read-only.
airflow-up:
	mkdir -p data/landing
	docker compose up -d --build airflow

logs:
	docker compose logs -f db

psql-dev:
	docker compose exec db psql -U $(POSTGRES_USER) -d gcw_dev

test:
	uv run pytest -m "not integration and not warehouse" $(IGNORE_WAREHOUSE)

test-integration:
	uv run pytest -m integration $(IGNORE_WAREHOUSE)

# Needs the pipeline to have run (gcw migrate, ingest, dbt build) so the marts exist.
test-warehouse:
	uv run --group dashboard pytest -m warehouse

# Streamlit on http://localhost:8501, connecting as gcw_reader.
dashboard-up:
	docker compose up -d --build dashboard

# One command, fresh export to dashboard: db + dashboard up and healthy, then the Airflow DAG once
# (check landing -> migrate -> ingest -> dbt build -> summary), the same path CI verifies.
# Safe to re-run: ingest is idempotent. Any failure exits non-zero. Prints counts, never content.
e2e:
	@test -f "$(ENV_FILE)" || { echo "error: env file not found (set ENV_FILE)"; exit 1; }
	$(COMPOSE) up -d --build --wait db dashboard
	$(COMPOSE) build airflow
	$(COMPOSE) run --rm -T --no-deps airflow bash -c 		'set -euo pipefail; airflow db migrate >/dev/null; airflow dags test gcw_pipeline; /opt/gcw-venv/bin/gcw last-run'
	@echo "dashboard: $(DASHBOARD_URL)"

lint:
	uv run ruff check
	uv run ruff format --check
	uv run mypy

fmt:
	uv run ruff check --fix
	uv run ruff format

dbt-debug:
	uv run --group dbt dbt debug --project-dir dbt --profiles-dir dbt

dbt-deps:
	uv run --group dbt dbt deps --project-dir dbt --profiles-dir dbt

dbt-build:
	uv run --group dbt dbt build --project-dir dbt --profiles-dir dbt

ingest:
	@echo "Real-data ingestion is run manually by Ryan in his own terminal. Not run by Claude Code."
	@exit 1

synth:
	uv run python tests/synthetic/instagram.py
