# Status
Last session: 2026-11-27 — Retrieval latency at real scale (D138), answer-identity, retrieval-misses, wrap-up groundwork (synthetic)

## Phase & state
Phase 3: everything the burst needs is built (D119-D140). What is left is Ryan's rerun with about 40 questions and review.
embeddings [x] retrieval eval [x synthetic, real pilot n=8] answers, prompt A/B [x synthetic, real pilot] hand grading [x synthetic] Ask + Wrapped [x] README [x] eval guards [x] consented views + gated Activity [x] real questions [8 of ~40] real evals [pilot] real grades [ ] real METRICS rows [pilot] deploy, usage window (later, D119)

## Done this session (PR #57)
- retrieval/search.py: Retriever sets jit off on its connection (D138). No SQL, view or consent change.
- answers/identity.py + `make answer-identity` (D136). tests/test_bench_retrieval.py + `make bench-retrieval`, marker `bench` (D137); metrics.py unit selection excludes it.
- retrieval/misses.py + `make retrieval-misses` (D139). METRICS: pilot eval, real-ingest rerun, benchmark before and after. docs/img: three screenshots recaptured, README caption. ci.yml: `timeout 30` on the stub-embed wait. D135 corrects D134's identity hash.
- Resolved: test-order flake (conftest.py removes the handler main() adds); latest verdict by wall clock (D140); dashboard.png predating D128; Mermaid render; no timing at corpus size (synthetic).

## Deviations from brief
- answer-identity does not exist on main: its main record is from 2420eff, whose retrieval, answer, tagging, dbt and migration files had an empty diff to c5df684. It also hashes ranked IDs and fails if the two connections differ.
- Benchmark "before" ran on the tree just before commit fb769c0 (benchmark file hash unchanged); JIT-on and JIT-off variants were added with the fix. Task 3's first check failed on metrics' test selection (fixed, not in the brief).
- Queued brief ran after tasks 1-5, before task 6. retrieval-misses adds two lines (source not eligible; top 10 differs) and refuses a changed corpus or question set.
- New screenshots show Streamlit's "Deploy" button; the old ones did not. Auto mode gave "no verdict" on three read-only commands; each reissued unchanged, as its notice said.

## Checks (dev stack, synthetic; code at dc1cb90 unless noted)
- make lint pass; make test 1191 passed, 317 deselected. make test-integration (llm, embed up; tree committed as c3026d9 + dc1cb90) 316 passed. make test-warehouse 31 passed. make dbt-build PASS=239 WARN=0 ERROR=0 TOTAL=239, both ask/reader consent tests pass. CI on b130eab: all jobs pass, consent flip included.
- answer-identity, pipeline user and gcw_ask: e83e96e678ec at 2420eff (main behaviour), b130eab and dc1cb90; 31 questions, 2 methods, 124 prompts, 223 searchable, corpus d45dea8ab979, embed e3ac6a56e412.
- Bench (11000 messages, 6074 windows, p50/p95 ms), fb769c0 -> b130eab: vector 1741.4/1868.7 -> 187.8/222.1; hybrid 3558.0/3856.0 -> 321.0/363.0; excerpts 62.7/78.2 -> 59.9/72.9; query embedding 147.4/161.8 -> 142.7/169.8. Identical IDs and scores with JIT on and off, current and pre-D131, also after a revocation.
- Pre-D131 at b130eab, JIT on / off, p50 ms: vector 550.3 / 103.2; hybrid 1170.2 / 159.5; excerpts 43.9 / 44.5.
- Plans, fb769c0: cost estimate 18521356; JIT 1471.783 of 1718.853 ms. NOT IN subplans hashed and run once (loops=1); seq scans single-digit ms. b130eab: vector 311.876 ms under EXPLAIN, no JIT (view join done at 133 ms, search's window filter at 308 ms); excerpt messages 51.4 ms.
- Flake: reproduced, then test_migrate_integration + test_dashboard_ask_integration 29 passed. Mermaid: rendered offline, mermaid-cli 12.0.0 (image sha256:18938e53497376d5), no syntax change. Screenshots: playwright/python:v1.65.0-noble, script not committed.

## Not verified
- Real data (Ryan): nothing run; no dbt model changed, so no real-ingest. After ~40 questions: wsl --shutdown, eval-all, grade-answers, grade-summary, retrieval-misses. Real latency with D138 is unmeasured (bench vectors and text are stubs). No eval pass this session, no browser check beyond the screenshots, README quickstart from a fresh clone, grading keys by hand.
- Acceptance not met: `make retrieval-misses SYNTHETIC=1` printed only its no-predictions failure line here: D126 refused retrieval-eval (swap 907 MiB; 318 and 337 after make down). Its full output is tested in gcw_test only.

## Open issues and questions
- Next latency step, needs a decision: search SQL is still above pre-D131: whole-window check twice (dbt/models/marts/ask_message_embeddings.sql:21, retrieval/search.py:28; D134 keeps both) and ask_messages evaluated three times. answers/excerpts.py:38 numbers every eligible message.
- Dashboard shows "Deploy" on localhost; `client.toolbarMode = "minimal"` in .streamlit/config.toml would likely hide it. ci.yml:105 (tagging stub wait) is still unbounded.
- A migration that alters or drops enrich.message_embeddings must drop analytics.ask_message_embeddings first. Ask is unavailable while dbt rebuilds the views. Gaps in session_id/session_index show sessions with no opted-in message; reply times use others' timestamps (D125). gcw_ask keeps PUBLIC rights in other databases and can change its own settings until the next dashboard-up. The CI tag-eval gate varies between runs.
- Swap often exceeds 256 MiB here: expect a WSL restart per eval-all (D126). After a failed eval-all do not compare by hand. consent_priorities.csv is never pruned. No promote or re-ingest between grading sessions; a reworded question keeps its grade; revocation plus consent-prune removes eval and grade rows. A worst-case prompt would likely exceed 600 s. tests/synthetic/instagram.py:297 "call ended" misses dbt/seeds/instagram_placeholder_patterns.csv:11.
- Carried: Read(./data/**) does not cover Bash; P2-5c items; stop-word over-redaction (D82); isolation_guard multi-host URLs; real-ingest-synthetic slow; label files of a purged thread stay; metrics.py external-eligible only; Qwen3.5-4B slower than D90's model; test_llm_server_local fails if llm serves another model; PR 47 chores.

## Suggested next session
Ryan: merge, then the real batch above and paste eval-all's and retrieval-misses' lines. Next brief: act on the misses breakdown, then S9 wrap-up.
