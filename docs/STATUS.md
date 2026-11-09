# Status
Last session: 2026-11-09 — P2-1b eligibility view, Docker preflight, METRICS rows

## Phase & state
Phase 2 (Enrichment + evals); Phase 1 complete.
consent ledger [x] redaction [ ] tagging pipeline [ ] eval harness + CI eval [ ] real labels + real eval [ ] profiles [ ]

## Done this session
- Resolved: Ryan's P2-1 real-data step (2026-11-09, as reported by Ryan): template 583 rows (new 583, kept 0, stale 0), modes 700/600, consent-load applied 002 and loaded 583 (yes 0, no 0, blank 583), real-ingest ok with dbt PASS=143 TOTAL=143, metrics yes 0 / eligible 0 / buildable 143, tree clean; CI on PR #30 green. Consent ledger ticked.
- dbt: fct_message_eligibility is a view (D75). dbt_project.yml: reader grants listed per mart (dbt merges model-level grants into the folder's, so the view could not opt out); new marts get no grant until listed.
- src/gcwrapped/docker.py (new): require_docker (`docker info`, output discarded, 30 s), DockerUnavailableError, start_database (moved from metrics.py; ledger.py no longer imports metrics for it).
- real_ingest.py: preflight before the log is created; prints "real-ingest: failed at docker (DockerUnavailableError)". `make e2e` starts compose itself, so this is its Docker path.
- ci.yml: reader denied on the view; analytics holds exactly one view; freshness step (flip one yes sender in a ledger copy, consent-load, no build, count drops by that sender's messages, metrics matches).
- Tests: tests/test_docker.py, plus cases in test_ledger.py and test_real_ingest.py; isolation-guard symlink test removes a stale link first.
- CLAUDE.md: phase line without D-range; session-end rule 6 (PR body carries the "Run in your terminal" block). README, METRICS rows (dbt 11/125/6/143, pytest 406+55 from main 468bca3), D75.

## Deviations from brief
- Grants: `grants={'+select': []}` in the model did not clobber the folder grant (verified with dbt ls), so grants moved to per-model entries in dbt_project.yml.
- CI's "no views in analytics" check now expects exactly fct_message_eligibility.
- Freshness: CI only. No integration test, because the view is built by dbt and a test copy would duplicate its definition. I ran the CI step's script locally against gcw_dev with the default .env (ledger override via make variable): eligible 97 -> 80, metrics 80; ledger reloaded afterwards.

## Checks
- make lint: pass. make test: 415 passed, 37 deselected. make test-integration: 37 passed. make test-warehouse: 18 passed.
- make e2e (.env): dbt PASS=143 WARN=0 ERROR=0 TOTAL=143, view in analytics, gcw_reader denied (psql), 6 marts granted.
- make metrics (gcw_dev, end of session): yes 6, eligible 97, buildable 143, pytest 415 + 55.
- Docker-down path: stubbed subprocess and a fake docker binary only (Docker not stopped).

## Not verified
- CI on this branch (freshness step, reader denial on the view). Real-data step: see the PR.

## Open issues and questions
- One metrics run right after test-integration failed "failed at dbt (CalledProcessError)" earlier; not reproduced. Cause not found.
- tests/isolation_guard.py check_database_url: libpq multi-host URLs unhandled; reject as unparsable if touched again.
- Question: real-ingest-synthetic run 3 takes ~323 s; parked.

## Suggested next session
P2-2: redaction module (D70). Tagging (P2-3) must read eligibility at send time per batch (D75).
