# Status
Last session: 2026-11-24 — S6: answers with citations (local), retrieval corpus guard, retrieval default and promote (synthetic)

## Phase & state
Phase 3 (RAG + privacy + deploy): answers, `make ask` and the automatic answer eval are built and run on synthetic data. The real embedding job has run (METRICS.md). Phase 2 is complete (D99).
embeddings [x] retrieval eval [x synthetic] answers (S6) [x synthetic] real questions [ ] real retrieval eval + compare [ ] real answer eval [ ] hand grading (S6b) [ ] web app (S7) [ ] deploy (S8) [ ] usage window [ ]

## Done this session
- docs: D109-D114; METRICS embedding row (ac9f07e); CLAUDE.md (phase, S6b, ask, answer-eval, retrieval-promote, corpus guard, make down).
- D109/D110: retrieval/ corpus guard, corpus_hash, CorpusMismatch, verdict file, `retrieval-promote`; migrations/007. Resolved: partly embedded corpus accepted; no stored default method.
- D111-D113: src/gcwrapped/answers/ (excerpts, prompt, pipeline, evaluation, report, commands); consent_prune.py covers answers/*.json; cli.py, Makefile `ask`, `answer-eval`.
- Chores: `make down` stops llm and embed (resolved); local_opener, write_private public; tagging/llamacpp.py split into LlamaCppChat + LlamaCppProvider (same tag request); llm ctx-size.
- Tests: test_answers{,_integration}.py; test_retrieval{,_integration}.py, test_llm_server_local.py (answer leak + grammar), tagging_stub.py.

## Deviations from brief
- llm `--ctx-size` 2048 to 32768 (D114; test_tagging.py expects it). Nothing else in the service changed.
- test_llm_server_local's tag test failed on the real model: it sent no thinking-off setting (D98), so the answer was cut off. It now sends it.
- "messages not embedded: N" also prints before the failure line of a refused run. `ask` also ends on end of input or Ctrl-C and adds "ask: ok" (D114).
- retrieval-promote has no SYNTHETIC form: the gcw_dev run set GCW_LABEL_DIR to the synthetic temporary directory. Synthetic runs set TMPDIR to the session scratchpad.
- A blank answer is invalid, and "<|" and over-long message text are changed in the prompt (D114). Dates use dbt's sent_at_local.
- The S5 test "gold with no vector is dropped" now expects the D109 refusal.

## Checks
- make lint: pass. make test: 958 passed, 202 deselected (882 before). make test-integration (llm and embed up): 202 passed, 0 skipped (174 passed, 1 skipped before). dbt build gcw_dev: PASS=190 WARN=0 ERROR=0 TOTAL=190. Unchanged: tagger prompt 11ac16d16643, embed version e3ac6a56e412. CI (PR #49, run 45551975783, commit before this line): all 7 jobs pass; eval-gate topic 0.650 (floor 0.55), sentiment 0.750 (floor 0.60); retrieval-gate 156 embedded then 0, recall@5 0.968 (floor 0.85), 0 dropped.
- Prompt-token count, worst case (25 lines, 5 x 2000 chars, system prompt, 500-char question; llm /tokenize, before the chat template; scratchpad script): English words 2879, random letters/digits 7778, digits 10678, Chinese 17985, emoji 29095. docker stats after answer-eval: llm 4.511GiB, embed 1.27GiB of 9.711GiB.
- gcw_dev, laptop CPU, version e3ac6a56e412. `make embed SYNTHETIC=1`: migration 007 applied; 223 eligible, 0 embedded, 223 already current, 0 failures, 0.20 s, 0.00 messages/s.
- retrieval-eval (synthetic, qwen3-embedding, messages not embedded 0, 31 questions, 0 dropped, 31 scored). vector: recall@1 0.323 [0.161, 0.484], @5 0.968 [0.903, 1.000], @10 1.000 [1.000, 1.000], MRR@10 0.591 [0.489, 0.700], p50 0.169 s, p95 0.219 s. hybrid: recall@1 0.323 [0.161, 0.484], @5 0.935 [0.839, 1.000], @10 1.000 [1.000, 1.000], MRR@10 0.551 [0.440, 0.672], p50 0.156 s, p95 0.215 s.
- retrieval-compare: scored 31, A 0.968, B 0.935, difference -0.032 [-0.161, 0.097], p95 B 0.215 s, latency met yes, winner vector (verdict written, corpus d45dea8ab979). promote METHOD=hybrid: "failed at verdict (NotWinnerError)"; enrich.retrieval_default has 0 rows.
- `make answer-eval SYNTHETIC=1` (qwen3.5-4b-q4_k_m, prompt 93518209d700, vector, not embedded 0): 31 questions, 0 dropped, 31 scored; invalid 0, 0.000 [0.000, 0.000]; answered 25, 0.806 [0.645, 0.935]; abstained 6, 0.194 [0.065, 0.355]; context has gold 30, 0.968 [0.903, 1.000]; citation hit 25, 0.806 [0.645, 0.935]; p50 12.200 s, p95 17.327 s. Stored file: mode 0600, only IDs, hashes, outcome and method names, numbers. Measured before the output check was rewritten without `assert` (same rules; unit tests ran after).
- `make ask SYNTHETIC=1`, two questions piped in: asked 2, answered 1, abstained 1, invalid 0, p50 10.503 s, p95 18.874 s, ask: ok. No file under TMPDIR or the repo changed; the cited message is local-eligible in gcw_dev.

## Not verified
- Real-data steps (Ryan): `make ask` smoke test after merge; retrieval-eval, compare and answer-eval once the 100 questions exist. Real answer latency, quality and llm memory are unmeasured; tagging was not re-measured at the new context size. A retrieval/*.json made before S6 has no corpus_hash: compare and consent-prune refuse it (rerun retrieval-eval).

## Open issues and questions
- Carried: Read(./data/**) does not cover Bash `cat data/...`; P2-5c items (placeholder NAME wildcard, pre-D95 METRICS rows not comparable); `gcw tag --no-start` does not verify the llm model; stop-word over-redaction (D82); isolation_guard multi-host URLs; real-ingest-synthetic slow (8m42s and 8m58s on PR #49, mostly its third step; PR #47 and #48 runs similar); label files of a purged thread stay (frozen samples and skips hold ids); metrics.py reports external-eligible only; Qwen3.5-4B ~1.6x slower than the D90 model; test_llm_server_local fails if llm serves another model; profiles not built. PR 47 chores: ci.yml allowlist, model re-hash check, METRICS placeholder row.
- One revocation plus consent-prune removes every stored retrieval or answer row that returned that sender: rerun the evals. A prompt near the token worst case would likely exceed the 600 s answer timeout and count as invalid (answers/pipeline.py). README.md:5 still says Phase 2 has started.

## Suggested next session
S6b hand grading of answer correctness.
