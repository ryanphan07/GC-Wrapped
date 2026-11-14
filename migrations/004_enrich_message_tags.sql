-- Model-derived labels (D89). One row per (message, model, prompt version), so a change of model or
-- prompt adds rows instead of overwriting, and S4 can compare them. No message text is stored here.
-- Rows are deleted (pruned) by `gcw tag` when their message is no longer local_ok (D88).
CREATE SCHEMA enrich;

CREATE TABLE enrich.message_tags (
    message_key    text NOT NULL REFERENCES raw.messages (message_key) ON DELETE CASCADE,
    topic          text NOT NULL CHECK (topic IN (
        'plans', 'school', 'work', 'food', 'entertainment',
        'sports_fitness', 'relationships', 'banter', 'news', 'other'
    )),
    sentiment      text NOT NULL CHECK (sentiment IN ('positive', 'neutral', 'negative')),
    model_id       text NOT NULL,
    prompt_version text NOT NULL,
    run_id         text NOT NULL,
    tagged_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (message_key, model_id, prompt_version)
);
