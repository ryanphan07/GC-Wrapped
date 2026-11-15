-- The current tag of each local_ok message, from the promoted model only (D92): the model named by
-- enrich.tag_default, or the D90 default (var gcw_default_tag_model) while that table is empty. Per
-- message, that model's most recently written row, so a newer prompt version replaces an older one.
-- Tags of any other model stay in enrich.message_tags for comparison but never reach profiles.
-- Joining the eligibility view (D75) means a message whose sender is no longer local_ok drops out
-- at once, even before `gcw tag` prunes the row (D88).
-- Per-message labels with keys: no reader grant (see dbt_project.yml); the aggregates below are
-- what the dashboard role sees. No message text here.
with promoted as (

    select coalesce(
        (select model_id from {{ ref('stg_enrich__tag_default') }} limit 1),
        '{{ var("gcw_default_tag_model") }}'
    ) as model_id

),

latest as (

    select distinct on (t.message_key)
        t.message_key,
        t.topic,
        t.sentiment,
        t.model_id,
        t.prompt_version,
        t.tagged_at
    from {{ ref('stg_enrich__message_tags') }} as t
    inner join promoted as p on p.model_id = t.model_id
    order by t.message_key, t.tagged_at desc, t.prompt_version

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
