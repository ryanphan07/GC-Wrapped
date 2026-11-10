# Status
Last session: 2026-11-10 — P2-2a-close: record the real re-audit, one word definition (D81)

## Phase & state
Phase 2 (Enrichment + evals); Phase 1 complete.
consent ledger [x] redaction [~] (real re-audit passed, D81 in; re-audit after D81 pending; NER eval/gate = P2-2b) tagging pipeline [ ] eval harness + CI eval [ ] real labels + real eval [ ] profiles [ ]

## Done this session
- METRICS.md: real re-audit row (after #33, 98df696) appended verbatim.
- src/gcwrapped/redaction.py: `word_spans` is the one word definition (raw text, isalnum or a combining mark after one). `MemberDirectory.find`/`_add`/`_tokens`, `residual_member_tokens`, `_backstop` (via find) and `_extend_to_words` (was `_extend_to_runs`) all use it; folding is applied per word. The `_Folded` offset map and the `_RUN` regexes are gone: offsets are raw already. Full names match as folded words joined by the same gap (whitespace = any whitespace run).
- tests/test_redaction.py: D81 regressions (symbol cases, full-width and ligature guards, glued-word gap guard, BMP sweep of every word break that folds to an alphanumeric); `pattern_contexts` now returns (text, extra names) instead of appending to `names`, and has ™ ℠ ℡ ㎏ in the glue plus a "symbol" kind.
- docs/decisions.md: D81, D82 (verbatim). CLAUDE.md: git/gh end-of-session steps run as separate commands. Priya™ blind spot removed from Open issues; D80 untouched.

## Deviations from brief
- none (no existing assertion changed; `_extend_to_runs` renamed `_extend_to_words`, no test referenced it).

## Checks
- Regression tests failed on unchanged code (6 of 10): the 4 `name_beside_a_symbol` cases, `ligature_inside_a_name` (with a trailing ™), `every_word_break_that_folds_to_alphanumerics`. The full-width cases and the glued-word guard passed on old code (guards).
- make lint: pass. make test: 512 passed (42 deselected). make test-integration: 42 passed (512 deselected). Baseline before: 502 / 42.
- make synth && make e2e: ok. make redaction-audit (gcw_dev, .env): scanned 170, residual 0, backstop replacements 0, ok.
- CI on this branch: not yet seen. ci.yml unchanged.

## Not verified
- Real data (Ryan): `make redaction-audit ENV_FILE=$HOME/gcw-private/real.env`; expect gcw_real, residual 0, backstop 0, ok.

## Open issues and questions
- Stop-word over-redaction (measured in METRICS 2026-11-10): decided, D82: keep redacting; revisit in P2-3 only if the tagging eval shows a cost.
- Interpretation: backstop 0 on real data means the 2026-11-09 residual tokens were closed at their source; residual 0 is guaranteed by the backstop and is not the signal.
- Names glued to letters/digits and nickname gap: only a labeled eval (P2-2b) measures them.
- One metrics run after test-integration failed "failed at dbt (CalledProcessError)" earlier; not reproduced.
- tests/isolation_guard.py check_database_url: libpq multi-host URLs unhandled.
- Question: real-ingest-synthetic run 3 takes ~323 s; parked.
- Redaction deps are dev-group only; P2-3 must decide where tagging runs.

## Suggested next session
P2-2b (NER eval set, scorer, CIs, D76 gate), then P2-3.
