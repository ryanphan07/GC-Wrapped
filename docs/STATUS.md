# Status
Last session: 2026-11-25 — Burst finish: hand grading, answer prompt A/B, eval-all, Wrapped mart, Ask and Wrapped pages, README (synthetic)

## Phase & state
Phase 3: everything the burst needs is built (D119-D125). The synthetic end-to-end run is INCOMPLETE (see Not verified). What is left is Ryan's real runs and review.
embeddings [x] retrieval eval [x synthetic] answers, prompt A/B [x built] hand grading [x built] Ask + Wrapped [x] README [x] real questions [ ] real evals [ ] real grades [ ] METRICS rows [ ] deploy, usage window (later, D119)

## Done this session
- answers/: prompt.py variant b; comparison.py (answer-compare, verdict, default, promote); grades.py and commands.py (grade-answers, grade-summary); report.py lines. migrations/008. eval_all.py. retrieval compare reports its winner. consent_prune.py covers grades/*.csv. tagging/scoring.py `nearest_rank` for percentiles. Makefile and cli.py targets.
- dbt: mart_member_wrapped (view), schema, unit and singular tests, reader grant. dashboard/: app.py router, views/{activity,wrapped,ask}.py; gcwrapped/dashboard/{ask,wrapped,services}.py; compose, Dockerfile, .streamlit/config.toml. README.md, docs/img/. CI: view list, mart and revocation checks.
- Review fixes (D125): server.allowedHosts, one keypress per grading prompt, grade saves re-read the file, migration test list. Comments corrected in fct_message_tags.sql and retrieval/window.py. D119-D125; CLAUDE.md. Resolved: profiles not built; README said test-integration resets raw in gcw_dev.

## Deviations from brief
- Part I partial: Claude Code stopped `eval-all` for low system memory during `answer-eval b`; it was not restarted (needs Ryan's go-ahead). Dev runs used GCW_LLM_PORT=8091: something outside the dev project holds 8081 (not inspected).
- `make answer-promote VARIANT=a|b`. grade-answers ends with four fixed count lines; new fixed failure line for dashboard-up. AnswerPipeline requires `variant`. answer-compare skips questions with ineligible references (D87); grade-summary counts the whole file.
- D124: mart is a view (CI view assertion updated); dashboard gets pipeline DB credentials; "embed" added to the local host names. README references docs/img/; old docs/images/ screenshot left in place.
- Screenshots ran in Microsoft's Playwright container (host Chromium libraries need sudo); script not committed.
- Review items declined: grade rows record no retrieval method or question hash (D120 fixes the columns); reply time not narrowed (D125); Ask page does not check the served model. Part A's commit was gated on a piped command (tests passed); later commits gate on make's exit status.

## Checks
- make lint: pass. make test: 1086 passed, 271 deselected (986 before). make test-integration with llm and embed DOWN: 268 passed, 3 skipped (local server tests). make test-warehouse: 22 passed. dbt build gcw_dev: PASS=215 WARN=0 ERROR=0 TOTAL=215 (190 before). Pins unchanged: tagger 11ac16d16643, answer A 23294c5ced29 / 93518209d700, embed e3ac6a56e412; answer B 89b98c82df03 / 1550fccbeddf (new). CI (PR #51, commit before this line): 14 checks pass (7 jobs in runs 45602012942 and 45602024410); integration 268 passed, 3 skipped, dbt 215/215, warehouse 22; eval-gate (floors 0.55 / 0.60) topic 0.650, sentiment 0.750 in both; retrieval-gate 156 embedded then 0, recall@5 0.968 (floor 0.85), 0 dropped.
- `make eval-all SYNTHETIC=1` (partial; embed e3ac6a56e412, qwen3.5-4b-q4_k_m): embed ok, 223 eligible, 0 embedded, 223 current. vector (31 scored, 0 dropped) recall@1 0.323 [0.186, 0.499], @5 0.968 [0.838, 0.994], @10 1.000 [0.890, 1.000], MRR@10 0.591 [0.489, 0.700], p50 0.161 s, p95 0.205 s; hybrid @1 0.323 [0.186, 0.499], @5 0.935 [0.793, 0.982], @10 1.000 [0.890, 1.000], MRR@10 0.551 [0.440, 0.672], p50 0.138 s, p95 0.200 s; compare -0.032 [-0.161, 0.097], latency met yes, winner vector.
- Same run, answer-eval a (93518209d700): 31 scored; invalid 0, timed out 0 [0.000, 0.110]; answered 25, 0.806 [0.637, 0.908]; abstained 6, 0.194 [0.092, 0.363]; context has gold 30, 0.968 [0.838, 0.994]; citation hit 25, 0.806 [0.637, 0.908]; p50 10.565 s, p95 17.934 s; prompt tokens p50 515, p95 629, max 669.
- Synthetic stack: dashboard-up healthy; Ask answered with a citation (docs/img/ask.png); websocket Host localhost and 127.0.0.1 got 101, another name 403; a revoked sender's mart row went 1 to 0 with no dbt build (rolled back).

## Not verified
- Synthetic: answer-eval b, answer-compare with the real model, `grade-answers SYNTHETIC=1 N=5`, `grade-summary SYNTHETIC=1` (all covered by stub tests only). Free about 7 GB, then `make eval-all SYNTHETIC=1`, `printf cycycycycy | make grade-answers SYNTHETIC=1 N=5`, `make grade-summary SYNTHETIC=1`, `make test-integration` with llm and embed up.
- README quickstart not run from a fresh clone; Mermaid diagram not rendered. Grading keys tested on a pseudo-terminal, not by hand.
- Real data (Ryan), in order: eval-all, grade-answers, grade-summary, real-ingest (builds dbt), dashboard-up (PR body block).

## Open issues and questions
- tagging/commands.py:63 start_llm (and embed): if another process holds the port, a later start can report healthy with no network, and the client talks to whatever holds the port. Give each stack its own GCW_LLM_PORT.
- Planning: fct_message_tags and agg_tags_by_* keep a revoked sender's rows until the next dbt build, so D119's "consent-prune removes everything" is too strong; Activity marts include non-consented senders, against CLAUDE.md "excluded from everything"; Wrapped reply time uses others' timestamps (D125); consent_priorities.csv is never pruned.
- Do not promote or re-ingest between grading sessions: one grades file can mix methods or corpora; a reworded question keeps its grade. dashboard/ask.py does not check the served model (rerun dashboard-up after tagging with another).
- A revocation plus consent-prune removes stored retrieval, answer and grade rows that cite that sender: rerun evals. A prompt near the token worst case would likely exceed the 600 s timeout.
- tests/synthetic/instagram.py:297 "call ended" does not match dbt/seeds/instagram_placeholder_patterns.csv:11, so synthetic calls count as user text. docs/images/dashboard-synthetic.png is unreferenced.
- Carried: Read(./data/**) does not cover Bash `cat data/...`; P2-5c items (placeholder NAME wildcard, pre-D95 METRICS rows); `gcw tag --no-start` does not verify the llm model; stop-word over-redaction (D82); isolation_guard multi-host URLs; real-ingest-synthetic slow; label files of a purged thread stay; metrics.py external-eligible only; Qwen3.5-4B slower than D90's model; test_llm_server_local fails if llm serves another model; PR 47 chores (ci.yml allowlist, model re-hash check, METRICS placeholder row).

## Suggested next session
Finish the synthetic Part I run, then Ryan's real batch and its METRICS.md rows.
