{{ config(materialized='view', post_hook='alter view {{ this }} set (security_barrier = true)') }}

-- The messages answering may read (D131): local_ok AND has_user_text (D88, D95), with the sender,
-- time, session and text an excerpt shows. gcw_ask reads this view and none of the relations
-- behind it: the view runs with its owner's rights (never security_invoker), and security_barrier
-- stops a reader's own functions and operators from seeing rows before the filter. A view, so a
-- revocation takes effect when consent-load commits. It depends on dbt tables, which a rebuild
-- drops with CASCADE, so it has to be a model: until this one is rebuilt there is no view to read.
select
    m.message_key,
    m.thread_id,
    f.session_id,
    m.sender_name,
    m.sent_at,
    f.sent_at_local,
    m.content
from {{ ref('stg_instagram__messages') }} as m
inner join {{ ref('fct_message_eligibility') }} as e on e.message_key = m.message_key
inner join {{ ref('fct_message_text') }} as t on t.message_key = m.message_key
inner join {{ ref('fct_messages') }} as f on f.message_key = m.message_key
where e.local_ok and t.has_user_text
