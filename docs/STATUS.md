# Status
Last session: 2026-11-26 — Ask reads consent-filtered views; D130 follow-up (D131-D133; synthetic)

## Phase & state
Phase 3: everything the burst needs is built (D119-D133). What is left is Ryan's real runs and review.
embeddings [x] retrieval eval [x synthetic] answers, prompt A/B [x synthetic] hand grading [x synthetic] Ask + Wrapped [x] README [x] eval guards [x] gcw_ask on consented views + gated Activity [x] synthetic eval-all [x] real questions [ ] real evals [ ] real grades [ ] real METRICS rows [ ] deploy, usage window (later, D119)

## Done this session
- dbt: new views ask_messages, ask_message_embeddings (security_barrier, owner's rights); gcw_ask grants moved to them; new test ask_relations_are_consented; reader_relations_are_consented now checks Wrapped messages_sent and thread_share.
- src: retrieval/search.py and answers/excerpts.py read only the views (consent-prune's SQL reads what it read before); dashboard/ask_role.py grants the two views and three default tables, no USAGE on raw.
- docker-compose.yml: checkip.amazonaws.com -> 127.0.0.1 (D132). tests: views built from the dbt SQL (ask_view_support.py), revocation, security_barrier, compose and net_util URL tests. CI, README, CLAUDE.md (gcw_ask, denied-call rule), METRICS.md (Ryan's synthetic eval-all), decisions D131-D133.
- Wrapped check (D128): every count, share and rate was already over opted-in typed messages; mart unchanged. Resolved: gcw_ask reading all of raw.messages and unpruned vectors by sender (D131); the foreign-Origin IP lookup (D132).

## Deviations from brief
- D131's "still ranks" is wrong: search still requires the whole window (D75). The vector view filters on the centre; a revoked neighbor's row stays readable, not searchable, until consent-prune. A whole-window view was tried first and broke test_a_stale_window_is_read_again_through_consent. D133.
- net_util.py has three URLs nothing requests (licence, help link, credit); the test excepts exactly those and pins what is requested.
- CI: the gcw_ask step moved after the ledger load, with a new stub-embed step (the views were empty where it was); raw refusals now name the schema; the read-only check uses enrich.tag_default (also tests/ask_permissions.py); the view list gained two names; embedding_stub.py gained --port.
- Real order: real-ingest must now come before eval-all (the brief put it after); README says so. Auto mode gave "no verdict" on some read-only commands; reissued singly, as its notice allowed. No change was affected.

## Checks (this session, dev stack, synthetic)
- make lint pass; make test 1168 passed, 306 deselected. make test-integration (llm and embed up) 306 passed. make test-warehouse 31 passed. make dbt-build PASS=239 WARN=0 ERROR=0 TOTAL=239.
- Identity (throwaway script, main vs final code, as pipeline user and as gcw_ask): 31 questions, 2 methods, 124 prompts; keys and prompt hashes identical, record sha256 0ed8713f77b8; 223 searchable, corpus d45dea8ab979, embed e3ac6a56e412.
- make retrieval-eval SYNTHETIC=1, vector 0.323/0.968/1.000, MRR 0.591; hybrid 0.323/0.935/1.000, MRR 0.551 (recall@1/5/10): equal to the brief. Run on the whole-window view; on the final code the guard refused (swap 258, later 416 MiB), so the identity check covers it.
- Planted leaks: unfiltered ask_messages failed dbt test (218 results), then rebuilt clean. Rolled back, with the compiled test SQL: no text rule 119 rows; refused vector 1; Wrapped share over all messages 13; Wrapped without text rule 12; clean 0.
- In the dashboard container: AppTest answered with one citation as gcw_ask, no errors; raw.messages, message_embeddings, fct_messages denied; getent and getaddrinfo give 127.0.0.1; get_external_ip() None.

## Not verified
- Real data (Ryan), dev stack down, in order: real-ingest (creates the views; evals fail without them), eval-all, grade-answers, grade-summary, dashboard-up (removes gcw_ask's old raw and vector grants).
- No answer-eval with the real model; no browser check (docs/img/dashboard.png predates D128); README quickstart from a fresh clone; Mermaid render; grading keys by hand.

## Open issues and questions
- Planning: dbt/models/marts/ask_message_embeddings.sql lets gcw_ask read a revoked neighbor's words (window_tsv) until consent-prune. Option with unchanged answers: null vector and lexemes for such rows in the view. A migration that alters or drops enrich.message_embeddings must drop the view first. Ask is unavailable while dbt rebuilds the views.
- Gaps in session_id/session_index show that sessions with no opted-in message exist; reply times use others' timestamps (D125). gcw_ask keeps PUBLIC rights in the cluster's other databases and can change its own password and settings until the next dashboard-up.
- Run order matters: test_migrate_integration before test_dashboard_ask_integration fails ..._written_logged_and_kept_nowhere (a log handler on a closed stream). The CI tag-eval gate varies between runs (926ea50, runs 45576711833, 45576729096); both clear the floors.
- Swap often exceeds 256 MiB here: expect a WSL restart per eval-all (D126). WSL clock steps backwards and the latest verdict is by file name (tagging/evaluation.py:331, retrieval/evaluation.py:429, answers/comparison.py:192). After a failed eval-all do not compare by hand.
- consent_priorities.csv is never pruned. No promote or re-ingest between grading sessions; a reworded question keeps its grade; revocation plus consent-prune removes eval and grade rows. A worst-case prompt would likely exceed 600 s. tests/synthetic/instagram.py:297 "call ended" misses dbt/seeds/instagram_placeholder_patterns.csv:11.
- Carried: Read(./data/**) does not cover Bash; P2-5c items; stop-word over-redaction (D82); isolation_guard multi-host URLs; real-ingest-synthetic slow; label files of a purged thread stay; metrics.py external-eligible only; Qwen3.5-4B slower than D90's model; test_llm_server_local fails if llm serves another model; PR 47 chores.

## Suggested next session
Ryan: merge, then the real batch in the order above. Next brief: S9 wrap-up.
