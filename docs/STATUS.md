# Status
Last session: 2026-11-15 — P2-5 (S4): tagging eval harness, model comparison under a fixed win rule, promoted model (D92, D93)

## Phase & state
Phase 2 (Enrichment + evals); burst plan in CLAUDE.md. P2-2b (NER gate) is needed only before a hosted provider (D84).
consent ledger [x] two-level opt-in [x] redaction [x] labeling tool [x] synthetic labeled sets [x] consent replies (Ryan, collecting) real labels [ ] tagging pipeline [x synthetic] purge unlisted threads [x] eval harness [x synthetic] CI eval (S4b) [ ] real eval + 4B vs 1.5B comparison [ ] profiles [ ]

## Done this session
- docs/decisions.md: D92, D93 verbatim. CLAUDE.md: commands for tag-eval/compare/promote, `llm-model MODEL=`, `tag MODEL=`; tag-bench removed; D93 protocol line. METRICS.md: the three real-data rows, SHA 5cafcc0 (#40).
- src/gcwrapped/tagging/model.py: registry (D90 model + Qwen2.5-1.5B-Instruct Q4_K_M, pinned commit and sha256). scoring.py (new, pure): accuracy, per-class F1, macro-F1, percentile bootstrap (2000, seed 20261114), `decide` = D93. evaluation.py (new): predictions CSV and verdict JSON (0600, refused inside the repo), compare, promote. commands.py, report.py, cli.py, Makefile: the new targets; tag-bench removed. `tag` resolves the promoted model.
- docker-compose.yml: llm file and --alias from GCW_LLM_MODEL_FILE / GCW_LLM_MODEL_ALIAS. migrations/005 `enrich.tag_default`. dbt: stg_enrich__tag_default, fct_message_tags filtered to the promoted model (var gcw_default_tag_model fallback), 2 new unit tests.
- src/gcwrapped/purge.py: runs migrate before the coverage check (addendum). Tests: test_tag_eval.py (66), test_tag_eval_integration.py (19), purge test for a database missing 004.

## Deviations from brief
- Addendum purge fix: purge() migrates first (dry run too, as consent-load does); the coverage check is unchanged.
- Synthetic runs share one temporary directory `<tmp>/gcw-synthetic-eval-<uid>` (0700, refused inside the repo) so tag-compare can find tag-eval's predictions across commands.
- Predictions files are named `<model_id>__<prompt_version>.csv`, so a compare always pairs runs of the current prompt.
- tag-eval throughput is attempted messages over summed per-message seconds, not wall clock.
- make tag with the llm service: `start` was split into start_db and start_llm(model_id), since the promoted model is read from the database before the llm starts.

## Checks
- make lint: pass. make test: 789 passed (98 deselected), baseline 723. make test-integration: 97 passed, 1 skipped (llm server not up), baseline 79. make test-warehouse: 20 passed. (pytest)
- dbt build on gcw_dev: PASS=179 WARN=0 ERROR=0 TOTAL=179, 9 unit tests (baseline 175). (dbt output)
- SYNTHETIC, not a real result, nothing promoted (enrich.tag_default has 0 rows; gcw_dev, 60 labels, 0 dropped, 0 failures, prompt 5b3245c5c605). `make tag-eval SYNTHETIC=1`:
  A qwen3-4b-instruct-2507-q4_k_m: topic acc 0.883 [0.800, 0.950], F1 0.885; sentiment acc 0.817 [0.717, 0.900], F1 0.814; 0.47 msg/s.
  B qwen2.5-1.5b-instruct-q4_k_m: topic acc 0.767 [0.650, 0.867], F1 0.766; sentiment acc 0.700 [0.583, 0.817], F1 0.707; 1.05 msg/s.
- SYNTHETIC `make tag-compare`: 60 messages; topic B minus A -0.117 [-0.233, 0.000]; sentiment -0.117 [-0.250, 0.017]; speedup 2.23; checks speedup pass, topic fail, sentiment fail; winner A (D93 keeps the 4B model). (command output)

## Not verified
- CI on this PR: watched at commit time (see the PR checks).
- Real data: nothing to run now. The real comparison runs in the real-data batch once labels exist: tag-eval for each arm, tag-compare, record in METRICS.md, then tag-promote only if B wins.

## Open issues and questions
- The `gcw tag --no-start` path (Airflow DAG) records the promoted model's id, but nothing checks that the running llm server serves that model; `make tag` always restarts it with the right one. A `/v1/models` check would close it.
- Stop-word over-redaction (D82); glued names and nicknames: only P2-2b measures them. tests/isolation_guard.py check_database_url: libpq multi-host URLs unhandled. real-ingest-synthetic run ~323 s; parked.
- Revoked consent leaves old label rows (D87); the eval drops them. Tags are pruned (D89). Label files of a purged thread stay (Ryan's to delete).
- metrics.py still reports external-eligible only. A real stack needs its own GCW_LLM_PORT if the dev llm is up at the same time.

## Suggested next session
S4b: a CI eval with a tiny real model (Ryan's choice, 2026-11-14); then S5 embeddings + retrieval.
