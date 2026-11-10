# Status
Last session: 2026-11-09 — P2-2a-fix: close residual member-token leaks (D79, D80)

## Phase & state
Phase 2 (Enrichment + evals); Phase 1 complete.
consent ledger [x] redaction [~] (leak fixes in; real-data re-audit pending; NER eval/gate = P2-2b) tagging pipeline [ ] eval harness + CI eval [ ] real labels + real eval [ ] profiles [ ]

## Done this session
- src/gcwrapped/redaction.py: `_choose` now merges overlapping spans into their union (type of the best-ranked) and extends every edge inside a word to the word's edge, repeating until stable; new `Redactor._backstop` (placeholders masked, 3 passes, `RedactionError` if a token survives); `RedactedMessage` gained 4 integer counters; comments in `_choose` corrected.
- src/gcwrapped/redaction_audit.py: 8 new fixed-shape lines (merges, extensions, backstop total/adjacent/standalone, directory tokens, stop-word tokens, stop-word matches); exit rule unchanged.
- tests: M1/M2 regressions, forced-residual backstop tests, raise-after-3-passes, placeholder-word test, new property test (names in/beside handles, URLs, emails, non-ASCII, overlapping names; derandomized), audit shape/sentinel tests.
- ci.yml (assert "backstop replacements: 0"), Makefile `sync`, CLAUDE.md, README (setup + privacy bullet), METRICS.md (real failed audit + pytest baseline), decisions.md D79 (verbatim), D80.

## Deviations from brief
- M1 reproduced only for the handle-vs-full-name case and a forced score-1.0 overlap (card); the URL and email variants did not fail on old code (kept as tests). D79 rule covers them anyway.
- The 'ñ'-before-token M2 case "@priyañsentinelnameb" passed on old code (leftover folds to a non-token); kept as a guard.
- `member-name tokens remaining: 0` was already asserted in ci.yml; only the backstop line was added.
- Extra: `holds_name` now compares against raw person results, not merged groups (D80.3).

## Checks
- Regression tests failed on unchanged code (5 of 8: m1 handle, m1 forced card, 3 m2 cases), pass now.
- make lint: pass. make test: 502 passed. make test-integration: 42 passed. Baseline on main 5a9907f: 485 / 42.
- make synth && make e2e: ok. make redaction-audit (gcw_dev, .env): scanned 170, residual 0, backstop replacements 0, merges 0, extensions 0, directory tokens 14, ok.
- make sync: ok; `import dbt, streamlit` ok. Airflow export identical to main's.
- CI on this branch: not yet seen.

## Not verified
- Real data (Ryan): `make redaction-audit ENV_FILE=$HOME/gcw-private/real.env`; expect residual 0 and read the backstop lines. CI audit step.

## Open issues and questions
- redaction.py: "Priya™"-style chars (fold to letters but not isalnum) are a blind spot of extension and backstop (D80).
- One metrics run after test-integration failed "failed at dbt (CalledProcessError)" earlier; not reproduced.
- tests/isolation_guard.py check_database_url: libpq multi-host URLs unhandled.
- Question: real-ingest-synthetic run 3 takes ~323 s; parked.
- Question: nickname/misspelling gap stays until a real audit or P2-2b shows its size.
- Redaction deps are dev-group only; P2-3 must decide where tagging runs.

## Suggested next session
Ryan's real re-audit; then P2-2b (NER eval set, scorer, CIs, D76 gate), then P2-3.
