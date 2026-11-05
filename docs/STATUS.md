Last session: 2026-11-04 — Privacy-safe export shape inspector
Brief completed: yes. mask_uri and assert_no_leak are implemented; the sentinel-leak, URI-masking and mask_uri tests pass (no xfail).
Done:
- scripts/inspect_export.py rewritten (same tool, not a second one) as an aggregates-only report: MESSAGES_DIR present?; entries next to inbox (name only if ^[a-z_]+(\.json)?$, else <x>); folder shapes <name>_<digits> / <digits> / other; histograms of digit-run length, message files per thread, timestamp digit length, message order; total messages; key frequencies as "N of M"; mojibake count (latin-1 lead+continuation byte); masked attachment URI shapes per attachment key; duplicate basenames per thread. No per-thread listings.
- tests/synthetic/sentinel.py (+ `make synth-sentinel` -> tests/fixtures/sentinel, gitignored): SENTINELNAME/SENTINELTEXT in names, folders, content, share, reactions, URIs; hex-free numeric folders; known counts in EXPECTED.
- tests/test_inspect_export.py: asserts folder shapes, digit histogram, files/thread, message and timestamp counts, mojibake, order, duplicate basenames, key presence against EXPECTED.
- `make inspect EXPORT_DIR=<path>` (errors clearly if unset). decisions.md entry appended verbatim.
Deviations from the brief (what + why):
- Order histogram has a 4th bucket "indeterminate" (file with <2 distinct timestamps) because oldest/newest is undefined there.
- Dropped the old per-path string-empty / mojibake / int-digit columns and the directory layout (they leaked structure or were per-thread); key table is now types, "N of M", array length.
- Duplicate basenames = extra DISTINCT URIs in one thread sharing a basename.
- Raw URIs are held in memory only, masked at render time, so aggregate tests don't depend on mask_uri.
- Workflow change: Claude Code now implements all code (no write-it-yourself scaffolds); mask_uri (last segment always uses the filename rule, other segments allowlisted) and assert_no_leak are done and the needs_ryan markers are removed. See decisions.md.
In progress / broken: nothing.
Next up:
1. Ryan runs `make inspect` on the real export (below), reviews, commits to docs/formats/instagram-schema.md via PR.
2. Reconciliation session (not started).
Open questions for planning: whether the real "inbox" listing needs more than 'inbox' next to it.
Measured this session (exact value + source): none on real data. Fixture counts come from tests/synthetic/sentinel.py EXPECTED; see PR #18 report.
Commits: (hash after PR creation)
Phase 1 checklist: parser [~] (provisional format) loader [x] dbt staging [x] marts [x] Airflow DAG [x] dashboard [x] one-command e2e [x] (synthetic only) inspector [~] real-format reconciliation [ ]
