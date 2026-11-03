-- A reply never crosses a session boundary, so 0 <= reply_seconds <= the session gap.
-- Returns violating rows; the test passes when none come back.
select
    message_key,
    reply_seconds
from {{ ref('fct_messages') }}
where reply_seconds < 0
    or reply_seconds > {{ var('session_gap_minutes') }} * 60
