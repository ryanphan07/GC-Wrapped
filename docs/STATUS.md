Last session: 2026-11-02 — CI integration checks, privacy hardening, export schema inspector
Brief completed: yes (all acceptance criteria met; CI verified on GitHub)
Done:
- CI `integration` job: pgvector/pgvector:pg17 service, runs docker/postgres/init/01-create-databases.sql, `make test-integration`, `make dbt-debug` (dbt group synced). Service starts on db `postgres` so the init SQL runs unmodified (it does CREATE DATABASE gcw_dev).
- .gitattributes `* text=auto eol=lf` + `git add --renormalize .`; `git ls-files --eol` shows no CRLF in the index.
- METRICS.md: Pipeline, dbt, Tagging eval, Retrieval eval, Serving, Usage tables (value | date | how measured | commit; a "metric" column was added first for row labels).
- CLAUDE.md Privacy section and 4 decisions.md entries added.
- .claude/settings.json: kept the existing data/ deny rules; added Read/Edit/Write on ~/gcw-private/**, Bash(*gcw-private*), Bash(*gcw_real*).
- scripts/inspect_export.py + tests/test_inspect_export.py (5 tests, sentinel leak test).
Deviations from the brief (what + why):
- METRICS tables have an extra leading "metric" column; the brief's four columns alone have no row label.
- decisions.md entries use `###` headings (brief used `##`) to match the file's append-only format.
- Inspector also treats a few extra things as safe-by-allowlist: non-thread folder names and file stems must match ^[a-z_]+$ (else `<dir>` / A_N shapes); thread folders are detected as dirs containing message_N.json. Mojibake signature also includes `â[\x80-\x9f]` (smart quotes/dashes).
- Work is on branch `ci-privacy-inspector`, not main (pushed to trigger CI). Not merged.
In progress / broken:
- docs/formats/instagram-schema.md (referenced by new CLAUDE.md rule) does not exist yet; Ryan generates it by running the inspector on the real export.
- scripts/ is not covered by `make lint` mypy (pyproject `files = src, tests`); I ran `mypy scripts/inspect_export.py` by hand (clean). Settings change was out of scope.
- CLAUDE.md has one pre-existing Privacy rules section overlapping the new one; left both.
Next up:
- Ryan: `uv run python scripts/inspect_export.py ~/gcw-private/<export>` in his own terminal; review the output for anything value-like before saving it to docs/formats/instagram-schema.md.
- Merge the branch; then parser session (Ryan writes it) + synthetic generator.
Open questions for planning:
- Add `scripts` to mypy `files` so lint covers it?
- Dedupe the two Privacy sections in CLAUDE.md?
Measured this session (exact value + source):
- `pytest -m "not integration"`: 12 passed, 1 deselected, 2026-11-02
- ruff check / ruff format --check / mypy --strict: no issues
- GitHub Actions run 41585188494 (commit 92d2f71): lint-test success, integration success (init SQL, pytest -m integration, dbt debug all passed)
- Sentinel test verified to fail when a file's contents were deliberately printed; reverted.
Commits: 92d2f71 — Add CI integration steps, privacy hardening and export schema inspector
Phase 1 checklist: parser [ ] loader [ ] dbt staging [ ] marts [ ] Airflow DAG [ ] dashboard [ ] one-command e2e [ ]
