Last session: 2026-11-02 — Key fix (share_url), mypy covers scripts, Privacy merge, merge to main
Brief completed: yes (CI/merge result recorded under Commits)
Done:
- Committed the parser work (10aecd5).
- share_url added to message_key (and the identity used for occurrence counting); new test: base/share-A/share-B messages get distinct keys. Existing tests cover reactions not changing the key and keys stable across overlapping exports.
- `scripts` added to mypy `files`; no new errors.
- CLAUDE.md: two Privacy sections merged into one (every distinct rule kept).
- decisions.md entry appended.
Deviations from the brief (what + why):
- None. Reactions-ignored test already existed (test_key_ignores_reactions_and_source_file); kept rather than duplicated.
In progress / broken:
- FORMAT IS PROVISIONAL pending a real-schema reconciliation: run scripts/inspect_export.py on the real export, diff against instagram-schema.md, adjust instagram_format.py and the generator.
- Message keys changed (share_url now hashed); nothing persisted yet, so no migration.
Next up:
- Ryan: inspector on the real export; reconcile the format module.
- Then loader + raw tables.
Open questions for planning:
- None.
Measured this session (exact value + source):
- `pytest -m "not integration"`: 52 passed, 1 deselected, 2026-11-02
- ruff check, ruff format --check, mypy --strict (incl. scripts): no issues
Commits: 10aecd5 — Add Instagram parser, synthetic generator and gcw parse command; (key fix commit + merge commit: see PR)
Phase 1 checklist: parser [x] (provisional format) loader [ ] dbt staging [ ] marts [ ] Airflow DAG [ ] dashboard [ ] one-command e2e [ ]
