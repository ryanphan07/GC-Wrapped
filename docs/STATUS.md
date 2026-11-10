# Status
Last session: 2026-11-10 — P2-3a (burst S2): labeling tool, synthetic labeled sets, burst plan, $0 local-only decision

## Phase & state
Phase 2 (Enrichment + evals); burst plan in CLAUDE.md. P2-2b (NER gate) is needed only before a hosted provider (D84).
consent ledger [x] redaction [x] labeling tool [x] synthetic labeled sets [x] real labels [ ] (Ryan) tagging pipeline [ ] eval harness + CI eval [ ] real eval [ ] profiles [ ]

## Done this session
- docs: D84-D86 verbatim, D87 (details they leave open). CLAUDE.md: phase, stack, scope, API line match D84; Burst plan; label-* commands. README: labeling bullet, quickstart uses `make synth`. .env.example: GCW_LABEL_DIR.
- src/gcwrapped/labeling/: schema (constants S3 imports), source (eligible SQL, context), sampling, files (writers, loader), flows, terminal, summary, commands. config.py LabelSettings; cli.py three subcommands.
- Makefile: label-tags/-questions/-summary; synth-landing; `make synth` also replaces data/landing; `make e2e` regenerates it first on the default .env only.
- tests/synthetic: 5th thread evalset_61827394501827364 appended (earlier histories pinned by digest), eval_set.py, write_eval_fixtures.py; tests/fixtures/eval/*.csv.
- New tests: eval fixtures + loader, sampling, flows, commands (guards, sentinel), terminal (pty), Makefile dry run, integration. test_consent.py counts for 5 threads.
- .env.example and ci.yml lines 42, 100, 162: new ID in the allowlist; nothing else in ci.yml.
- Resolved: the stale dev landing (#36) is prevented by construction.

## Deviations from brief
- Task 7: no target wrote data/landing (`make synth` wrote tests/fixtures/synthetic only). Per Ryan, `make synth` now also replaces data/landing and `make e2e` regenerates it first. Staleness is prevented by construction, not detected by comparison: no test or tool may read or hash data/ (CLAUDE.md, no exception), so tests read `make -n` output.
- e2e regenerates only on the default .env: a real env file may leave its landing at ./data/landing (real_ingest.py) and regeneration deletes what is there.
- CLAUDE.md edits beyond the brief: the "no external API calls" line (stale against D84) and the label-* command line. Added D87 and `question_skips.csv` (message_id, skipped_at; resume must not re-show skips). Sentiment keys g/n/b.
- "tags labeled: n of N" uses the frozen sample size (193 on gcw_dev, pool under 200). Sender names are not shown in flows (D87).

## Checks
- make lint: pass. make test: 641 passed (46 deselected), baseline 529. make test-integration: 46 passed (641 deselected), baseline 42. (pytest output)
- gcw_dev (.env, allowlist overridden on the command line): make e2e twice; dbt build PASS=143 ERROR=0 (airflow task log); second run rows inserted 0, parsed 360. redaction-audit: scanned 360, member-name tokens remaining 0, backstop 0.
- gcw_dev scripted runs, label dir outside the repo: label-tags 5 then resumed to 6; label-questions 1 answered (skip then undo); label-summary printed the 19 fixed lines. Label dir inside the repo: non-zero, fixed message.

## Not verified
- CI on the PR. Hand use in a real terminal (tested via pty and pipes).
- Dev .env (gitignored, not edited): `GCW_THREAD_ALLOWLIST=1784563920183746,293847561029384,48271936502817364,51928374650192837,61827394501827364`.
- Real data: the labeling run below; needs consent-load and a dbt build on the real stack first.

## Open issues and questions
- Stop-word over-redaction (D82); glued names and nicknames: only P2-2b measures them.
- tests/isolation_guard.py check_database_url: libpq multi-host URLs unhandled. real-ingest-synthetic run 3 ~323 s; parked.
- Redaction deps are dev-group only; S3 must decide where tagging runs. Revoked consent leaves old label rows (D87); eval must drop them.

## Suggested next session
S3: local llama.cpp backend (hosted fails closed, D84) and tagging pipeline, importing gcwrapped.labeling.schema and source. S4 needs Ryan's labels.
