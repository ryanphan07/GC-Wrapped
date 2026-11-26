-- One row per session with at least one opted-in message; times and counts cover those messages
-- only (D128). Sessions are still cut over all messages, so session_id does not depend on consent.
-- started_by_member_key is the sender of the session's opening message, null when that sender has
-- not opted in. A single-message session has duration 0.
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
from {{ ref('fct_consented_messages') }}
group by session_id, thread_id, session_index
