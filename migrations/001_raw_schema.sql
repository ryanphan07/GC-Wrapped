-- Raw landing tables. Loaded by `gcw ingest`; cleaning happens later in dbt (see decisions.md).
CREATE SCHEMA raw;

-- One row per `gcw ingest` run. Written outside the message transaction so a failed run is
-- still recorded. `error` holds an exception type name only, never message text.
CREATE TABLE raw.load_runs (
    load_id       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    started_at    timestamptz NOT NULL DEFAULT now(),
    finished_at   timestamptz,
    status        text NOT NULL DEFAULT 'running'
                  CHECK (status IN ('running', 'succeeded', 'failed')),
    roots_count   integer NOT NULL,
    rows_parsed   integer,
    rows_inserted integer,
    error         text
);

-- message_key is the dedup key from the parser; first write wins (ON CONFLICT DO NOTHING).
CREATE TABLE raw.messages (
    message_key  text PRIMARY KEY,
    platform     text NOT NULL,
    thread_id    text NOT NULL,
    thread_title text,
    sender_name  text NOT NULL,
    sent_at      timestamptz NOT NULL,
    content      text,
    media_count  integer NOT NULL,
    share_url    text,
    reactions    jsonb NOT NULL DEFAULT '[]'::jsonb,
    source_file  text NOT NULL,
    load_id      bigint NOT NULL REFERENCES raw.load_runs (load_id),
    loaded_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX messages_load_id_idx ON raw.messages (load_id);
