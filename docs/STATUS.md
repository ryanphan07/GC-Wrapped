# Status
Last session: 2026-11-14 — P2-4 (burst S3): local llama.cpp server and tagging pipeline

## Phase & state
Phase 2 (Enrichment + evals); burst plan in CLAUDE.md. P2-2b (NER gate) is needed only before a hosted provider (D84).
consent ledger [x] two-level opt-in [x] redaction [x] labeling tool [x] synthetic labeled sets [x] consent replies (Ryan, collecting) real labels [ ] tagging pipeline [x synthetic] eval harness + CI eval [ ] real eval [ ] profiles [ ]

## Done this session
- docs: D89, D90 verbatim; CLAUDE.md Stack, Commands and the PR-body-file rule; README tagging bullet; METRICS.md row (e512679 baseline).
- docker-compose.yml: `llm` service (image pinned by digest, profile `llm`, 127.0.0.1 only, ctx 2048, 1 slot, 8 threads, `--no-slots --no-webui`, health check); airflow gets GCW_LLM_URL from GCW_AIRFLOW_LLM_URL (empty = tag task skipped). .env.example documents the new vars.
- src/gcwrapped/tagging/: model.py (pinned GGUF, checksum download), prompt.py, provider.py (interface, hosted stub), llamacpp.py (URL guard, no proxy, no redirect, one retry), runner.py (resumable, batched, prunes), report.py (shape-checked lines), commands.py. cli.py: tag, tag-bench, llm-model. config.py: TaggingSettings. Makefile: llm-model, tag, tag-bench.
- migrations/004_enrich_message_tags.sql. dbt: stg_enrich__message_tags, fct_message_tags (no reader grant), agg_tags_by_thread, agg_tags_by_sender (reader grant), tests and one unit test.
- airflow DAG: optional `tag` leaf after dbt build (exit 99 skip, `--no-start`). ci.yml: tagging step against tests/tagging_stub.py; no model download.
- Tests: tests/test_tagging.py, test_tagging_integration.py, test_llm_server_local.py (skips unless the llm service is up), tagging_stub.py; warehouse and migrate tests adapted.

## Deviations from brief
- Model file from the unsloth GGUF repo at commit a06e946b (Qwen publishes no pinned equivalent I could verify); same model, sha256 3605803b… checked.
- tests/conftest.py clean_db also drops schema `enrich` (after its gcw_test assertion; the isolation guard is untouched), since 004's FK points at raw.
- `gcw tag --no-start` for the DAG (no docker there). tag-bench refuses any database but gcw_dev. dbt PASS 144 -> 175; migrations 3 -> 4.
- CI fix (PR #39 integration): root cause src/gcwrapped/ledger.py:415 (`run_load` calls `start_database`, i.e. `docker compose up`, which the job's Postgres service container lacks; failed as `consent-load: failed at database (CalledProcessError)`). Fix: `gcw consent-load --no-start` (cli.py, default unchanged; `run_load` untouched) used by the stub-tagging step in ci.yml; 2 tests added.

## Checks
- make lint: pass. make test: 722 passed (70 deselected), baseline 665. make test-integration: 69 passed, 1 skipped (llm leak test; it passed alone against the live server), baseline 50. make test-warehouse: 20 passed. Airflow image DAG tests: 5 passed. (pytest output)
- dbt build on gcw_dev: PASS=175 WARN=0 ERROR=0 (dbt output).
- make tag-bench (gcw_dev, 60 synthetic, qwen3-4b-instruct-2507-q4_k_m, prompt 5b3245c5c605): failures 0; seconds 148.21; 0.40 msg/s; p50 2.408 s; p95 3.545 s; synthetic topic accuracy 0.883; sentiment 0.817 (sanity check, not an eval).
- make tag (gcw_dev): 193 local-eligible, tagged 193, failures 0, 365.73 s, 0.53 msg/s, p50 1.890 s, p95 2.254 s. Immediate rerun: tagged 0, already 193. No out-of-memory (~4 GiB available with db + llm up). After flipping one synthetic sender to no + consent-load: pruned 8, 185 left, tagged 0.

## Not verified
- CI on the PR (stub-tagging step ran locally against gcw_dev: consent-load --no-start, tag 193, rerun 0, no leak; local DB was prepared by ingest/dbt build, not CI's psql reset, psql absent), including the new stub-tagging step and the DAG's skipped tag task in `airflow dags test`.
- Real data: none this session. The real tagging run is part of the real-data batch.

## Open issues and questions
- Stop-word over-redaction (D82); glued names and nicknames: only P2-2b measures them.
- tests/isolation_guard.py check_database_url: libpq multi-host URLs unhandled. real-ingest-synthetic run 3 ~323 s; parked.
- Revoked consent leaves old label rows (D87); eval must drop them. Tags are pruned (D89).
- metrics.py still reports external-eligible only; add local-eligible if wanted.
- A real stack needs its own GCW_LLM_PORT if the dev llm is up at the same time.

## Suggested next session
S4: tagging eval harness on the synthetic set (win rule fixed in advance, compare with a smaller model); real eval waits for the real-data batch.
