# Status
Last session: 2026-11-26 — The dashboard looks up no public IP at startup (D130; synthetic)

## Phase & state
Phase 3: everything the burst needs is built (D119-D130). What is left: the synthetic eval run under the D126 guard, then Ryan's real runs and review.
embeddings [x] retrieval eval [x synthetic] answers, prompt A/B [x synthetic] hand grading [x synthetic] Ask + Wrapped [x] README [x] eval guards [x built] gcw_ask + gated Activity [x] real questions [ ] real evals [ ] real grades [ ] METRICS rows [ ] deploy, usage window (later, D119)

## Done this session
- .streamlit/config.toml: browser.serverAddress = "localhost" (D130); gatherUsageStats = false was already there. tests/test_dashboard_pages.py pins both.
- .github/workflows/ci.yml (e2e job): the dashboard log has its startup URL line and no "External URL" line.
- README.md (Privacy, Dashboard access), docs/decisions.md (D130).
- Resolved: the "External URL" line in the dashboard log. It was a public-IP lookup.

## Deviations from brief
- The README sentence says "it sends no usage statistics" and cites D130.
- Added to the CI step: it first requires the startup URL line, so an empty log cannot pass.

## Checks (this session, dev stack, synthetic)
- Lookup (Streamlit 1.66.1, uv.lock): streamlit/web/bootstrap.py `_print_url` calls streamlit/net_util.py `get_external_ip`, which GETs http://checkip.amazonaws.com, then https://checkip.amazonaws.com if there is no response, 1 s timeout each. It ran because the image's --server.address=0.0.0.0 is a wildcard and the container is headless.
- Option: browser.serverAddress set explicitly. server.address cannot skip it while the container binds 0.0.0.0.
- make lint pass; make test 1167 passed, 307 deselected. Integration, warehouse and dbt tests not run locally (CI runs them).
- make dashboard-up ok; docker compose logs dashboard: "External URL" count 0, "URL: http://localhost:8501" present; /, /wrapped, /ask, health 200; make down.
- In-process with the lookup stubbed (nothing sent): startup calls it 0 times with the option and 1 time without; the websocket check calls it once for a foreign Origin with Host localhost:8501 (see Open issues), 0 times for local origins.

## Not verified
- Real data (Ryan), dev stack down, in order: eval-all, grade-answers, grade-summary, real-ingest, dashboard-up. real-ingest must precede dashboard-up: until that rebuild Activity reads a missing table and the old reader grants remain. That dashboard-up also rebuilds the image with D130.
- No browser check; docs/img/dashboard.png predates D128.
- After `wsl --shutdown`: `make eval-all SYNTHETIC=1` under the D126 guard (no answer-eval pass has run under it with a real model); README quickstart from a fresh clone; Mermaid render; grading keys by hand.

## Open issues and questions
- Planning (D130 covers startup only): `is_url_from_allowed_origins` (streamlit/web/server/server_util.py:58) still calls `get_external_ip` when a websocket arrives with an allowed Host and a foreign Origin, so a web page open in the browser can trigger the lookup. The connection is still refused. D130's title is broader than this. No config option found that removes the call without touching the D125 checks.
- Planning: gcw_ask can read all of raw.messages (and unpruned vectors), non-consented senders included, because answering does; consent rests on the query. A consented view or row-level security would close it.
- Gaps in session_id/session_index show that sessions with no opted-in message exist; reply times use others' timestamps (D125). gcw_ask keeps PUBLIC rights in the cluster's other databases and can change its own password and settings until the next dashboard-up.
- Run order matters: test_migrate_integration before test_dashboard_ask_integration fails ..._written_logged_and_kept_nowhere (a log handler on a closed stream); default order passes; not checked on main. The CI tag-eval gate gives topic 0.683 / sentiment 0.733 or 0.650 / 0.750, both on one commit too (926ea50, runs 45576711833, 45576729096); both clear the floors.
- Swap often exceeds 256 MiB here: expect a WSL restart per eval-all (D126). WSL clock steps backwards and the latest verdict is by file name (tagging/evaluation.py:331, retrieval/evaluation.py:429, answers/comparison.py:192). After a failed eval-all do not compare by hand; swap and other-stack checks are point checks.
- consent_priorities.csv is never pruned. No promote or re-ingest between grading sessions; a reworded question keeps its grade; revocation plus consent-prune removes eval and grade rows. A worst-case prompt would likely exceed 600 s. tests/synthetic/instagram.py:297 "call ended" misses dbt/seeds/instagram_placeholder_patterns.csv:11.
- Carried: Read(./data/**) does not cover Bash; P2-5c items; stop-word over-redaction (D82); isolation_guard multi-host URLs; real-ingest-synthetic slow; label files of a purged thread stay; metrics.py external-eligible only; Qwen3.5-4B slower than D90's model; test_llm_server_local fails if llm serves another model; PR 47 chores.

## Suggested next session
Ryan: restart WSL, run the synthetic eval-all, then the real batch. Next brief: S9 wrap-up (or the gcw_ask message filter, if planning wants it).
