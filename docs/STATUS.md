# Status
Last session: 2026-11-28 — Bound the llm server's memory growth during an answer pass (D142, synthetic)

## Phase & state
Phase 3: everything the burst needs is built (D119-D142). Real retrieval at n=41 is recorded; the real answer passes are Ryan's rerun.
embeddings [x] retrieval eval [x synthetic, x real n=41] answers, prompt A/B [x synthetic, real pilot] hand grading [x synthetic] Ask + Wrapped [x] README [x] eval guards [x] consented views + gated Activity [x] real questions [41] real evals [retrieval n=41; answers failed SwapGrew] real grades [ ] real METRICS rows [retrieval n=41] deploy, usage window (later, D119)

## Done this session
- docker-compose.yml: llm service gets `--cache-ram 0` (D142) and a comment. Image, model, prompts, sampling, retrieval, client and D126 limits unchanged.
- tests/test_tagging.py: the static llm service test pins `--cache-ram "0"` (exactly once, no `-cram`). tests/test_llm_server_local.py: new integration test reads the running dev llm's command line (/props does not report the setting).
- METRICS.md: real retrieval eval and comparison at n=41 (43418d8, PR #58); real vector latency before/after D138; synthetic llm memory before/after D142. docs/decisions.md: D142.

## Deviations from brief
- Task 1: the guard's own start/end lines of the two A passes were cut off by `tail` in my command; their memory numbers come from the sampler (every 3-5 s). B's guard lines are recorded.
- Task 1: answer-eval ran directly; the guard did not refuse (swap 92-210 MiB at start), so no throwaway answer script was needed. The sampler script (scratchpad, not committed) is the only extra tool.
- The "before" baseline for B is Context's 2026-11-26 run; I ran B only with the fix.

## Checks (dev stack, synthetic; base 43418d8 plus this PR)
- make lint pass; make test 1191 passed, 318 deselected; make test-integration (db, llm, embed up) 317 passed, 1192 deselected. dbt not run (no model changed).
- llama-server --help (pinned image): `--cache-ram` default 8192 MiB (-1 no limit, 0 off); `--cache-prompt` default on; `--cache-idle-slots` default on (needs cache-ram); `--ctx-checkpoints` default 32 per slot; `--checkpoint-min-step` 8192; `--cache-reuse` 0; `--load-mode` auto (mmap); `--slot-save-path` off.
- Memory (sampler; METRICS.md Serving row): before, A: llm RssAnon +1950448 KiB over 31 questions (2527168→4477616, also the peak), host available 5306→3507 MiB, swap 101→137; embed 959.5→965.3 MiB, db 30.63→36.89 MiB (peak 44.20), gcw 85936→86320 KiB: only llm grows. After, A: llm +218432 KiB (2526864→2745296), available 5363→5150, swap 209→209. After, B: guard 5304→5101 MiB available, swap 210→210; llm within 2700640-2760580 KiB all pass.
- Per question: before about 61 MiB (projects to roughly 2.5 GiB at 41 and 4.8 GiB at 80 synthetic questions, below the 8192 MiB cap; real prompts are longer: the real run dropped 3528 MiB at 41). After: no per-question trend; a one-time ~200 MiB on the first question, so 41 and 80 project to about the same. Projections, not measured.
- Outcomes after the fix equal Context: A 25/6/0, citation hit 25, prompt tokens 515/629/669; B 27/3/1 (marker not cited), citation hit 27, 566/680/720. make answer-identity SYNTHETIC=1: record e83e96e678ec before and after.

## Not verified
- Real data (Ryan): after merge, wsl --shutdown, then eval-all, grade-answers, grade-summary, retrieval-misses (block in the PR). Real prompts are longer; flat growth is shown on synthetic only.
- The Ask page with the cache off (only the returning-to-an-earlier-question case costs more, D142). README quickstart from a fresh clone; grading keys by hand.

## Open issues and questions
- A migration that alters or drops enrich.message_embeddings must drop analytics.ask_message_embeddings first. Ask is unavailable while dbt rebuilds the views. Gaps in session_id/session_index show sessions with no opted-in message; reply times use others' timestamps (D125). gcw_ask keeps PUBLIC rights in other databases and can change its own settings until the next dashboard-up. The CI tag-eval gate varies between runs.
- Swap often exceeds 256 MiB here at start: a WSL restart before eval-all is still advised (D126). After a failed eval-all do not compare by hand. consent_priorities.csv is never pruned. No promote or re-ingest between grading sessions; a reworded question keeps its grade; revocation plus consent-prune removes eval and grade rows. A worst-case prompt would likely exceed 600 s. tests/synthetic/instagram.py:297 "call ended" misses dbt/seeds/instagram_placeholder_patterns.csv:11.
- Carried: Read(./data/**) does not cover Bash; P2-5c items; stop-word over-redaction (D82); isolation_guard multi-host URLs; real-ingest-synthetic slow; label files of a purged thread stay; metrics.py external-eligible only; Qwen3.5-4B slower than D90's model; test_llm_server_local fails if llm serves another model; PR 47 chores; `make retrieval-misses SYNTHETIC=1` full output only tested in gcw_test.

## Suggested next session
Ryan: merge, then the real batch and paste eval-all's and retrieval-misses' lines. Next brief: record real answer rows, act on the misses breakdown, then S9 wrap-up.
