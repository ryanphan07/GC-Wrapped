{{ config(materialized='view') }}

-- A view (D75), so external_ok always reflects the ledger currently loaded in raw.sender_consent:
-- a revoked consent takes effect when consent-load commits, with no dbt build in between.
-- gcw_reader gets no SELECT on it (see the grants list in dbt_project.yml).
-- The one definition of which messages may leave the machine (D69). external_ok is true only when
-- the message's sender is "yes" in the consent ledger for that thread. A blank, a "no", a sender
-- missing from the ledger, and an empty ledger all give false. No message text is carried here.
select
    m.message_key,
    m.thread_id,
    m.sender_name,
    coalesce(c.consent = 'yes', false) as external_ok
from {{ ref('stg_instagram__messages') }} as m
left join {{ ref('stg_consent__sender_consent') }} as c
    on c.thread_id = m.thread_id
    and c.sender_name = m.sender_name
