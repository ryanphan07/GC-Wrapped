-- The promoted retrieval method (D110): at most one row, written only by `gcw retrieval-promote`
-- after a retrieval-compare verdict that names the method the winner. Answers use it; with no row
-- they use vector, the D104 baseline. No message key or text here, so purge-unlisted has nothing
-- to cover.
CREATE TABLE enrich.retrieval_default (
    singleton   boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    method      text NOT NULL CHECK (method IN ('vector', 'hybrid')),
    promoted_at timestamptz NOT NULL DEFAULT now()
);
