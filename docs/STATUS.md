# Status
Last session: 2026-11-18 — P2-5b: experiment arms move to Qwen3.5 (D98)

## Phase & state
Phase 2 (Enrichment + evals); burst plan in CLAUDE.md. P2-2b (NER gate) is needed only before a hosted provider (D84).
consent ledger [x] two-level opt-in [x] redaction [x] labeling tool [x] synthetic labeled sets [x] consent replies [x 58 of 58 yes] user-text definition [x] real labels [ ] tagging pipeline [x synthetic] purge unlisted threads [x] eval harness [x synthetic] CI eval (S4b) [ ] real eval + Qwen3.5 4B vs 2B comparison [ ] profiles [ ]

## Done this session
- docs/decisions.md: D98 (the brief's D96; D96 and D97 were taken). CLAUDE.md: Stack, Commands, D93 protocol line.
- src/gcwrapped/tagging/model.py: registry = Qwen3.5-4B (default), Qwen3.5-2B (arm B), Qwen3-4B-Instruct-2507 (reference); Qwen2.5-1.5B removed. `ModelSpec.enable_thinking` (False, or None for the reference).
- llamacpp.py, provider.py, commands.py: client sends `chat_template_kwargs` from the registry entry; prompt.py `prompt_version_for` hashes it in; tag-compare refuses arms with different prompt versions.
- docker-compose.yml, dbt/dbt_project.yml: default model and file now Qwen3.5-4B.
- METRICS.md: three rows from the brief (SHA 5cafcc0, then c31d12b for #42).
- Tests: registry pins for all three, request setting per registry entry, prompt version differs, dbt var equality (existing); two integration tests use the per-model version.

## Deviations from brief
- Decision is D98, not D96 (taken by D96, D97).
- GGUFs come from unsloth (Qwen has no official GGUF repos for 3.5-4B/2B); commits pinned in model.py.
- The llama.cpp image pin was not changed: it already runs Qwen3.5.

## Checks
- make lint: pass. make test: 797 passed, 138 deselected (baseline 795). make test-integration: 137 passed, 1 skipped (llm service not running at that time), 797 deselected (baseline 137).
- Synthetic, gcw_dev, not a real result, nothing promoted (60 labels; prompt version 11ac16d16643 for Qwen3.5, 5b3245c5c605 for the reference):
  - qwen3.5-4b: topic acc 0.967, sentiment acc 0.900; 0.29 msg/s (p50 3.25 s)
  - qwen3.5-2b: topic acc 0.917, sentiment acc 0.883; 0.72 msg/s (p50 1.31 s)
  - qwen3-4b-instruct-2507 (reference): topic acc 0.883, sentiment acc 0.817; 0.46 msg/s (p50 2.00 s)
  - tag-compare 4B vs 2B: speedup 2.44 pass; topic lower bound fail (diff -0.050, interval [-0.133, 0.017]); sentiment lower bound fail ([-0.117, 0.083]); winner qwen3.5-4b (source: make tag-eval / tag-compare SYNTHETIC=1 output).
- No reasoning text: a direct request to the server returned only `content` (no reasoning field), 12 completion tokens.

## Not verified
- Real evals (after merge, from the planning chat's commands): make tag-eval for all three models, make tag-compare for the arms.
- Qwen3.5-4B ran about 1.6x slower per message than the D90 model (3.25 s vs 2.00 s p50), though no thinking text appears. Likely the architecture on CPU; matters for the bulk-tagging time estimate.

## Open issues and questions
- Carried: Read(./data/**) does not cover Bash `cat data/...` (needs a deny rule outside this brief); P2-5c items (placeholder NAME wildcard, METRICS rows before D95 not comparable); `gcw tag --no-start` does not verify the llm model; stop-word over-redaction (D82); isolation_guard multi-host URLs; real-ingest-synthetic ~323 s; label files of a purged thread stay; metrics.py reports external-eligible only.
- Old tags under the D90 model keep prompt version 5b3245c5c605, which is intentionally unchanged.

## Suggested next session
Real label/tag evals (Ryan), then S4b CI eval, S5 embeddings.
