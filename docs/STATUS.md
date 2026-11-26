# Status
Last session: 2026-11-25 — Trustworthy real eval runs: served-model check, stack isolation, memory guard, invalid reasons (D126, synthetic)

## Phase & state
Phase 3: everything the burst needs is built (D119-D126). This session's synthetic acceptance run did NOT pass: the new guard refused it (swap). What is left is that run, then Ryan's real runs and review.
embeddings [x] retrieval eval [x synthetic] answers, prompt A/B [x synthetic] hand grading [x synthetic] Ask + Wrapped [x] README [x] eval guards [x built] real questions [ ] real evals [ ] real grades [ ] METRICS rows [ ] deploy, usage window (later, D119)

## Done this session
- gcwrapped/: tagging/llamacpp.py `require_served_model` (tag, tag-eval, embed, retrieval-eval, answer-eval, ask, grade-answers, dashboard-up, each Ask page question). docker.py `require_no_other_stack`. New memory.py (retrieval-eval, answer-eval). start_llm `fresh` (answer-eval). answers/ nine invalid reasons, printed by answer-eval and ask. config.py ComposeSettings.
- tests/: new served-model, stack-isolation, memory-guard and eval-guard integration tests; stubs answer /v1/models; conftest.py fakes meminfo and docker ps for every test; local-server served-model tests.
- README, CLAUDE.md (real-container privacy rule, command lines), .env.example and compose comments; deleted docs/images/dashboard-synthetic.png; METRICS rows for a22d573; D126.
- Resolved: wrong server on a taken port; Ask page and `tag --no-start` not checking the model; A/B p95 under memory pressure (D126); D119 wording (D126); unreferenced screenshot; last session's unrun synthetic steps (Ryan, below).

## Deviations from brief
- Model identity: the pinned server's /v1/models `data[].id` is the --alias, not the file name. Expected, as the only entry: llm = the registry id start_llm passes as GCW_LLM_MODEL_ALIAS; embed = the compose alias qwen3-embedding-0.6b-q8_0.
- Task 5: both services already had --no-slots (D89, D102) with static and local-server tests; compose flags unchanged. Pinned --help confirms `--slots, --no-slots` (default enabled).
- A server that is not listening now fails "at model (ProviderUnavailable)" (was answering, embedding or tagging); tests updated. Where there is no docker binary (Airflow tag task, dashboard) the other-stack check does nothing; the Ask page makes the model check only. Compare and promote commands use no model service and check neither.
- Acceptance `make eval-all SYNTHETIC=1` not met (below). B's invalid-reason counts not measured.

## Checks
- Ryan, 2026-11-25, on a22d573 after a WSL restart with swap empty: `make eval-all SYNTHETIC=1` all 7 steps ok; `printf cycycycycy | make grade-answers SYNTHETIC=1 N=5` and `make grade-summary SYNTHETIC=1` ok; `make test-integration` with llm and embed up 271 passed, 0 skipped. answer-compare winner a (citation hit and failure conditions not met; B had 1 invalid output of 31).
- a22d573, this session: make test 1086 passed; dbt build PASS=215; dbt ls counts in METRICS.md.
- Branch: make lint pass; make test 1154 passed, 295 deselected; make test-warehouse 22 passed; make dbt-build PASS=215 WARN=0 ERROR=0 TOTAL=215. make test-integration, llm and embed up: third run 295 passed, 0 skipped. Runs 1 and 2 had 294 passed, 1 failed each (test_load_run_duration_covers_the_parse..., test_tag_promote_sets_the_one_row...); each passed alone; dmesg shows "Time jumped backwards" during the runs.
- `make eval-all SYNTHETIC=1`, 2 attempts: embed ok (223 eligible, 0 embedded), then `retrieval-eval: failed at memory (SwapInUse)`: swap in use at start 296 MiB, then 325 MiB; available 7848, 7882 MiB.
- Dev smoke: `make ask SYNTHETIC=1`, 1 question: answered, all invalid reasons 0, 581 prompt tokens, 20.896 s. Real docker: plain start_llm kept the container, fresh replaced it. GET /slots 501 on 8081 and 8082. CI: see the PR.

## Not verified
- After `wsl --shutdown`: `make eval-all SYNTHETIC=1` should print memory lines for four passes and end `answer-compare: ok`; note B's invalid-reason counts. No eval pass has run under the guard with real models. README quickstart not run from a fresh clone; Mermaid not rendered; grading keys never pressed by hand.
- Real data (Ryan), dev stack down, in order: eval-all, grade-answers, grade-summary, real-ingest, dashboard-up.

## Open issues and questions
- Planning: with the stack down, swap stayed over 256 MiB here (about 7.8 GiB available), and it rose during integration runs with models up (to 594 MiB). Expect a WSL restart before each eval-all; a long real pass may trip SwapGrew. Limits not tuned (D126).
- WSL clock steps backwards. Latest verdict is chosen by wall-clock file name (tagging/evaluation.py:331, retrieval/evaluation.py:429, answers/comparison.py:192), so a step between two verdicts could make promote read the older one.
- A refused or failed pass stores nothing, so its earlier stored run stays: after a failed eval-all, do not run a compare by hand. Other-stack and swap checks are point checks: a stack started mid-run, or swap that rises and falls inside a pass, is not seen. Model identity is the alias only.
- Planning: Activity marts include non-consented senders, against CLAUDE.md "excluded from everything"; Wrapped reply time uses others' timestamps (D125); consent_priorities.csv is never pruned. Do not promote or re-ingest between grading sessions; a reworded question keeps its grade. Revocation plus consent-prune removes stored eval and grade rows: rerun evals. A prompt near the token worst case would likely exceed the 600 s timeout.
- tests/synthetic/instagram.py:297 "call ended" does not match dbt/seeds/instagram_placeholder_patterns.csv:11, so synthetic calls count as user text.
- Carried: Read(./data/**) does not cover Bash (`cat data/...`, or `grep -r` from the root: one such command was blocked this session before it ran); P2-5c items; stop-word over-redaction (D82); isolation_guard multi-host URLs; real-ingest-synthetic slow; label files of a purged thread stay; metrics.py external-eligible only; Qwen3.5-4B slower than D90's model; test_llm_server_local fails if llm serves another model; PR 47 chores.

## Suggested next session
Ryan: restart WSL, run the synthetic eval-all, then the real batch. Next brief: Ask page database credentials and Activity marts.
