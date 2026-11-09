-- Per-sender consent for external processing (decisions.md D69). Loaded by `gcw consent-load` from
-- the CSV ledger, all-or-nothing: the table is replaced in one transaction. Blank consent ('') means
-- undecided and is treated as "no" everywhere downstream. Names are display names, as exported.
CREATE TABLE raw.sender_consent (
    thread_id   text NOT NULL,
    sender_name text NOT NULL,
    consent     text NOT NULL CHECK (consent IN ('yes', 'no', '')),
    loaded_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (thread_id, sender_name)
);
