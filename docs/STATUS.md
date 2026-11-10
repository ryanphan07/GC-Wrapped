# Status
Last session: 2026-11-10 — P2-2a-union: words under two segmentations, used as a union (D83)

## Phase & state
Phase 2 (Enrichment + evals); Phase 1 complete. P2-3 is BLOCKED until Ryan's real rerun meets the pass rule below.
consent ledger [x] redaction [~] (D83 in; real rerun pending; NER eval/gate = P2-2b) tagging pipeline [ ] eval harness + CI eval [ ] real labels + real eval [ ] profiles [ ]

## Done this session
- METRICS.md: real audit row after #34 (coverage regressed) appended verbatim. docs/decisions.md: D83 verbatim.
- src/gcwrapped/redaction.py: `words()` returns raw and folded words (folded = pre-D81 rule, offsets mapped to raw, glue read from folded text); `all_words` is their deduped union. Tokens, `_add` (both segmentations, multi-word names too), `find` (both, overlaps merge, longest match's identities, tie = union), `residual_member_tokens` (distinct spans), backstop (via find) and `_extend_to_words` (repeat until stable) use it. `MemberDirectory` exposes raw_only/folded_only tokens with a source category.
- src/gcwrapped/redaction_audit.py: 11 new fixed lines (35 + ok printed).
- tests/test_redaction.py: D83 regressions, mirror BMP sweep, decorated_names strategy (display_names uses it), decorated-mention property, reference superset property, src-never-imports-reference test. tests/reference_redaction_98df696.py: frozen copy. tests/test_redaction_audit.py: shape, category, sentinel tests.
- README privacy bullet: one sentence (D83). The ⑴ concern is closed by D83.

## Deviations from brief
- Audit prints 35 fixed lines plus the ok line (36 lines); the brief's "36 fixed lines" counted ok.
- Mirror sweep tests a name before the character only if its fold starts with a non-alphanumeric, and after it only if the fold ends with one. "priya½" folds to "priya1/2" (glued to a digit): the documented gap (D81/D83), unmatchable by any union.
- One assertion changed: test_new_lines_have_fixed_shapes now slices `lines[-19:-11]` (new lines follow the old ones) and corrupts `lines[-13]`.
- Gap matching: whitespace in a name's gap matches any whitespace run under both segmentations (raw was equality for non-space glue before).

## Checks
- Regression tests failed on unchanged main (4 of 4): enclosed-letter name + plain mention, name ending ⑵ + plain mention, "<name>⑴", mention in enclosed letters. Guards (Priya™, D81 BMP sweep) passed there.
- make lint: pass. make test: 524 passed (42 deselected). make test-integration: 42 passed (524 deselected). Baseline: 512 / 42.
- make synth && make e2e && make redaction-audit (gcw_dev, .env): scanned 170, residual 0, backstop 0, ok; folded-only lines all 0; redaction seconds 0.56 (source: audit output).
- CI: not yet seen. ci.yml unchanged.

## Not verified
- Real data (Ryan): `make redaction-audit ENV_FILE=$HOME/gcw-private/real.env`. Pass rule: gcw_real, residual 0, backstop 0, ok, messages changed >= 9375, directory tokens >= 599. Paste the fixed lines only.

## Open issues and questions
- Stop-word over-redaction: D82, revisit in P2-3 only if the tagging eval shows a cost.
- Backstop 0 on real data means mechanisms were closed at source; residual 0 is not the signal.
- Names glued to letters/digits (incl. "name½") and nickname gap: only P2-2b measures them.
- tests/isolation_guard.py check_database_url: libpq multi-host URLs unhandled.
- real-ingest-synthetic run 3 takes ~323 s; parked.
- Redaction deps are dev-group only; P2-3 must decide where tagging runs.

## Suggested next session
Ryan's real rerun; then P2-2b
