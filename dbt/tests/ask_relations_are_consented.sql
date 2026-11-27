-- Neither view gcw_ask reads (D131) holds a row of a message the local-work rule refuses: the
-- sender "yes" or "local" in the consent ledger for that thread (D88), and text a person typed
-- (D95). Both halves are restated here from the ledger, the content and the seed, not read from
-- fct_message_eligibility or fct_message_text, which the views are filtered by. A vector row
-- belongs to its centre message. The message view also holds every allowed message, once.
-- Returns violating rows; the test passes when none come back.
with allowed as (

    select m.message_key
    from {{ ref('stg_instagram__messages') }} as m
    inner join {{ ref('stg_consent__sender_consent') }} as c
        on c.thread_id = m.thread_id
        and c.sender_name = m.sender_name
    where c.consent in ('yes', 'local')
        and m.content ~ '\S'
        and not exists (
            select 1
            from {{ ref('instagram_placeholder_patterns') }} as p
            where btrim(m.content) ~* ('^(?:' || p.pattern || ')$')
        )

),

refused_messages as (

    select 'ask_messages' as relation, message_key as item
    from {{ ref('ask_messages') }}
    where message_key not in (select message_key from allowed)

),

refused_vectors as (

    select 'ask_message_embeddings' as relation, message_key as item
    from {{ ref('ask_message_embeddings') }}
    where message_key not in (select message_key from allowed)

),

message_count as (

    select 'ask_messages row count' as relation, found.n::text as item
    from (select count(*) as n from {{ ref('ask_messages') }}) as found
    where found.n <> (select count(*) from allowed)

)

select * from refused_messages
union all
select * from refused_vectors
union all
select * from message_count
