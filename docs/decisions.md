# Decisions (append-only)

### 2026-10-29 — Business logic lives in the `gcwrapped` package; Airflow DAGs only call it
Chose this over putting logic inside DAG files so everything is testable and runnable without Airflow. Tradeoff: one more layer of indirection.

### 2026-10-29 — Airflow uses a landing-zone pattern, not a time-based schedule
New export file → ingest → dbt build → archive. Chose this over a cron schedule because exports are manual files. Tradeoff: runs depend on someone dropping a file.

### 2026-10-29 — Use the pgvector Postgres image from day one
Chose `pgvector/pgvector:pg17` over plain Postgres so Phase 3 needs no database migration. Tradeoff: slightly less standard image.

### 2026-10-29 — Separate `gcw_dev` (synthetic) and `gcw_real` databases; Claude Code never reads real data
Chose this over a single database because Claude Code is a third-party API. Tradeoff: two databases to manage, and real-data bugs must be reproduced with synthetic fixtures.

### 2026-10-29 — Phase 1 stores attachment metadata only
Any later analysis of image or video content runs on a local model only, since images can't be reliably redacted. Tradeoff: no media insights until Phase 2+.

### 2026-10-29 — dbt in its own uv dependency group; Airflow never in the project venv
Chose this to avoid dependency conflicts. Tradeoff: multiple environments.

### 2026-10-29 — Proceed despite existing "chat Wrapped" products
Many consumer apps and OSS repos already generate Wrapped-style chat stats (WhatsApp, Instagram, Messenger, iMessage). Chose to proceed over switching domains because the project's value is the pipeline (Airflow + dbt + tested warehouse models), evaluated RAG, the privacy architecture, and real users. None of the existing tools demonstrate those, and real users are far easier to get with friends' own group chat. Tradeoff: the Wrapped UI itself is not differentiated, so keep it thin and position the project as a data platform with evaluated RAG.

### 2026-10-29 — Instagram DMs as the first platform
Chose Instagram's JSON data export over iMessage (needs a Mac, which Ryan doesn't have), Messenger (end-to-end encrypted and older chats come in two separate export formats), and TikTok (text-only DMs, no media). Instagram includes all members' messages, reactions, shared links, and media, and any member can export it. Tradeoff: no per-message IDs, so the dedup key must be derived; Meta's JSON mis-encodes non-ASCII text, so the parser must repair it.

### 2026-11-02 — Message dedup key
Decision: message_key = sha256(platform | thread_id | timestamp_ms | sender_name | content | sorted attachment URIs | occurrence), where occurrence is the 0-based index among messages identical on all other fields. Text is mojibake-fixed before hashing. Loader enforces uniqueness on message_key (ON CONFLICT DO NOTHING).
Alternative: hash without an occurrence index; or use file position as the ID.
Why: Meta exports have no message IDs, and each new export re-includes full history, so the key must be stable across exports. File position changes when files are re-chunked. The occurrence index is stable because messages sharing every other field are interchangeable, so their order doesn't matter.
Tradeoff: Instagram exports display names, not usernames. A renamed member produces new keys, so overlapping exports would duplicate. Accepted for Phase 1; fix later with a member alias mapping (dbt seed).

### 2026-11-02 — Parser is faithful; cleaning lives in dbt (ELT)
Decision: The parser fixes encoding and structure only. System-message detection, local-time conversion and member identity are handled in dbt staging models.
Alternative: Clean and classify in Python at parse time.
Why: Raw data stays reloadable when cleaning rules change; transformations are versioned, tested SQL; this matches how data teams use dbt.
Tradeoff: Staging models carry more logic; raw tables contain noise.

### 2026-11-02 — Real data isolated from Claude Code
Decision: Real exports and gcw_real credentials live outside the repo (~/gcw-private/). Claude Code works only with synthetic data in gcw_dev. .claude/settings.json deny rules are a backstop.
Alternative: Gitignored data/ folder inside the repo.
Why: Gitignore stops commits, not reads. Claude Code is a third-party API, so it must not be able to reach real messages.
Tradeoff: Real-data runs are manual and happen in Ryan's terminal; only aggregate counts are reported back.

