-- One row per message. Types, names and light derivations only; no joins, no filtering.
with source as (

    select * from {{ source('raw', 'messages') }}

),

renamed as (

    select
        message_key,
        platform,
        thread_id,
        thread_title,
        sender_name,
        sent_at,
        sent_at at time zone '{{ var("gcw_timezone") }}' as sent_at_local,
        content,
        coalesce(content ~ '\S', false) as has_content,
        media_count,
        share_url,
        source_file,
        load_id,
        loaded_at
    from source

)

select
    *,
    case
        when has_content
            then cardinality(regexp_split_to_array(regexp_replace(content, '^\s+|\s+$', '', 'g'), '\s+'))
        else 0
    end as word_count,
    case
        when media_count > 0 then 'media'
        when share_url is not null then 'share'
        when has_content then 'text'
        else 'empty'
    end as message_type
from renamed
