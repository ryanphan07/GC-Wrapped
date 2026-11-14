# Status
Last session: 2026-11-14 — P2-3c: purge threads removed from the allowlist (D91)

## Phase & state
Phase 2 (Enrichment + evals); burst plan in CLAUDE.md. P2-2b (NER gate) is needed only before a hosted provider (D84).
consent ledger [x] two-level opt-in [x] redaction [x] labeling tool [x] synthetic labeled sets [x] consent replies (Ryan, collecting) real labels [ ] tagging pipeline [x synthetic] purge unlisted threads [x synthetic] eval harness + CI eval [ ] real eval [ ] profiles [ ]

## Done this session
- docs/decisions.md: D91 verbatim. CLAUDE.md: `make purge-unlisted [CONFIRM=1]` under Commands; end-of-session step 6 (watch CI, at most 2 fixes). README: one privacy bullet.
- src/gcwrapped/purge.py (new), cli.py, Makefile: `purge-unlisted`. Guards: ALLOWED_DATABASES, non-empty allowlist, and a catalog check that refuses if raw/enrich has a message_key/thread_id table it does not cover. One transaction deletes tags, consent rows, messages (reactions are a jsonb column of raw.messages); dry run runs the same deletes and rolls back. Fixed, shape-checked lines.
- src/gcwrapped/ledger.py: consent-load skips well-formed rows of non-allowlisted threads and prints `rows skipped (thread not allowlisted): N`. `unknown_thread` error kind removed (no longer produced).
- Tests: tests/test_purge_integration.py (new, 9), test_ledger.py and test_ledger_integration.py adapted and extended.

## Deviations from brief
- Acceptance run on gcw_dev used `ENV_FILE=.env` with `GCW_THREAD_ALLOWLIST=<4 of 5 IDs>` on the make command line, not a temporary env file: CLAUDE.md forbids any non-.env ENV_FILE. Afterwards `make e2e` + `make consent-load` restored gcw_dev; its 88 tags (S3) are not restored.
- The brief's "reactions" table does not exist: reactions are a jsonb column on raw.messages, so they are counted from the deleted messages.
- Tag rows are deleted explicitly (children first) though the FK already cascades, so the tags count is reported.

## Checks
- make lint: pass. make test: 723 passed (80 deselected), baseline 722. make test-integration: 79 passed, 1 skipped (llm server not up), baseline 69. make test-warehouse: 20 passed. (pytest output)
- dbt build on gcw_dev with the 4-ID allowlist after the purge: PASS=175 WARN=0 ERROR=0 (dbt output).
- gcw_dev purge: dry run 1 thread, 120 messages, 55 reactions, 88 tags, 3 consent rows, nothing deleted (repeated dry run identical); CONFIRM=1 deleted those; second run 0; empty allowlist refused (`failed at allowlist (AllowlistError)`).

## Not verified
- CI on this PR: see the PR checks (watched at commit time).
- Real data: Ryan's step below. Run: back up real.env, remove IDs, dry run, paste; after planning confirms, CONFIRM=1 then real-ingest; paste fixed lines.

## Open issues and questions
- Stop-word over-redaction (D82); glued names and nicknames: only P2-2b measures them.
- tests/isolation_guard.py check_database_url: libpq multi-host URLs unhandled. real-ingest-synthetic run ~323 s; parked.
- Revoked consent leaves old label rows (D87); eval must drop them. Tags are pruned (D89).
- Label files under GCW_LABEL_DIR for a purged thread are outside the warehouse and stay (Ryan's to delete).
- metrics.py still reports external-eligible only. A real stack needs its own GCW_LLM_PORT if the dev llm is up at the same time.

## Suggested next session
S4: tagging eval harness on the synthetic set (win rule fixed in advance, compare with a smaller model); real eval waits for the real-data batch.
