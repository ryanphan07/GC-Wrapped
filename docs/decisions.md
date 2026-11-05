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

### 2026-11-03 — Airflow in its own image, dbt in an isolated venv
Decision: Airflow runs from the official image; gcwrapped and dbt are installed in a separate virtualenv inside it, and tasks shell out to that venv.
Alternative: Install dbt into Airflow's environment; or use astronomer-cosmos to render dbt models as tasks.
Why: Airflow pins many dependencies via constraints files and dbt often conflicts with them; isolation is the common production pattern. One dbt build task is simpler to explain and debug than a task per model at this size.
Tradeoff: Per-model retries and lineage in the Airflow UI are lost; dbt's own logs and docs cover that.

### 2026-11-03 — Daily schedule with full idempotent re-ingest
Decision: The DAG runs daily, catchup off, one active run, re-ingesting every export in the landing dir.
Alternative: A file sensor plus a manifest of already-loaded exports.
Why: ON CONFLICT on a stable message_key makes re-ingest a no-op for known messages, so the simplest schedule is also correct; retries are safe for the same reason.
Tradeoff: Each run re-parses all exports; fine at group-chat scale, revisit if parse time grows.

### 2026-11-03 — Landing dir logic lives in the gcw CLI, installed editable in the image
Decision: Finding export roots (gcw check-landing, gcw ingest --landing DIR) and the run summary (gcw last-run) are Python in src/gcwrapped; DAG tasks are one-line shell commands. The image installs gcwrapped editable from /opt/gcw so migrations/ is found next to src/.
Alternative: Compute roots and query load_runs in the DAG file (Airflow imports project code); or install a wheel and add a MIGRATIONS_DIR env override.
Why: Keeps the DAG free of business logic and of project imports (the point of the isolated venv), and makes the logic unit-testable without Airflow. Editable install avoids a config knob and a code change to migrate.py.
Tradeoff: The runtime image is not a pure installed package; source at /opt/gcw is part of it.

### 2026-11-03 — DAG tests run in a test image stage
Decision: airflow/Dockerfile has a runtime stage and a test stage (runtime + pytest + airflow/tests). CI runs the tests with docker run, then airflow dags test on the runtime image.
Alternative: pip-install pytest in the container at CI time, or ship pytest in the runtime image.
Why: The tests need Airflow's exact environment, and the runtime image should not carry test tooling.
Tradeoff: Two image targets to keep in sync; layers are shared so the extra build is seconds.

### 2026-11-03 — Streamlit for the Phase 1 dashboard
Decision: The Phase 1 dashboard is a Streamlit app reading the analytics marts.
Alternative: Metabase or Superset; or the Phase 3 React frontend now.
Why: Dashboard code is versioned and testable in CI (AppTest); Metabase stores dashboards in its own app DB, configured by clicking. React belongs to Phase 3 with the API.
Tradeoff: Less polished than a BI tool; replaced or complemented by the React app later.

### 2026-11-03 — Read-only reader role scoped to marts
Decision: The dashboard connects as gcw_reader, which has USAGE on analytics and SELECT on mart tables only, granted via dbt grants config. CI asserts permission denied on raw and staging.
Alternative: Reuse the pipeline's read-write user.
Why: Message text exists only in raw and staging; scoping the role makes "text never leaves staging" a database guarantee, not a convention.
Tradeoff: Every new mart needs grants (handled by dbt config); one more credential to manage.

### 2026-11-03 — Warehouse tests get their own pytest marker
Decision: Tests that need a built warehouse (dbt marts plus the reader role) use the `warehouse` marker and `make test-warehouse`; `make test` excludes both `integration` and `warehouse`. Streamlit and pandas live in the `dashboard` uv group, and mypy ignores missing imports for them so lint does not need that group.
Alternative: Mark them `integration`; or put the dashboard group in the default sync.
Why: `integration` tests run before dbt build in CI and reset schemas, so tests that read marts would fail there. A separate marker keeps the order explicit and keeps the lint job's install small.
Tradeoff: Another marker to remember; DataFrames are untyped (Any) under mypy.

