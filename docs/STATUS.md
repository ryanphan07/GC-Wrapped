# Status
Last session: 2026-11-17 — P2-5c: one definition of real user text, Instagram placeholders excluded (D95)

## Phase & state
Phase 2 (Enrichment + evals); burst plan in CLAUDE.md. P2-2b (NER gate) is needed only before a hosted provider (D84).
consent ledger [x] two-level opt-in [x] redaction [x] labeling tool [x] synthetic labeled sets [x] consent replies (Ryan, collecting) user-text definition [x] real labels [ ] tagging pipeline [x synthetic] purge unlisted threads [x] eval harness [x synthetic] CI eval (S4b) [ ] real eval + 4B vs 1.5B comparison [ ] profiles [ ]

## Done this session
- docs/decisions.md: D95 verbatim. CLAUDE.md: eligibility line (has_user_text), `make text-audit`. README: one sentence in the labeling bullet.
- dbt: seed `instagram_placeholder_patterns` (19 patterns, schema analytics, no reader grant); table `fct_message_text` (has_user_text, placeholder_pattern_id; wraps every pattern in ^(?:...)$, `~*` on trimmed content); 1 unit test.
- src/gcwrapped: labeling/source.py (ELIGIBLE_SQL joins fct_message_text, so the tagger and tag-eval follow), tagging/runner.py (prune also drops tags of non-user-text), consent_coverage.py (text messages = user text), text_audit.py (new), cli.py, Makefile `text-audit`.
- tests: placeholder_support.py (stand-in view over the real seed), test_placeholder_patterns_integration.py (every template matches only its own pattern, 12 near misses do not), test_text_audit.py and _integration.py (fixed lines, sentinels), placeholder rows in the labeling, tagging, coverage and tag-eval integration tests (not in the index, never sent to the stub, prune on becoming a placeholder, eval drops the label).
- Synthetic: appended thread `placeholders_70192837465019283` (80 messages, one rng, append only; EVAL_THREAD_ID added). ID added to .env.example and the three ci.yml allowlist lines. test_consent.py counts and the earlier-threads digest test updated for the new thread.

## Deviations from brief
- The brief's "Ryan's observed templates" section was an unfilled placeholder ("RYAN'S OBSERVED TEMPLATES GO HERE"), so the seed holds only the brief's list. Templates in it are my recollection of the English export, not checked against real data (docs/formats/instagram-schema.md documents none). Add rows from the audit's residuals.
- Prefix "NAME " is optional on liked/reacted/attachment/story/unsent/call patterns, required on group and poll events.
- fct_message_text is a table, not a view: CI's e2e asserts fct_message_eligibility is the only view in analytics, and the flags depend on content and the seed, not consent. A changed seed row applies at the next dbt build.
- Acceptance e2e ran with `make e2e GCW_THREAD_ALLOWLIST=<six ids>` because the dev .env is Ryan's to edit.

## Checks
- make lint: pass. make test: 795 passed (138 deselected), baseline 789 (#41). make test-integration: 137 passed, 1 skipped (llm server not up), baseline 97. make test-warehouse: 20 passed. (pytest)
- dbt build on gcw_dev via make e2e: PASS=190 WARN=0 ERROR=0 TOTAL=190, baseline 179 (dbt output). First e2e failed on my unit test (uneven expected columns); fixed. CI e2e failed once on the only-view check (fct_message_text was a view); fixed by making it a table.
- make text-audit on gcw_dev (synthetic): messages 440, non-blank 307, placeholders 24 (12 of 19 patterns nonzero), user text 283 (1-3 chars 40, 4-10 48, 11+ 195), emoji-only 12, URL-only 0; text-audit: ok. (command output)

## Not verified
- CI on this PR: watched after push.
- Dev .env: set `GCW_THREAD_ALLOWLIST=1784563920183746,293847561029384,48271936502817364,51928374650192837,61827394501827364,70192837465019283` (adds the last ID), then `make synth`.
- Real data (Ryan): `make real-ingest ENV_FILE=$HOME/gcw-private/real.env`, then `make text-audit ENV_FILE=$HOME/gcw-private/real.env`; paste the fixed lines. Then delete tag_sample.json once (D95); labels are kept.

## Open issues and questions
- src/gcwrapped/consent_coverage.py and tag eval counts change on real data (fewer text messages); METRICS.md rows from before D95 are not comparable.
- The NAME wildcard matches any prefix: "my mom liked a message" is a placeholder. Accepted (D95 tradeoff); a real-data residual check is the audit.
- Carried: `gcw tag --no-start` does not verify the running llm model (/v1/models check); stop-word over-redaction (D82); isolation_guard multi-host URLs; real-ingest-synthetic ~323 s; label files of a purged thread stay; metrics.py reports external-eligible only.

## Suggested next session
Ryan's real text-audit result decides new seed rows; then the model upgrade, S4b CI eval, S5 embeddings.
