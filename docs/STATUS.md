# Status
Last session: 2026-11-27 — Wrap-up nits before the real rerun: no Deploy button, bounded CI wait, D141 (synthetic)

## Phase & state
Phase 3: everything the burst needs is built (D119-D141). What is left is Ryan's rerun with about 40 questions and review.
embeddings [x] retrieval eval [x synthetic, real pilot n=8] answers, prompt A/B [x synthetic, real pilot] hand grading [x synthetic] Ask + Wrapped [x] README [x] eval guards [x] consented views + gated Activity [x] real questions [8 of ~40] real evals [pilot] real grades [ ] real METRICS rows [pilot] deploy, usage window (later, D119)

## Done this session
- .streamlit/config.toml: client.toolbarMode = "minimal"; tests/test_dashboard_pages.py pins it. docs/img: three screenshots recaptured.
- .github/workflows/ci.yml: `timeout 30` on the tagging stub's wait. No assertion removed.
- METRICS.md: one new row giving the model id of the real pilot answer rows. docs/decisions.md: D141.
- Resolved: "Deploy" button on the dashboard; unbounded tagging stub wait; latency next step (D141: keep both window checks).

## Deviations from brief
- Item 3: the three pilot rows are unchanged. Editing them breaks "never edit a METRICS row" (METRICS.md, CLAUDE.md, D65); asked, and Ryan chose a correction row. The model id comes from the brief's report of the printed lines.
- D141 is appended verbatim, so it states measured numbers outside METRICS.md and STATUS.md (D65, CLAUDE.md).
- The removed latency item also noted that answers/excerpts.py:38 numbers every eligible message; D141 does not mention that.
- Auto mode gave "no verdict" on two read-only commands; each reissued as its notice said.

## Checks (dev stack, synthetic; the tree of this PR)
- make lint pass; make test 1191 passed, 317 deselected. Integration, warehouse and dbt tests not run here (no code or model they cover changed); CI runs them.
- make dashboard-up: /, /wrapped, /ask returned 200 (curl). No Deploy button: on all three pages Playwright counted 0 `stAppDeployButton` elements and 0 "Deploy" texts; a second instance in the same container with toolbarMode=auto counted 1 and 1. make down afterwards; docker ps listed no containers.
- Screenshots: playwright/python:v1.65.0-noble with playwright 1.65.0, in the dashboard container's network, script not committed; all three viewed, sizes unchanged.

## Not verified
- Real data (Ryan): nothing run; no dbt model changed, so no real-ingest. After ~40 questions: wsl --shutdown, eval-all, grade-answers, grade-summary, retrieval-misses. Real latency with D138 is unmeasured (bench vectors and text are stubs).
- Still from PR #57: `make retrieval-misses SYNTHETIC=1` has printed only its no-predictions line on the dev stack (D126 refused retrieval-eval on swap; full output tested in gcw_test only). README quickstart from a fresh clone; grading keys by hand.

## Open issues and questions
- A migration that alters or drops enrich.message_embeddings must drop analytics.ask_message_embeddings first. Ask is unavailable while dbt rebuilds the views. Gaps in session_id/session_index show sessions with no opted-in message; reply times use others' timestamps (D125). gcw_ask keeps PUBLIC rights in other databases and can change its own settings until the next dashboard-up. The CI tag-eval gate varies between runs.
- Swap often exceeds 256 MiB here: expect a WSL restart per eval-all (D126). After a failed eval-all do not compare by hand. consent_priorities.csv is never pruned. No promote or re-ingest between grading sessions; a reworded question keeps its grade; revocation plus consent-prune removes eval and grade rows. A worst-case prompt would likely exceed 600 s. tests/synthetic/instagram.py:297 "call ended" misses dbt/seeds/instagram_placeholder_patterns.csv:11.
- Carried: Read(./data/**) does not cover Bash; P2-5c items; stop-word over-redaction (D82); isolation_guard multi-host URLs; real-ingest-synthetic slow; label files of a purged thread stay; metrics.py external-eligible only; Qwen3.5-4B slower than D90's model; test_llm_server_local fails if llm serves another model; PR 47 chores.

## Suggested next session
Ryan: merge, then the real batch above and paste eval-all's and retrieval-misses' lines. Next brief: act on the misses breakdown, then S9 wrap-up.