### 2026-11-03 — One-command e2e via compose and the DAG, verified in CI
Decision: `make e2e ENV_FILE=…` starts Postgres and the dashboard with compose and runs the Airflow DAG once; CI runs the same command on synthetic data, twice, to prove idempotency.
Alternative: A separate script that bypasses Airflow; or testing compose only by hand.
Why: The real-data run should use exactly the code path CI verifies; the second run proves re-ingest safety end to end.
Tradeoff: CI is slower (image builds, compose startup).

### 2026-11-03 — Single source of truth for the target database
Decision: One env variable (GCW_DB_NAME) decides the target database for gcw, dbt and the dashboard; a mismatch fails fast. gcw and the dashboard build their URLs from components, and an explicit DATABASE_URL / DASHBOARD_DATABASE_URL is accepted only if it names the same database. dbt's profile reads GCW_DB_NAME with no default.
Alternative: Separate variables per tool.
Why: With separate variables, a real run could ingest into gcw_real and build dbt models in gcw_dev without any error.
Tradeoff: Tool-native config (DATABASE_URL, profiles) is derived rather than set directly.

### 2026-11-03 — Replace lone surrogates in the parser
Decision: clean_text replaces lone UTF-16 surrogates with U+FFFD before hashing.
Alternative: Let the load fail.
Why: Postgres rejects them, and one bad row rolls back the entire load.
Tradeoff: The original code unit is lost; it was not displayable text anyway.

### 2026-11-03 — Project display name "GC Wrapped"; compose name pinned
Decision: The display name is "GC Wrapped" with no subtitle; the repo slug matches the renamed GitHub repo (GC-Wrapped); code identifiers (gcwrapped, gcw, gcw_* DBs, GCW_* vars) are unchanged; docker-compose sets an explicit project name (`name: gc-wrapped`).
Alternative: Rename the package and identifiers too; leave compose naming to the folder name.
Why: Identifiers already match the name, and renaming them breaks imports and existing env files. Compose derives volume names from the folder by default, so renaming the folder would orphan the database volume.
Tradeoff: The package name (gcwrapped) and display name differ slightly in form. Existing volumes created under the old folder-derived project name are not picked up by `gc-wrapped`; they must be migrated once (see STATUS).

### 2026-11-03 — Separate schemas for staging and marts
Decision: Staging and intermediate models build into `staging`; marts build into `analytics`. gcw_reader has USAGE on analytics only.
Alternative: One schema, isolated by per-relation grants.
Why: Message text exists only in raw and staging. Schema-level isolation means a future grant misconfiguration on a single model cannot expose text to the reader role.
Tradeoff: A custom generate_schema_name macro to maintain.

### 2026-11-03 — Dashboard never renders error details
Decision: Streamlit runs with client.showErrorDetails = false; config and connection errors show a fixed message. An AppTest asserts a sentinel password never renders.
Alternative: Default Streamlit error display.
Why: Validation errors include input values (connection settings, passwords), and Streamlit shows tracebacks in the browser by default.
Tradeoff: Debugging needs the server logs instead of the browser.

### 2026-11-03 — Dead config removed; CHAT_TIMEZONE is read by dbt
Decision: dbt's gcw_timezone var reads env_var('CHAT_TIMEZONE', 'America/Los_Angeles'), and compose passes it to the Airflow container. The unused LANDING_DIR and chat_timezone fields were removed from Settings (the DAG reads GCW_LANDING_DIR, set by compose).
Alternative: Keep the reserved fields in Settings.
Why: Nothing in Python read them, so changing them silently did nothing; the one consumer of the time zone is dbt.
Tradeoff: Settings has no time zone field; if Python code needs it later, add it back with a reader.

