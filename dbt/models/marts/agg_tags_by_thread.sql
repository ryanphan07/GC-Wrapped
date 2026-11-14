-- Counts of current tags per thread, topic and sentiment (D89). Counts only; for profiles.
select
    thread_id,
    topic,
    sentiment,
    count(*) as messages
from {{ ref('fct_message_tags') }}
group by thread_id, topic, sentiment
