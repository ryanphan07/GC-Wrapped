-- One row per thread and local calendar day with at least one opted-in message (D128). A session is
-- counted once, on the local date of its first opted-in message, so daily sessions_started sum to
-- the rows of fct_sessions.
with messages as (

    select
        *,
        row_number() over (
            partition by session_id order by sent_at, message_key
        ) as place_in_session
    from {{ ref('fct_consented_messages') }}

)

select
    thread_id,
    sent_at_local::date as activity_date,
    count(*) as messages,
    count(distinct member_key) as active_members,
    count(*) filter (where place_in_session = 1) as sessions_started
from messages
group by thread_id, sent_at_local::date
