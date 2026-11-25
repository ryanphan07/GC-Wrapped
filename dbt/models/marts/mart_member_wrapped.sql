{{ config(materialized='view') }}

-- One Wrapped profile per consented member of a thread, the consent ledger's key (D123).
-- Computed only over messages that are local_ok and user text (D88, D95): a member with none has
-- no row, and only such messages are counted, in the thread share too. One exception (D125): reply
-- time is the time since the previous turn, whoever sent it, and sessions and turns are cut over
-- all messages, so another sender's timestamp (never their text) can enter a member's median.
-- A view (D124), like fct_message_eligibility (D75): joining eligibility at read time means a
-- revoked sender's profile is gone as soon as consent-load commits, with no dbt build in between
-- and no stored copy for consent-prune to chase. Reply time is the existing reply-time model's
-- (fct_messages.reply_seconds, on the first message of a reply). Topics and sentiment are the
-- promoted tag model's (fct_message_tags, D92) over the same messages: model estimates (D99).
-- No message text.
with messages as (

    select
        m.message_key,
        m.member_key,
        m.thread_id,
        m.sender_name,
        m.sent_at_local,
        m.reply_seconds
    from {{ ref('fct_messages') }} as m
    inner join {{ ref('fct_message_eligibility') }} as e on e.message_key = m.message_key
    inner join {{ ref('fct_message_text') }} as t on t.message_key = m.message_key
    where e.local_ok and t.has_user_text

),

members as (

    select
        member_key,
        thread_id,
        sender_name as member_name,
        count(*) as messages_sent,
        percentile_cont(0.5) within group (order by reply_seconds) as median_reply_seconds,
        min(sent_at_local)::date as first_message_date,
        max(sent_at_local)::date as last_message_date
    from messages
    group by member_key, thread_id, sender_name

),

threads as (

    select thread_id, count(*) as thread_messages
    from messages
    group by thread_id

),

-- Local wall-clock hour and weekday (1 = Monday), each counted on its own; ties go to the
-- earliest.
hours as (

    select distinct on (member_key) member_key, hour_of_day
    from (
        select
            member_key,
            extract(hour from sent_at_local)::int as hour_of_day,
            count(*) as n
        from messages
        group by member_key, extract(hour from sent_at_local)::int
    ) as counted
    order by member_key, n desc, hour_of_day

),

weekdays as (

    select distinct on (member_key) member_key, weekday
    from (
        select
            member_key,
            extract(isodow from sent_at_local)::int as weekday,
            count(*) as n
        from messages
        group by member_key, extract(isodow from sent_at_local)::int
    ) as counted
    order by member_key, n desc, weekday

),

tags as (

    select m.member_key, g.topic, g.sentiment
    from messages as m
    inner join {{ ref('fct_message_tags') }} as g on g.message_key = m.message_key

),

-- Most frequent first; ties go to the alphabetically first topic.
ranked_topics as (

    select
        member_key,
        topic,
        row_number() over (partition by member_key order by count(*) desc, topic) as place
    from tags
    group by member_key, topic

),

top_topics as (

    select
        member_key,
        max(topic) filter (where place = 1) as top_topic_1,
        max(topic) filter (where place = 2) as top_topic_2,
        max(topic) filter (where place = 3) as top_topic_3
    from ranked_topics
    group by member_key

),

-- Shares of the member's tagged messages.
sentiments as (

    select
        member_key,
        avg((sentiment = 'positive')::int)::double precision as positive_share,
        avg((sentiment = 'neutral')::int)::double precision as neutral_share,
        avg((sentiment = 'negative')::int)::double precision as negative_share
    from tags
    group by member_key

)

select
    m.member_key,
    m.thread_id,
    m.member_name,
    m.messages_sent,
    (m.messages_sent::double precision / t.thread_messages) as thread_share,
    h.hour_of_day as most_active_hour,
    w.weekday as most_active_weekday,
    m.median_reply_seconds,
    m.first_message_date,
    m.last_message_date,
    p.top_topic_1,
    p.top_topic_2,
    p.top_topic_3,
    s.positive_share,
    s.neutral_share,
    s.negative_share
from members as m
inner join threads as t on t.thread_id = m.thread_id
inner join hours as h on h.member_key = m.member_key
inner join weekdays as w on w.member_key = m.member_key
left join top_topics as p on p.member_key = m.member_key
left join sentiments as s on s.member_key = m.member_key
