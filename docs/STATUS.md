# Status
Last session: 2026-11-24 — S4b: CI eval gate (tiny model), consent-prune, eval diagnostics, METRICS rows for the real Phase 2 runs

## Phase & state
Phase 2 (Enrichment + evals) is complete (D99); Phase 3 (embeddings + retrieval, S5) is next. P2-2b (NER gate) is needed only before a hosted provider (D84).
consent ledger [x] two-level opt-in [x] redaction [x] labeling tool [x] synthetic labeled sets [x] consent replies [x 58 of 58 yes] user-text definition [x] real labels [x] tagging pipeline [x] purge unlisted threads [x] eval harness [x] CI eval (S4b) [x on merge] real eval + Qwen3.5 4B vs 2B comparison [x] profiles [ ]

## Done this session
- docs/decisions.md D99-D101 (verbatim); CLAUDE.md: Phase 3 next, consent-prune and the gate-floor rule. METRICS.md: Tagging eval rows from the brief (the 2026-11-15 consent row already existed, so none added).
- src/gcwrapped/tagging/model.py: `qwen3.5-0.8b-q4_k_m` (unsloth GGUF, pinned commit and sha256, thinking off). tests: registry (4 models), prompt version pinned at 11ac16d16643.
- scripts/eval_gate.py, tests/fixtures/eval/gate_floors.json, tests/test_eval_gate.py: floors topic 0.55, sentiment 0.60; fixed output. .github/workflows/ci.yml: `eval-gate` job (cached model, tag-eval SYNTHETIC=1, gate).
- tagging/{scoring,evaluation,report}.py: tag-eval majority-baseline lines and one confusion line per gold class (shape-checked; `report.check_eval_lines`). labeling/summary.py: the two sample lines.
- src/gcwrapped/consent_prune.py, cli.py, Makefile: `make consent-prune [APPLY=1]`; tests/test_consent_prune_integration.py (8).

## Deviations from brief
- No 0.8B GGUF under Qwen/ (401); used unsloth, as for the 4B and 2B.
- A question whose source message is ineligible is dropped whole (the file format needs the source inside the gold); it counts under "questions left with no gold".
- frozen sample files and skips.csv (ids only) are not pruned. METRICS placeholder row kept (rows are never edited).
- The two prompt shuffles in the bite test did not trip the gate (below); removing the label lists did.

## Checks
- make lint: pass. make test: 811 passed, 147 deselected (798 before). make test-integration: 146 passed, 1 skipped (138 before; llm down).
- dbt build gcw_dev: PASS=190 WARN=0 ERROR=0 TOTAL=190.
- Floor run, qwen3.5-0.8b-q4_k_m, synthetic (60 labels, gcw_dev, local CPU): topic accuracy 0.683, sentiment accuracy 0.733 (chance 0.10, 0.33); floors 0.55 and 0.60.
- Gate bites, local, not committed: (1) topic definitions rotated against names: topic 0.633, sentiment 0.767, gate ok; (2) names rotated: 0.600 / 0.733, gate ok; (3) topic and sentiment lists removed from the system prompt: topic 0.500, sentiment 0.717, gate FAILED (topic below 0.55), exit 1. The gate catches gross breakage, not subtle drift.
- consent-prune on gcw_dev (one sender set to no, consent-load, temp label dir): dry run ineligible 54, tags 54, label rows 32, gold refs 13, no-gold questions 12, prediction rows 32; APPLY=1 same counts; reruns all 0; files 0600. Ledger restored and retagged after.
- Tag-label sampler (labeling/sampling.py): stratified by thread (floor per thread, the rest proportional, seeded). "201 of 200" compared the label-file rows with the frozen sample size, so one label is for a message outside the current sample, likely made under an earlier draw (the flow offers only sample messages); new lines show it. Not verifiable here.
- CI eval-gate job (PR #47, run 45500020093): pass, 2m34s job time, tag-eval step 94 s; topic accuracy 0.650 (floor 0.55), sentiment 0.750 (floor 0.60), majority baselines 0.100 / 0.367, "eval-gate: ok". It differs from the local 0.683 / 0.733 (CPU nondeterminism), so margins are about 0.10 and 0.15. All 6 CI jobs pass.

## Not verified
- Real-data steps below (Ryan). The eval-gate floors have run only on this laptop and one CI run.

## Open issues and questions
- Carried: Read(./data/**) does not cover Bash `cat data/...`; P2-5c items (placeholder NAME wildcard, pre-D95 METRICS rows not comparable); `gcw tag --no-start` does not verify the llm model; stop-word over-redaction (D82); isolation_guard multi-host URLs; real-ingest-synthetic ~323 s; label files of a purged thread stay (consent-prune now removes rows; frozen samples and skips still hold ids); metrics.py reports external-eligible only.
- Qwen3.5-4B is about 1.6x slower per message than the D90 model on CPU.
- test_llm_server_local fails if the llm service runs a model other than the 4B default.

## Suggested next session
S5 embeddings + retrieval (synthetic), then the real batch.
