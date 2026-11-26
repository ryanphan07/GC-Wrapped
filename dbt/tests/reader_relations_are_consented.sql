-- No relation gcw_reader can read names or counts a sender, or counts a message, that the consent
-- ledger does not allow for local work (D88, D128). Checked against the ledger itself rather than
-- the eligibility view the marts are built from. One block per way of leaking; a new reader grant
-- needs its own (a warehouse test pins the granted relations).
-- Returns violating rows; the test passes when none come back.
with allowed as (

    select
        m.message_key,
        m.thread_id,
        {{ member_key('m.thread_id', 'm.sender_name') }} as member_key,
        m.sent_at_local::date as activity_date
    from {{ ref('stg_instagram__messages') }} as m
    inner join {{ ref('stg_consent__sender_consent') }} as c
        on c.thread_id = m.thread_id
        and c.sender_name = m.sender_name
    where c.consent in ('yes', 'local')

),

named as (

    select 'fct_consented_messages' as relation, thread_id, sender_name as member_name, member_key
    from {{ ref('fct_consented_messages') }}
    union all
    select 'agg_member_activity', thread_id, member_name, member_key
    from {{ ref('agg_member_activity') }}
    union all
    select 'mart_member_wrapped', thread_id, member_name, member_key
    from {{ ref('mart_member_wrapped') }}
    union all
    select 'agg_tags_by_sender', thread_id, null, member_key
    from {{ ref('agg_tags_by_sender') }}
    union all
    select 'fct_sessions', thread_id, null, started_by_member_key
    from {{ ref('fct_sessions') }}
    where started_by_member_key is not null

),

unconsented_names as (

    select n.relation, n.member_key as item
    from named as n
    where not exists (select 1 from allowed as a where a.member_key = n.member_key)
    union all
    -- a name column that disagrees with its row's key would slip past the check above
    select n.relation, n.member_key
    from named as n
    where n.member_name is not null
        and not exists (
            select 1
            from {{ ref('stg_consent__sender_consent') }} as c
            where c.thread_id = n.thread_id
                and c.sender_name = n.member_name
                and c.consent in ('yes', 'local')
        )

),

unconsented_messages as (

    select 'fct_consented_messages' as relation, message_key as item
    from {{ ref('fct_consented_messages') }}
    where message_key not in (select message_key from allowed)
    union all
    -- the only source of both tag aggregates
    select 'fct_message_tags', message_key
    from {{ ref('fct_message_tags') }}
    where message_key not in (select message_key from allowed)

),

-- Reactions between two opted-in senders, the only ones that may be counted.
reactions as (

    select r.reactor_member_key, r.author_member_key
    from {{ ref('fct_reactions') }} as r
    where not r.is_self_reaction
        and r.message_key in (select message_key from allowed)
        and r.reactor_member_key in (select member_key from allowed)

),

member_counts as (

    select
        'agg_member_activity' as relation,
        m.member_key as item
    from {{ ref('agg_member_activity') }} as m
    where m.messages <> (select count(*) from allowed as a where a.member_key = m.member_key)
        or m.reactions_given
            <> (select count(*) from reactions as r where r.reactor_member_key = m.member_key)
        or m.reactions_received
            <> (select count(*) from reactions as r where r.author_member_key = m.member_key)

),

daily_counts as (

    select 'agg_activity_daily' as relation, thread_id || ' ' || activity_date as item
    from (
        select
            thread_id,
            activity_date,
            count(*) as messages,
            count(distinct member_key) as active_members
        from allowed
        group by thread_id, activity_date
    ) as expected
    full outer join {{ ref('agg_activity_daily') }} as d using (thread_id, activity_date)
    where d.messages is distinct from expected.messages
        or d.active_members is distinct from expected.active_members

),

session_counts as (

    select 'fct_sessions' as relation, session_id as item
    from (
        select
            f.session_id,
            count(*) as message_count,
            count(distinct a.member_key) as participant_count
        from allowed as a
        inner join {{ ref('fct_messages') }} as f using (message_key)
        group by f.session_id
    ) as expected
    full outer join {{ ref('fct_sessions') }} as s using (session_id)
    where s.message_count is distinct from expected.message_count
        or s.participant_count is distinct from expected.participant_count

),

thread_tag_counts as (

    select 'agg_tags_by_thread' as relation, t.thread_id as item
    from {{ ref('agg_tags_by_thread') }} as t
    group by t.thread_id
    having sum(t.messages)
        > (select count(*) from allowed as a where a.thread_id = t.thread_id)

)

select * from unconsented_names
union all
select * from unconsented_messages
union all
select * from member_counts
union all
select * from daily_counts
union all
select * from session_counts
union all
select * from thread_tag_counts
