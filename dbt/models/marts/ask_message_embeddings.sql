{{ config(materialized='view', post_hook='alter view {{ this }} set (security_barrier = true)') }}

-- The vectors answering may read (D131): the rows of every embed version whose centre message is in
-- ask_messages now. The same rule as that view: owner's rights, security_barrier. No message text.
--
-- A row's vector and lexemes were made from the text of its whole window, so both are NULL when any
-- member is not in ask_messages, e.g. a neighbour who has since revoked (D134). The row and its IDs
-- stay until consent-prune deletes it (D105): excerpt loading reads the window IDs and takes only
-- the members ask_messages holds. Search checks the whole window itself too (retrieval/search.py).
select
    m.message_key,
    m.window_message_keys,
    m.embed_version,
    w.embedding,
    w.window_tsv
from {{ source('enrich', 'message_embeddings') }} as m
inner join {{ ref('ask_messages') }} as a on a.message_key = m.message_key
-- One row holding both when no member is refused, none (so NULLs) otherwise: one check per row.
left join lateral (
    select m.embedding, m.window_tsv
    where not exists (
        select 1
        from unnest(m.window_message_keys) as k (key)
        where k.key not in (select message_key from {{ ref('ask_messages') }})
    )
) as w on true
