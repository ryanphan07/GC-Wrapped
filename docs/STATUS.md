# Status
Last session: 2026-11-09 — P2-1 per-sender consent ledger, Phase 2 kickoff

## Phase & state
Phase 2 (Enrichment + evals) started; Phase 1 complete. Consent ledger unticked until CI is green.
consent ledger [ ] redaction [ ] tagging pipeline [ ] eval harness + CI eval [ ] real labels + real eval [ ] profiles [ ]

## Done this session
- src/gcwrapped/ledger.py, cli.py, config.py, Makefile, migrations/002: consent-template, consent-load, raw.sender_consent (D69, D74).
- dbt: source, stg_consent__sender_consent, mart fct_message_eligibility (external_ok), tests, 2 unit tests.
- metrics.py: two consent lines after the load lines. tests/synthetic/instagram.py: make synth writes the ledger.
- isolation_guard.py: GCW_CONSENT_LEDGER rule (additive). conftest.py: gcw_test URL is postgresql:// and guard-checked.
- ci.yml: e2e job runs template/load/eligibility; real-ingest-synthetic asserts zeros. Docs: CLAUDE.md, README, METRICS note, D69-D74.
- Resolved: Ryan's real-data metrics re-run after D67 (ok; lines identical to the 6b36f8f run; tree clean), as reported by Ryan.

## Deviations from brief
- Code is ledger.py (consent.py holds the thread allowlist). Dev ledger: tests/fixtures/synthetic_consent/ (gitignored), not under data/.
- make synth's exports go to tests/fixtures/synthetic but e2e ingests data/landing: I reset gcw_dev (synthetic) and regenerated data/landing.
- Two synthetic participants renamed to SENTINELNAME names so the ledger's sentinel rows exist in the warehouse (else unknown_sender).
- consent-load applies pending migrations after validation (D74); ledger rows exclude reactor-only members (message_count 0).
- Not run: gcw_real temp env file through make (harness denied it). A subprocess test covers the same CLI path.

## Checks
- make lint: pass. make test: 406 passed, 37 deselected. make test-integration: 37 passed. make test-warehouse: 18 passed.
- dbt build (make e2e, .env): PASS=143 WARN=0 ERROR=0 TOTAL=143 (was 125: +2 models, +14 data tests, +2 unit tests).
- Chain synth, e2e, template x2, load x2, e2e, metrics: all exit 0. Template: 0 new rows, file unchanged. Load x2: 9 rows, yes 6, no 2, blank 1, identical.
- make metrics (gcw_dev): consent senders yes 6; external-eligible 97 = 17+10+20+20+21+9 messages of yes senders; buildable 143; pytest 406 + 55.
- 5 bad-ledger cases via make: exit 1, allowed shapes only, no sentinel, table unchanged.

## Not verified
- CI on this branch (e2e consent step; buildable == TOTAL). Refusal via make (see Deviations). Real-data step: see the PR.

## Open issues and questions
- One metrics run right after test-integration failed "failed at dbt (CalledProcessError)"; 8 later runs passed. Cause not found (dbt output captured).
- tests/isolation_guard.py check_database_url: libpq multi-host URLs unhandled; reject as unparsable if touched again.
- Question: real-ingest-synthetic run 3 takes ~323 s (likely Airflow retries on the failure path); parked.

## Suggested next session
P2-2: redaction module (D70). Tick the consent ledger once CI is green and Ryan's real-data step works.
