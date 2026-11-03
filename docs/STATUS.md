Last session: 2026-11-02 — Instagram parser + synthetic generator (provisional format)
Brief completed: yes (all acceptance criteria met locally; CI not run, branch not pushed)
Done:
- Settings split: `Settings` (no required fields) vs `DatabaseSettings` / `get_database_settings()` (required DB vars). `gcw parse` runs with no DB env.
- `ParsedMessage` (pydantic, frozen) + `Reaction`; repr/str show only a key prefix so a stray log can't leak text. Old dataclass + `AttachmentMeta` removed.
- `parsers/instagram_format.py` (all format assumptions), `parsers/instagram.py` (fix_mojibake, message_key, parse_message, parse_export, InstagramParser).
- `tests/synthetic/instagram.py` + `make synth` (also `uv run python tests/synthetic/instagram.py`): 2 threads, multi-file, newest-first, mojibake, photos/shares/reactions, empty and absent content, identical pair per thread, second overlapping export chunked differently (30 vs 17).
- `gcw parse PATH [PATH ...]` prints threads, messages, duplicates dropped, elapsed seconds.
- docs/formats/instagram-schema.md (PROVISIONAL, with sources); decisions.md entry.
Deviations from the brief (what + why):
- `gcw parse` and `parse_export` take one or more roots. Within one export the occurrence index makes keys unique, so duplicates only arise across overlapping exports; multi-root is the only way "duplicates dropped" is meaningful.
- Occurrence counters are per thread across all of its files (per-file would break when an identical pair straddles a chunk boundary).
- `make synth` writes to tests/fixtures/synthetic/ (gitignored). No `make` on this machine, so the target was not executed; the uv command it wraps was.
In progress / broken:
- FORMAT IS PROVISIONAL pending a real-schema reconciliation: run scripts/inspect_export.py on the real export, diff against instagram-schema.md, adjust instagram_format.py and the generator.
- Key edge: two messages differing only by share link (same ms, sender, no other difference) would share a base key; accepted per the dedup-key decision.
- scripts/ still not covered by mypy (carried over).
Next up:
- Ryan: inspector on the real export; reconcile the format module.
- Then loader + raw tables.
Open questions for planning:
- Add `scripts` to mypy `files`? Dedupe the two Privacy sections in CLAUDE.md? (carried over)
- Branch ci-privacy-inspector still unmerged; this work sits on top of it.
Measured this session (exact value + source):
- `pytest -m "not integration"`: 51 passed, 1 deselected, 2026-11-02
- ruff check, ruff format --check, mypy --strict: no issues
- Synthetic seed 1234: export_a = 105 messages, 2 threads; export_a + export_b = 140 messages, 59 duplicates dropped (`gcw parse`)
Commits: none yet (proposed below)
Phase 1 checklist: parser [x] (provisional format) loader [ ] dbt staging [ ] marts [ ] Airflow DAG [ ] dashboard [ ] one-command e2e [ ]
