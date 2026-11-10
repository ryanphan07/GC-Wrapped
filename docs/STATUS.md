# Status
Last session: 2026-11-10 — P2-2a close-out: real audit recorded, glued-name guard, synthetic decorated names

## Phase & state
Phase 2 (Enrichment + evals); Phase 1 complete. P2-2a is done (real audit met the pass rule). P2-2b (NER eval and D76 gate) is required only before any external model call.
consent ledger [x] redaction [x] (P2-2a; NER eval/gate = P2-2b, before any external call) tagging pipeline [ ] eval harness + CI eval [ ] real labels + real eval [ ] profiles [ ]

## Done this session
- METRICS.md: real audit row after #35 appended verbatim (commit e4cf89e); lockfile confirms en-core-web-lg 3.8.0, presidio-analyzer 2.2.366.
- tests/test_redaction.py: glued-name tests (⑴, ⒈, ™; sweep over every char whose fold starts and ends non-alphanumeric) with a spy on the spans handed to replacement (non-overlapping).
- tests/synthetic/instagram.py: 4th thread (decorated_51928374650192837): members in enclosed letters, ending "™", ending "⑵"; messages mention each in plain letters. `build_conversation` takes optional `texts`; earlier threads' histories are unchanged.
- tests/test_redaction_audit.py: stale ™ comment fixed; new test that the decorated thread gives raw-only and folded-only tokens.
- .env.example and .github/workflows/ci.yml: new thread ID added to the allowlist.

## Deviations from brief
- Glued-name tests PASSED on unchanged main (guards, not a bug): no redaction.py change, no decision added.
- Sweep excludes "⒈" (folds to "1.", starts alphanumeric); it is covered by the explicit parametrized test. Sweep covers 45 characters.
- Allowlist edits approved by Ryan: ci.yml lines 42, 100, 162 and .env.example line 46 (new ID 51928374650192837 appended). Nothing else in ci.yml changed.
- CI e2e root cause (PR #36 runs 42819080491, 42819103532): the "Consent ledger" step failed at .github/workflows/ci.yml:214 (`new rows added: 0`; got 1). `consent_ledger_rows` (tests/synthetic/instagram.py:~336) only listed senders in the overlap of export_a and export_b, but e2e ingests both, so the decorated thread's third member (SENTINELDECB™, written only outside the overlap) was in the warehouse with no ledger row. Fix: ledger now lists every sender in the full history (12 rows, was 11); with one export ingested the extras are only stale. No privacy check, ci.yml step or redaction.py touched.
- Local dev landing (data/landing) predated the 4th thread, so make e2e ingested nothing new; regenerated with `instagram.py --out data/landing` (synthetic only), then e2e inserted 30 rows.
- tests/test_redaction.py _check_glued: assertion tightened to `len(redactor.handed) == len(texts)`.
- Tests adapted for the 4th thread: tests/test_consent.py (ID unpacking, shape/allowlist counts in two tests), tests/test_env_file.py (ID unpacking).

## Checks
- make lint: pass. make test: 529 passed (42 deselected). make test-integration: 42 passed (528 deselected). Baseline 524 / 42. (Counts from pytest output.)
- make synth && make e2e && make redaction-audit (gcw_dev, .env, after fix): member-name tokens remaining 0, backstop replacements 0, redaction-audit: ok; directory tokens raw-only: 2; directory tokens folded-only: 3; messages with a match on a folded-only token: 7. make consent-template: new rows added: 0. make lint test: 529 passed; make test-integration: 42 passed.

## Not verified
- CI rerun on PR #36 after this push.
- Real data: none (redaction.py unchanged).

## Open issues and questions
- Stop-word over-redaction: D82, revisit in P2-3 only if the tagging eval shows a cost.
- Names glued to letters/digits (incl. "name½") and nickname gap: only P2-2b measures them.
- tests/isolation_guard.py check_database_url: libpq multi-host URLs unhandled.
- real-ingest-synthetic run 3 takes ~323 s; parked.
- Redaction deps are dev-group only; P2-3 must decide where tagging runs.

## Suggested next session
Planning chat sets the burst plan.
