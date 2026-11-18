# Status
Last session: 2026-11-18 — P2-5b follow-up: D98 guard tests, thinking-off proof, cache check

## Phase & state
Phase 2 (Enrichment + evals); burst plan in CLAUDE.md. P2-2b (NER gate) is needed only before a hosted provider (D84).
consent ledger [x] two-level opt-in [x] redaction [x] labeling tool [x] synthetic labeled sets [x] consent replies [x 58 of 58 yes] user-text definition [x] real labels [ ] tagging pipeline [x synthetic] purge unlisted threads [x] eval harness [x synthetic] CI eval (S4b) [ ] real eval + Qwen3.5 4B vs 2B comparison [ ] profiles [ ]

## Done this session
- src/gcwrapped/tagging/commands.py: `PromptVersionMismatch` replaces the ValueError in run_tag_compare (failure line now names why).
- tests/test_tag_eval_integration.py: tag-compare 4B vs the reference fails at predictions with the fixed line and writes no verdict (fails if the check is removed, verified).
- tests/test_tag_eval.py: `commands._provider` against StubLlm sends chat_template_kwargs exactly when enable_thinking is not None, for every registry entry.
- CLAUDE.md Commands: generic id placeholders restored for tag-eval, tag-compare, tag-promote.

## Deviations from brief
- make test shows 798 (+1), not +2: the compare test is an integration test (needs gcw_dev), counted in test-integration (+1).

## Checks
- make lint: pass. make test: 798 passed, 139 deselected. make test-integration: 138 passed, 1 skipped (llm not running then).
- Synthetic, gcw_dev, not real results, no METRICS rows (llm service, synthetic text, production prompt and client, one box CPU):
  - Thinking-off proof (qwen3.5-4b): usage.prompt_tokens 325 with enable_thinking false vs 323 with true: they differ, so the setting reaches the template (completion_tokens 21 vs 48).
  - Cache check, 5 requests each; prompt_ms / predicted_ms medians:
    - qwen3.5-4b-q4_k_m: 881.1 / 1540.1; cache_n 321 (warm from the proof run), then 290 of ~326 from request 2 on: prefix reused.
    - qwen3-4b-instruct-2507-q4_k_m: 412.4 / 1382.2; request 1 cache_n 0, prompt_n 316 (cold), then cache_n 305 from request 2 on: prefix reused.
  - Source: probe script output (timings.* from the server response).

## Not verified
- Real evals (after merge, from the planning chat's commands): make tag-eval for all three models, make tag-compare for the arms.
- Qwen3.5-4B ran about 1.6x slower per message than the D90 model (3.25 s vs 2.00 s p50), though no thinking text appears. Likely the architecture on CPU; matters for the bulk-tagging time estimate.

## Open issues and questions
- Carried: Read(./data/**) does not cover Bash `cat data/...` (needs a deny rule outside this brief); P2-5c items (placeholder NAME wildcard, METRICS rows before D95 not comparable); `gcw tag --no-start` does not verify the llm model; stop-word over-redaction (D82); isolation_guard multi-host URLs; real-ingest-synthetic ~323 s; label files of a purged thread stay; metrics.py reports external-eligible only.
- Old tags under the D90 model keep prompt version 5b3245c5c605, which is intentionally unchanged.

## Suggested next session
Real label/tag evals (Ryan), then S4b CI eval, S5 embeddings.
