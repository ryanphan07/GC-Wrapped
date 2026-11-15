-- The promoted tagging model (D92): at most one row, written only by `gcw tag-promote` after a
-- winning tag-compare verdict. fct_message_tags shows only this model's tags; with no row it falls
-- back to the D90 default. No message key or text here, so purge-unlisted has nothing to cover.
CREATE TABLE enrich.tag_default (
    singleton   boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    model_id    text NOT NULL,
    promoted_at timestamptz NOT NULL DEFAULT now()
);
