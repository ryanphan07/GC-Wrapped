-- One row per member. Reaction counts exclude self-reactions. Reply stats are attributed to the
-- member who replied (the sender of the turn's first message).
with messages as (

    select * from {{ ref('fct_messages') }}

),

reactions as (

    select * from {{ ref('fct_reactions') }}
    where not is_self_reaction

),

message_stats as (

    select
        member_key,
        count(*) as messages,
        sum(word_count) as words,
        count(*) filter (where message_type = 'media') as media_messages,
        count(*) filter (where message_type = 'share') as share_messages,
        count(*) filter (where is_session_start) as sessions_started,
        percentile_cont(0.5) within group (order by reply_seconds) as median_reply_seconds,
        percentile_cont(0.9) within group (order by reply_seconds) as p90_reply_seconds
    from messages
    group by member_key

),

hour_counts as (

    select
        member_key,
        extract(hour from sent_at_local)::int as hour_of_day,
        count(*) as n
    from messages
    group by member_key, extract(hour from sent_at_local)::int

),

most_active_hour as (

    select distinct on (member_key) member_key, hour_of_day
    from hour_counts
    order by member_key, n desc, hour_of_day

),

reactions_given as (

    select reactor_member_key as member_key, count(*) as reactions_given
    from reactions
    group by reactor_member_key

),

reactions_received as (

    select author_member_key as member_key, count(*) as reactions_received
    from reactions
    group by author_member_key

),

given_emoji_counts as (

    select reactor_member_key as member_key, emoji, count(*) as n
    from reactions
    group by reactor_member_key, emoji

),

top_reaction_given as (

    select distinct on (member_key) member_key, emoji
    from given_emoji_counts
    order by member_key, n desc, emoji

),

received_emoji_counts as (

    select author_member_key as member_key, emoji, count(*) as n
    from reactions
    group by author_member_key, emoji

),

top_reaction_received as (

    select distinct on (member_key) member_key, emoji
    from received_emoji_counts
    order by member_key, n desc, emoji

)

select
    d.member_key,
    d.thread_id,
    d.member_name,
    coalesce(s.messages, 0) as messages,
    coalesce(s.words, 0) as words,
    coalesce(s.media_messages, 0) as media_messages,
    coalesce(s.share_messages, 0) as share_messages,
    coalesce(s.sessions_started, 0) as sessions_started,
    s.median_reply_seconds,
    s.p90_reply_seconds,
    coalesce(g.reactions_given, 0) as reactions_given,
    coalesce(r.reactions_received, 0) as reactions_received,
    tg.emoji as top_reaction_given,
    tr.emoji as top_reaction_received,
    h.hour_of_day as most_active_hour
from {{ ref('dim_members') }} as d
left join message_stats as s using (member_key)
left join most_active_hour as h using (member_key)
left join reactions_given as g using (member_key)
left join reactions_received as r using (member_key)
left join top_reaction_given as tg using (member_key)
left join top_reaction_received as tr using (member_key)
