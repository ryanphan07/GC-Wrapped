-include .env
export

.PHONY: up down logs psql-dev test test-integration lint fmt synth dbt-debug dbt-deps dbt-build ingest airflow-up test-warehouse dashboard-up

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
	uv run pytest -m "not integration and not warehouse"

test-integration:
	uv run pytest -m integration

# Needs the pipeline to have run (gcw migrate, ingest, dbt build) so the marts exist.
test-warehouse:
	uv run --group dashboard pytest -m warehouse

# Streamlit on http://localhost:8501, connecting as gcw_reader.
dashboard-up:
	docker compose up -d --build dashboard

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
