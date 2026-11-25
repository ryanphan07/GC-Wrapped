-- Every Wrapped profile belongs to a sender the consent ledger allows for local work (D88, D123),
-- checked against the ledger itself rather than the eligibility view the mart is built from.
-- Returns violating rows; the test passes when none come back.
select
    w.member_key
from {{ ref('mart_member_wrapped') }} as w
where not exists (
    select 1
    from {{ ref('stg_consent__sender_consent') }} as c
    where c.thread_id = w.thread_id
        and c.sender_name = w.member_name
        and c.consent in ('yes', 'local')
)
