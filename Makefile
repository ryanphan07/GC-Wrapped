# `make <target> ENV_FILE=...` selects the env file for everything: make's own environment AND compose.
# Compose gives shell variables precedence over --env-file, so including a different file here
# (not always .env) is what stops dev values leaking into a run that names another env file.
ENV_FILE ?= .env
-include $(ENV_FILE)
export
# Explicit on purpose: host-side `gcw` reads its settings from this file only (config.env_file_path).
export ENV_FILE

# That module imports streamlit/pandas (dashboard group), which lint-test and dev installs lack.
IGNORE_WAREHOUSE = --ignore=tests/test_dashboard_warehouse.py

# Every compose call takes its variables, including COMPOSE_PROJECT_NAME (which overrides the pinned
# `name:` in docker-compose.yml), from ENV_FILE. Real runs keep theirs outside the repo.
COMPOSE = docker compose --env-file "$(ENV_FILE)"
DASHBOARD_URL = http://localhost:$(or $(DASHBOARD_PORT),8501)

.PHONY: up down logs psql-dev test test-integration lint fmt synth dbt-debug dbt-deps dbt-build ingest airflow-up test-warehouse dashboard-up e2e inspect synth-sentinel parse-check check-landing real-ingest metrics consent-template consent-load redaction-audit

up:
	mkdir -p data/landing data/archive
	$(COMPOSE) up -d --wait

down:
	$(COMPOSE) down

# Airflow UI on http://localhost:8080. Mounts GCW_LANDING_HOST_DIR (default data/landing) read-only.
airflow-up:
	mkdir -p data/landing
	$(COMPOSE) up -d --build airflow

logs:
	$(COMPOSE) logs -f db

psql-dev:
	$(COMPOSE) exec db psql -U $(POSTGRES_USER) -d gcw_dev

test:
	uv run pytest -m "not integration and not warehouse" $(IGNORE_WAREHOUSE)

test-integration:
	uv run pytest -m integration $(IGNORE_WAREHOUSE)

# Needs the pipeline to have run (gcw migrate, ingest, dbt build) so the marts exist.
test-warehouse:
	uv run --group dashboard pytest -m warehouse

# Streamlit on http://localhost:8501, connecting as gcw_reader.
dashboard-up:
	$(COMPOSE) up -d --build dashboard

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

# Content-free format report (counts, histograms, masked URI shapes) for an export ROOT.
# Real exports: Ryan runs this himself, e.g. make inspect EXPORT_DIR="$EXPORT" > /tmp/instagram-schema.md
inspect:
	@test -n "$(EXPORT_DIR)" || { echo "error: set EXPORT_DIR to the export root (make inspect EXPORT_DIR=<path>)"; exit 1; }
	@uv run python scripts/inspect_export.py "$(EXPORT_DIR)"

# Sentinel export for the inspector's leak test: tests/fixtures/sentinel (gitignored).
synth-sentinel:
	uv run python tests/synthetic/sentinel.py --out tests/fixtures/sentinel

# Parse a whole export with no database; prints counts and exception class names only.
# Real exports: Ryan runs this himself, e.g. make parse-check EXPORT_DIR="$EXPORT"
parse-check:
	@test -n "$(EXPORT_DIR)" || { echo "error: set EXPORT_DIR to the export root (make parse-check EXPORT_DIR=<path>)"; exit 1; }
	@uv run gcw parse-check "$(EXPORT_DIR)"

# Consent check of a landing dir, counts only: thread folders by shape and how many allowlisted
# IDs were found. Reads GCW_THREAD_ALLOWLIST from ENV_FILE (default .env). Never prints names.
check-landing:
	@uv run gcw check-landing "$(or $(GCW_LANDING_HOST_DIR),./data/landing)"

# Guarded real-data run: guards -> check-landing -> e2e against the env file's own stack. Full
# output goes to GCW_LOG_DIR (private); the terminal gets allowlisted summary lines only. Ryan runs
# this himself: make real-ingest ENV_FILE=<env file outside the repo>. The $(origin) test passes an
# empty value when ENV_FILE was left at its .env default, which the guards refuse.
real-ingest:
	@uv run gcw real-ingest --env-file "$(if $(filter file,$(origin ENV_FILE)),,$(ENV_FILE))"

# Aggregate counts report (fixed labels, numbers only): warehouse named by ENV_FILE, dbt manifest,
# pytest collection. Starts only the db service and leaves it running (`make down` stops it).
# Writes nothing tracked; dbt writes its gitignored dbt/target. Real-data runs are Ryan's.
metrics:
	@uv run --group dbt --group dashboard gcw metrics

# Per-sender consent ledger (D69). GCW_CONSENT_LEDGER (CSV, outside the repo for real data) is
# required. consent-template creates the ledger or adds the warehouse's missing senders with blank
# consent; consent-load validates all of it, then loads raw.sender_consent in one transaction. Both
# start only the db service and print fixed labels and numbers, never names. Real-data runs are Ryan's.
consent-template:
	@uv run gcw consent-template

consent-load:
	@uv run gcw consent-load

# Redacts every ingested message in memory (gcwrapped.redaction, D70) and prints aggregate counts
# only: scanned, changed, placeholders by type, member-name tokens remaining. Writes nothing.
# Exit 1 if any member-name token survives. Real-data runs are Ryan's:
#   make redaction-audit ENV_FILE=<env file outside the repo>
redaction-audit:
	@uv run gcw redaction-audit
