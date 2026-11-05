Last session: 2026-11-04 — Reconcile parser and generator to the real Instagram format
Brief completed: yes. `make lint test` passes (133 tests); `make test-integration` 18 passed on a freshly rebuilt dev DB; `make e2e` run twice on fresh synthetic landing: raw.messages = 170 after run 1 (170 inserted) and 170 after run 2 (0 inserted).
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
- make test-warehouse not run. `make e2e` with the default landing fails (`ingest failed (ValueError at row 0)`): ./data/landing holds export folders from the old generator (not inspected, per privacy rules) that no longer match thread_id(). Regenerate it (`uv run python tests/synthetic/instagram.py --out data/landing` after clearing it) to fix. Verified e2e used `make e2e GCW_LANDING_HOST_DIR=<fresh synthetic dir>`.
- CI coverage: ci.yml `integration` job runs make test-integration (+ dbt build, test-warehouse); `e2e` job runs make e2e twice and asserts `rows inserted: 0` on the second.
Next up: Ryan runs parse-check, then Session B (allowlist, real-ingest, metrics)
Open questions for planning:
- (resolved) raw.source_file is now "<thread_id>/<message_N.json>"; see decisions.md 2026-11-04.
- parse-check as written exits 1 on any error; confirm that is wanted.
Measured this session (exact value + source): none on real data. Test count 131 passed, 18 deselected (make test).
Commits: 44605cf — docs: replace provisional Instagram spec with the real format report; 686ac49 — feat: reconcile parser, key and generator to the real Instagram format
Phase 1 checklist: parser [x] (real format, pending parse-check on the full export) loader [x] dbt staging [x] marts [x] Airflow DAG [x] dashboard [x] one-command e2e [~] (needs re-verification on rebuilt dev DB) inspector [x] real-format reconciliation [x]
