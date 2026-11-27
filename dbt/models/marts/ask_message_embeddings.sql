{{ config(materialized='view', post_hook='alter view {{ this }} set (security_barrier = true)') }}

-- The vectors answering may read (D131): the rows of every embed version whose centre message is in
-- ask_messages now. The same rule as that view: owner's rights, security_barrier. No message text.
--
-- A row whose window includes a neighbour who has since revoked is still here until consent-prune
-- deletes it (D105), and its vector and lexemes were made from that neighbour's text too. It never
-- ranks: search also requires every window member to be in ask_messages (retrieval/search.py).
-- Excerpt loading reads its window IDs and takes only the members ask_messages holds.
select
    m.message_key,
    m.window_message_keys,
    m.embed_version,
    m.embedding,
    m.window_tsv
from {{ source('enrich', 'message_embeddings') }} as m
inner join {{ ref('ask_messages') }} as a on a.message_key = m.message_key
