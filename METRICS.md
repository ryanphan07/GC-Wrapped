# Metrics

Every value must be an exact number with its source (`raw.load_runs`, dbt output, or a command Ryan ran). Every row carries a date and a commit SHA; eval rows also carry the model and dataset version. Nothing is estimated or rounded. Add new rows; never edit or delete an existing row.

## Pipeline

| metric | value | date | how measured | commit |
|---|---|---|---|---|
| Parser benchmark (make parse-check; parser only, no DB load; whole local inbox incl. non-consented threads, counts only) | 110,348 messages in 422 threads parsed in 6.12 s; 0 errors; 0 duplicates; fix_mojibake repaired 22,649 message bodies (21% of 107,236 text messages) and 64,888 text fields in total. Not a pipeline throughput or ingested-data number. | 2026-11-04 | `make parse-check`, run by Ryan in his own terminal; counts only | not recorded (parse-check merged in ecdc16a) |
| Real ingest, fresh DB (1 consented thread) | 3,220 messages parsed and loaded, 0 duplicates; consent allowlist skipped 421 of 422 thread folders without opening them; dbt build PASS=125 WARN=0 ERROR=0 TOTAL=125; 167.73 s end to end incl. container startup and migrations; ingest step 0.39 s | 2026-11-05 | make real-ingest ENV_FILE=<real env>, run by Ryan in his own terminal, summary lines only | 309060e |
| Real ingest re-run (idempotency) | 3,220 parsed, 0 inserted, 3,220 skipped as already loaded; dbt PASS=125 ERROR=0; 75.97 s end to end; ingest step 0.32 s | 2026-11-05 | same | 309060e |
| Real ingest re-run after PR #21 (first run with corrected load_runs timing) | 3,220 parsed, 0 inserted, 3,220 skipped as already loaded; allowlist skipped 421 of 422 thread folders; dbt build PASS=125 ERROR=0; 210.46 s pipeline time incl. container startup; ingest step 0.54 s; load_runs duration 0.49 s | 2026-11-05 | make real-ingest ENV_FILE=<real env>, run by Ryan, summary lines only | 9e0df42 |
| Real ingest, 15 newly consented threads added (16 total) | 33,997 parsed, 30,777 inserted, 3,220 skipped as already loaded (exactly the previously ingested thread); allowlist skipped 406 of 422 thread folders; dbt build PASS=125 ERROR=0; 125.59 s pipeline time incl. container startup; ingest step 4.59 s; load_runs duration 4.55 s | 2026-11-05 | make real-ingest ENV_FILE=<real env>, run by Ryan, summary lines only | 0c34d3f |
| dbt project and test suite | 9 dbt models, 111 data tests (109 generic, 2 singular), 4 dbt unit tests, 2 sources, 0 seeds; 254 unit + 40 non-unit (22 integration, 18 dashboard-warehouse) pytest tests collected | 2026-11-05 | make metrics, default .env | 251e9f9 |
| Real warehouse snapshot (make metrics) | 33,997 raw messages, 33,997 modeled (fct_messages), 16 threads, 461 distinct sender display names; latest load (id 4) succeeded: 33,997 parsed, 30,777 inserted, 4.55 s | 2026-11-05 | make metrics ENV_FILE=<real env>, run by Ryan, fixed-label output only | 3950299 |
| Test suite | 194 unit + 19 integration = 213 passing | 2026-11-05 | make test, make test-integration | 309060e |
| Real warehouse snapshot after metrics hardening (make metrics) | target database gcw_real; 33,997 raw messages, 33,997 modeled, 16 threads, 461 distinct senders; latest load (id 4) succeeded: 33,997 parsed, 30,777 inserted, 4.55 s; 9 dbt models, 111 data tests (109 generic, 2 singular), 4 dbt unit tests, 2 sources, 0 seeds, 1 hook, 125 buildable nodes; 261 unit + 40 non-unit pytest tests collected | 2026-11-05 | make metrics ENV_FILE=<real env>, run by Ryan, fixed-label output only; no dbt/target or dbt/logs created | 6b36f8f |
| Dev warehouse and test suite (make metrics, synthetic) | target database gcw_dev; 170 raw messages, 170 modeled, 3 threads, 7 distinct senders; latest load (id 1) succeeded: 170 parsed, 170 inserted, 0.06 s; 9 dbt models, 111 data tests (109 generic, 2 singular), 4 dbt unit tests, 2 sources, 0 seeds, 1 hook, 125 buildable nodes; 317 unit + 40 non-unit pytest tests collected | 2026-11-05 | make metrics, default .env | 13b5d31 |
| pytest tests collected | 406 unit + 55 non-unit | 2026-11-09 | make metrics (gcw_dev) | 468bca3 |
| Redaction audit, real data: FAILED (residual 3) — target database gcw_real; messages scanned 33997, with text 32780, changed 9375; placeholders person 12516, phone 23, email 4, url 274, handle 174, address 11, card 0, ssn 1, ip 0, iban 0; member-name tokens remaining 3; redaction seconds 86.29; "redaction-audit: failed (residual member tokens)". Model en_core_web_lg 3.8.0, presidio-analyzer 2.2.366, dataset gcw_real/export_a | 2026-11-09 | make redaction-audit ENV_FILE=<real env>, run by Ryan, aggregates only, after the #32 merge | 5a9907f |
| pytest on main before P2-2a-fix | make test: 485 passed (42 deselected); make test-integration: 42 passed (485 deselected) | 2026-11-09 | make test, make test-integration (default .env) | 5a9907f |
| Redaction audit, real data, after P2-2a-fix: ok — target database gcw_real; messages scanned 33997, with text 32780, changed 9375; placeholders person 12218, phone 23, email 4, url 277, handle 175, address 11, card 0, ssn 1, ip 0, iban 0; member-name tokens remaining 0; backstop replacements 0 (adjacent 0, standalone 0); overlap merges 4165; boundary extensions 42; directory tokens 599, of which stop words 2; member matches on stop-word tokens 5051; redaction seconds 98.81; "redaction-audit: ok". Model en_core_web_lg 3.8.0, presidio-analyzer 2.2.366, dataset gcw_real/export_a | 2026-11-10 | make redaction-audit ENV_FILE=<real env>, run by Ryan, aggregates only, after the #33 merge | 98df696 |
| Redaction audit, real data, after P2-2a-close (D81): checks ok but coverage regressed vs 98df696 — target database gcw_real; messages scanned 33997, with text 32780, changed 9363; placeholders person 12018, phone 23, email 4, url 277, handle 175, address 11, card 0, ssn 1, ip 0, iban 0; member-name tokens remaining 0; backstop replacements 0 (adjacent 0, standalone 0); overlap merges 4164; boundary extensions 41; directory tokens 598, of which stop words 2; member matches on stop-word tokens 5051; redaction seconds 93.21; "redaction-audit: ok". Model en_core_web_lg 3.8.0, presidio-analyzer 2.2.366, dataset gcw_real/export_a | 2026-11-10 | make redaction-audit ENV_FILE=<real env>, run by Ryan, aggregates only, after the #34 merge | 8a85d5d |
| Redaction audit, real data, after P2-2a-union (D83): ok, pass rule met — target database gcw_real; messages scanned 33997, with text 32780, changed 9375; placeholders person 12218, phone 23, email 4, url 277, handle 175, address 11, card 0, ssn 1, ip 0, iban 0; member-name tokens remaining 0; backstop replacements 0 (adjacent 0, standalone 0); overlap merges 4165; boundary extensions 41; directory tokens 601, of which stop words 2, raw-only 2, folded-only 3 (one symbol 0, several symbols 1, mixed 0, folded break 2); member matches on stop-word tokens 5051; matches on folded-only tokens: one symbol 0, several symbols 201, mixed 0, folded break 2; messages with a match on a folded-only token 203; redaction seconds 95.76; "redaction-audit: ok". Model en_core_web_lg 3.8.0, presidio-analyzer 2.2.366, dataset gcw_real/export_a | 2026-11-10 | make redaction-audit ENV_FILE=<real env>, run by Ryan, aggregates only, after the #35 merge | e4cf89e |

