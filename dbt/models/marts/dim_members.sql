-- One row per (thread, display name) seen as a sender or a reactor. Reactors who never sent a
-- message are kept (message_count 0) so every fact row's member_key has a match here.
with senders as (

    select
        thread_id,
        sender_name as member_name,
        min(sent_at) as first_message_at,
        max(sent_at) as last_message_at,
        count(*) as message_count
    from {{ ref('stg_instagram__messages') }}
    group by thread_id, sender_name

),

reactors as (

    select distinct
        m.thread_id,
        r.actor as member_name
    from {{ ref('stg_instagram__reactions') }} as r
    inner join {{ ref('stg_instagram__messages') }} as m using (message_key)

),

members as (

    select thread_id, member_name from senders
    union
    select thread_id, member_name from reactors

)

select
    {{ member_key('members.thread_id', 'members.member_name') }} as member_key,
    members.thread_id,
    members.member_name,
    senders.first_message_at,
    senders.last_message_at,
    coalesce(senders.message_count, 0) as message_count
from members
left join senders using (thread_id, member_name)
