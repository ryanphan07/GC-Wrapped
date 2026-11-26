-- The opted-in messages (D128): the fct_messages rows whose sender is local_ok as of this build.
-- The one gate the Activity marts and page read through; fct_messages keeps every message for the
-- pipeline and has no reader grant. Session, turn and reply fields are the ones cut over all
-- messages (D125), so another sender's timestamp can shape them. turn_id is left out: it numbers
-- every turn, so its gaps would count other senders' turns. No message text.
select
    m.message_key,
    m.member_key,
    m.thread_id,
    m.sender_name,
    m.sent_at,
    m.sent_at_local,
    m.message_type,
    m.word_count,
    m.session_id,
    m.session_index,
    m.is_session_start,
    m.is_turn_start,
    m.reply_seconds
from {{ ref('fct_messages') }} as m
inner join {{ ref('fct_message_eligibility') }} as e on e.message_key = m.message_key
where e.local_ok