### 2026-11-02 — Format spec via a value-free schema inspector
Decision: The Instagram format spec is produced by scripts/inspect_export.py, run locally by Ryan on the real export. It outputs structure only (key paths, types, presence, mojibake rate, array lengths), never values. A sentinel test enforces this.
Alternative: Hand-written spec; or letting the coding agent read a sample export.
Why: The coding agent must never see real messages, but the parser needs an accurate format spec.
Tradeoff: Semantics that need content (e.g. system-message phrasings) aren't captured; those get handled later in dbt.

### 2026-11-02 — Build the parser against a provisional public-format spec
Chose to build the parser and synthetic generator now from Instagram's publicly described JSON format (community parsers; Meta publishes no schema) over waiting for Ryan's inspector output on a real export. All format assumptions live in `src/gcwrapped/parsers/instagram_format.py`, shared by parser and generator, and `docs/formats/instagram-schema.md` is marked PROVISIONAL. Tradeoff: if the real export differs (root path, share/reaction shapes, uri stability), some rework is needed, but it should be confined to that module and the generator.

### 2026-11-02 — message_key covers immutable fields only
Decision: Add share_url to message_key. The key hashes every immutable field (thread, timestamp, sender, content, attachment URIs, share URL) and excludes mutable ones (reactions).
Alternative: Keep share_url out and rely on the occurrence index to separate such messages.
Why: The occurrence index is only order-independent when messages in a group are truly identical; including all immutable fields keeps that invariant true. Reactions change after export, so including them would change keys between exports.
Tradeoff: A message edited after an earlier export gets a new key and duplicates; accepted as rare.

### 2026-11-02 — Raw tables via plain SQL migrations
Decision: Raw landing tables are created by numbered SQL files applied by `gcw migrate`, tracked in schema_migrations.
Alternative: Alembic; or CREATE TABLE IF NOT EXISTS inside the loader.
Why: Raw schemas are small and SQL-native; a 50-line runner is fully explainable and has no ORM dependency. IF NOT EXISTS can't evolve a schema.
Tradeoff: No autogenerated migrations or downgrade path; revisit if the schema churns.

### 2026-11-02 — Idempotent bulk load: COPY + ON CONFLICT DO NOTHING
Decision: Each load COPYs parsed rows into a temp table, then inserts into raw.messages with ON CONFLICT (message_key) DO NOTHING, in one transaction. Every run is recorded in raw.load_runs.
Alternative: Row-by-row INSERTs; or truncate-and-reload.
Why: COPY is Postgres's fastest bulk path; ON CONFLICT on a stable key makes re-runs and overlapping exports safe, which Airflow retries will need. load_runs gives runtime and row counts for METRICS.md.
Tradeoff: First-write-wins: a later export never updates an existing row (e.g. new reactions). Acceptable for Phase 1; revisit if reactions matter for Wrapped.

### 2026-11-03 — Content-free error paths in the loader
Decision: Exceptions raised during a load are wrapped in a LoadError carrying only exception class, row index and field name; only that summary is stored in raw.load_runs.error. Sentinel tests enforce it.
Alternative: Store and raise the original exception text.
Why: Exception text can echo message content into the Postgres server log, the load_runs table, and anything that gets pasted while debugging.
Tradeoff: Less detail when debugging a real-data failure; reproduce it locally with the same row index instead.

### 2026-11-03 — dbt layering and materializations
Decision: staging (views) → intermediate → marts (tables), with fct_/dim_/agg_ naming. Full rebuilds; no incremental models.
Alternative: Incremental fact models; or a single layer of models.
Why: Standard dbt structure keeps cleaning, logic and presentation separate and testable. At group-chat volume a full rebuild takes seconds, so incremental logic would add complexity for no benefit.
Tradeoff: Rebuild time grows linearly with data; revisit incremental only if a build exceeds about a minute.

