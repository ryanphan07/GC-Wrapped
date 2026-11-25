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

.PHONY: up down logs psql-dev test test-integration lint fmt synth dbt-debug dbt-deps dbt-build ingest airflow-up test-warehouse dashboard-up e2e inspect synth-sentinel parse-check check-landing real-ingest metrics consent-template consent-load purge-unlisted consent-prune consent-coverage redaction-audit text-audit label-tags label-questions label-summary llm-model tag tag-eval tag-compare tag-promote embed retrieval-eval retrieval-compare retrieval-promote ask answer-eval answer-compare answer-promote eval-all synth-landing sync

# Install every dependency group `make e2e`, the dbt targets and the dashboard need (`uv sync` alone drops them).
sync:
	uv sync --group dbt --group dashboard

up:
	mkdir -p data/landing data/archive
	$(COMPOSE) up -d --wait

# Also stops the llm and embed services: compose leaves profile services alone unless named.
down:
	$(COMPOSE) --profile llm --profile embed down

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
#
# Local dev only (ENV_FILE left at the default .env, which is the synthetic stack): data/landing is
# replaced from the generator first, so an e2e can never ingest a landing that predates the
# generator (a stale one once hid a new thread). Any other env file, a real run included, skips
# this: data/landing is never touched and its landing is never regenerated or deleted.
E2E_REGENERATE = $(if $(filter .env,$(ENV_FILE)),synth-landing)
e2e: $(E2E_REGENERATE)
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

# Synthetic exports, default seed: tests/fixtures/synthetic (tests) and data/landing (the dev stack's
# landing, synthetic only). data/landing is replaced wholesale on every run: emptied, then written
# from tests/synthetic/instagram.py. Nothing in it is ever read, so it cannot go stale or mix
# generations; there is deliberately no check that compares it with the generator.
synth: synth-landing
	uv run python tests/synthetic/instagram.py

synth-landing:
	uv run python tests/synthetic/instagram.py --out data/landing

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

# Purge threads removed from GCW_THREAD_ALLOWLIST (D91): deletes every warehouse row of a thread not
# listed (messages, reactions, tags, that thread's consent rows). Dry run unless CONFIRM=1; prints
# counts only. Starts only the db service. Real-data runs are Ryan's.
purge-unlisted:
	@uv run gcw purge-unlisted $(if $(filter 1,$(CONFIRM)),--confirm,)

# Consent coverage (D88): fixed aggregate lines, safe to paste. Also writes consent_priorities.csv
# (senders ranked by text messages, with names) to GCW_LABEL_DIR, which must exist outside the
# repo; that file is never printed. Refuses a database outside ALLOWED_DATABASES.
# Real-data runs are Ryan's: make consent-coverage ENV_FILE=<env file outside the repo>
consent-coverage:
	@uv run gcw consent-coverage

# Redacts every ingested message in memory (gcwrapped.redaction, D70) and prints aggregate counts
# only: scanned, changed, placeholders by type, member-name tokens remaining. Writes nothing.
# Exit 1 if any member-name token survives. Real-data runs are Ryan's:
#   make redaction-audit ENV_FILE=<env file outside the repo>
redaction-audit:
	@uv run gcw redaction-audit

# User text vs Instagram placeholders (D95): fixed aggregate lines (counts per placeholder pattern id
# and user text by shape), never text. Needs the dbt objects built (make dbt-build / real-ingest).
# Refuses a database outside ALLOWED_DATABASES. Real-data runs are Ryan's:
#   make text-audit ENV_FILE=<env file outside the repo>
text-audit:
	@uv run gcw text-audit

# Remove everything derived from messages that are no longer eligible (D101, D105): tag rows, embedding
# rows, hand-label rows, question gold references and stored predictions in GCW_LABEL_DIR. Run consent-load first so the
# revocation is in the warehouse. Dry run unless APPLY=1; counts only. Real-data runs are Ryan's:
#   make consent-prune ENV_FILE=<env file outside the repo> [APPLY=1]
consent-prune:
	@uv run gcw consent-prune $(if $(filter 1,$(APPLY)),--apply)

# Local hand labeling (D85, D86). Interactive: shows real messages in THIS terminal only and writes
# labels to GCW_LABEL_DIR (must exist, outside the repo). Both refuse a database outside
# ALLOWED_DATABASES. Keys: topic 1-9 and 0, sentiment g/n/b, u undo, q quit; resume where you left
# off. Real-data runs are Ryan's: make label-tags ENV_FILE=<env file outside the repo>
label-tags:
	@uv run gcw label-tags

label-questions:
	@uv run gcw label-questions

# Fixed aggregate lines only (counts per topic and sentiment, questions answered); safe to paste.
label-summary:
	@uv run gcw label-summary

# Local tagging (D89, D90) and the model experiment (D92, D93). llm-model [MODEL=<id>] downloads a
# registered GGUF to GCW_MODEL_DIR (default ~/.cache/gcw/models) and checks its sha256; rerunning it
# changes nothing. tag starts only the db and llm services, tags every local-eligible text message
# not yet tagged for the model and prompt (LIMIT=n caps the run), prunes tags of messages no longer
# local_ok, and prints fixed lines only; it uses the promoted model unless MODEL= is given.
# tag-eval runs one model over the hand labels (SYNTHETIC=1: the committed synthetic set, gcw_dev
# only, temporary directory), stores predictions and prints scores with 95% intervals. tag-compare
# applies the D93 win rule to two stored runs and records the verdict. tag-promote makes a model the
# default, only if the latest verdict names it the winner. Real-data runs are Ryan's:
# make tag ENV_FILE=<env file outside the repo>
llm-model:
	@uv run gcw llm-model $(if $(MODEL),--model $(MODEL))

