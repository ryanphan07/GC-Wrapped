{{ config(materialized='view') }}

-- A view (D75), so the flags always reflect the ledger currently loaded in raw.sender_consent:
-- a revoked consent takes effect when consent-load commits, with no dbt build in between.
-- gcw_reader gets no SELECT on it (see the grants list in dbt_project.yml).
-- The one definition of which messages may be processed (D69, D88). local_ok is true when the
-- sender is "yes" or "local" in the consent ledger for that thread: work on this machine
-- (labeling, tagging, embeddings, answers, profiles). external_ok is true only for "yes": the
-- only messages that may leave the machine, and only after redaction. A blank, a "no", a sender
-- missing from the ledger, and an empty ledger all give false for both. No message text here.
select
    m.message_key,
    m.thread_id,
    m.sender_name,
    coalesce(c.consent in ('yes', 'local'), false) as local_ok,
    coalesce(c.consent = 'yes', false) as external_ok
from {{ ref('stg_instagram__messages') }} as m
left join {{ ref('stg_consent__sender_consent') }} as c
    on c.thread_id = m.thread_id
    and c.sender_name = m.sender_name
