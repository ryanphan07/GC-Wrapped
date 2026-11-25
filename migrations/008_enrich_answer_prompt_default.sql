-- The promoted answer prompt variant (D121): at most one row, written only by `gcw answer-promote`
-- after an answer-compare verdict that names the variant the winner. Answers use it; with no row
-- they use "a", the baseline. No message key or text here, so purge-unlisted has nothing to cover.
CREATE TABLE enrich.answer_prompt_default (
    singleton   boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    variant     text NOT NULL CHECK (variant IN ('a', 'b')),
    promoted_at timestamptz NOT NULL DEFAULT now()
);
