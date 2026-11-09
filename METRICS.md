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

raw.load_runs durations from 2026-11-05 runs (0.00 / 0.01 s) were wrong (bug fixed later); not recorded.

Commit citations (D73): merges are squashes, so a PR-branch commit is cited as "<sha> (PR #n)" and rows measured on main cite the main SHA. Existing rows are not edited; 251e9f9 above is a PR #24 branch commit and 13b5d31 is a PR #29 branch commit.

## dbt

| metric | value | date | how measured | commit |
|---|---|---|---|---|
| dbt model count | 9 | 2026-11-03 | `dbt ls --resource-type model --quiet \| wc -l`; CI `dbt build` on main PASS=124 WARN=0 ERROR=0 | dab91cb |
| dbt data test count (generic + singular) | 111 | 2026-11-03 | `dbt ls --resource-type test --quiet \| wc -l`; CI `dbt build` on main PASS=124 WARN=0 ERROR=0 | dab91cb |
| dbt unit test count | 4 | 2026-11-03 | `dbt ls --resource-type unit_test --quiet \| wc -l`; CI `dbt build` on main PASS=124 WARN=0 ERROR=0 | dab91cb |

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
