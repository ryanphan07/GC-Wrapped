# Metrics

Every value must be an exact number with its source (`raw.ingestion_runs`, dbt output, or a command Ryan ran) and a date. Nothing is estimated or rounded.

## Pipeline

| metric | value | date | how measured | commit |
|---|---|---|---|---|
|  |  |  |  |  |

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
