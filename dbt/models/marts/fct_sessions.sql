-- One row per session. A single-message session has duration 0.
select
    session_id,
    thread_id,
    session_index,
    min(sent_at) as started_at,
    max(sent_at) as ended_at,
    extract(epoch from max(sent_at) - min(sent_at))::double precision as duration_seconds,
    count(*) as message_count,
    count(*) filter (where is_turn_start) as turn_count,
    count(distinct member_key) as participant_count,
    max(member_key) filter (where is_session_start) as started_by_member_key
from {{ ref('fct_messages') }}
group by session_id, thread_id, session_index
