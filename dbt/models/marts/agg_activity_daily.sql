-- One row per thread and local calendar day with at least one message. A session is counted once,
-- on the local date of its first message, so daily sessions_started sum to the total sessions.
select
    thread_id,
    sent_at_local::date as activity_date,
    count(*) as messages,
    count(distinct member_key) as active_members,
    count(*) filter (where is_session_start) as sessions_started
from {{ ref('fct_messages') }}
group by thread_id, sent_at_local::date
