-- The promoted tagging model (D92): zero rows (use the D90 default) or one. Renames only.
select
    model_id,
    promoted_at
from {{ source('enrich', 'tag_default') }}
