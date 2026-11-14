-- One row per (message, model, prompt version) as written by `gcw tag`. Renames only.
select
    message_key,
    topic,
    sentiment,
    model_id,
    prompt_version,
    run_id,
    tagged_at
from {{ source('enrich', 'message_tags') }}
