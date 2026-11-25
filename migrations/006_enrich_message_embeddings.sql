-- Message embeddings (D102, D103). One row per local-eligible user-text message: the vector of the
-- window around it (up to 2 neighbours each side in the same session). window_message_keys lists
-- every message whose text shaped the vector, so consent-prune can delete a row when any of them
-- stops being eligible (D105). window_tsv is the full-text form of the same window text (D104); it
-- and the vector are derived from message text and live only here. No raw text column: the window
-- text is rebuilt from raw.messages when needed. Search is exact, so there is no vector index.
-- purge-unlisted deletes rows explicitly (D106); ON DELETE CASCADE is only the backstop.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE enrich.message_embeddings (
    message_key         text PRIMARY KEY
                        REFERENCES raw.messages (message_key) ON DELETE CASCADE,
    window_message_keys text[] NOT NULL CHECK (message_key = ANY (window_message_keys)),
    embed_version       text NOT NULL,
    model_id            text NOT NULL,
    embedding           vector(1024) NOT NULL,
    window_tsv          tsvector NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now()
);
