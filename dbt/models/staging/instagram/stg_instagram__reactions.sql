-- One row per distinct (message, actor, emoji), unnested from raw.messages.reactions.
-- Exact repeats are export artifacts and are dropped here.
select distinct
    m.message_key,
    r.reaction ->> 'actor' as actor,
    r.reaction ->> 'emoji' as emoji
from {{ source('raw', 'messages') }} as m
cross join lateral jsonb_array_elements(m.reactions) as r (reaction)
