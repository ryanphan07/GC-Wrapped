{{ config(materialized='table') }}

-- The one definition of "a person typed this" (D95, amends D85 and D87). A message has user text
-- when its content is non-blank and does not fully match any Instagram placeholder pattern in the
-- seed. Every pattern is wrapped in ^(?: ... )$ here, so a seed row cannot be unanchored by
-- mistake, and matching is case-insensitive on the trimmed decoded content. placeholder_pattern_id
-- is the first matching pattern by id (null when none), so per-pattern counts add up to the
-- placeholder total. Labeling, tagging and the coming embeddings select local_ok AND has_user_text.
-- A table (the regexes run once per build, and it depends on content and the seed, not on consent, so
-- it needs no view semantics like fct_message_eligibility): a changed seed row applies at the next
-- dbt build. No message text in the output. gcw_reader gets no SELECT (see dbt_project.yml).
with patterns as (

    select
        pattern_id,
        '^(?:' || pattern || ')$' as anchored
    from {{ ref('instagram_placeholder_patterns') }}

),

matched as (

    select
        m.message_key,
        min(p.pattern_id) as placeholder_pattern_id
    from {{ ref('stg_instagram__messages') }} as m
    inner join patterns as p
        on m.has_content
        and btrim(m.content) ~* p.anchored
    group by m.message_key

)

select
    m.message_key,
    m.has_content and x.placeholder_pattern_id is null as has_user_text,
    x.placeholder_pattern_id
from {{ ref('stg_instagram__messages') }} as m
left join matched as x using (message_key)