raw.load_runs durations from 2026-11-05 runs (0.00 / 0.01 s) were wrong (bug fixed later); not recorded.

Commit citations (D73): merges are squashes, so a PR-branch commit is cited as "<sha> (PR #n)" and rows measured on main cite the main SHA. Existing rows are not edited; 251e9f9 above is a PR #24 branch commit and 13b5d31 is a PR #29 branch commit.

## dbt

| metric | value | date | how measured | commit |
|---|---|---|---|---|
| dbt model count | 9 | 2026-11-03 | `dbt ls --resource-type model --quiet \| wc -l`; CI `dbt build` on main PASS=124 WARN=0 ERROR=0 | dab91cb |
| dbt data test count (generic + singular) | 111 | 2026-11-03 | `dbt ls --resource-type test --quiet \| wc -l`; CI `dbt build` on main PASS=124 WARN=0 ERROR=0 | dab91cb |
| dbt unit test count | 4 | 2026-11-03 | `dbt ls --resource-type unit_test --quiet \| wc -l`; CI `dbt build` on main PASS=124 WARN=0 ERROR=0 | dab91cb |
| dbt model count | 11 | 2026-11-09 | make metrics (gcw_dev) | 468bca3 |
| dbt data test count (generic + singular) | 125 (123 generic, 2 singular) | 2026-11-09 | make metrics (gcw_dev) | 468bca3 |
| dbt unit test count | 6 | 2026-11-09 | make metrics (gcw_dev) | 468bca3 |
| dbt buildable nodes | 143 | 2026-11-09 | make metrics (gcw_dev); make e2e dbt build PASS=143 TOTAL=143 | 468bca3 |

## Tagging eval

| metric | value | date | how measured | commit |
|---|---|---|---|---|
|  |  |  |  |  |

## Retrieval eval

| metric | value | date | how measured | commit |
|---|---|---|---|---|
|  |  |  |  |  |

## Serving

| metric | value | date | how measured | commit |
|---|---|---|---|---|
|  |  |  |  |  |

## Usage

| metric | value | date | how measured | commit |
|---|---|---|---|---|
|  |  |  |  |  |
