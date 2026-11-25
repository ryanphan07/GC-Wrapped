# Status
Last session: 2026-11-25 — S6a: answer diagnostics and eval reporting fixes (synthetic)

## Phase & state
Phase 3 (RAG + privacy + deploy): `make ask` shows what the model saw when it abstains, answer-eval reports prompt size and timeouts, single rates use Wilson intervals, consent-prune handles old eval formats. Synthetic only.
embeddings [x] retrieval eval [x synthetic] answers (S6, S6a) [x synthetic] real questions [ ] real retrieval eval + compare [ ] real answer eval [ ] hand grading (S6b) [ ] web app (S7) [ ] deploy (S8) [ ] usage window [ ]

## Done this session
- docs: D115-D118; CLAUDE.md (phase, ask, answer-eval, consent-prune). Resolved: README.md:5 said Phase 2 had started.
- D115/D116: tagging/llamacpp.py (ChatTimeout, ChatRefused); answers/ (timed_out outcome, token counts, shown excerpts on abstention, numbers line, new summary and eval lines). Resolved: a timeout counted as invalid.
- D117: retrieval/evaluation.py `wilson_interval`, `rate` (recall@k and the answer rates; MRR and compare keep the bootstrap).
- D118: consent_prune.py pruning reader `read_row_ids`, "old-format eval files to delete: N".
- Tests: test_answers{,_integration}.py, test_retrieval.py, test_consent_prune_integration.py, test_llm_server_local.py (token counts), tagging_stub.py (slow, not_json, usage).

## Deviations from brief
- No write-time check of answer rows exists, so none was extended: timed_out is validated on read and covered by write/read round trips.
- MRR@10 keeps the bootstrap (D117), so it can still be [0,0] or [1,1] if every reciprocal rank is equal; no measured interval is.
- D118: an old-format file with an empty rows list has no readable IDs and fails closed. "Old format" is any file only the pruning reader accepts.
- ChatTimeout is a timeout awaiting or reading the response; one while connecting or sending still means server unavailable (unchanged).
- Added "answer failed: model timed out" for ask. Token percentiles cover rows with a count. D115-D118 are dated 2026-11-25. Synthetic runs set TMPDIR to the scratchpad.

## Checks
- make lint: pass. make test: 986 passed, 226 deselected (958 before). make test-integration (llm and embed up): 226 passed, 0 skipped (202 before). dbt build gcw_dev: PASS=190 WARN=0 ERROR=0 TOTAL=190. Unchanged (pinned in test_answers.py): tagger prompt 11ac16d16643, answer prompts 23294c5ced29 / 93518209d700, embed version e3ac6a56e412. CI (PR #50, commit before this line): 14 checks pass (7 jobs in runs 45576711833 and 45576729096); eval-gate (floors 0.55 / 0.60) topic 0.650, sentiment 0.750 in the first, 0.683 and 0.733 in the second (same commit; main run 45509161069 also gave the second pair); retrieval-gate 156 embedded then 0, recall@5 0.968 (floor 0.85), 0 dropped.
- gcw_dev, laptop CPU, embed e3ac6a56e412. `make embed SYNTHETIC=1`: 223 eligible, 0 embedded, 223 current. retrieval-eval (31 questions, 0 dropped): vector recall@1 0.323 [0.186, 0.499], @5 0.968 [0.838, 0.994], @10 1.000 [0.890, 1.000], MRR@10 0.591 [0.489, 0.700], p50 0.187 s, p95 0.273 s; hybrid @1 0.323 [0.186, 0.499], @5 0.935 [0.793, 0.982], @10 1.000 [0.890, 1.000], MRR@10 0.551 [0.440, 0.672], p50 0.159 s, p95 0.188 s. compare: difference -0.032 [-0.161, 0.097], winner vector.
- `make answer-eval SYNTHETIC=1` (qwen3.5-4b-q4_k_m, prompt 93518209d700, vector): 31 scored; invalid 0 [0.000, 0.110]; timed out 0 [0.000, 0.110]; answered 25, 0.806 [0.637, 0.908]; abstained 6, 0.194 [0.092, 0.363]; context has gold 30, 0.968 [0.838, 0.994]; citation hit 25, 0.806 [0.637, 0.908]; p50 11.254 s, p95 17.889 s; prompt tokens p50 515, p95 629, max 669.
- `make ask SYNTHETIC=1`, three questions piped: answered 1, abstained 2, invalid 0, timed out 0; p50 12.252 s, p95 17.862 s; prompt tokens p50 553, p95 694. Both abstentions listed the shown messages (11 and 7); each answer had its numbers line; no file changed under TMPDIR or the repo (data/ not looked at).
- Pinned llm server, one synthetic prompt sent 3 times (scratchpad script): usage.prompt_tokens 226 each time, with 0 then 222 cached, so cached tokens are counted. docker stats after ask: llm 5.464GiB, embed 1.05GiB of 9.711GiB.

## Not verified
- Real-data step (Ryan): `make ask` on real data after merge; real prompt sizes, latency and answer quality are unmeasured. retrieval-eval, compare and answer-eval wait for the 100 questions.

## Open issues and questions
- Carried: Read(./data/**) does not cover Bash `cat data/...`; P2-5c items (placeholder NAME wildcard, pre-D95 METRICS rows not comparable); `gcw tag --no-start` does not verify the llm model; stop-word over-redaction (D82); isolation_guard multi-host URLs; real-ingest-synthetic slow (about 9 min, mostly its third step); label files of a purged thread stay (frozen samples and skips hold ids); metrics.py reports external-eligible only; Qwen3.5-4B ~1.6x slower than the D90 model; test_llm_server_local fails if llm serves another model; profiles not built. PR 47 chores: ci.yml allowlist, model re-hash check, METRICS placeholder row.
- One revocation plus consent-prune removes every stored retrieval or answer row that returned that sender: rerun the evals. A prompt near the token worst case would likely exceed the 600 s answer timeout (now counted as timed out).
- tests/synthetic/instagram.py:297 writes "call ended", which dbt/seeds/instagram_placeholder_patterns.csv:11 does not match, so synthetic call messages count as user text and show up as answer sources. Real call wording not checked here (text-audit is Ryan's).

## Suggested next session
S6b hand grading of answer correctness.
