-- Counts of current tags per member (thread + display name), topic and sentiment (D89).
select
    member_key,
    thread_id,
    topic,
    sentiment,
    count(*) as messages
from {{ ref('fct_message_tags') }}
group by member_key, thread_id, topic, sentiment
