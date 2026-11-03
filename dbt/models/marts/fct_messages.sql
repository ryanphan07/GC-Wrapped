-- One row per message: staging fields plus session and turn fields. Message text stays in staging.
select
    m.message_key,
    {{ member_key('m.thread_id', 'm.sender_name') }} as member_key,
    m.thread_id,
    m.sender_name,
    m.sent_at,
    m.sent_at_local,
    m.message_type,
    m.word_count,
    m.media_count,
    s.session_id,
    s.session_index,
    s.turn_id,
    s.is_session_start,
    s.is_turn_start,
    s.reply_seconds
from {{ ref('stg_instagram__messages') }} as m
inner join {{ ref('int_messages_sessionized') }} as s using (message_key)
