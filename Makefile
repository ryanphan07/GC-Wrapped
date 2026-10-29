-include .env
export

.PHONY: up down logs psql-dev test test-integration lint fmt dbt-debug ingest

up:
	mkdir -p data/landing data/archive
	docker compose up -d --wait

down:
	docker compose down

logs:
	docker compose logs -f db

psql-dev:
	docker compose exec db psql -U $(POSTGRES_USER) -d gcw_dev

test:
	uv run pytest -m "not integration"

test-integration:
	uv run pytest -m integration

lint:
	uv run ruff check
	uv run ruff format --check
	uv run mypy

fmt:
	uv run ruff check --fix
	uv run ruff format

dbt-debug:
	uv run --group dbt dbt debug --project-dir dbt --profiles-dir dbt

ingest:
	@echo "Real-data ingestion is run manually by Ryan in his own terminal. Not run by Claude Code."
	@exit 1
