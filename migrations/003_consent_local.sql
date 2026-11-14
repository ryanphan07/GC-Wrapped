-- D88: the ledger gains a second opt-in level, "local" (this machine only). The CHECK from 002 only
-- allows yes, no and blank, so it is replaced; 002 itself is never edited (it is already applied
-- on existing databases). Rows already loaded stay valid.
ALTER TABLE raw.sender_consent DROP CONSTRAINT sender_consent_consent_check;
ALTER TABLE raw.sender_consent
    ADD CONSTRAINT sender_consent_consent_check CHECK (consent IN ('yes', 'local', 'no', ''));
