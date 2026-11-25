# Status
Last session: 2026-11-24 — S5: local embeddings, vector and hybrid retrieval, retrieval eval and compare (synthetic)

## Phase & state
Phase 3 (RAG + privacy + deploy): embeddings and retrieval are built and tested on synthetic data; nothing real is embedded yet. Phase 2 is complete (D99).
embeddings [x synthetic] retrieval eval [x synthetic] real questions [ ] real embed [ ] real retrieval eval + compare [ ] answers with citations (S6) [ ] web app (S7) [ ] deploy (S8) [ ] usage window [ ]

## Done this session
- Task 0: the old "ineligible messages" counted only distinct ineligible message IDs still referenced by a derived record; APPLY deletes only from enrich.message_tags (now also message_embeddings) and rewrites tag_labels.csv, questions.csv, predictions/*.csv (now also retrieval/*.json); no message rows (both modes first apply pending migrations). Line renamed "ineligible messages with derived records".
- docs: D102-D108; CLAUDE.md; METRICS.md three Tagging eval rows (83eccbb).
- docker-compose.yml `embed` service; migrations/006_enrich_message_embeddings.sql; tagging/model.py EMBED_REGISTRY (`make llm-model` fetches it); config.py, .env.example GCW_EMBED_URL/PORT. src/gcwrapped/retrieval/ (window, client, embed, search, evaluation, report, commands); cli.py, Makefile `embed`, `retrieval-eval`, `retrieval-compare`.
- consent_prune.py (D105: embedding rows, retrieval predictions); purge.py (D106).
- CI `retrieval-gate` job, scripts/retrieval_gate.py, floor 0.85 (D108). Tests: test_retrieval{,_integration,_gate}.py, test_embed_server_local.py, embedding_stub.py.

## Deviations from brief
- purge-unlisted changed (not in the brief; asked, Ryan chose): deletes embedding rows and prints "embeddings: N" (D106).
- Postgres image already pgvector/pgvector:pg17, unchanged (still not pinned by digest). Session = dbt fct_messages.session_id (60-minute gap), not the 30-minute rule.
- Integration tests write to gcw_test (D68), not gcw_dev; task 7 was also run by hand on gcw_dev.
- "Already current" also needs the same window, not only the same version (D107). retrieval-compare accepts only A=vector B=hybrid and reads eligibility from the database (D87).
- Retrieval prediction rows count under the existing "prediction rows to prune" line. METRICS: the re-run row says "identical" without repeating the accuracies; macro-F1 not added (the cd95e19 row has it).

## Checks
- make lint: pass. make test: 882 passed, 175 deselected (811 before). make test-integration: 174 passed, 1 skipped (146 before; llm down; embed up). Prompt version 11ac16d16643.
- dbt build gcw_dev: PASS=190 WARN=0 ERROR=0 TOTAL=190. CI (PR #48, run 45518212799, commit before this line): all 7 jobs pass; retrieval-gate (2m0s) 156 eligible, 156 embedded then 0, 3.49 messages/s, recall@5 0.968 (floor 0.85), MRR@10 0.588, p95 0.248 s, 0 dropped.
- Synthetic, gcw_dev, laptop CPU, version e3ac6a56e412: `make embed SYNTHETIC=1` 223 eligible, 223 embedded, 0 failures, 36.15 s, 6.17 messages/s; rerun 0 embedded, 223 already current.
- Synthetic retrieval-eval, 31 questions, 0 dropped. vector: recall@1 0.323 [0.161, 0.484], @5 0.968 [0.903, 1.000], @10 1.000 [1.000, 1.000], MRR@10 0.591 [0.489, 0.700], p50 0.131 s, p95 0.148 s. hybrid: recall@1 0.323 [0.161, 0.484], @5 0.935 [0.839, 1.000], @10 1.000 [1.000, 1.000], MRR@10 0.551 [0.440, 0.672], p50 0.133 s, p95 0.154 s.
- Synthetic compare: scored 31, A 0.968, B 0.935, difference -0.032 [-0.161, 0.097], p95 B 0.154 s, latency met yes, winner vector.
- Task 7 on gcw_dev (one sender to no): embedding rows to prune 98 (equals an independent SQL count; 54 are their own), tags 54, prediction rows 62; APPLY same; rerun all 0; make embed 169 eligible, 44 embedded, no window holds them. Ledger restored (98 re-embedded); tags back to 265.

## Not verified
- Real-data steps (Ryan): llm-model and embed after merge; retrieval-eval and compare once the 100 questions exist. Real throughput and latency are unmeasured.

## Open issues and questions
- Carried: Read(./data/**) does not cover Bash `cat data/...`; P2-5c items (placeholder NAME wildcard, pre-D95 METRICS rows not comparable); `gcw tag --no-start` does not verify the llm model; stop-word over-redaction (D82); isolation_guard multi-host URLs; real-ingest-synthetic ~323 s; label files of a purged thread stay (frozen samples and skips hold ids); metrics.py reports external-eligible only; Qwen3.5-4B ~1.6x slower than the D90 model; test_llm_server_local fails if llm serves another model; profiles not built.
- Carried PR 47 chores: ci.yml allowlist, make down profile (also leaves `embed` running), model re-hash check, METRICS placeholder row.
- retrieval-eval does not refuse a partly embedded corpus and compare does not check the corpus: finish make embed, then run both methods with no embed or consent change between them.
- One revocation plus consent-prune removes every stored retrieval row that returned that sender (62 of 62 on synthetic): rerun both evals. No config holds the default retrieval method yet (S6; vector unless D104 says otherwise).

## Suggested next session
S6 answers with citations.