### 2026-11-03 — Separate compose stack for real data
Decision: Real-data runs use their own compose project (gc-wrapped-real) with a separate Postgres volume and superuser password, set only in an env file outside the repo.
Alternative: One Postgres cluster hosting both gcw_dev and gcw_real.
Why: The dev .env lives in the repo where the coding agent can read it; with a shared cluster, the dev superuser could read real messages. Separate stacks mean repo credentials can't reach real data.
Tradeoff: Two stacks to manage; the real stack is started only for runs and stopped afterward.

### 2026-11-03 — The Makefile includes ENV_FILE, not always .env
Decision: The Makefile does `-include $(ENV_FILE)` (default .env) and every compose call passes `--env-file "$(ENV_FILE)"`.
Alternative: Keep `-include .env` and rely on --env-file alone.
Why: Compose gives shell variables precedence over --env-file (verified), and `export` puts .env values in the shell. A run with ENV_FILE=<other> would have silently used the dev passwords and database name from .env.
Tradeoff: ENV_FILE is parsed by make, so values must be plain KEY=value (no quotes or spaces), as in .env.example.

## 2026-11-04 — Real-data guardrails: gitignore real.env, deny Read/Edit/Write, single private root
Decision: real.env lives at ~/gcw-private/real.env (outside the repo); `real.env` in .gitignore is a backstop. All real data and private scripts live under ~/gcw-private (landing/, local/, archive/), denied to Claude Code across Read/Edit/Write/Bash.
Alternative: Bash-only deny patterns and an in-repo real.env (the previous state).
Why: real.env was not gitignored (only .env was), and Bash-only rules left the Read tool able to open it. One private root means one set of deny rules covers everything real.
Tradeoff: the private root is a fixed convention that must be documented for anyone cloning the repo.

## 2026-11-04 — Format discovery via a content-free inspector, run only by Ryan
Decision: the schema inspector emits aggregates only (counts, key frequencies, masked URI shapes) and is tested against a sentinel fixture that asserts no name or content leaks. Claude Code builds and tests it on synthetic data; only Ryan runs it on real exports.
Alternative: inspect real files by hand, or let Claude Code read samples.
Why: the format must be learned from real data, but no real names or content may reach a third-party API or the repo. A leak test turns "I was careful" into an enforced property.
Tradeoff: aggregates can hide rare edge cases; the shape buckets ("other", <x>) surface them as counts to investigate.

## 2026-11-04 — Inspector name gates are allowlists, and the leak fixture is adversarial to them
Decision: URI segments, file extensions, and inbox siblings print only if they're in a fixed allowlist of structural names; everything else is <x>. The sentinel fixture includes a lowercase sentinel placed where a name could pass a lowercase pattern.
Alternative: pattern gates ([a-z_]+) with uppercase-only sentinels (the previous state).
Why: real Instagram folder names are lowercased usernames, which pass [a-z_]+. Uppercase sentinels are always masked by the pattern itself, so the old test could never detect a lowercase leak. Deny-by-default plus an adversarial fixture closes that gap.
Tradeoff: new structural folders show as <x> until added to the allowlist deliberately. JSON key names still use a pattern gate (an allowlist would defeat key discovery); Ryan's manual review before committing the report is the backstop.

## 2026-11-04 — Claude Code implements all code; retire the write-it-yourself workflow
Decision: Claude Code implements all code, including privacy-critical functions; Ryan makes design choices in the planning chat instead of writing implementations.
Alternative: Scaffold-only "write it yourself" functions (tests + signatures, NotImplementedError) for Ryan to implement.
Why: Hand implementation was the bottleneck during recruiting; design choices and close diff review carry the interview value.
Tradeoff: Less hands-on implementation practice; Ryan must review every diff closely enough to explain each line.

## 2026-11-04 — thread_id is the folder's trailing numeric ID
Decision: thread_id = the digit run that is either the whole folder name or follows its last underscore (<name>_<digits> or <digits>). Any other folder name raises, and the error never includes the folder name.
Alternative: the full folder name.
Why: the real export has 416 <name>_<digits> and 6 bare <digits> folders (15-17 digits). The name part embeds a participant's display name, which would put PII in every row and log line. It also changes when a chat is renamed, which would re-key the whole thread and break dedup across exports.
Tradeoff: a future export with a different folder scheme fails loudly instead of degrading silently.

