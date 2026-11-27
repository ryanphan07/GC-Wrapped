# Status
Last session: 2026-11-26 — The vector view hides vectors and lexemes a revocation touched (D134; synthetic)

## Phase & state
Phase 3: everything the burst needs is built (D119-D135). What is left is Ryan's real runs and review.
embeddings [x] retrieval eval [x synthetic] answers, prompt A/B [x synthetic] hand grading [x synthetic] Ask + Wrapped [x] README [x] eval guards [x] gcw_ask on consented views + gated Activity [x] synthetic eval-all [x] real questions [ ] real evals [ ] real grades [ ] real METRICS rows [ ] deploy, usage window (later, D119)

## Done this session
- dbt/models/marts/ask_message_embeddings.sql: same rows; embedding and window_tsv NULL unless every window member is in ask_messages (lateral join). _marts.yml says so.
- dbt/tests/ask_relations_are_consented.sql: fails on a readable vector or lexemes whose window holds a refused message.
- tests/test_retrieval_integration.py: new test after a revocation, as pipeline user and as gcw_ask. test_a_stale_window_is_read_again_through_consent unchanged.
- .github/workflows/ci.yml: consent-flip step counts readable windows holding the flipped sender's keys (above 0 before, 0 after). README vector sentence; decisions D134.
- Resolved: gcw_ask reading a revoked neighbor's words (window_tsv) until consent-prune (D134).

## Deviations from brief
- 0ed8713f77b8 not reproducible: last session's script was not committed. Ryan chose one new script run on main and this branch against the same gcw_dev; D134's Verify line still cites the old hash.
- Looking for that script, listed file names in earlier sessions' scratchpads under /tmp, outside the repo; opened none.
- CI also gained the above-0 assertion before the flip, so the zero after it is not vacuous.
- git switch -c was once chained after make lint test (rule: git steps run alone). Auto mode gave "no verdict" on a read-only command, the commit, the push and the PR body write; each was reissued unchanged, as its notice said.

## Checks (this session, dev stack, synthetic)
- make lint pass; make test 1168 passed, 307 deselected. make test-integration (llm and embed up) 307 passed. make test-warehouse 31 passed. make dbt-build PASS=239 WARN=0 ERROR=0 TOTAL=239.
- Identity (throwaway record.py; main via git stash, view md5 c6c415115053, then branch, 498ba3d796c3; pipeline user and gcw_ask): 31 questions, 2 methods, 124 prompts; record sha256 03420715f321 in all four; 223 searchable, corpus d45dea8ab979, embed e3ac6a56e412.
- Planted leak (rolled back, one sender revoked, compiled test SQL): view 210 rows, 5 with both NULL, 0 with one; violations with new view 0, main's view 5, after rollback 0.
- EXPLAIN ANALYZE of the new view then: allowed keys in a hashed SubPlan; execution 3.393 ms.

## Not verified
- Real data (Ryan), dev stack down, in order: real-ingest (creates or replaces the views; evals fail without them), eval-all, grade-answers, grade-summary, dashboard-up (removes gcw_ask's old raw and vector grants).
- No retrieval-eval or answer-eval run; no timing at real corpus size; no browser check (docs/img/dashboard.png predates D128); README quickstart from a fresh clone; Mermaid render; grading keys by hand.

## Open issues and questions
- A migration that alters or drops enrich.message_embeddings must drop analytics.ask_message_embeddings first. Ask is unavailable while dbt rebuilds the views.
- Gaps in session_id/session_index show that sessions with no opted-in message exist; reply times use others' timestamps (D125). gcw_ask keeps PUBLIC rights in the cluster's other databases and can change its own password and settings until the next dashboard-up.
- Run order matters: test_migrate_integration before test_dashboard_ask_integration fails ..._written_logged_and_kept_nowhere (a log handler on a closed stream). The CI tag-eval gate varies between runs (926ea50, runs 45576711833, 45576729096); both clear the floors.
- Swap often exceeds 256 MiB here: expect a WSL restart per eval-all (D126). WSL clock steps backwards and the latest verdict is by file name (tagging/evaluation.py:331, retrieval/evaluation.py:429, answers/comparison.py:192). After a failed eval-all do not compare by hand.
- consent_priorities.csv is never pruned. No promote or re-ingest between grading sessions; a reworded question keeps its grade; revocation plus consent-prune removes eval and grade rows. A worst-case prompt would likely exceed 600 s. tests/synthetic/instagram.py:297 "call ended" misses dbt/seeds/instagram_placeholder_patterns.csv:11.
- Carried: Read(./data/**) does not cover Bash; P2-5c items; stop-word over-redaction (D82); isolation_guard multi-host URLs; real-ingest-synthetic slow; label files of a purged thread stay; metrics.py external-eligible only; Qwen3.5-4B slower than D90's model; test_llm_server_local fails if llm serves another model; PR 47 chores.

## Suggested next session
Ryan: merge, then the real batch in the order above. Next brief: S9 wrap-up.