### 2026-11-03 — message_type priority and dbt version floor
Decision: stg_instagram__messages.message_type is media (media_count > 0), else share (share_url set), else text (non-whitespace content), else empty. dbt-postgres is pinned >=1.10 so generic tests can use the `arguments:` property.
Alternative: Classify by content alone; or keep >=1.8 and the older top-level test args.
Why: Photos and shares often carry a caption, and the attachment is what makes them that kind of message. The older test syntax is deprecated and warns on every parse.
Tradeoff: A captioned photo counts as media, not text; word_count still counts its caption. Floor of 1.10 is above the brief's 1.8 minimum.

### 2026-11-03 — Sessions, turns and reply time definitions
Decision: Session = messages with no gap > session_gap_minutes (default 60). Turn = consecutive same-sender messages within a session. Reply time = gap from the end of one turn to the start of the next turn by a different sender, within a session only. Thresholds are dbt vars.
Alternative: Use platform reply-to metadata; or measure every sender change regardless of gap.
Why: Instagram exports have no reply-to IDs. Without a session bound, "replying" the next morning would dominate the averages. Turns stop rapid multi-message bursts from counting as many replies.
Tradeoff: Results depend on the threshold; replies to older messages within a busy session are attributed to the most recent turn.

### 2026-11-03 — dbt unit tests for transformation logic
Decision: Window-function logic (sessions, turns, reply time) is covered by dbt unit tests with fixed inputs and expected outputs; generic and singular tests cover data shape.
Alternative: Rely only on generic tests against synthetic data.
Why: Generic tests catch nulls and duplicates, not wrong logic. Unit tests pin down edge cases at the threshold boundaries.
Tradeoff: More YAML to maintain when the models change.

### 2026-11-03 — dim_members includes reactors who never sent a message
Decision: dim_members is the union of (thread, name) pairs seen as senders and as reactors; non-senders have message_count 0 and null first/last message times. Reaction counts in agg_member_activity exclude self-reactions.
Alternative: Senders only, with fct_reactions allowed to fail its relationships test; or count self-reactions.
Why: Lurkers who only react are common in real group chats, and a senders-only dimension would break the fact-to-dim relationship test on real data. Self-reactions say nothing about how others engage with a member.
Tradeoff: The dimension is slightly broader than "senders"; consumers must treat first/last_message_at as nullable.

### 2026-11-03 — Dedupe reactions in staging
Decision: stg_instagram__reactions drops exact duplicate (message_key, actor, emoji) rows; the unique test stays at error severity, and a warn-level test checks (message_key, actor).
Alternative: Relax the unique test, or fail the build on export artifacts.
Why: Instagram allows one reaction per person per message, so exact repeats are export artifacts. The strict test then guards the dedup; the warn test reveals whether real exports ever contain multiple reactions per person.
Tradeoff: If real exports do carry legitimate multiple reactions per person, the warn test will flag it and the model needs revisiting.

### 2026-11-03 — Strip NUL bytes in the parser
Decision: The parser removes \x00 from text fields before hashing.
Alternative: Let the load fail on NUL bytes.
Why: Postgres text cannot store NUL, and server-side errors echo the offending value into the server log. Since ParsedMessage already guarantees types, this closes the last realistic path for content to reach the log. log_error_verbosity=terse is a backstop.
Tradeoff: A small deviation from the faithful-parser rule, limited to a byte that no chat client displays.

### 2026-11-03 — Sessions attributed to their start date
Decision: Daily aggregates count a session once, on the local date of its first message (sessions_started).
Alternative: Count a session on every day it has messages.
Why: Summing daily sessions should equal total sessions; counting per day double-counts sessions that cross midnight.
Tradeoff: Late-night sessions appear entirely on the day they started.
