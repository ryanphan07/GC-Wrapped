-- The current tag of each local_ok message: per message, the most recently written row (whatever
-- model and prompt version produced it). Joining the eligibility view (D75) means a message whose
-- sender is no longer local_ok drops out at once, even before `gcw tag` prunes the row (D88).
-- Per-message labels with keys: no reader grant (see dbt_project.yml); the aggregates below are
-- what the dashboard role sees. No message text here.
with latest as (

    select distinct on (message_key)
        message_key,
        topic,
        sentiment,
        model_id,
        prompt_version,
        tagged_at
    from {{ ref('stg_enrich__message_tags') }}
    order by message_key, tagged_at desc, model_id, prompt_version

)

select
    m.message_key,
    m.thread_id,
    {{ member_key('m.thread_id', 'm.sender_name') }} as member_key,
    l.topic,
    l.sentiment,
    l.model_id,
    l.prompt_version,
    l.tagged_at
from latest as l
inner join {{ ref('fct_message_eligibility') }} as e on e.message_key = l.message_key
inner join {{ ref('stg_instagram__messages') }} as m on m.message_key = l.message_key
where e.local_ok
