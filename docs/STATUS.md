# Status
Last session: 2026-11-13 — P2-3b (burst, between S2 and S3): two-level opt-in consent (D88), local eligibility, consent coverage report

## Phase & state
Phase 2 (Enrichment + evals); burst plan in CLAUDE.md. P2-2b (NER gate) is needed only before a hosted provider (D84).
consent ledger [x] two-level opt-in [x] redaction [x] labeling tool [x] synthetic labeled sets [x] consent replies (Ryan, collecting) real labels [ ] tagging pipeline [ ] eval harness + CI eval [ ] real eval [ ] profiles [ ]

## Done this session
- docs: D88 verbatim; METRICS.md row (11a9753, the #37 squash). CLAUDE.md: privacy line, Burst plan order, consent-coverage command. README: "two gates" bullet now the two opt-in levels, labeling bullet "senders who opted in".
- src/gcwrapped/ledger.py: values yes/local/no/blank (else bad_value, all-or-nothing as before); consent-load prints "consented (local): N" after the yes line.
- migrations/003_consent_local.sql: replaces the CHECK on raw.sender_consent to allow "local" (002 untouched).
- dbt: fct_message_eligibility adds local_ok; staging accepted_values has the four values; unit tests: yes = both, local = local_ok only, no/blank/missing/other-thread = neither, empty ledger = neither.
- src/gcwrapped/labeling/source.py: local_ok instead of external_ok.
- src/gcwrapped/consent_coverage.py, cli.py, Makefile: `make consent-coverage` (database guard, label-dir guard, 15 fixed lines, shape-checked). Priority list `consent_priorities.csv` (rank, thread_id, thread_title, sender_name, text_messages, cumulative_share, consent) written atomically, 0600, to GCW_LABEL_DIR only, never printed.
- tests/synthetic/instagram.py: "José Núñez" (weekend thread, not in the eval thread) is now "local".
- Tests: test_consent_coverage.py (+integration), ledger/migrate/labeling tests adapted.

## Deviations from brief
- New migration 003: the table's CHECK (002) rejected "local", so a schema change was unavoidable; consent-load applies it via migrate like 002.
- Coverage reads the loaded raw.sender_consent and fct_message_eligibility (not the CSV). "Senders" are (thread, display name) pairs; the consent column adds "missing" for a sender with no ledger row. "Text message" = non-blank content (labeling's rule). "M" in "N of M threads" = all threads in raw.messages. The ranking includes already-consented senders (their consent shows in the file).
- Synthetic counts that changed: dbt build PASS 143 -> 144 (one new not_null test, local_ok); consent-load on gcw_dev now has "consented (yes): 9, (local): 1" (was 10 yes); migrations applied 2 -> 3.
- ci.yml unchanged: its yes/external_ok checks still hold because external_ok is unchanged.

## Checks
- make lint: pass. make test: 665 passed (50 deselected), baseline 641. make test-integration: 50 passed (665 deselected), baseline 46. (pytest output)
- gcw_dev: make synth && make e2e twice, dbt build PASS=144 ERROR=0 (airflow task log); second run rows inserted 0, parsed 360. consent-load: ledger rows 15, yes 9, local 1, no 3, blank 2. consent-coverage: text messages 253, local-eligible 193, external-eligible 180, threads 5 of 5, senders for 50/80/90%: 4/9/11; priority file mode 600.
- A ledger value outside the four ("Local"): consent-load exits 1 with "ledger error: bad_value at line 2", table unchanged (counts identical afterwards). Run through ledger.run_load, because .env's GCW_CONSENT_LEDGER wins over an environment override.

## Not verified
- CI on the PR. Real data: the step below.
- Existing real databases get migration 003 on the next consent-load (it calls migrate).

## Open issues and questions
- Stop-word over-redaction (D82); glued names and nicknames: only P2-2b measures them.
- tests/isolation_guard.py check_database_url: libpq multi-host URLs unhandled. real-ingest-synthetic run 3 ~323 s; parked.
- Redaction deps are dev-group only; S3 must decide where tagging runs. Revoked consent leaves old label rows (D87); eval must drop them.
- metrics.py still reports external-eligible only; add local-eligible if wanted.

## Suggested next session
S3: tagging pipeline on synthetic data with a local llama.cpp backend (hosted fails closed, D84), selecting messages with local_ok, importing gcwrapped.labeling.schema and source.
