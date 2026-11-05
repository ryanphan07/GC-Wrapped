Last session: 2026-11-04 — Reconcile parser and generator to the real Instagram format
Brief completed: partial. `make lint test` passes (131 tests). NOT verified: `make e2e` on a rebuilt dev DB and its second-run row counts, and `make test-integration` (Docker daemon was not running in this session).
Done:
- docs/formats/instagram-schema.md committed unmodified (sha256 1de5bf2d...b20 verified before and after).
- docs/formats/reconciliation.md: assumption vs. real vs. code table, mapped vs. ignored fields, inbox-only note (closes the old open question about siblings of inbox).
- instagram_format.py: real constants, thread_id() = trailing digits (raises ValueError without the folder name), key_basename(), thread_folder_shape(), unmapped-key names, `sticker` removed, banner removed.
- instagram.py: message_key hashes attachment basenames (export URIs only); _Identity uses the same inputs; media_count counts external URIs too.
- Generator: <name>_<15-17 digits> and bare-digit folders, extensionless photos, external video URLs (query differs per export), newest-first chunking (max_per_file overridable), Vietnamese/emoji mojibake, every unmapped key, optional fields missing on some, export b renames one folder and chunks differently.
- `make parse-check EXPORT_DIR=<path>` (`gcw parse-check`): counts and exception class names only; new module parse_check.py, production parse path untouched. Sentinel leak test passes.
- Tests added for thread_id, rename/root/query/shuffle key stability, external media_count, unmapped keys, overlap union.
- decisions.md: three entries appended verbatim; two duplicate guardrails entries merged.
Deviations from the brief (what + why):
- sentinel.build() takes `malformed=False`; the parse-check test passes True. Adding the bad message to the shared fixture would have changed the inspector's EXPECTED counts.
- parse-check also counts folders that fail thread_id() (ValueError) and unreadable files (ParseError) as errors, and exits 1 if any error occurs. The brief only specified per-message errors.
- generate_pair default (max_per_file = 10000) gives single-file threads like the real export; tests pass max_per_file=30 for multi-file coverage. `make synth` fixtures therefore no longer have multi-file threads.
- Added a third synthetic thread (bare-digit folder) so both real folder shapes exist.
In progress / broken:
- Not run: make e2e, make test-integration, make test-warehouse (Docker down). Dev DB must be rebuilt (keys changed).
Next up: Ryan runs parse-check, then Session B (allowlist, real-ingest, metrics)
Open questions for planning:
- raw.source_file stores the relative path including the name-bearing thread folder (e.g. .../inbox/<name>_<digits>/message_1.json) on every row. That reintroduces the PII the thread_id decision avoids. Not changed (out of scope: no new raw columns, no brief guidance). Options: store only message_N.json, or the thread_id plus file name.
- parse-check as written exits 1 on any error; confirm that is wanted.
Measured this session (exact value + source): none on real data. Test count 131 passed, 18 deselected (make test).
Commits: 44605cf — docs: replace provisional Instagram spec with the real format report; 686ac49 — feat: reconcile parser, key and generator to the real Instagram format
Phase 1 checklist: parser [x] (real format, pending parse-check on the full export) loader [x] dbt staging [x] marts [x] Airflow DAG [x] dashboard [x] one-command e2e [~] (needs re-verification on rebuilt dev DB) inspector [x] real-format reconciliation [x]
