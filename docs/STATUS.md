# Status
Last session: 2026-11-09 — P2-2a redaction module and redaction-audit

## Phase & state
Phase 2 (Enrichment + evals); Phase 1 complete.
consent ledger [x] redaction [~] (module + audit built; NER eval/gate = P2-2b) tagging pipeline [ ] eval harness + CI eval [ ] real labels + real eval [ ] profiles [ ]

## Done this session
- src/gcwrapped/redaction.py (new): MemberDirectory, Redactor.redact_thread, residual_member_tokens, load_model/RedactionModelError. Presidio + member-name/@handle/address/lenient-phone recognizers; own span replacement; per-call pseudonyms never returned; no network (sockets blocked in tests).
- src/gcwrapped/redaction_audit.py (new), cli.py (lazy import), Makefile `redaction-audit`, log.py (Presidio loggers at ERROR).
- pyproject/uv.lock: `redaction` group (presidio-analyzer 2.2.366, spacy 3.8.18, en_core_web_lg 3.8.0 wheel URL) included by `dev`; hypothesis 6.170.15 in dev. Airflow export verified to omit them.
- Tests: tests/test_redaction.py (incl. hypothesis property), test_redaction_audit.py, test_redaction_audit_integration.py.
- ci.yml: uv cache enabled on every setup-uv; e2e job "Redaction audit" step. README privacy bullet, CLAUDE.md (privacy rule, command). decisions.md: D76, D77 verbatim, D78.

## Deviations from brief
- Member directory read from raw.messages (senders + reaction actors), not analytics.dim_members: same facts, no dbt dependency (D78).
- Added a lenient phone regex, a person-span merge and a "pattern containing a name wins" rule: the property test and a handle case showed leaks without them (D78).
- Ambiguity (D77.4) judged within the thread (D78).
- Dependencies are in a group under `dev`, not runtime deps, so the Airflow image is unchanged (D78).

## Checks
- make lint: pass. make test: 485 passed, 41 deselected. make test-integration: 41 passed. make test-warehouse: 18 passed.
- make synth && make e2e (.env): exit 0. make redaction-audit (gcw_dev): scanned 170, with text 117, changed 10, person 29, other types 0, residual 0, 0.56 s, "redaction-audit: ok". make metrics raw messages: 170 (equal).
- CI on this branch: not yet seen.

## Not verified
- CI (new audit step, uv cache, model install time). Real data: `make redaction-audit ENV_FILE=$HOME/gcw-private/real.env`.

## Open issues and questions
- One metrics run right after test-integration failed "failed at dbt (CalledProcessError)" earlier; not reproduced.
- tests/isolation_guard.py check_database_url: libpq multi-host URLs unhandled; reject as unparsable if touched again.
- Question: real-ingest-synthetic run 3 takes ~323 s; parked.
- Question: nickname/misspelling gap stays until a real-data audit or P2-2b shows its size.

## Suggested next session
P2-2b: NER eval set, scorer with bootstrap CIs, baseline and CI gate (D76). Then P2-3 tagging.
