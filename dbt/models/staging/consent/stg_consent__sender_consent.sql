-- One row per (thread, sender display name) in the loaded consent ledger. Renames only.
select
    thread_id,
    sender_name,
    consent,
    loaded_at
from {{ source('raw', 'sender_consent') }}
