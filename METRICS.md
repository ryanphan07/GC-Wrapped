# Metrics

Every value must be an exact number with its source (`raw.ingestion_runs`, dbt output, or a command Ryan ran) and a date. Nothing is estimated or rounded.

## Pipeline

| metric | value | date | how measured | commit |
|---|---|---|---|---|
| Parser benchmark (make parse-check; parser only, no DB load; whole local inbox incl. non-consented threads, counts only) | 110,348 messages in 422 threads parsed in 6.12 s; 0 errors; 0 duplicates; fix_mojibake repaired 22,649 message bodies (21% of 107,236 text messages) and 64,888 text fields in total. Not a pipeline throughput or ingested-data number. | 2026-11-04 | `make parse-check`, run by Ryan in his own terminal; counts only | not recorded (parse-check merged in ecdc16a) |

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
