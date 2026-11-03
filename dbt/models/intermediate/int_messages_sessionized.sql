-- One row per message with its session, turn and reply time. Window functions only.
--   session: a gap > session_gap_minutes from the previous message in the thread starts a new one
--   turn:    a new session, or a different sender than the previous message, starts a new one
--   reply:   first message of a turn that is not also the first of a session; the previous message
--            is by definition the last message of the previous turn
with messages as (

    select message_key, thread_id, sender_name, sent_at
    from {{ ref('stg_instagram__messages') }}

),

with_previous as (

    select
        *,
        lag(sent_at) over thread_order as prev_sent_at,
        lag(sender_name) over thread_order as prev_sender_name
    from messages
    window thread_order as (partition by thread_id order by sent_at, message_key)

),

boundaries as (

    select
        *,
        coalesce(
            sent_at - prev_sent_at > make_interval(mins => {{ var('session_gap_minutes') }}),
            true
        ) as is_session_start
    from with_previous

),

turn_boundaries as (

    select
        *,
        is_session_start or sender_name is distinct from prev_sender_name as is_turn_start
    from boundaries

),

numbered as (

    select
        *,
        sum(is_session_start::int) over thread_running as session_index,
        sum(is_turn_start::int) over thread_running as turn_index
    from turn_boundaries
    window thread_running as (
        partition by thread_id order by sent_at, message_key
        rows between unbounded preceding and current row
    )

)

select
    message_key,
    thread_id,
    thread_id || ':s' || session_index as session_id,
    session_index::int as session_index,
    thread_id || ':t' || turn_index as turn_id,
    is_session_start,
    is_turn_start,
    case
        when is_turn_start and not is_session_start
            then extract(epoch from sent_at - prev_sent_at)::double precision
    end as reply_seconds
from numbered
