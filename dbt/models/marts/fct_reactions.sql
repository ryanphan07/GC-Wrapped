-- One row per (message, reactor, emoji). The thread comes from the reacted-to message.
select
    {{ dbt_utils.generate_surrogate_key(['r.message_key', 'r.actor', 'r.emoji']) }} as reaction_key,
    r.message_key,
    m.thread_id,
    {{ member_key('m.thread_id', 'r.actor') }} as reactor_member_key,
    r.actor as reactor_name,
    r.emoji,
    {{ member_key('m.thread_id', 'm.sender_name') }} as author_member_key,
    m.sender_name as author_name,
    r.actor = m.sender_name as is_self_reaction
from {{ ref('stg_instagram__reactions') }} as r
inner join {{ ref('stg_instagram__messages') }} as m using (message_key)
