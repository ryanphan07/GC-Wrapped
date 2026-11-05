Last session: 2026-11-04 — Privacy-safe export shape inspector
Brief completed: partial. Two items are scaffolded by design (write-it-yourself), so the acceptance criteria that depend on them are not yet met: `make inspect EXPORT_DIR=tests/fixtures/sentinel` raises NotImplementedError (mask_uri), and the sentinel-leak test, URI-masking count test and mask_uri case test are xfail until implemented.
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
- Non-strict xfail on the 3 tests needing Ryan's code, so `make lint test` passes now and they go green on their own.
In progress / broken: `make inspect` fails until mask_uri is implemented. Verified by stubbing mask_uri in a scratch run that the rest of the report contains no sentinel strings.
Next up:
1. Ryan implements the allowlist mask_uri and assert_no_leak (scripts/inspect_export.py, tests/test_inspect_export.py), removes the needs_ryan markers (now strict: they must go once implemented), runs `make lint test`.
2. Ryan runs `make inspect` on the real export (below), reviews, commits to docs/formats/instagram-schema.md via PR.
3. Reconciliation session (not started).
Open questions for planning: mask_uri edge cases (a URI segment equal to another thread's lowercase-only folder name passes [a-z_]+); whether the real "inbox" listing needs more than 'inbox' next to it.
Measured this session (exact value + source): none on real data. Fixture counts come from tests/synthetic/sentinel.py EXPECTED; 91 passed, 3 xfailed from `make test`.
Commits: (hash after PR creation)
Phase 1 checklist: parser [~] (provisional format) loader [x] dbt staging [x] marts [x] Airflow DAG [x] dashboard [x] one-command e2e [x] (synthetic only) inspector [~] real-format reconciliation [ ]