tag:
	@uv run gcw tag $(if $(LIMIT),--limit $(LIMIT)) $(if $(MODEL),--model $(MODEL))

tag-eval:
	@test -n "$(MODEL)" || { echo "error: set MODEL=<id> (make tag-eval MODEL=<id> [SYNTHETIC=1])"; exit 1; }
	@uv run gcw tag-eval --model $(MODEL) $(if $(filter 1,$(SYNTHETIC)),--synthetic)

tag-compare:
	@test -n "$(A)" -a -n "$(B)" || { echo "error: set A=<id> and B=<id> (make tag-compare A=<id> B=<id> [SYNTHETIC=1])"; exit 1; }
	@uv run gcw tag-compare --a $(A) --b $(B) $(if $(filter 1,$(SYNTHETIC)),--synthetic)

tag-promote:
	@test -n "$(MODEL)" || { echo "error: set MODEL=<id> (make tag-promote MODEL=<id>)"; exit 1; }
	@uv run gcw tag-promote --model $(MODEL)

# Local embeddings and retrieval (D102-D104). The model is fetched once with
# `make llm-model MODEL=qwen3-embedding-0.6b-q8_0`. embed starts only the db and embed services,
# embeds every local-eligible text message whose window has no current vector (resumable, rerunning
# embeds nothing new) and prints fixed lines only. retrieval-eval searches each question with one
# method over those vectors, stores the results (IDs only) and prints recall@k and MRR@10;
# retrieval-compare applies the D104 win rule to the two stored runs. SYNTHETIC=1 means gcw_dev
# only (and for the eval: the committed synthetic questions and a temporary directory). retrieval-eval
# is refused unless every eligible message has a current vector (D109); retrieval-compare records
# its verdict, and retrieval-promote METHOD=<m> makes <m> the method answers use, only if the latest
# verdict names it the winner (D110). The embed service stays running (make down stops it).
# Real-data runs are Ryan's: make embed ENV_FILE=<env file outside the repo>
embed:
	@uv run gcw embed $(if $(filter 1,$(SYNTHETIC)),--synthetic)

retrieval-eval:
	@test -n "$(METHOD)" || { echo "error: set METHOD=vector|hybrid (make retrieval-eval METHOD=vector [SYNTHETIC=1])"; exit 1; }
	@uv run gcw retrieval-eval --method $(METHOD) $(if $(filter 1,$(SYNTHETIC)),--synthetic)

retrieval-compare:
	@test -n "$(A)" -a -n "$(B)" || { echo "error: set A=vector and B=hybrid (make retrieval-compare A=vector B=hybrid [SYNTHETIC=1])"; exit 1; }
	@uv run gcw retrieval-compare --a $(A) --b $(B) $(if $(filter 1,$(SYNTHETIC)),--synthetic)

retrieval-promote:
	@test -n "$(METHOD)" || { echo "error: set METHOD=vector|hybrid (make retrieval-promote METHOD=vector)"; exit 1; }
	@uv run gcw retrieval-promote --method $(METHOD)

# Answers with citations from the local model (D111-D113). ask and answer-eval start only the db,
# llm and embed services. ask reads questions at a prompt (a blank line ends it), prints each answer and its cited
# messages to the terminal only, writes nothing, and ends with fixed summary lines. answer-eval
# answers each question once, stores IDs, hashes and numbers (never answer text) and prints fixed
# lines. SYNTHETIC=1 means gcw_dev only (and for the eval: the committed synthetic questions and a
# temporary directory). answer-eval takes VARIANT=a|b, the prompt variant (D121; default: the
# promoted one). answer-compare applies the D121 win rule to the stored A and B runs and records the
# verdict; answer-promote VARIANT=<v> makes <v> the variant answers use, only if the latest verdict
# names it the winner. Both start only the db service. Real-data runs are Ryan's:
# make ask ENV_FILE=<env file outside the repo>
ask:
	@uv run gcw ask $(if $(filter 1,$(SYNTHETIC)),--synthetic)

answer-eval:
	@uv run gcw answer-eval $(if $(filter 1,$(SYNTHETIC)),--synthetic) $(if $(VARIANT),--variant $(VARIANT))

answer-compare:
	@uv run gcw answer-compare $(if $(filter 1,$(SYNTHETIC)),--synthetic)

answer-promote:
	@test -n "$(VARIANT)" || { echo "error: set VARIANT=a|b (make answer-promote VARIANT=a)"; exit 1; }
	@uv run gcw answer-promote --variant $(VARIANT)

# Every eval in order, stopping at the first failure (D122): embed, both retrieval evals, compare,
# promote, answer-eval with prompt a and b, answer-compare, answer-promote. SYNTHETIC=1 skips both
# promotes. Unattended and long on real data. Real-data runs are Ryan's:
# make eval-all ENV_FILE=<env file outside the repo>
eval-all:
	@uv run gcw eval-all $(if $(filter 1,$(SYNTHETIC)),--synthetic)
