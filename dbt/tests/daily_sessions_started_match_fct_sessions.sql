-- Each session is counted once, on its start date, so the daily counts must add up to the total.
-- Returns a row only when they differ; the test passes when none come back.
with daily as (

    select coalesce(sum(sessions_started), 0) as total from {{ ref('agg_activity_daily') }}

),

sessions as (

    select count(*) as total from {{ ref('fct_sessions') }}

)

select
    daily.total as daily_total,
    sessions.total as session_total
from daily
cross join sessions
where daily.total <> sessions.total