## 2026-11-04 — Attachment identity is the URI basename; external URIs are not hashed
Decision: message_key hashes the sorted basenames of attachment URIs under your_instagram_activity/. URIs outside it (5 videos in the real export) count toward media_count but are not hashed. This supersedes the attachment-URI part of "2026-11-02 — Message dedup key".
Alternative: full URIs (the 2026-11-02 rule), or hashing external URIs too.
Why: full URIs embed the name-bearing thread folder and the export layout, so a rename changes every key. Basenames are unique within threads in the real export (0 duplicates). External URIs don't point at export files and may carry per-export query parameters, so hashing them risks unstable keys.
Tradeoff: two messages that differ only in an external attachment share an identity. The occurrence index still keeps both rows.

## 2026-11-04 — Real-format fields not mapped in Phase 1
Decision: the parser maps sender_name, timestamp_ms, content, title, photos/videos/audio_files (count plus basename for the key), share.link, and reactions (actor, reaction). It ignores call_duration, share.share_text, share.original_content_owner, share.profile_share_name/username, joinable_mode, image, magic_words, thread_path, is_still_participant, is_geoblocked_for_viewer, is_unsent_image_by_messenger_kid_parent, reaction timestamps and attachment creation_timestamp.
Alternative: load every field into raw.
Why: these fields either carry extra PII (joinable_mode.link is a live group invite link, profile_share_name is a person's name, thread_path embeds the folder name) or aren't used by any Phase 1 model. Adding a nullable raw column later is a small migration, and the daily full re-ingest backfills it.
Tradeoff: this departs from "parser is faithful". Raw holds less than the export.

## 2026-11-04 — raw.source_file stores thread_id/file name, not the export path
Decision: source_file = "<thread_id>/<message_N.json>".
Alternative: the path relative to the export root (previous behavior), or dropping the column.
Why: the export path contains the name-bearing thread folder, so every row, log line and failing dbt test would carry a display name. Identifier and lineage fields spread further than content columns, so they must be PII-free. Keeping the column preserves row-to-file lineage for debugging.
Tradeoff: locating the file on disk takes a lookup by digit suffix (one glob) instead of a direct path.

## 2026-11-05 — Consent allowlist: deny by default, no bypass switch
Decision: ingestion loads only threads whose numeric thread_id is listed in GCW_THREAD_ALLOWLIST (comma-separated digits). Threads are filtered at discovery, before any file in them is opened. Folders that don't match a thread-ID shape are skipped and counted, never parsed. A missing or empty allowlist makes ingest fail. There is no bypass flag: dev .env, .env.example and CI list the synthetic thread IDs explicitly.
Alternative: a GCW_SYNTHETIC=1 switch that disables the allowlist in dev, or filtering after parsing.
Why: consent is per chat, so it must be enforced in code at the earliest point. A bypass switch is one misconfigured variable away from ingesting every chat. Listing the synthetic IDs keeps a single code path in dev, CI and real. Filtering before reading means non-consented messages are never parsed into memory.
Tradeoff: adding a synthetic thread means updating the env files too. A mistyped real ID allowlists nothing, so check-landing reports allowlisted IDs that weren't found and fails when none match.

## 2026-11-05 — Format-survey tools read every thread; only ingestion is consent-gated
Decision: make inspect and make parse-check read the whole local export but print aggregates only and store nothing. Anything that loads data (gcw ingest, the Airflow DAG, make e2e, make real-ingest) requires the allowlist.
Alternative: gate the survey tools by the allowlist too.
Why: the format has to be validated on as much real data as possible. The full-inbox survey found 6 bare-digit folders and 5 external attachment URIs that a single chat might not contain. Counts that never leave Ryan's machine and are never stored don't put anyone's messages into the dataset.
Tradeoff: non-consented chats are read locally, in memory, for counts. This is disclosed in the README privacy section.

## 2026-11-05 — real-ingest prints allowlisted summary lines only; full logs stay private
Decision: make real-ingest ENV_FILE=<path> checks guards, runs check-landing and the e2e pipeline against that env's stack, writes the full output to GCW_LOG_DIR (set in the env file, outside the repo), and prints only lines that fully match a fixed set of patterns made of fixed labels and numbers.
Alternative: print the normal pipeline output, or strip known names with a denylist.
Why: Airflow logs, dbt output and Python tracebacks can echo row values, and a denylist can't know every name. Allowlisting output shapes is safe by construction and is leak-tested with sentinels.
Tradeoff: debugging a failed real run means opening the private log locally. The log is never pasted.

## 2026-11-05 — Gated and ungated parsing are separate functions
Decision: `parse_export_consented(roots, allowlist, stats)` is the only parser entry point ingestion uses; `parse_export` stays ungated for `gcw parse` and tests. Both share one dedupe and per-thread parse loop. Thread discovery for ingestion is `select_threads`, which decides from the folder name alone and lists inside allowlisted folders only. `find_export_roots` now only checks that an inbox folder exists, so it no longer lists thread folders.
Alternative: one `parse_export` with an optional `allowlist=None` argument, or a required keyword.
Why: an optional argument makes "ungated" the default and one forgotten argument a silent bypass. A required keyword would have touched about 60 test call sites with `allowlist=None` noise. Two names make the gated path greppable (the only caller is `_ingest`) and a unit test proves a skipped folder is never listed or read.
Tradeoff: the ungated function still exists, so a future ingest path could call it; the survey-tools decision above and review are the backstop.

## 2026-11-05 — real-ingest guards go beyond the brief in one place; make synth clears safely
Decision: real-ingest also refuses a GCW_LOG_DIR inside the repo. `make synth` empties its output directory but refuses a filesystem root, the home directory, a git checkout, or the working directory or a parent of it.
Alternative: only the guards listed in the brief; `rm -rf` of whatever --out names.
Why: the log holds raw pipeline output, and a log file inside the repo is one `git add .` from being committed. `--out` is a path typed on a command line, and recursive deletion of it should not be able to hit a project or home directory.
Tradeoff: a log directory must live elsewhere; `--out` must be a dedicated scratch folder.


## 2026-11-05 — real-ingest is verified end to end only in CI
Decision: Claude Code never runs make real-ingest locally (deny rule in .claude/settings.json plus CLAUDE.md). A CI job runs it on every PR against a synthetic env file with its own compose project, ports, database (gcw_ci) and a log dir outside the workspace, on the sentinel landing. The job fails if a sentinel string reaches the terminal, and it also runs a deliberately failing allowlisted thread to check the failure path.
Alternative: a CLAUDE.md exception letting the assistant run it locally with /tmp/gcw-synth-*.env files.
Why: permission rules match command prefixes and deny beats allow, so an exception could only be written as prose. Keeping the rule absolute keeps it tool-enforced. CI also turns the run into an end-to-end leak test against real Airflow, dbt and traceback output, which unit tests with hand-written noise cannot do.
Tradeoff: about 4-6 extra CI minutes per PR, and runner wiring bugs surface only after a push.

## 2026-11-05 — Host-side settings load only the file named by ENV_FILE
Decision: when ENV_FILE names a file other than .env, host-side gcw commands load settings from that file and the process environment only, never from the repo's .env. .env is used only when ENV_FILE is unset or is .env.
Alternative: env_file=".env" for every settings class, with process env overriding it (previous behavior).
Why: with the previous behavior, any key the real env file lacked was silently filled from the dev .env, so a host command could target the dev database or use the synthetic allowlist. One file per run is the only predictable rule.
Tradeoff: an env file must be complete. A missing key now fails or falls back to the code default instead of borrowing dev's value.
