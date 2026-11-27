# Decisions (append-only)

## D1 — 2026-10-29 — Business logic lives in the `gcwrapped` package; Airflow DAGs only call it
Chose this over putting logic inside DAG files so everything is testable and runnable without Airflow. Tradeoff: one more layer of indirection.

## D2 — 2026-10-29 — Airflow uses a landing-zone pattern, not a time-based schedule
New export file → ingest → dbt build → archive. Chose this over a cron schedule because exports are manual files. Tradeoff: runs depend on someone dropping a file.

## D3 — 2026-10-29 — Use the pgvector Postgres image from day one
Chose `pgvector/pgvector:pg17` over plain Postgres so Phase 3 needs no database migration. Tradeoff: slightly less standard image.

## D4 — 2026-10-29 — Separate `gcw_dev` (synthetic) and `gcw_real` databases; Claude Code never reads real data
Chose this over a single database because Claude Code is a third-party API. Tradeoff: two databases to manage, and real-data bugs must be reproduced with synthetic fixtures.

## D5 — 2026-10-29 — Phase 1 stores attachment metadata only
Any later analysis of image or video content runs on a local model only, since images can't be reliably redacted. Tradeoff: no media insights until Phase 2+.

## D6 — 2026-10-29 — dbt in its own uv dependency group; Airflow never in the project venv
Chose this to avoid dependency conflicts. Tradeoff: multiple environments.

## D7 — 2026-10-29 — Proceed despite existing "chat Wrapped" products
Many consumer apps and OSS repos already generate Wrapped-style chat stats (WhatsApp, Instagram, Messenger, iMessage). Chose to proceed over switching domains because the project's value is the pipeline (Airflow + dbt + tested warehouse models), evaluated RAG, the privacy architecture, and real users. None of the existing tools demonstrate those, and real users are far easier to get with friends' own group chat. Tradeoff: the Wrapped UI itself is not differentiated, so keep it thin and position the project as a data platform with evaluated RAG.

## D8 — 2026-10-29 — Instagram DMs as the first platform
Chose Instagram's JSON data export over iMessage (needs a Mac, which Ryan doesn't have), Messenger (end-to-end encrypted and older chats come in two separate export formats), and TikTok (text-only DMs, no media). Instagram includes all members' messages, reactions, shared links, and media, and any member can export it. Tradeoff: no per-message IDs, so the dedup key must be derived; Meta's JSON mis-encodes non-ASCII text, so the parser must repair it.

## D9 — 2026-11-02 — Message dedup key
Decision: message_key = sha256(platform | thread_id | timestamp_ms | sender_name | content | sorted attachment URIs | occurrence), where occurrence is the 0-based index among messages identical on all other fields. Text is mojibake-fixed before hashing. Loader enforces uniqueness on message_key (ON CONFLICT DO NOTHING).
Alternative: hash without an occurrence index; or use file position as the ID.
Why: Meta exports have no message IDs, and each new export re-includes full history, so the key must be stable across exports. File position changes when files are re-chunked. The occurrence index is stable because messages sharing every other field are interchangeable, so their order doesn't matter.
Tradeoff: Instagram exports display names, not usernames. A renamed member produces new keys, so overlapping exports would duplicate. Accepted for Phase 1; fix later with a member alias mapping (dbt seed).

## D10 — 2026-11-02 — Parser is faithful; cleaning lives in dbt (ELT)
Decision: The parser fixes encoding and structure only. System-message detection, local-time conversion and member identity are handled in dbt staging models.
Alternative: Clean and classify in Python at parse time.
Why: Raw data stays reloadable when cleaning rules change; transformations are versioned, tested SQL; this matches how data teams use dbt.
Tradeoff: Staging models carry more logic; raw tables contain noise.

## D11 — 2026-11-02 — Real data isolated from Claude Code
Decision: Real exports and gcw_real credentials live outside the repo (~/gcw-private/). Claude Code works only with synthetic data in gcw_dev. .claude/settings.json deny rules are a backstop.
Alternative: Gitignored data/ folder inside the repo.
Why: Gitignore stops commits, not reads. Claude Code is a third-party API, so it must not be able to reach real messages.
Tradeoff: Real-data runs are manual and happen in Ryan's terminal; only aggregate counts are reported back.

## D12 — 2026-11-02 — Format spec via a value-free schema inspector
Decision: The Instagram format spec is produced by scripts/inspect_export.py, run locally by Ryan on the real export. It outputs structure only (key paths, types, presence, mojibake rate, array lengths), never values. A sentinel test enforces this.
Alternative: Hand-written spec; or letting the coding agent read a sample export.
Why: The coding agent must never see real messages, but the parser needs an accurate format spec.
Tradeoff: Semantics that need content (e.g. system-message phrasings) aren't captured; those get handled later in dbt.

## D13 — 2026-11-02 — Build the parser against a provisional public-format spec
Chose to build the parser and synthetic generator now from Instagram's publicly described JSON format (community parsers; Meta publishes no schema) over waiting for Ryan's inspector output on a real export. All format assumptions live in `src/gcwrapped/parsers/instagram_format.py`, shared by parser and generator, and `docs/formats/instagram-schema.md` is marked PROVISIONAL. Tradeoff: if the real export differs (root path, share/reaction shapes, uri stability), some rework is needed, but it should be confined to that module and the generator.

## D14 — 2026-11-02 — message_key covers immutable fields only
Decision: Add share_url to message_key. The key hashes every immutable field (thread, timestamp, sender, content, attachment URIs, share URL) and excludes mutable ones (reactions).
Alternative: Keep share_url out and rely on the occurrence index to separate such messages.
Why: The occurrence index is only order-independent when messages in a group are truly identical; including all immutable fields keeps that invariant true. Reactions change after export, so including them would change keys between exports.
Tradeoff: A message edited after an earlier export gets a new key and duplicates; accepted as rare.

## D15 — 2026-11-02 — Raw tables via plain SQL migrations
Decision: Raw landing tables are created by numbered SQL files applied by `gcw migrate`, tracked in schema_migrations.
Alternative: Alembic; or CREATE TABLE IF NOT EXISTS inside the loader.
Why: Raw schemas are small and SQL-native; a 50-line runner is fully explainable and has no ORM dependency. IF NOT EXISTS can't evolve a schema.
Tradeoff: No autogenerated migrations or downgrade path; revisit if the schema churns.

## D16 — 2026-11-02 — Idempotent bulk load: COPY + ON CONFLICT DO NOTHING
Decision: Each load COPYs parsed rows into a temp table, then inserts into raw.messages with ON CONFLICT (message_key) DO NOTHING, in one transaction. Every run is recorded in raw.load_runs.
Alternative: Row-by-row INSERTs; or truncate-and-reload.
Why: COPY is Postgres's fastest bulk path; ON CONFLICT on a stable key makes re-runs and overlapping exports safe, which Airflow retries will need. load_runs gives runtime and row counts for METRICS.md.
Tradeoff: First-write-wins: a later export never updates an existing row (e.g. new reactions). Acceptable for Phase 1; revisit if reactions matter for Wrapped.

## D17 — 2026-11-03 — Content-free error paths in the loader
Decision: Exceptions raised during a load are wrapped in a LoadError carrying only exception class, row index and field name; only that summary is stored in raw.load_runs.error. Sentinel tests enforce it.
Alternative: Store and raise the original exception text.
Why: Exception text can echo message content into the Postgres server log, the load_runs table, and anything that gets pasted while debugging.
Tradeoff: Less detail when debugging a real-data failure; reproduce it locally with the same row index instead.

## D18 — 2026-11-03 — dbt layering and materializations
Decision: staging (views) → intermediate → marts (tables), with fct_/dim_/agg_ naming. Full rebuilds; no incremental models.
Alternative: Incremental fact models; or a single layer of models.
Why: Standard dbt structure keeps cleaning, logic and presentation separate and testable. At group-chat volume a full rebuild takes seconds, so incremental logic would add complexity for no benefit.
Tradeoff: Rebuild time grows linearly with data; revisit incremental only if a build exceeds about a minute.

## D19 — 2026-11-03 — message_type priority and dbt version floor
Decision: stg_instagram__messages.message_type is media (media_count > 0), else share (share_url set), else text (non-whitespace content), else empty. dbt-postgres is pinned >=1.10 so generic tests can use the `arguments:` property.
Alternative: Classify by content alone; or keep >=1.8 and the older top-level test args.
Why: Photos and shares often carry a caption, and the attachment is what makes them that kind of message. The older test syntax is deprecated and warns on every parse.
Tradeoff: A captioned photo counts as media, not text; word_count still counts its caption. Floor of 1.10 is above the brief's 1.8 minimum.

## D20 — 2026-11-03 — Sessions, turns and reply time definitions
Decision: Session = messages with no gap > session_gap_minutes (default 60). Turn = consecutive same-sender messages within a session. Reply time = gap from the end of one turn to the start of the next turn by a different sender, within a session only. Thresholds are dbt vars.
Alternative: Use platform reply-to metadata; or measure every sender change regardless of gap.
Why: Instagram exports have no reply-to IDs. Without a session bound, "replying" the next morning would dominate the averages. Turns stop rapid multi-message bursts from counting as many replies.
Tradeoff: Results depend on the threshold; replies to older messages within a busy session are attributed to the most recent turn.

## D21 — 2026-11-03 — dbt unit tests for transformation logic
Decision: Window-function logic (sessions, turns, reply time) is covered by dbt unit tests with fixed inputs and expected outputs; generic and singular tests cover data shape.
Alternative: Rely only on generic tests against synthetic data.
Why: Generic tests catch nulls and duplicates, not wrong logic. Unit tests pin down edge cases at the threshold boundaries.
Tradeoff: More YAML to maintain when the models change.

## D22 — 2026-11-03 — dim_members includes reactors who never sent a message
Decision: dim_members is the union of (thread, name) pairs seen as senders and as reactors; non-senders have message_count 0 and null first/last message times. Reaction counts in agg_member_activity exclude self-reactions.
Alternative: Senders only, with fct_reactions allowed to fail its relationships test; or count self-reactions.
Why: Lurkers who only react are common in real group chats, and a senders-only dimension would break the fact-to-dim relationship test on real data. Self-reactions say nothing about how others engage with a member.
Tradeoff: The dimension is slightly broader than "senders"; consumers must treat first/last_message_at as nullable.

## D23 — 2026-11-03 — Dedupe reactions in staging
Decision: stg_instagram__reactions drops exact duplicate (message_key, actor, emoji) rows; the unique test stays at error severity, and a warn-level test checks (message_key, actor).
Alternative: Relax the unique test, or fail the build on export artifacts.
Why: Instagram allows one reaction per person per message, so exact repeats are export artifacts. The strict test then guards the dedup; the warn test reveals whether real exports ever contain multiple reactions per person.
Tradeoff: If real exports do carry legitimate multiple reactions per person, the warn test will flag it and the model needs revisiting.

## D24 — 2026-11-03 — Strip NUL bytes in the parser
Decision: The parser removes \x00 from text fields before hashing.
Alternative: Let the load fail on NUL bytes.
Why: Postgres text cannot store NUL, and server-side errors echo the offending value into the server log. Since ParsedMessage already guarantees types, this closes the last realistic path for content to reach the log. log_error_verbosity=terse is a backstop.
Tradeoff: A small deviation from the faithful-parser rule, limited to a byte that no chat client displays.

## D25 — 2026-11-03 — Sessions attributed to their start date
Decision: Daily aggregates count a session once, on the local date of its first message (sessions_started).
Alternative: Count a session on every day it has messages.
Why: Summing daily sessions should equal total sessions; counting per day double-counts sessions that cross midnight.
Tradeoff: Late-night sessions appear entirely on the day they started.

## D26 — 2026-11-03 — Airflow in its own image, dbt in an isolated venv
Decision: Airflow runs from the official image; gcwrapped and dbt are installed in a separate virtualenv inside it, and tasks shell out to that venv.
Alternative: Install dbt into Airflow's environment; or use astronomer-cosmos to render dbt models as tasks.
Why: Airflow pins many dependencies via constraints files and dbt often conflicts with them; isolation is the common production pattern. One dbt build task is simpler to explain and debug than a task per model at this size.
Tradeoff: Per-model retries and lineage in the Airflow UI are lost; dbt's own logs and docs cover that.

## D27 — 2026-11-03 — Daily schedule with full idempotent re-ingest
Decision: The DAG runs daily, catchup off, one active run, re-ingesting every export in the landing dir.
Alternative: A file sensor plus a manifest of already-loaded exports.
Why: ON CONFLICT on a stable message_key makes re-ingest a no-op for known messages, so the simplest schedule is also correct; retries are safe for the same reason.
Tradeoff: Each run re-parses all exports; fine at group-chat scale, revisit if parse time grows.

## D28 — 2026-11-03 — Landing dir logic lives in the gcw CLI, installed editable in the image
Decision: Finding export roots (gcw check-landing, gcw ingest --landing DIR) and the run summary (gcw last-run) are Python in src/gcwrapped; DAG tasks are one-line shell commands. The image installs gcwrapped editable from /opt/gcw so migrations/ is found next to src/.
Alternative: Compute roots and query load_runs in the DAG file (Airflow imports project code); or install a wheel and add a MIGRATIONS_DIR env override.
Why: Keeps the DAG free of business logic and of project imports (the point of the isolated venv), and makes the logic unit-testable without Airflow. Editable install avoids a config knob and a code change to migrate.py.
Tradeoff: The runtime image is not a pure installed package; source at /opt/gcw is part of it.

## D29 — 2026-11-03 — DAG tests run in a test image stage
Decision: airflow/Dockerfile has a runtime stage and a test stage (runtime + pytest + airflow/tests). CI runs the tests with docker run, then airflow dags test on the runtime image.
Alternative: pip-install pytest in the container at CI time, or ship pytest in the runtime image.
Why: The tests need Airflow's exact environment, and the runtime image should not carry test tooling.
Tradeoff: Two image targets to keep in sync; layers are shared so the extra build is seconds.

## D30 — 2026-11-03 — Streamlit for the Phase 1 dashboard
Decision: The Phase 1 dashboard is a Streamlit app reading the analytics marts.
Alternative: Metabase or Superset; or the Phase 3 React frontend now.
Why: Dashboard code is versioned and testable in CI (AppTest); Metabase stores dashboards in its own app DB, configured by clicking. React belongs to Phase 3 with the API.
Tradeoff: Less polished than a BI tool; replaced or complemented by the React app later.

## D31 — 2026-11-03 — Read-only reader role scoped to marts
Decision: The dashboard connects as gcw_reader, which has USAGE on analytics and SELECT on mart tables only, granted via dbt grants config. CI asserts permission denied on raw and staging.
Alternative: Reuse the pipeline's read-write user.
Why: Message text exists only in raw and staging; scoping the role makes "text never leaves staging" a database guarantee, not a convention.
Tradeoff: Every new mart needs grants (handled by dbt config); one more credential to manage.

## D32 — 2026-11-03 — Warehouse tests get their own pytest marker
Decision: Tests that need a built warehouse (dbt marts plus the reader role) use the `warehouse` marker and `make test-warehouse`; `make test` excludes both `integration` and `warehouse`. Streamlit and pandas live in the `dashboard` uv group, and mypy ignores missing imports for them so lint does not need that group.
Alternative: Mark them `integration`; or put the dashboard group in the default sync.
Why: `integration` tests run before dbt build in CI and reset schemas, so tests that read marts would fail there. A separate marker keeps the order explicit and keeps the lint job's install small.
Tradeoff: Another marker to remember; DataFrames are untyped (Any) under mypy.

## D33 — 2026-11-03 — One-command e2e via compose and the DAG, verified in CI
Decision: `make e2e ENV_FILE=…` starts Postgres and the dashboard with compose and runs the Airflow DAG once; CI runs the same command on synthetic data, twice, to prove idempotency.
Alternative: A separate script that bypasses Airflow; or testing compose only by hand.
Why: The real-data run should use exactly the code path CI verifies; the second run proves re-ingest safety end to end.
Tradeoff: CI is slower (image builds, compose startup).

## D34 — 2026-11-03 — Single source of truth for the target database
Decision: One env variable (GCW_DB_NAME) decides the target database for gcw, dbt and the dashboard; a mismatch fails fast. gcw and the dashboard build their URLs from components, and an explicit DATABASE_URL / DASHBOARD_DATABASE_URL is accepted only if it names the same database. dbt's profile reads GCW_DB_NAME with no default.
Alternative: Separate variables per tool.
Why: With separate variables, a real run could ingest into gcw_real and build dbt models in gcw_dev without any error.
Tradeoff: Tool-native config (DATABASE_URL, profiles) is derived rather than set directly.

## D35 — 2026-11-03 — Replace lone surrogates in the parser
Decision: clean_text replaces lone UTF-16 surrogates with U+FFFD before hashing.
Alternative: Let the load fail.
Why: Postgres rejects them, and one bad row rolls back the entire load.
Tradeoff: The original code unit is lost; it was not displayable text anyway.

## D36 — 2026-11-03 — Project display name "GC Wrapped"; compose name pinned
Decision: The display name is "GC Wrapped" with no subtitle; the repo slug matches the renamed GitHub repo (GC-Wrapped); code identifiers (gcwrapped, gcw, gcw_* DBs, GCW_* vars) are unchanged; docker-compose sets an explicit project name (`name: gc-wrapped`).
Alternative: Rename the package and identifiers too; leave compose naming to the folder name.
Why: Identifiers already match the name, and renaming them breaks imports and existing env files. Compose derives volume names from the folder by default, so renaming the folder would orphan the database volume.
Tradeoff: The package name (gcwrapped) and display name differ slightly in form. Existing volumes created under the old folder-derived project name are not picked up by `gc-wrapped`; they must be migrated once (see STATUS).

## D37 — 2026-11-03 — Separate schemas for staging and marts
Decision: Staging and intermediate models build into `staging`; marts build into `analytics`. gcw_reader has USAGE on analytics only.
Alternative: One schema, isolated by per-relation grants.
Why: Message text exists only in raw and staging. Schema-level isolation means a future grant misconfiguration on a single model cannot expose text to the reader role.
Tradeoff: A custom generate_schema_name macro to maintain.

## D38 — 2026-11-03 — Dashboard never renders error details
Decision: Streamlit runs with client.showErrorDetails = false; config and connection errors show a fixed message. An AppTest asserts a sentinel password never renders.
Alternative: Default Streamlit error display.
Why: Validation errors include input values (connection settings, passwords), and Streamlit shows tracebacks in the browser by default.
Tradeoff: Debugging needs the server logs instead of the browser.

## D39 — 2026-11-03 — Dead config removed; CHAT_TIMEZONE is read by dbt
Decision: dbt's gcw_timezone var reads env_var('CHAT_TIMEZONE', 'America/Los_Angeles'), and compose passes it to the Airflow container. The unused LANDING_DIR and chat_timezone fields were removed from Settings (the DAG reads GCW_LANDING_DIR, set by compose).
Alternative: Keep the reserved fields in Settings.
Why: Nothing in Python read them, so changing them silently did nothing; the one consumer of the time zone is dbt.
Tradeoff: Settings has no time zone field; if Python code needs it later, add it back with a reader.

## D40 — 2026-11-03 — Separate compose stack for real data
Decision: Real-data runs use their own compose project (gc-wrapped-real) with a separate Postgres volume and superuser password, set only in an env file outside the repo.
Alternative: One Postgres cluster hosting both gcw_dev and gcw_real.
Why: The dev .env lives in the repo where the coding agent can read it; with a shared cluster, the dev superuser could read real messages. Separate stacks mean repo credentials can't reach real data.
Tradeoff: Two stacks to manage; the real stack is started only for runs and stopped afterward.

## D41 — 2026-11-03 — The Makefile includes ENV_FILE, not always .env
Decision: The Makefile does `-include $(ENV_FILE)` (default .env) and every compose call passes `--env-file "$(ENV_FILE)"`.
Alternative: Keep `-include .env` and rely on --env-file alone.
Why: Compose gives shell variables precedence over --env-file (verified), and `export` puts .env values in the shell. A run with ENV_FILE=<other> would have silently used the dev passwords and database name from .env.
Tradeoff: ENV_FILE is parsed by make, so values must be plain KEY=value (no quotes or spaces), as in .env.example.

## D42 — 2026-11-04 — Real-data guardrails: gitignore real.env, deny Read/Edit/Write, single private root
Decision: real.env lives at ~/gcw-private/real.env (outside the repo); `real.env` in .gitignore is a backstop. All real data and private scripts live under ~/gcw-private (landing/, local/, archive/), denied to Claude Code across Read/Edit/Write/Bash.
Alternative: Bash-only deny patterns and an in-repo real.env (the previous state).
Why: real.env was not gitignored (only .env was), and Bash-only rules left the Read tool able to open it. One private root means one set of deny rules covers everything real.
Tradeoff: the private root is a fixed convention that must be documented for anyone cloning the repo.

## D43 — 2026-11-04 — Format discovery via a content-free inspector, run only by Ryan
Decision: the schema inspector emits aggregates only (counts, key frequencies, masked URI shapes) and is tested against a sentinel fixture that asserts no name or content leaks. Claude Code builds and tests it on synthetic data; only Ryan runs it on real exports.
Alternative: inspect real files by hand, or let Claude Code read samples.
Why: the format must be learned from real data, but no real names or content may reach a third-party API or the repo. A leak test turns "I was careful" into an enforced property.
Tradeoff: aggregates can hide rare edge cases; the shape buckets ("other", <x>) surface them as counts to investigate.

## D44 — 2026-11-04 — Inspector name gates are allowlists, and the leak fixture is adversarial to them
Decision: URI segments, file extensions, and inbox siblings print only if they're in a fixed allowlist of structural names; everything else is <x>. The sentinel fixture includes a lowercase sentinel placed where a name could pass a lowercase pattern.
Alternative: pattern gates ([a-z_]+) with uppercase-only sentinels (the previous state).
Why: real Instagram folder names are lowercased usernames, which pass [a-z_]+. Uppercase sentinels are always masked by the pattern itself, so the old test could never detect a lowercase leak. Deny-by-default plus an adversarial fixture closes that gap.
Tradeoff: new structural folders show as <x> until added to the allowlist deliberately. JSON key names still use a pattern gate (an allowlist would defeat key discovery); Ryan's manual review before committing the report is the backstop.

## D45 — 2026-11-04 — Claude Code implements all code; retire the write-it-yourself workflow
Decision: Claude Code implements all code, including privacy-critical functions; Ryan makes design choices in the planning chat instead of writing implementations.
Alternative: Scaffold-only "write it yourself" functions (tests + signatures, NotImplementedError) for Ryan to implement.
Why: Hand implementation was the bottleneck during recruiting; design choices and close diff review carry the interview value.
Tradeoff: Less hands-on implementation practice; Ryan must review every diff closely enough to explain each line.

## D46 — 2026-11-04 — thread_id is the folder's trailing numeric ID
Decision: thread_id = the digit run that is either the whole folder name or follows its last underscore (<name>_<digits> or <digits>). Any other folder name raises, and the error never includes the folder name.
Alternative: the full folder name.
Why: the real export has 416 <name>_<digits> and 6 bare <digits> folders (15-17 digits). The name part embeds a participant's display name, which would put PII in every row and log line. It also changes when a chat is renamed, which would re-key the whole thread and break dedup across exports.
Tradeoff: a future export with a different folder scheme fails loudly instead of degrading silently.

## D47 — 2026-11-04 — Attachment identity is the URI basename; external URIs are not hashed
Decision: message_key hashes the sorted basenames of attachment URIs under your_instagram_activity/. URIs outside it (5 videos in the real export) count toward media_count but are not hashed. This supersedes the attachment-URI part of "2026-11-02 — Message dedup key".
Alternative: full URIs (the 2026-11-02 rule), or hashing external URIs too.
Why: full URIs embed the name-bearing thread folder and the export layout, so a rename changes every key. Basenames are unique within threads in the real export (0 duplicates). External URIs don't point at export files and may carry per-export query parameters, so hashing them risks unstable keys.
Tradeoff: two messages that differ only in an external attachment share an identity. The occurrence index still keeps both rows.

## D48 — 2026-11-04 — Real-format fields not mapped in Phase 1
Decision: the parser maps sender_name, timestamp_ms, content, title, photos/videos/audio_files (count plus basename for the key), share.link, and reactions (actor, reaction). It ignores call_duration, share.share_text, share.original_content_owner, share.profile_share_name/username, joinable_mode, image, magic_words, thread_path, is_still_participant, is_geoblocked_for_viewer, is_unsent_image_by_messenger_kid_parent, reaction timestamps and attachment creation_timestamp.
Alternative: load every field into raw.
Why: these fields either carry extra PII (joinable_mode.link is a live group invite link, profile_share_name is a person's name, thread_path embeds the folder name) or aren't used by any Phase 1 model. Adding a nullable raw column later is a small migration, and the daily full re-ingest backfills it.
Tradeoff: this departs from "parser is faithful". Raw holds less than the export.

## D49 — 2026-11-04 — raw.source_file stores thread_id/file name, not the export path
Decision: source_file = "<thread_id>/<message_N.json>".
Alternative: the path relative to the export root (previous behavior), or dropping the column.
Why: the export path contains the name-bearing thread folder, so every row, log line and failing dbt test would carry a display name. Identifier and lineage fields spread further than content columns, so they must be PII-free. Keeping the column preserves row-to-file lineage for debugging.
Tradeoff: locating the file on disk takes a lookup by digit suffix (one glob) instead of a direct path.

## D50 — 2026-11-05 — Consent allowlist: deny by default, no bypass switch
Decision: ingestion loads only threads whose numeric thread_id is listed in GCW_THREAD_ALLOWLIST (comma-separated digits). Threads are filtered at discovery, before any file in them is opened. Folders that don't match a thread-ID shape are skipped and counted, never parsed. A missing or empty allowlist makes ingest fail. There is no bypass flag: dev .env, .env.example and CI list the synthetic thread IDs explicitly.
Alternative: a GCW_SYNTHETIC=1 switch that disables the allowlist in dev, or filtering after parsing.
Why: consent is per chat, so it must be enforced in code at the earliest point. A bypass switch is one misconfigured variable away from ingesting every chat. Listing the synthetic IDs keeps a single code path in dev, CI and real. Filtering before reading means non-consented messages are never parsed into memory.
Tradeoff: adding a synthetic thread means updating the env files too. A mistyped real ID allowlists nothing, so check-landing reports allowlisted IDs that weren't found and fails when none match.

## D51 — 2026-11-05 — Format-survey tools read every thread; only ingestion is consent-gated
Decision: make inspect and make parse-check read the whole local export but print aggregates only and store nothing. Anything that loads data (gcw ingest, the Airflow DAG, make e2e, make real-ingest) requires the allowlist.
Alternative: gate the survey tools by the allowlist too.
Why: the format has to be validated on as much real data as possible. The full-inbox survey found 6 bare-digit folders and 5 external attachment URIs that a single chat might not contain. Counts that never leave Ryan's machine and are never stored don't put anyone's messages into the dataset.
Tradeoff: non-consented chats are read locally, in memory, for counts. This is disclosed in the README privacy section.

## D52 — 2026-11-05 — real-ingest prints allowlisted summary lines only; full logs stay private
Decision: make real-ingest ENV_FILE=<path> checks guards, runs check-landing and the e2e pipeline against that env's stack, writes the full output to GCW_LOG_DIR (set in the env file, outside the repo), and prints only lines that fully match a fixed set of patterns made of fixed labels and numbers.
Alternative: print the normal pipeline output, or strip known names with a denylist.
Why: Airflow logs, dbt output and Python tracebacks can echo row values, and a denylist can't know every name. Allowlisting output shapes is safe by construction and is leak-tested with sentinels.
Tradeoff: debugging a failed real run means opening the private log locally. The log is never pasted.

## D53 — 2026-11-05 — Gated and ungated parsing are separate functions
Decision: `parse_export_consented(roots, allowlist, stats)` is the only parser entry point ingestion uses; `parse_export` stays ungated for `gcw parse` and tests. Both share one dedupe and per-thread parse loop. Thread discovery for ingestion is `select_threads`, which decides from the folder name alone and lists inside allowlisted folders only. `find_export_roots` now only checks that an inbox folder exists, so it no longer lists thread folders.
Alternative: one `parse_export` with an optional `allowlist=None` argument, or a required keyword.
Why: an optional argument makes "ungated" the default and one forgotten argument a silent bypass. A required keyword would have touched about 60 test call sites with `allowlist=None` noise. Two names make the gated path greppable (the only caller is `_ingest`) and a unit test proves a skipped folder is never listed or read.
Tradeoff: the ungated function still exists, so a future ingest path could call it; the survey-tools decision above and review are the backstop.

## D54 — 2026-11-05 — real-ingest guards go beyond the brief in one place; make synth clears safely
Decision: real-ingest also refuses a GCW_LOG_DIR inside the repo. `make synth` empties its output directory but refuses a filesystem root, the home directory, a git checkout, or the working directory or a parent of it.
Alternative: only the guards listed in the brief; `rm -rf` of whatever --out names.
Why: the log holds raw pipeline output, and a log file inside the repo is one `git add .` from being committed. `--out` is a path typed on a command line, and recursive deletion of it should not be able to hit a project or home directory.
Tradeoff: a log directory must live elsewhere; `--out` must be a dedicated scratch folder.


## D55 — 2026-11-05 — real-ingest is verified end to end only in CI
Decision: Claude Code never runs make real-ingest locally (deny rule in .claude/settings.json plus CLAUDE.md). A CI job runs it on every PR against a synthetic env file with its own compose project, ports, database (gcw_ci) and a log dir outside the workspace, on the sentinel landing. The job fails if a sentinel string reaches the terminal, and it also runs a deliberately failing allowlisted thread to check the failure path.
Alternative: a CLAUDE.md exception letting the assistant run it locally with /tmp/gcw-synth-*.env files.
Why: permission rules match command prefixes and deny beats allow, so an exception could only be written as prose. Keeping the rule absolute keeps it tool-enforced. CI also turns the run into an end-to-end leak test against real Airflow, dbt and traceback output, which unit tests with hand-written noise cannot do.
Tradeoff: about 4-6 extra CI minutes per PR, and runner wiring bugs surface only after a push.

## D56 — 2026-11-05 — Host-side settings load only the file named by ENV_FILE
Decision: when ENV_FILE names a file other than .env, host-side gcw commands load settings from that file and the process environment only, never from the repo's .env. .env is used only when ENV_FILE is unset or is .env.
Alternative: env_file=".env" for every settings class, with process env overriding it (previous behavior).
Why: with the previous behavior, any key the real env file lacked was silently filled from the dev .env, so a host command could target the dev database or use the synthetic allowlist. One file per run is the only predictable rule.
Tradeoff: an env file must be complete. A missing key now fails or falls back to the code default instead of borrowing dev's value.

## D57 — 2026-11-05 — A named ENV_FILE must exist
Decision: if ENV_FILE names a file other than .env and that file does not exist, host-side settings raise an error naming the variable only. ENV_FILE unset or .env keeps the old behavior.
Alternative: pydantic-settings' default, which silently ignores a missing env file.
Why: a typo in the path would otherwise run with process-environment settings only, and whether that fails depends on which keys happen to be exported. Failing loudly is the predictable rule.
Tradeoff: a run with a mistyped path stops immediately instead of possibly working from exported variables.

## D58 — 2026-11-05 — Unhandled CLI errors print the exception type only
Decision: gcw's entry point catches any unhandled Exception, prints "gcw: failed: <type name>" and exits 1. GCW_TRACEBACK=1 restores the traceback for synthetic-data debugging.
Alternative: Python's default handler, which prints the traceback and the exception message.
Why: settings load before real-ingest's own error handling. pydantic's ValidationError and logging's level errors include the raw input value, so a malformed value in the real env file (thread IDs, credentials) would reach the terminal, and from there the planning chat.
Tradeoff: real-data failures show only a type name. Diagnosis means reproducing on synthetic data or reading the private log.

## D59 — 2026-11-05 — make metrics prints fixed-label counts and never writes tracked files
Decision: make metrics prints one fixed label per line with a numeric value (plus the target database and load status), from the dbt manifest, pytest collection, and the warehouse named by ENV_FILE. It does not edit METRICS.md; rows are copied in after a person reads them.
Alternative: have the target update METRICS.md itself, or print a free-form summary.
Why: a target Ryan runs on real data must not modify tracked files, and every number is read by a person before it becomes a resume claim. Fixed line shapes make the leak test strict: any line that doesn't match an allowed pattern fails.
Tradeoff: a manual copy step, and each new metric needs a new label and a test update.

## D60 — 2026-11-05 — dbt counts come from the manifest, not dbt build output
Decision: dbt model and test counts are read by resource type from target/manifest.json after dbt parse.
Alternative: parse the PASS=/TOTAL= summary that dbt build prints.
Why: the build summary is one total across models, tests, seeds and sources. The manifest splits them and needs no database.
Tradeoff: it counts what is defined, not what ran. dbt build in CI and in real-ingest shows they run.

## D61 — 2026-11-05 — make metrics enforces its output shapes at runtime
Decision: the allowed line patterns live in metrics.py, and every rendered line must fully match its pattern before anything is printed. A mismatch prints "metrics: failed at render (ShapeError)". Tests import the same patterns.
Alternative: keep the patterns only in tests, which then prove the property only on synthetic values.
Why: the leak guarantee has to hold on the real database, where values come from data the tests never saw (for example, an unexpected load status).
Tradeoff: a new metric needs a pattern in the source, and an unexpected value fails the whole report instead of printing it.

## D62 — 2026-11-05 — dbt parse in make metrics runs without real credentials
Decision: the dbt step passes placeholder profile values and writes dbt's target and log directories to a temporary directory.
Alternative: hand dbt the ENV_FILE's database settings and let it write dbt/target and dbt/logs in the repo.
Why: dbt parse never connects, so real credentials add risk and no value (least privilege), and a real-data run should leave nothing in the repo tree Claude Code reads.
Tradeoff: no partial-parse reuse between runs, so every parse is a full parse (a few seconds slower).

## D63 — 2026-11-05 — "distinct senders" instead of "participants"
Decision: the metric stays count(distinct sender_name) across fct_messages and is labeled "warehouse distinct senders".
Alternative: per-(thread, name) memberships as in dim_members, or keep the label "participants".
Why: across threads, distinct display names are the closer proxy for people. "Participants" implied everyone in the chat, including people who never sent a message.
Tradeoff: two people with the same display name count once, and one person whose display name differs between threads counts twice.

## D64 — 2026-11-05 — make metrics counts dbt hooks so its total reconciles with dbt build
Decision: add "dbt hooks" (manifest operation nodes) and "dbt buildable nodes" (models + seeds + data tests + unit tests + hooks) to the report.
Alternative: leave the 124 vs PASS=125 gap unexplained, or add a hard-coded offset.
Why: the on-run-end grant in dbt_project.yml is an operation node; dbt build runs it and counts it in PASS/TOTAL. The gap was this one hook, not a project change.
Tradeoff: a new buildable resource type (e.g. a snapshot) must be added to the sum by hand.

## D65 — 2026-11-05 — Adopt fixed session rules and a test-isolation guard
Decision: Adopt fixed session rules in CLAUDE.md: a fixed-format docs/STATUS.md (max 40 lines), a "deviate and record" rule for factual mismatches in briefs with an explicit list of stop-and-ask triggers, a single home for measured numbers (METRICS.md, rows added never edited), ID'd append-only decisions, and an automated guard proving the test suite cannot target the real stack.
Alternative: Free-form STATUS.md, "stop and ask on any design question", numbers stated wherever convenient, and relying only on .claude/settings.json deny rules for data isolation.
Why: Briefs are written without repo access, so factual mismatches are expected and should not cost a session; a fixed handoff makes review fast and turns open issues directly into the next brief; one home for numbers prevents drift in resume metrics; deny rules restrict Claude Code but not the test code itself, so isolation needs its own test.
Tradeoff: More ceremony per session, and Claude Code may occasionally adapt where Ryan would have chosen differently, mitigated by recording every deviation in STATUS.md for review.

## D66 — 2026-11-05 — Close PR 26 workflow gaps: stop trigger, carried-forward issues, no-merge, URL guard
Decision: Add a stop-and-ask trigger for briefs that conflict with recorded decisions or expand phase scope; carry open issues forward in STATUS.md until resolved; state that Claude Code never merges; extend the test-isolation guard to DATABASE_URL and DASHBOARD_DATABASE_URL. Refines D65.
Alternative: Keep the rules as merged in PR 26.
Why: PR 26 removed the old "say so if the brief conflicts with STATUS/decisions or expands scope" and "open items stay until resolved" rules without replacing them; "never merge unless green" read as permission to merge; and D34 accepts explicit database URLs, which the guard did not inspect, so a URL could point the suite at the real stack's port.
Tradeoff: One more stop trigger may end a session the brief intended as a deliberate change, so briefs that supersede a decision or expand scope must now say so explicitly.

## D67 — 2026-11-05 — dbt subprocess in make metrics gets an allowlisted environment
Decision: dbt deps and dbt parse in make metrics get only a named allowlist of process variables (PATH, HOME, locale, temp-dir and CA-certificate variables, each only when set), the placeholder profile values, and DBT_SEND_ANONYMOUS_USAGE_STATS=false. Extends D62 ("dbt parse in make metrics runs without real credentials").
Alternative: inherit os.environ and override only the five profile variables (the PR 25 behavior).
Why: when make runs with the real env file, the environment can hold real settings and future API keys, and parsing needs none of them. An allowlist stays safe when new secrets are added; an override list does not. Turning telemetry off removes a network call from real-data runs.
Tradeoff: a variable dbt really needs must be added to the list by hand. Until it is, the dbt step fails (fails closed) instead of leaking.

## D68 — 2026-11-05 — pytest writes go to a dedicated gcw_test database
Decision: every test that migrates, writes to or truncates a database uses gcw_test, on the same dev Postgres container (and the CI service container). gcw_test is created if missing and migrated at session start. Only make synth and make e2e write to gcw_dev.
Alternative: keep sharing gcw_dev and re-run make e2e after tests; wrap each test in a rolled-back transaction; start a throwaway container per session (testcontainers).
Why: tests emptied the dev warehouse that make metrics and the dashboard read. A second database on the existing container is the smallest change and works the same locally and in CI. Rollback can't cover code that commits on its own (loader, migrations). A container per session adds a dependency and startup time.
Tradeoff: one more database to keep migrated. The session fixture handles that, at the cost of a few seconds per integration run.

## D69 — 2026-11-09 — Per-sender consent ledger gates external processing
Decision: a CSV ledger outside the repo (GCW_CONSENT_LEDGER), one row per (thread_id, sender display name), consent yes, no or blank (blank means no). It is validated in full and loaded all-or-nothing into raw.sender_consent. One dbt model decides which messages may leave the machine: only messages whose sender is "yes" in that thread.
- Context sent with a consented message replaces any non-consented neighbor with a placeholder and never sends its text.
- A change from yes to no deletes that sender's stored tags on the next tagging run.
- The thread allowlist still gates ingestion.
Alternative: a per-thread external_ok flag, or thread-level consent for every allowlisted thread.
Why: exports include people who left the chats and never saw the ask, and consent is each person's decision. Deny-by-default per sender means a missing answer never sends anyone's text.
Tradeoff: Ryan maintains a ledger of a few hundred rows. A person with display names in several threads needs one row per thread. Tagged messages lose the text of non-consented neighbors as context.

## D70 — 2026-11-09 — Redaction with Presidio plus a member-name recognizer (implemented in P2-2)
Decision: before any external call, text goes through Presidio (spaCy NER, pinned model) with a custom recognizer for every sender display name and its name tokens, plus pattern recognizers for phone numbers, emails, URLs, @handles and street addresses. Matches become typed placeholders; people become per-thread <PERSON_n>, and sender labels are pseudonymized the same way. The placeholder mapping lives in memory only. City and place names are kept.
Alternative: regex plus a member-name dictionary only; or a local LLM that rewrites names out.
Why: a dictionary misses names of people who aren't senders. LLM rewriting is nondeterministic and hard to test. Presidio is widely used and deterministic with a pinned model.
Tradeoff: name tokens that are also common words get over-redacted, and the spaCy model is a large dependency. Keeping city names keeps topic signal at some re-identification risk.

## D71 — 2026-11-09 — Phase 2 tags with a hosted model behind a backend interface
Decision: tagging uses a small hosted model (Claude Haiku class) through a backend interface. A local llama.cpp backend arrives in Phase 3 and is compared on the eval set under a win rule fixed before the run.
Alternative: local only, or both backends in Phase 2.
Why: the best tagging quality per hour of work, and the interface keeps local mode a drop-in.
Tradeoff: per-call cost and an external dependency. Until local mode exists, only consented, redacted text can be tagged.

## D72 — 2026-11-09 — Two eval sets: synthetic in CI, real hand-labeled in ~/gcw-private
Decision: a committed synthetic labeled set runs in CI on every change. About 150 hand-labeled real messages, sampled only from consented senders, live in ~/gcw-private and are scored by a make target that prints aggregates only. Reports give accuracy and macro-F1 per tag with bootstrap 95% confidence intervals.
Alternative: synthetic only, or real only.
Why: CI needs an automatic regression signal; the headline number has to reflect real chats; real messages can't enter Git or CI.
Tradeoff: two numbers to explain, and the CI score is not the resume number. CI needs an API key secret, but only synthetic text is ever sent.

## D73 — 2026-11-09 — METRICS.md SHAs under squash merges
Decision: a PR-branch commit is cited as "<sha> (PR #<n>)". Rows measured on main cite the main SHA.
Alternative: switch gcw-merge to merge commits, or cite only squash SHAs (unknown until merge).
Why: gcw-merge squashes and deletes branches, so branch SHAs aren't on main. GitHub keeps them through the PR.
Tradeoff: one extra hop to find the commit. Existing rows are not edited; a note under the table maps 251e9f9 to PR #24 and 13b5d31 to PR #29.

## D74 — 2026-11-09 — Ledger implementation choices that D69 leaves open (P2-1)
Decision: (1) The code is `src/gcwrapped/ledger.py`; `consent.py` already holds the thread allowlist. (2) dbt names: source `raw.sender_consent`, staging view `stg_consent__sender_consent`, mart `fct_message_eligibility` (message_key, thread_id, sender_name, external_ok), in `analytics` like the other marts; it carries no text. (3) The template and the load's "sender in the warehouse" check read `analytics.dim_members` rows with message_count > 0, so reactors who never sent a message get no ledger row. (4) `consent-load` validates first, then applies pending migrations (what `gcw migrate` does), then replaces the table, so a database that predates migration 002 works on the first load. (5) The synthetic ledger is written by `make synth` (`--ledger`, default GCW_CONSENT_LEDGER, which must then be inside the repo; a path under ~/gcw-private is refused) to `tests/fixtures/synthetic_consent/` (gitignored). Two synthetic participants now carry SENTINELNAME display names so the ledger's sentinel senders exist in the dev warehouse.
Alternative: put consent code in consent.py; give reactors rows; fail `consent-load` until `gcw migrate` ran; leave sentinel rows out of the synthetic ledger.
Why: (3) only senders' text can be sent, so only senders need a decision, and a shorter ledger is easier to maintain. (4) Ryan's first real run is consent-load, before any real-ingest has applied migration 002. (5) A sentinel row for a sender absent from the warehouse would fail validation (unknown_sender), and the acceptance run needs the load to pass.
Tradeoff: `consent-load` can change the schema of the database it targets, but only by applying migrations the repo already ships. A person who only reacts in one thread has no row there; if they later send a message, the next template run adds them.

## D75 — 2026-11-09 — fct_message_eligibility is a view
Decision: fct_message_eligibility is materialized as a view over the staging models, so external_ok always reflects the currently loaded consent ledger. gcw_reader gets no SELECT on it.
Alternative: keep the table and have the tagging job run `dbt build --select fct_message_eligibility` first; or keep the table and have tagging re-check raw.sender_consent at send time.
Why: a revoked consent must take effect the moment the ledger is loaded, without relying on every caller to remember a rebuild. A view keeps one definition and is always current. A send-time re-check would be a second definition that can drift. The dashboard has no use for who declined.
Tradeoff: the join runs on every query (trivial at tens of thousands of rows). Anything reading the view sees a ledger change immediately, so the tagging job (P2-3) must read eligibility at send time for each batch rather than once per run.

## D76 — 2026-11-09 — Redaction quality gate: zero-leak for deterministic types, per-example regression gate for NER (gate implemented in P2-2b)
Decision: Deterministic recognizers (member names, phone numbers, emails, @handles, and card, SSN, IP and IBAN patterns) must catch 100% of their spans on committed synthetic sets and in property tests; any miss fails CI. Names only spaCy NER can catch (people who are not members) are scored on a committed synthetic set of 30-60 messages with hand-placed spans. The report gives recall and precision with bootstrap 95% confidence intervals, recorded in METRICS.md. The model is pinned and inference is deterministic, so CI fails if any example that passed in the committed baseline now misses. Precision is report-only. Changing the set or accepting a regression means re-baselining in the same PR with a new decision entry.
Alternative: sentinel tests only (no number); a report-only recall number with no gate; an aggregate tolerance margin on recall.
Why: the deterministic part can and must be perfect. The probabilistic part can miss, so it gets a measured baseline instead of a false 100% claim. With a fixed set and a pinned model there is no run-to-run noise, so a tolerance margin would only hide real regressions.
Tradeoff: the synthetic NER set is small and written by us, so its recall says little about real chats and its confidence intervals are wide. A deliberate change that trades a few misses for other gains needs an explicit re-baseline.

## D77 — 2026-11-09 — Redaction implementation choices that D70 leaves open
Decision: (1) spaCy en_core_web_lg, pinned as a direct wheel dependency at an exact version, with spaCy and presidio-analyzer pinned to compatible versions. (2) The name dictionary is every member in analytics.dim_members across all threads, reactors and "no" senders included. (3) Each display name contributes the full name and its tokens (split on whitespace and punctuation). Tokens shorter than 2 characters or containing no letter are dropped. Each name and token also gets an accent-stripped variant. Matching is case-insensitive and whole-word. (4) Per-thread pseudonyms <PERSON_n> are assigned in order of first appearance and shared by sender labels and mentions. A token that matches more than one member becomes <PERSON> (unnumbered). NER names not in the dictionary are numbered by their casefolded text. (5) Placeholders: <PERSON_n> or <PERSON>, <PHONE>, <EMAIL>, <URL>, <HANDLE>, <ADDRESS>, <CARD>, <SSN>, <IP>, <IBAN>. Locations, organizations and dates are kept. (6) The mapping lives in memory for one call and is never returned, stored or logged. (7) Local redaction may read any ingested message; the consent ledger gates external processing only.
Alternative: en_core_web_trf; a dictionary of consented senders only; full names only, no tokens; one pseudonym map across all threads.
Why: (1) It is Presidio's default; the transformer model adds torch for an unproven gain. (2) A "no" sender's name is still PII inside a "yes" sender's message. (3) Chats use first names and drop accents. (4) Stable labels within a thread keep who-said-what signal for tagging without identifying anyone; a global map would let pseudonyms link people across chats.
Tradeoff: name tokens that are common words ("May", "Will") get over-redacted. Nicknames and misspellings not in the dictionary rely on NER and can leak; this is a documented gap. The US-centric address pattern misses other formats. The model adds several hundred MB to installs and CI.

## D78 — 2026-11-09 — Redaction implementation details D77 leaves open (P2-2a)
Decision: (1) The redaction packages (presidio-analyzer, spacy, the en_core_web_lg wheel) sit in a `redaction` dependency group that the `dev` group includes, plus hypothesis in `dev`. `uv sync`, `uv run` and CI get them; the Airflow image export (`--no-dev`) does not, so that image is unchanged. `gcw redaction-audit` imports the module lazily for the same reason. (2) "Ambiguous" in D77(4) is judged within the thread being redacted: a token that is more than one member of that thread is <PERSON>. A matched member who is not in the thread is numbered by name, like an NER name. (3) The member directory is read from raw.messages (senders and reaction actors), the same facts analytics.dim_members is built from, so the audit needs no dbt run. (4) Spans are chosen by score, then length. Two exceptions, both so no fragment of a name or handle is left: a person span that overlaps a member match merges with it into one span, and a handle, URL, email or address that contains a member name counts as score 1.0 and wins by length. (5) A lenient North American phone pattern (score 0.6) backs up Presidio's phone recognizer, which skips numbers the phonenumbers library calls invalid; a property test found `(212) 210-1000` surviving. (6) tldextract is pointed at its bundled suffix list because Presidio's email check would otherwise fetch the list over the network; a test blocks sockets for the whole module. (7) Presidio's loggers are set to ERROR in `setup_logging` and in the module.
Alternative: put the packages in runtime dependencies (changes the Airflow image); scope ambiguity across all threads; read analytics.dim_members.
Why: keeps Airflow untouched as the brief requires; global ambiguity would blank most first names once two members share one; raw has no dbt dependency and is a superset-safe source; the exceptions close real leaks the plain rule left.
Tradeoff: the lenient phone pattern can over-redact a 10-digit number that is not a phone. A merged NER span can swallow neighbouring digits into <PERSON_n>. Pseudonym numbers in the audit restart per 2000-message batch, which changes no count.

## D79 — 2026-11-09 — Residual member tokens: root-cause fixes plus a fail-closed backstop
Decision: Redaction closes residual-token mechanisms at their source and adds a backstop. (1) Overlapping spans merge into their union instead of the lower-ranked span being dropped; the union takes the type (and, for people, the identities) of the span ranked first by the existing order (score, then length). (2) Every chosen span is extended outward to the boundaries of any alphanumeric run it starts or ends inside, so no replacement leaves part of a word. (3) After replacement, the output, with placeholders masked by spaces of equal length, is scanned with the member dictionary; every match becomes a person placeholder under the usual numbering rules, repeated until none remain. If a dictionary token still remains after 3 passes, redaction raises RedactionError instead of returning the text. Backstop replacements are counted and reported by make redaction-audit; tests and the CI audit on synthetic data require the count to be 0.
Alternative: root-cause fixes only; a backstop only.
Why: the first real-data audit found member tokens surviving (counts in METRICS.md), from cases the synthetic tests did not cover. The fixes keep the algorithm correct and also close name-fragment leaks, which a dictionary backstop cannot see. The backstop stops an unknown mechanism from leaking a whole name, and its count shows whether one exists.
Tradeoff: more code, and somewhat more over-redaction (a merged or extended span can absorb neighbouring text into one placeholder). The residual check can no longer fail, so the backstop count becomes the signal to watch. The backstop sees dictionary tokens only, not nicknames or fragments.

## D80 — 2026-11-09 — Implementation details D79 leaves open (P2-2a-fix)
Decision: (1) "Overlap merges" counts spans absorbed into a group's union, including a span fully inside another, so the number includes harmless duplicates (Presidio often returns a URL and an email for one string). (2) "Alphanumeric run" is a letter or digit (str.isalnum, so non-ASCII letters count) plus combining marks; underscore separates words, as in the member dictionary. "Boundary extensions" counts span edges moved, one per edge. Merging and extending repeat until neither changes a span, because extending two neighbours inside one word makes them overlap. (3) A pattern result (handle, URL, email, address, card, ...) that fully contains any person result, member or NER, still scores 1.0 (D78.4), so "@priya_x" stays a handle; the old code compared against merged person groups, this compares against raw results. (4) A member span keeps the identities of the dictionary match on its own text, not on the union it ends up in. A person found only by NER is numbered by the casefolded text of the final (extended) span. (5) "Backstop next to a placeholder" is a replaced token that touches a placeholder with no whitespace between; the rest are standalone. Backstop replacements are added to the person count. (6) The stop-word lines are measurement only: "directory tokens that are stop words" intersects the dictionary with spaCy's English STOP_WORDS, and "member matches on stop-word tokens" counts directory matches in the original texts whose matched text is a single such token. Dictionary membership is unchanged. (7) `make sync` is `uv sync --group dbt --group dashboard`; plain `uv sync` drops those groups.
Alternative: count only merges between partly overlapping spans; stop at one backstop pass; make the backstop count fail the audit.
Why: (1) separating "contained" from "partial" would need a second counter nobody asked for; the trend is what matters. (2) Matches the notion of a word the dictionary already uses, so a span edge and a dictionary match never disagree about where a word ends. (5) The adjacent/standalone split points at the mechanism: adjacent suggests a span ending mid-word, standalone a dropped member span. (7) A dev who runs plain `uv sync` and then `make e2e`-adjacent commands otherwise gets missing dbt or streamlit.
Tradeoff: the merge count is noisy as an absolute number. A character such as "™" that is not alphanumeric but folds to letters is still treated as a word break by extension while the dictionary sees it as part of the word, so "Priya™" is a known blind spot of both the extension and the backstop; it is not covered by a test.

## D81 — 2026-11-10 — One definition of a word for redaction
Decision: Word boundaries are decided on the raw text: a word is a maximal run of characters that are alphanumeric (str.isalnum) or combining marks following one. Folding (normalization, case, accents) is applied inside a word and never creates or removes a boundary. MemberDirectory matching, residual_member_tokens, the backstop and span-edge extension (D79.2) all use this one definition, from one shared function.
Alternative: keep folding the whole text before tokenizing, so the dictionary and the extension disagree about characters such as "™" that are not alphanumeric but fold to letters.
Why: when the dictionary and the extension disagree about where a word ends, a name next to such a character is missed by the recognizer, the backstop and the audit at once (D80 tradeoff), so the audit cannot see the leak. One definition removes that class of disagreement.
Tradeoff: a name glued to letters or digits ("priya123") is still one word and still not matched. That gap is invisible to the audit and is measured only by a labeled eval set (P2-2b).

## D82 — 2026-11-10 — Stop-word member tokens stay redacted for now
Decision: Member-name tokens that are also English stop words are redacted like any other member token. Revisit only if the P2-3 tagging eval shows the extra placeholders lower tagging accuracy; then compare a context rule (redact such a token only when spaCy tags it PROPN or NER labels it PERSON) against this baseline as a pre-registered experiment whose win rule includes a leak bound fixed before the run.
Alternative: a capitalization rule (redact only capitalized, multi-word or @-prefixed uses); a context rule now.
Why: over-redaction is the safe failure and its cost is not measured yet. Lowercase-heavy chat makes the capitalization rule leak real mentions, and a context rule turns the deterministic member-name guarantee probabilistic before there is labeled data to measure it.
Tradeoff: the measured over-redaction (METRICS.md, 2026-11-10) stays: common-word uses of those tokens become person placeholders, which degrades text sent to the model and makes those members look mentioned far more than they are.

## D83 — 2026-11-10 — Words under two segmentations, used as a union (supersedes D81's single definition)
Decision: Redaction considers words under two segmentations and uses their union everywhere. Raw words (D81): runs of str.isalnum characters, plus combining marks after one, decided on the raw text and folded per word. Folded words (the pre-D81 rule): alphanumeric runs of the per-character fold of the text, mapped back to raw offsets. One function returns both. Name tokens are the union of both segmentations of each display name. MemberDirectory.find matches words of either segmentation and merges overlapping matches. residual_member_tokens counts distinct spans whose word is a token under either. The backstop uses find. Span extension moves an edge that cuts a word of either segmentation out to that word's edge, repeated until stable.
Alternative: D81's raw rule alone; revert to the folded rule alone.
Why: after D81 the real audit's redaction coverage fell (counts in METRICS.md) while residual and backstop stayed 0, because they shared the new definition. Neither rule alone is right: "Priya™" needs the raw rule, while a display name written in symbol letters ("ⓙⓔⓝⓝⓨ") or ending in a character such as "⑵" needs the folded rule. The union's coverage is a superset of both, which makes "coverage never drops" checkable.
Tradeoff: more over-redaction (junk tokens such as "kg" from a "㎏" in a display name come back), somewhat more matching work, and two segmentations to explain. A shared blind spot is still possible for anything neither rule sees (names glued to letters or digits, nicknames); only a labeled eval (P2-2b) measures that.

## D84 — 2026-11-10 — $0 and local-only models for the burst; hosted providers stay off until P2-2b
Decision: All model work in the burst runs locally at no cost: llama.cpp for tagging and answers, a local embedding model, and the app served from Ryan's laptop through a free tunnel. No message text reaches any third party. The code keeps a provider interface, but any hosted provider fails closed unless the D76 NER gate (P2-2b) has passed.
Alternative: a hosted LLM API and a small cloud VM, after P2-2b.
Why: zero budget, the strongest privacy story (real text never leaves the machine), and it takes P2-2b off the critical path. The redaction layer and consent gate remain the required path for any future external call.
Tradeoff: the app is up only while the laptop is on, answers are slower, and local-model quality is lower than hosted models.

## D85 — 2026-11-10 — Tag schema: one topic from a fixed list plus sentiment; inside jokes deferred
Decision: Each message gets exactly one topic from: plans, school, work, food, entertainment, sports_fitness, relationships, banter, news, other; and exactly one sentiment from: positive, neutral, negative. The labeler and the model see the same input: the message plus up to 2 previous eligible messages in its thread as context. Only messages with text that pass fct_message_eligibility are sampled, tagged, embedded or used as context.
Alternative: multi-label topics; free-form topics; an inside-joke tag.
Why: single-label fixed classes give a clean accuracy and macro-F1 with confidence intervals; inside jokes cannot be labeled consistently, so their score would be noise; one eligibility definition keeps the consent rule in one place.
Tradeoff: a message touching two topics is forced into one; "other" absorbs ambiguity; messages from "no" or blank senders never appear in tags, profiles, context or answers.

## D86 — 2026-11-10 — Labeling: a local terminal tool, frozen samples, labels outside the repo
Decision: A terminal tool shows real messages only in Ryan's terminal and writes labels as CSV to GCW_LABEL_DIR, which must be outside the repo. The tag sample (200 messages) and the question candidates (enough to reach 100 answered) are drawn once with a fixed seed, stratified by thread in proportion to eligible messages with a floor of 5 per thread where available, and frozen to a file so labeling can stop and resume. A question's gold is the shown message plus any numbered context messages Ryan marks. Ryan writes questions in his own words rather than copying the message. Only an aggregate summary target is ever pasted. CI uses committed synthetic labeled sets.
Alternative: a local web UI; a notebook; a sample drawn fresh each run.
Why: a terminal tool needs no server and keeps real text off disk except in the private directory; a frozen sample makes the eval set reproducible; paraphrased questions reduce word-overlap bias in retrieval recall.
Tradeoff: one annotator, so no agreement score; 200 and 100 items give fairly wide confidence intervals; questions written from one message favor single-message answers.

## D87 — 2026-11-10 — Implementation details D85 and D86 leave open (P2-3a)
Decision:
- Eligible means a message with non-blank text whose sender is "yes" in the ledger for that thread (`analytics.fct_message_eligibility.external_ok`, D69, D75), read in sent order with message_key as the tie-break, like the redaction audit. The SQL and the context rule live in `gcwrapped.labeling.source`; the schema constants (topics, sentiments, context sizes, sample sizes, seed, floor, CSV headers) live in `gcwrapped.labeling.schema`. S3's tagger imports both.
- Neither flow shows sender names: the model will see pseudonymized names (D70), so the labeler sees text only. A neighbor from a "no", blank or unlisted sender is not in the index, so it is skipped, never shown or masked.
- The question candidate pool is 300 (three times the target, because some messages cannot make a question) drawn with its own salt, so it is independent of the tag sample. The flow stops at 100 answered. Skips are kept in `question_skips.csv` (message_id, skipped_at) so a resume does not show them again; undo reverts whichever of the last answer or skip came later.
- The frozen files (`tag_sample.json`, `question_candidates.json`) hold message IDs, the seed and the target, never text, and are never redrawn. A frozen ID that is no longer eligible (consent revoked) is skipped, not shown. Labels already written for it stay in the file; whatever scores against them must drop labels whose message is not eligible at scoring time. An empty pool is refused rather than frozen. A pool smaller than the target is sampled whole and frozen; delete the frozen file if the ledger was incomplete when it was drawn.
- Every label is written the moment it is decided, by rewriting the whole file atomically (temp file, rename, mode 0600), so quitting, a crash or Ctrl-C loses nothing and undo is a rewrite without the last row. Label files hold IDs, labels and Ryan's own question text; the loader and every error print a kind and a line number, never a value.
- Input is a single keypress on a terminal (cbreak mode, restored after each key, without flushing typed-ahead keys) and one character at a time from a pipe, so a scripted run works. Message text is stripped of control and format characters before printing, because a chat message is untrusted input.
- The synthetic labeled sets (60 tagged messages, 31 questions) come from an appended fifth generator thread of only "yes" senders. The thread uses the generator's one rng after the earlier four, so their histories are unchanged (a test pins a digest). The CSVs under tests/fixtures/eval/ are written with the real parser's message IDs by `tests/synthetic/write_eval_fixtures.py`, and a test requires the committed files to equal what the generator produces.
- The dev landing cannot go stale by construction, not by comparison: `make synth` replaces data/landing from the generator on every run, and `make e2e` does the same first when ENV_FILE is the default .env. Any other env file, a real run included, never regenerates or deletes anything, because a real env file may leave its landing at ./data/landing. The data/ rule means no test or tool reads or hashes data/, so there is no test that diffs it against the generator; tests read the Makefile's `make -n` output instead.
Alternative: show sender names; a larger or per-thread-quota pool; keep labels in SQLite; a test that hashes data/landing.
Why: each choice keeps one definition (eligibility, schema) in one module, keeps real text out of everything but the terminal, and keeps the real-data boundary absolute.
Tradeoff: no sender cue for sentiment; a revoked consent leaves its old label rows on disk; a hand-edited or stale data/landing is only fixed the next time `make synth` or a default-env `make e2e` runs; a non-default env file that relies on a stale landing is its owner's to refresh.

## D88 — 2026-11-13 — Explicit opt-in at two levels: "yes" (local and redacted external) or "local" (this machine only); local work uses local_ok (amends D69, D85, D87)
Decision: The consent ledger accepts yes, local, no or blank. local_ok = consent is yes or local; external_ok = consent is yes (unchanged). Blank, no, or a sender missing from the ledger is excluded from everything. fct_message_eligibility carries both columns. Labeling, tagging, embeddings, answers and profiles use local_ok; anything that leaves the machine uses external_ok and redaction (D70). This replaces "passes fct_message_eligibility" in D85 and "external_ok" in D87's definition of eligible.
Alternative: thread-level consent for local work, with the ledger only for external calls (blank allowed locally); one level of yes covering both.
Why: Ryan chose explicit per-person opt-in for everything, so no one is processed on silence. Two levels let someone agree to on-laptop processing without agreeing to an outside AI service, so a yes is never stretched beyond what was asked.
Tradeoff: nothing works for a sender until they answer, so coverage depends on reply rates; answers, tags and profiles have gaps for non-responders; the ledger has one more value to validate.

## D89 — 2026-11-13 — Tagger: a local llama.cpp server, schema-constrained JSON, the same input the labeler saw
Decision: Tagging calls a llama.cpp server (official CPU image, pinned by digest) running as a compose service on this machine. Its port is published on 127.0.0.1 only, and the client refuses any base URL whose host is not localhost, 127.0.0.1 or the compose service name. Each request asks for one message's topic and sentiment with a JSON schema whose fields are the D85 enums, so constrained decoding can only produce valid labels; an invalid response gets one retry, then counts as a failure and stays untagged. The input is exactly what the labeler saw (D85, D87): the message plus up to 2 previous local-eligible messages, raw text, no sender names; redaction applies only to external calls (D70). Results go to enrich.message_tags (message_key, topic, sentiment, model_id, prompt_version, run_id, tagged_at), one row per (message_key, model_id, prompt_version). A run skips messages already tagged for the same model and prompt version, and deletes tags of messages that are no longer local_ok. A hosted provider exists only as a stub that always raises (D84).
Alternative: llama-cpp-python in-process; free-form output parsed afterwards; Ollama; tagging inside the Airflow container.
Why: a separate server keeps the model process out of pipeline code and sits behind the same interface a hosted provider would; constrained decoding makes an unparseable answer near impossible instead of a cleanup job; provenance columns make model and prompt changes comparable in S4; pruning means a revoked consent removes derived data, not just hides it.
Tradeoff: CPU throughput makes a full run take hours; one more service to run; two messages of context can mislabel replies that depend on older messages.

## D90 — 2026-11-13 — Default model: a ~4B instruct model at 4 bits with a small context
Decision: The default model is Qwen3-4B-Instruct-2507 as a Q4_K_M GGUF (exact file and sha256 pinned in config), served with a 2048-token context and 1 parallel slot. The file lives outside the repo (~/.cache/gcw/models) and is fetched by make llm-model with a checksum check. Bulk tagging does not run Airflow, so the model, Postgres and the client fit in WSL's 7.6 GiB.
Alternative: a 7-8B model; a 1-2B model; a larger context.
Why: at 4 bits a 4B model needs roughly 2.5 GB, which leaves room for Postgres in 7.6 GiB, and it is about the largest size that keeps CPU tagging near 1-2 s per message. S4 compares it with a smaller model under a win rule fixed in advance.
Tradeoff: lower quality than 7B+ models. If the pinned model or its GGUF is unavailable, the closest permissively licensed ~4B instruct model replaces it, recorded under Deviations from brief.

## D91 — 2026-11-14 — Removing a thread from the allowlist purges its data
Decision: make purge-unlisted deletes every warehouse row whose thread is not in GCW_THREAD_ALLOWLIST: raw messages and everything keyed to them (reactions, enrich.message_tags and any other table that references a message or a thread), and that thread's raw.sender_consent rows. dbt marts drop the thread on their next build. It dry-runs by default and prints counts only; it deletes only with CONFIRM=1, in one transaction. consent-load skips ledger rows whose thread is not allowlisted and reports how many it skipped; the ledger CSV itself stays Ryan's to edit.
Alternative: keep the rows and filter them out in dbt; delete by hand in psql.
Why: the allowlist is the ingestion consent gate, so a thread that leaves it should leave the warehouse, not linger behind a filter. Dry run then confirm makes a destructive step reviewable, and a make target keeps Ryan out of ad hoc SQL on real data.
Tradeoff: deletion from the warehouse is permanent (re-adding the ID and running real-ingest restores it from the export, which still holds the chat); one more step to remember.

## D92 — 2026-11-14 — Tagging eval: stored predictions per model, bootstrap intervals, only the promoted model's tags count
Decision: make tag-eval MODEL=<id> runs one model over a labeled set (Ryan's tag_labels.csv in GCW_LABEL_DIR, or with SYNTHETIC=1 the committed synthetic set on gcw_dev) with the production prompt and client, and writes predictions (message_id, topic, sentiment, seconds; no text) to GCW_LABEL_DIR/predictions/ with mode 0600 (synthetic runs use a temporary directory). Labels whose message is not local_ok at scoring time are dropped (D87), and a failure counts as wrong. It reports accuracy and macro-F1 for topic and sentiment, each with a 95% percentile bootstrap interval (2000 resamples, fixed seed), plus throughput. make tag-compare A=<id> B=<id> scores both prediction sets on the same messages with a paired bootstrap and prints a verdict under D93; the verdict is written to GCW_LABEL_DIR/experiments/. enrich.tag_default (one row, model_id) names the promoted model; fct_message_tags uses only that model's tags (its latest prompt version), falling back to the D90 model when the table is empty. make tag-promote MODEL=<id> refuses unless the latest tag-compare verdict names that model as the winner. make tag tags with the promoted model unless MODEL= is given.
Alternative: score the stored enrich tags instead of fresh predictions; normal-approximation intervals; "the latest written tag wins".
Why: stored predictions make a comparison rerunnable without re-tagging; bootstrap intervals need no distribution assumption on about 200 items; a paired bootstrap compares both models on the same messages, which removes most of the sampling noise; promotion behind a recorded verdict means a losing experiment can never reach profiles.
Tradeoff: one annotator and about 200 labels give intervals several points wide; predictions are one more private file to keep; a prompt edit is not separately gated (only the model is promoted).

## D93 — 2026-11-14 — Experiment: 4B vs ~1.5B model, non-inferiority win rule fixed before any run
Decision: Arm A is the D90 default (Qwen3-4B-Instruct-2507 Q4_K_M). Arm B is Qwen2.5-1.5B-Instruct Q4_K_M (pinned file and sha256; if unavailable, the closest permissively licensed 1.5-2B instruct model, recorded under Deviations from brief). Same prompt, temperature 0, same machine, run back to back on the same labeled messages. B becomes the default only if all three hold: B's messages per second is at least 1.5 times A's; the lower bound of the 95% paired-bootstrap interval of (B minus A) topic accuracy is at least -0.05; the same for sentiment accuracy. Otherwise A stays. The result is recorded in METRICS.md either way, with model ids, prompt version, label count and interval bounds.
Alternative: a superiority test; a prompt experiment (few-shot against zero-shot).
Why: the small model's payoff is speed, so it only has to be shown not much worse, which is a non-inferiority test. Fixing the margin before any run prevents moving the goalposts, and throughput is the laptop's real bottleneck.
Tradeoff: with about 200 labels an honest 5-point margin may be undecidable (an interval that straddles -0.05 keeps A), so B can lose for lack of evidence rather than for being worse.

## D95 — 2026-11-17 — Real user text excludes Instagram placeholders; one definition, patterns as data (amends D85, D87)
Decision: A message counts as user text only if its content is non-blank and does not fully match any Instagram placeholder pattern. The patterns live in a dbt seed (pattern id, case-insensitive anchored regex, description; names and emoji as wildcards), so a new pattern is a data change. One dbt model exposes has_user_text per message; the labeling source, the tagger (which imports it) and the coming embeddings select local_ok AND has_user_text. make text-audit prints aggregate counts per pattern and of the remaining user text, never text. The frozen tag sample is redrawn once after this change by deleting tag_sample.json; existing labels are kept and placeholder labels drop out at scoring.
Alternative: label placeholders as other/neutral; filter in Python only; drop all short messages.
Why: placeholders carry no words a person wrote, so labeling, tagging or retrieving them measures nothing and inflates "other" and profile counts. One definition in dbt keeps labeling, tagging and search consistent, and seed patterns are reviewable and extendable without code changes. Short real messages ("lol", an emoji) are kept, because they are real banter.
Tradeoff: patterns are English-export specific and can miss a template nobody has listed (the audit's residual counts are how a miss shows up); an over-broad pattern could drop real text, so every pattern is fully anchored and unit-tested.

## D96 — 2026-11-18 — No cross-session memory for Claude Code beyond the repo docs
Decision: No cross-session memory for Claude Code beyond the repo docs: no claude-mem plugin, and Serena's onboarding and memory tools are denied in .claude/settings.json.
Alternative: claude-mem (captures every tool call, compresses it with Claude via the Agent SDK, injects a memory index at session start) or Serena's memories in .serena/memories.
Why: CLAUDE.md, docs/STATUS.md and docs/decisions.md are the only source of truth between sessions and are reviewed in every PR; a second memory can inject stale context that contradicts them, Serena's sits in a gitignored folder no review sees, and claude-mem's compression spends Pro usage every session.
Tradeoff: each session re-reads the repo docs instead of getting an automatic history index.

## D97 — 2026-11-18 — Removed the Serena MCP server
Decision: Removed the Serena MCP server (user-scope MCP entry, the serena-agent uv tool, ~/.serena). The mcp__serena__* deny entries in .claude/settings.json and the .serena/ line in .gitignore stay, so a reinstall stays safe under D96.
Alternative: debug why Serena 2.1.0 (claude-code context, --project-from-cwd) exposed only initial_instructions and no symbol tools.
Why: the repo is small enough that Claude Code's own Read/Grep plus pyright-lsp diagnostics cover navigation and type errors; a server that loads but provides no usable tools adds startup cost and config for nothing. Its deny entries stay so a reinstall stays safe under D96.
Tradeoff: no symbol-level edits; revisit if whole-file reads become a visible cost as the repo grows.

## D98 — 2026-11-18 — Experiment arms move to Qwen3.5: 4B default, 2B challenger, thinking off; Qwen3-4B kept as a reference (amends D90, D93)
Decision: Before any real run, the default model (arm A) becomes Qwen3.5-4B and the challenger (arm B) becomes Qwen3.5-2B, both 4-bit GGUF, pinned by commit and sha256, served by the same pinned llama.cpp server with thinking disabled on every request. The D93 win rule is unchanged (B wins only if at least 1.5x faster and both paired lower bounds are at least -0.05). Qwen3-4B-Instruct-2507 (D90) stays registered as a reference: it is evaluated with tag-eval on the same labels for the record, but it is not an arm and is never compared or promoted.
Alternative: keep the D90/D93 arms; a three-way contest with new rules.
Why: Qwen3.5-4B has the same memory footprint as the D90 model and is a generation newer; a modern 2B has a realistic chance of passing non-inferiority, which would halve bulk tagging time on the laptop. Changing arms before any run keeps the experiment honest; the reference run gives a measured old-vs-new number instead of an assumption.
Tradeoff: Qwen3.5 benchmarks are mostly published in thinking mode, so its non-thinking quality on this task is unknown until measured; one more model file to pin and keep.
Note: the brief numbered this D96; D96 and D97 were already taken, so it is D98. Implementation: the registry entry carries `enable_thinking` (false for both Qwen3.5 models, none for the reference); the client sends `chat_template_kwargs` from it, and the prompt version hashes it in, so thinking on and off never share a version. tag-compare refuses two models with different prompt versions, so the reference cannot be compared with an arm. The GGUFs come from unsloth because Qwen publishes no official GGUF repos for these sizes.

## D99 — 2026-11-24 — Experiment result: Qwen3.5-4B stays the tagging default; tagger accepted at measured quality for Phase 2; context experiment deferred
Decision: Under D93/D94 the Qwen3.5-2B challenger did not pass (its topic difference interval lies entirely below zero; sentiment is inconclusive), so nothing is promoted and Qwen3.5-4B tags the real warehouse. Phase 2 accepts the tagger at its measured quality (METRICS.md). Individual message tags are never shown to users; topic and sentiment shares in profiles are presented as model estimates. A context-window prompt experiment (preceding messages as context) is deferred; if it runs, it needs a dev/test split of the labels or new labels, plus a win rule fixed before the run.
Alternative: run the context experiment now; tag conversation segments instead of single messages.
Why: the remaining burst priorities are retrieval, deployment and real users. The tagger is well above the most-common-topic rate in the labels. Tuning prompts on the same 199 labels would overfit the eval set.
Tradeoff: topic shares are rough and may lean toward the classes the model over-predicts; a later improvement costs another full re-tag of about 5 hours.

## D100 — 2026-11-24 — CI eval gate: a tiny Qwen3.5 model scores the synthetic labels on every PR
Decision: A CI job runs the production prompt and client through tag-eval (SYNTHETIC=1) on the committed synthetic labels with the smallest Qwen3.5 model (0.8B Q4_K_M, thinking off, pinned file and sha256, cached in actions/cache keyed by sha256), served by the same pinned llama.cpp image. The PR fails if topic or sentiment accuracy is below a floor committed in the repo. The floors are set once from a local run: measured accuracy minus 0.10, rounded down to the nearest 0.05. Changing a floor requires a new decisions entry.
Alternative: no model in CI, scoring only saved predictions; the production 4B model in CI.
Why: it catches a broken prompt, label list, schema or parser on every PR, which the stub server cannot. The 4B model is too slow for free runners. A model from the same family keeps the thinking-off path exercised.
Tradeoff: a few extra minutes per CI run and one more pinned model. A pass shows the pipeline works, not that real quality holds; real quality lives in METRICS.md.

## D101 — 2026-11-24 — consent-prune removes every derived record of a message that is no longer eligible
Decision: make consent-prune ENV_FILE=<env> finds messages that are no longer eligible (not local_ok, or not user text under D95) and removes what was derived from them: tag rows (the existing prune), rows in the private label files, gold message references in the questions file, and rows in stored prediction files under GCW_LABEL_DIR. It is a dry run by default, printing counts per kind; APPLY=1 deletes, rewriting each file atomically (temporary file, then rename) and keeping mode 0600. Output is fixed lines only.
Alternative: prune tags only (current behavior); delete whole label files on any revocation.
Why: a revoked or purged sender's data must leave every derived store, including private hand-made files. The dry run comes first because labels are hand work that can't be regenerated.
Tradeoff: pruning labels shrinks the eval set, so earlier METRICS eval rows can no longer be reproduced exactly; the recorded results stay in METRICS.md.

## D102 — 2026-11-24 — Local embedding model: Qwen3-Embedding-0.6B served by the pinned llama.cpp image
Decision: Embeddings come from Qwen3-Embedding-0.6B (official Qwen GGUF, Q8_0, pinned commit and sha256), served by the same pinned llama.cpp image as a separate compose service with --embedding and last-token pooling, full 1024 dimensions, L2-normalized, cosine distance. Queries get the model's instruction prefix; stored messages get none.
Alternative: bge-small-en-v1.5 (33M parameters, 384 dimensions); nomic-embed-text-v1.5.
Why: multilingual and strong for its size, which helps with slang and mixed-language chat; reuses the existing serving, pinning and hash checks, so no new runtime; stays local and $0 (D84).
Tradeoff: much slower per message than bge-small on CPU; the query prefix is one more thing that must match between eval and app.

## D103 — 2026-11-24 — Retrieval unit: one vector per message, embedded with its neighbors
Decision: Each local-eligible user-text message gets one vector, computed from a window: up to 2 eligible messages before and 2 after it in the same thread and conversation session, in time order, one line per message as "sender: text". The row stores the center message ID and the IDs of every message in the window. Search returns center message IDs, so recall and citations stay at the message level. Search is exact (no ANN index). Embeddings live only in the warehouse of the stack that made them.
Alternative: each message alone; conversation segments split by time gaps.
Why: short replies ("7pm works") carry almost no meaning alone; the tag eval showed single messages lack context. Keeping one vector per message keeps the 100-question gold (message IDs) and citations one-to-one. At about 6,000 vectors an exact scan takes milliseconds, so an index adds nothing yet.
Tradeoff: neighboring windows overlap, so top results can be near-duplicates; a neighbor can outrank the gold message; any window that contains a message later made ineligible must be deleted, because that message's text shaped the vector.

## D104 — 2026-11-24 — Pre-registered vector vs hybrid retrieval comparison
Decision: Baseline: vector search (D103). Variant: hybrid, the vector list and a Postgres full-text list (simple configuration, query lexemes joined with OR, over the same window text), top 50 from each, fused by reciprocal rank fusion with k = 60. Eval set: Ryan's 100 hand-written questions, gold = message IDs. Metric: recall@5 (a hit if any gold ID is in the top 5); MRR@10 secondary. Hybrid becomes the default only if the paired-bootstrap 95% interval of per-question recall@5 (hybrid minus vector) lies entirely above 0 and hybrid's p95 retrieval latency (query embedding plus SQL) is under 1.0 s on the laptop CPU. Otherwise vector stays; the result is recorded either way.
Alternative: pick by the point estimate; tune fusion weights on the same questions.
Why: the win rule is fixed before any real run, so it can't be bent toward a result; the simple configuration avoids English stemming and stop words on slang, names and mixed language; OR matching, because an AND query returns almost nothing for natural-language questions.
Tradeoff: with 100 questions only a large gap clears the rule; the questions were paraphrased on purpose (D86), which handicaps keyword search.

## D105 — 2026-11-24 — consent-prune scope: embeddings in, ID-only files out
Decision: consent-prune also deletes every embedding row whose center message or any window member is ineligible, and stored retrieval prediction rows that reference an ineligible message. Frozen sample files and skips.csv keep message IDs of ineligible messages; they hold IDs only, no text.
Alternative: prune the frozen samples and skips too.
Why: an embedding encodes the text of every message in its window, so it is derived content. The frozen sample is what makes the label set reproducible, and an ID carries no content.
Tradeoff: ID-only files still record that a message once existed; a full purge of a person (deleting the messages themselves) would need a separate step.

## D106 — 2026-11-24 — purge-unlisted deletes embedding rows explicitly and counts them (extends D91)
Decision: make purge-unlisted covers enrich.message_embeddings: it deletes the rows whose center message is in a thread not on the allowlist, in the same transaction as the other deletes, and prints "embeddings: N" in both the dry run and the apply. The foreign key's ON DELETE CASCADE stays as a backstop, not as the mechanism. A window never crosses threads (D103), so deleting by the center's thread removes every window that contains an unlisted message; a test proves it.
Alternative: only add the table to the covered list and let the cascade delete the rows.
Why: Ryan's choice. D91 requires every table keyed to a message to be purged, and the dry run must show what a real run will remove.
Tradeoff: one more output line in an existing make target.

## D107 — 2026-11-24 — Implementation details D102-D105 leave open (S5)
Decision:
- Session: analytics.fct_messages.session_id, the dbt rule (a gap over session_gap_minutes starts a new one), computed over all messages, eligible or not. Window lines use the sender display name from raw.messages; line breaks inside a name or message become spaces. The cap is 2000 characters; the farthest neighbors are dropped first, the later one first on a tie; a center longer than the cap is cut.
- The query prefix is the model card's own retrieval instruction, unchanged. The pinned server appends the end-of-text token itself, so the client adds none. The client normalizes every vector again and rejects any that is not 1024 finite numbers. One slot, context and batch of 8192 tokens.
- The embed version hashes the model sha256, the window constants, the line format, the session rule, the dimensions and the query instruction. It does not hash the server image or the session gap; a changed gap shows up as changed windows.
- A row is "already current" only if it has this version and model and lists exactly the window the message has now, so a newly consented or newly ingested neighbor causes a re-embed. make embed deletes nothing; consent-prune does (D105).
- Consent applies at query time too: search skips a row if its center or any window member is not eligible now (D75, D88), before anything is pruned.
- Full-text ranking is ts_rank with default normalization, ties by message ID. Fusion ties go to the better vector rank, then the message ID.
- A question is dropped if its source message is not eligible (as in D101) or none of its gold messages is searchable; otherwise its gold is cut to the searchable ones. Latency is each question's query embedding plus SQL, nearest-rank percentiles, no warm-up call; "under 1.0 s" is strict.
- Predictions are GCW_LABEL_DIR/retrieval/<method>.json: IDs, a sha256 of each question, seconds; mode 0600. retrieval-compare accepts only A=vector B=hybrid, fails if the two files differ in embed version, question source, or any question's hash, source or gold, and does not score a question whose row in either file refers to a message no longer eligible (D87).
- consent-prune removes a whole retrieval prediction row if its source, gold or any result is ineligible, counted in "prediction rows to prune". "ineligible messages" is renamed "ineligible messages with derived records", which is what it counts.
Alternative: version-only "current" (stale windows after new consent or ingest); ts_rank_cd; prune inside make embed.
Why: each choice is fixed before any real run, keeps one eligibility rule in force wherever a vector is read, and keeps destructive steps in consent-prune with its dry run.
Tradeoff: ts_rank has no inverse document frequency, so common words weigh as much as rare ones in the hybrid's text list; one revoked sender can remove most stored retrieval rows, so the eval is rerun after a prune.

## D108 — 2026-11-24 — CI retrieval gate: the pinned embedding model scores the synthetic questions on every PR
Decision: A CI job runs make embed twice (the second must embed nothing) and make retrieval-eval METHOD=vector SYNTHETIC=1 with the cached pinned embedding model. The PR fails if recall@5 is below the floor in tests/fixtures/eval/retrieval_floors.json (0.85) or any question was dropped. The floor was set once from a local run by the D100 rule: measured recall@5 minus 0.10, rounded down to the nearest 0.05. Changing it requires a new decisions entry.
Alternative: stub embeddings only in CI; gating hybrid or MRR too.
Why: it catches a broken window builder, query prefix, client, migration or search query on every PR, which a stub cannot.
Tradeoff: one more CI job and a larger cached model. A pass shows the pipeline works, not that real retrieval is good; real numbers live in METRICS.md.

## D109 — 2026-11-24 — Retrieval eval refuses an incomplete or changed corpus
Decision: make retrieval-eval refuses (fixed failure line, nothing stored) unless every local-eligible user-text message has a searchable row of the current embed version; on success it prints "messages not embedded: 0". Each retrieval predictions file stores a corpus hash: the first 12 hex characters of the sha256 of the sorted searchable message IDs. retrieval-compare refuses two files whose corpus hashes differ.
Alternative: rely on running make embed first and changing nothing between the two eval runs (S5 behavior).
Why: a partial corpus silently cuts gold and drops questions, and a smaller corpus is easier to search, so the numbers would look better than they are while the command prints "ok". A procedure that depends on remembering an order is not a guarantee.
Tradeoff: any consent change or ingest between the two eval runs forces make embed and both evals again; a hash of IDs shows that two corpora differ, not how.

## D110 — 2026-11-24 — The default retrieval method changes only by promotion
Decision: The method answers use is stored in the warehouse (enrich.retrieval_default, one row, like enrich.tag_default); with no row it is vector, the D104 baseline. retrieval-compare records its verdict (numbers, method names, embed version and corpus hash only) in GCW_LABEL_DIR/experiments/, as tag-compare does. make retrieval-promote METHOD=<m> is refused unless the latest retrieval-compare verdict names <m> the winner under D104 and was made on the current embed version.
Alternative: a config or env setting; choosing the method in code.
Why: the same protocol as tag-promote (D93): a default changes only through a recorded, pre-registered comparison, never by editing config.
Tradeoff: a migration and a table for one value.

## D111 — 2026-11-24 — Answers: local model, numbered source messages, schema-constrained citations
Decision: An answer uses the top 5 results of the default retrieval method (D110). The messages of their windows are deduplicated, grouped into excerpts (contiguous runs of one thread and session; excerpts ordered by their best retrieval rank, messages in time order) and numbered 1..N across all excerpts, one line each: "[n] sender, date time: text". Source text is rebuilt from raw.messages for the request and stored nowhere. The local llm service (the tag default model, thinking off, temperature 0) must return JSON matching a schema enforced by llama.cpp's grammar, fields in this order: answerable (boolean), citations (at most 5 integers, each in 1..N), answer (text with [n] markers, length capped). The code accepts an answer only if answerable is true, citations is non-empty and every [n] in the text is a citation. answerable false with no citations is an abstention ("not in the chat"). Anything else is an invalid output: shown as a fixed failure, never retried. Each citation maps to one message ID. The system prompt marks the excerpts as quoted chat data, never instructions.
Alternative: free text with [n] markers checked afterwards; the retrieved messages plus a one-line summary and no generated answer; numbering whole windows instead of messages.
Why: the grammar makes citing a message the model was not shown impossible rather than unlikely; per-message numbers make citations precise and absorb overlapping windows; choosing answerable and the citations before writing ties the text to its sources; temperature 0 makes eval runs repeatable up to CPU nondeterminism.
Tradeoff: stiffer prose; a constrained citation can still point at the wrong message among those shown, which the eval measures; up to 25 numbered lines lengthen the prompt and the CPU latency.

## D112 — 2026-11-24 — Answer eval, automatic part; generated answers are never stored
Decision: make answer-eval [SYNTHETIC=1] runs the answer pipeline once per scorable question (the retrieval-eval drop rules and the D109 corpus guard apply) and prints fixed lines: questions, dropped, scored, invalid outputs, answered, abstained, context has gold (any gold message among the numbered sources), citation hit (answered and cites at least one gold message), each rate over scored questions with a 95% bootstrap interval, and p50/p95 seconds per question (query embedding, SQL and generation). Results go to GCW_LABEL_DIR/answers/<model id>__<answer prompt version>.json, mode 0600: question ID and hash, source and gold IDs, numbered source IDs, cited IDs, outcome, seconds, embed version, corpus hash and retrieval method; never the generated answer text. consent-prune removes rows that refer to an ineligible message, as it does for retrieval predictions. Correctness by hand grading is a later session and stores grades, not answers.
Alternative: store answer text for later grading; judge correctness with a local model.
Why: generated answers are derived from chat text, so not storing them leaves nothing new to prune or leak; "context has gold" next to "citation hit" separates retrieval misses from generation misses.
Tradeoff: citation hit is not correctness (a right citation can come with a wrong answer); hand grading will regenerate answers, which can differ slightly from this run under CPU nondeterminism.

## D113 — 2026-11-24 — make ask: questions from a prompt, content to the terminal only
Decision: make ask [SYNTHETIC=1] is an interactive loop. It reads each question from a prompt (never from argv or a make variable), prints the answer and its cited messages (sender, date, text) to the terminal, and writes nothing anywhere else: no file, no log line with text. A blank line ends it, and it prints fixed summary lines: questions asked, answered, abstained, invalid outputs, p50 and p95 seconds. Real-data runs are Ryan's.
Alternative: make ask Q="...".
Why: a question about the chat is itself personal data, and argv is saved in shell history and visible in the process list.
Tradeoff: not scriptable; scripted runs use make answer-eval.

## D114 — 2026-11-24 — Implementation details D109-D113 leave open (S6)
Decision:
- Excerpts are built from the stored window members of the retrieved rows. Every member is read again through the eligibility rule when the request is built. Two messages are contiguous when no other eligible message of their thread and session lies between them. An excerpt ranks by the best retrieval rank of any window that reaches one of its messages.
- A source line shows the local time dbt already derives (fct_messages.sent_at_local, the gcw_timezone var) as YYYY-MM-DD HH:MM. Sender and text are put on one line, one message's text is cut at the window cap (2000 characters), and "<|" is broken to "< |" in senders, text and the question, so chat text cannot open a chat-template turn.
- The answer text is capped at 600 characters and a request at 768 output tokens, with a 600 s timeout. A refused or timed-out request is an invalid output. With no source messages the answer is an abstention and the model is not asked.
- Beyond D111's rules, a blank answer text is invalid. A marker is [n] or a comma-separated group such as [1, 2], and every number in it must be a citation.
- The llm service's context size is 32768 tokens, sized from a token count of the worst-case synthetic prompt in several scripts (docs/STATUS.md).
- The answer prompt version hashes the system prompt, the schema the builder makes for two messages, the user-turn markers, the line and date formats, the text cap, the excerpt rule, the top 5 and the output token limit; a model's template setting changes it, as for the tagger (D98).
- A refused corpus (D109) prints "messages not embedded: N" and then the failure line. The corpus hash joins the sorted IDs with newlines. Verdict files are experiments/retrieval-compare-<UTC timestamp>.json, which tag-promote does not read. retrieval-promote has no synthetic form, like tag-promote.
- make ask reads the promoted model and method once at start, checks consent on every question, ends on a blank line, end of input or Ctrl-C, replaces control characters in everything it prints from the chat or the model, and ends with "ask: ok" after the D113 summary lines. consent-prune counts answer rows under "prediction rows to prune".
Alternative: a smaller context that holds ordinary text only; retrying a refused request; reading the window members without a second eligibility check.
Why: each is fixed before any real answer, keeps one eligibility rule wherever text is read, and bounds the prompt by what D111 shows the model.
Tradeoff: the larger context costs memory on every llm start, tagging included; a prompt near the worst case would be slow enough on the laptop CPU to hit the timeout.

## D115 — 2026-11-25 — make ask shows what the model was shown when it abstains
Decision: When an answer abstains, make ask prints "not in the chat" and then the numbered excerpts the model was shown, in the same line format as cited messages, control characters replaced, to the terminal only. Every answer (answered, abstained, invalid, timed out) is followed by one fixed numbers-only line: sources shown, prompt tokens, seconds. The summary lines add prompt tokens p50 and p95 and a timed out count. Nothing is written anywhere; D113 still holds.
Alternative: a flag that turns sources on; an aggregate-only diagnostic target.
Why: an abstention with no context cannot be diagnosed. Ryan reads his own chat locally, and make ask already prints chat text to the terminal for cited messages (D113), so this adds no new channel. The numbers line can be pasted without any text.
Tradeoff: more chat text on screen per question. What web app users see is decided separately in S7.

## D116 — 2026-11-25 — Answer eval records prompt size and separates timeouts
Decision: The answer pipeline records the server's usage.prompt_tokens and usage.completion_tokens (null when absent). A request that times out is its own outcome, timed_out, instead of invalid; invalid stays for refused requests and bad output. answer-eval prints the timed out count, rate and interval, and prompt tokens p50, p95 and max. Stored answer rows gain the two token counts (numbers only).
Alternative: count tokens with a separate /tokenize request; keep timeouts under invalid.
Why: a timeout is a laptop-speed failure and a bad output is a model failure; they have different fixes. The llm context size (D114) and S7 latency work need measured prompt sizes on real data, not a worst case.
Tradeoff: one more outcome everywhere outcomes are counted; answers files written before this change no longer read (none exist on real data; rerun answer-eval).

## D117 — 2026-11-25 — Single rates use Wilson score intervals
Decision: Every reported rate over questions in the retrieval and answer reports (recall@1/5/10, invalid, timed out, answered, abstained, context has gold, citation hit) uses the Wilson score 95% interval. MRR@10 and retrieval-compare's paired difference keep the bootstrap. Win rules (D104) and CI floors (D100, D108) are unchanged; they use point estimates or the paired bootstrap. Tagging reports are unchanged.
Alternative: keep the percentile bootstrap for everything.
Why: the percentile bootstrap gives [0, 0] or [1, 1] when every question fails or succeeds, which claims a certainty a 31- or 100-question set does not have (Wilson for 0 of 31 is [0.000, 0.110]).
Tradeoff: two interval methods in the reports; intervals already in STATUS.md (all synthetic) shift slightly.

## D118 — 2026-11-25 — consent-prune never fails because of an old eval file format
Decision: consent-prune reads retrieval and answer eval files with a pruning reader that needs only each row's message IDs, each checked against the message ID pattern. A file the strict reader accepts is pruned row by row, as today. A file only the pruning reader accepts (an old format: no corpus_hash, or no token fields) is deleted whole if any row refers to an ineligible message, else left untouched; it prints "old-format eval files to delete: N". A file with no readable row IDs still fails closed, as today. compare, promote and the eval readers stay strict.
Alternative: fail closed on any file the strict reader rejects (current behavior); delete every old-format file.
Why: a consent revocation must never be blocked by an eval file's format. Old-format files cannot be compared anyway, and every eval file can be regenerated.
Tradeoff: an old-format file with one ineligible reference loses all its rows, not just that one.

## D119 — 2026-11-25 — The burst's finish line: measured and local, not deployed
Decision: The burst ends when the real retrieval eval (vector vs hybrid under D104), the real answer eval with its prompt A/B (D121) and a hand grade of answers (D120) are recorded in METRICS.md, and the dashboard has an Ask page and a per-member Wrapped page, run locally. The user-facing app is the existing Streamlit dashboard. S8 (tunnel deploy) and the usage window move to later, leisure work.
Alternative: a tunnel deploy for friends with login and per-user access (original S7/S8); a FastAPI + React app.
Why: Ryan wants the burst done in days. FastAPI, React, Docker and Kubernetes already appear on his resume from internships, so this project's distinct value is the pipeline, the RAG system and its measured evals on real hand-labeled data. A laptop-served link is down whenever the laptop sleeps.
Tradeoff: no "deployed" or "active users" claim for this project; the brief's "used by real people" goal is deferred. Members do not delete their own data through an app: revoking consent plus consent-prune removes everything derived from their messages, run by Ryan.

## D120 — 2026-11-25 — Hand grading of answers (S6b)
Decision: make grade-answers [N=50] takes the scorable questions (retrieval-eval's drop rules and the D109 corpus guard) in a fixed seeded order, skips those already graded, and for each of the next N regenerates the answer with the promoted model, retrieval method and answer prompt variant. It prints the question, the answer (or "not in the chat") and the cited messages to the terminal only, as make ask does (D113). For an answer Ryan types c (correct), p (partial) or w (wrong), then y or n for whether the cited messages support it. An abstention, invalid output or timeout is recorded with its outcome and no keystroke. Each grade is written as it is entered, so a rerun or Ctrl-C keeps finished grades. Grades go to GCW_LABEL_DIR/grades/<model id>__<answer prompt version>.csv, mode 0600: question ID, outcome, grade, supported, cited message IDs, seconds; never question, answer or message text. make grade-summary prints counts and three rates with Wilson 95% intervals: answer accuracy = correct / graded questions (abstentions and failures count as not correct, since every eval question is answerable by construction), precision = correct / answered, and supported = supported / answered. consent-prune covers grade rows.
Alternative: grade every question; a local-model judge; store answer text and grade later.
Why: 50 grades is about an hour of Ryan's time and gives a real, if wide, accuracy interval; resuming lets him extend the sample later. A 4B local judge would be weaker than Ryan. Not storing answer text keeps D112's rule.
Tradeoff: about ±13 points of interval at 50; graded answers are regenerated, so they can differ slightly from the answer-eval run (CPU nondeterminism); one grader, no inter-rater check.

## D121 — 2026-11-25 — One pre-registered answer prompt variant (B) against the baseline (A)
Decision: Answer prompt A is the current one, unchanged. Prompt B changes only the system prompt's instructions: answerable is true when any shown message states or directly shows something that answers the question, even partly, and false only when no message is relevant; a partial answer says what the messages do not show; the messages are casual chat, so nicknames, slang, abbreviations and typos are matched to the question by meaning. B keeps A's data-not-instructions paragraph, schema, excerpts, top 5 and token limits. Win rule, fixed before any real run: on the real scored questions, paired per question, B becomes the default only if the 95% paired bootstrap interval of (B − A) citation hit rate lies above 0, B's invalid plus timed-out rate is at most 2 percentage points above A's, and B's p95 seconds per question is at most 1.25 × A's. Otherwise A stays. The verdict is recorded either way. make answer-promote changes the default only to the latest verdict's winner, made with the current model, prompt versions and embed version.
Alternative: edit the prompt by feel after reading answers; several variants; grade both arms by hand.
Why: the real smoke test abstained on 3 of 4 questions Ryan knows the chat answers. One variant written before any real eval result, judged by a rule fixed in advance, is an experiment, not tuning to the test set. Citation hit is automatic and paired, so both arms are scored the same way with no grader.
Tradeoff: the same 100 questions choose the prompt and report its numbers, a mild optimistic bias kept small by allowing one comparison only; citation hit can reward answering over answering correctly, which the hand grade of the winner (D120) checks.

## D122 — 2026-11-25 — make eval-all runs every real eval in one command
Decision: make eval-all [SYNTHETIC=1] runs, in order, stopping at the first failure: embed (expects nothing new), retrieval-eval vector, retrieval-eval hybrid, retrieval-compare, retrieval-promote of that verdict's winner, answer-eval with prompt A, answer-eval with prompt B, answer-compare, answer-promote of that verdict's winner. The synthetic form skips both promote steps. Each step prints a fixed header line ("== <step> ==") and that command's existing fixed lines; a failure prints the step's existing failure line and stops.
Alternative: Ryan runs nine commands by hand.
Why: one unattended command and one paste, with no chance of running steps out of order or changing consent between paired runs.
Tradeoff: a long single run (two answer-evals dominate, roughly 1.5 hours on the laptop); promotion is automatic but still refused unless a verdict names the winner (D110, D121).

## D123 — 2026-11-25 — Wrapped profiles are a dbt mart; Ask and Wrapped pages are local only
Decision: A dbt model, mart_member_wrapped, holds one row per consented sender (the consent ledger's key), computed only over local-eligible user-text messages: messages sent, share of their threads' messages, most active hour and weekday in local time, median reply time (from the existing reply-time model), first and last message date, the top 3 topics and the sentiment shares from the promoted tag model's tags (D92). dbt tests: unique and not null key, shares between 0 and 1, every row's sender eligible in the ledger. The dashboard gains a Wrapped page (pick a member, see their card) and an Ask page that calls the same answer pipeline as make ask with the promoted variant: it shows the answer and its cited messages, or "not in the chat", writes nothing, and logs and caches no question or answer text. The dashboard stays bound to 127.0.0.1 with Streamlit usage statistics off. README screenshots come from the synthetic stack only.
Alternative: compute profiles in Python inside the app; a FastAPI + React app (D119).
Why: a tested mart keeps profiles in the warehouse with every other metric, under the same consent rule; one answer pipeline keeps one consent check and one citation path.
Tradeoff: topics and sentiment carry the 4B tagger's error (METRICS.md); the app runs only on Ryan's laptop.

## D124 — 2026-11-25 — Implementation details D119-D123 leave open (burst finish)
Decision:
- mart_member_wrapped is a view, like fct_message_eligibility (D75), so a revocation removes a profile when consent-load commits, with no dbt build and no stored copy that consent-prune would have to chase. The CI check that lists the analytics views names both. A thread share is over that thread's eligible user-text messages. The busiest hour and the busiest weekday are counted separately; ties go to the earliest, and topic ties to the alphabetically first.
- The Ask page reads messages as make ask does, so the dashboard container gets the pipeline's database credentials and opens the Ask connection read-only; the other pages keep the gcw_reader role. It reaches the models by their compose names, so "embed" joins localhost, 127.0.0.1 and "llm" as a local host name. make dashboard-up runs gcw dashboard-services (db, llm with the promoted model, embed) and then builds and starts the dashboard. Everything from the chat or the model is rendered as plain text, never as Markdown.
- make answer-promote takes VARIANT=a|b, as retrieval-promote takes METHOD. answer-compare does not score a question whose row in either run refers to a message no longer eligible (D87, as retrieval-compare). "At most 2 percentage points above" is checked in whole numbers, so exactly 2 passes; p95 at exactly 1.25 times passes. The verdict stores failure counts beside the scored count.
- The grading order sorts questions by a hash of a fixed seed and the question ID, so adding or dropping a question moves no other. Keys typed before an answer is shown are discarded on a real terminal. grade-summary counts every row of the current model's and variant's file and prints the current retrieval method; a grade row does not record the method.
- eval-all names its headers embed, retrieval-eval vector, retrieval-eval hybrid, retrieval-compare, retrieval-promote, answer-eval a, answer-eval b, answer-compare and answer-promote. Embedding something new in the first step is not a failure.
Alternative: a table mart rebuilt by dbt; a second database role with read access to message text; running the dashboard on the host instead of adding a local host name; a retrieval method column in the grades file.
Why: each keeps one eligibility rule in force wherever derived data or text is read, reuses make ask's path instead of a second one, and stays inside the columns and commands D119-D123 name.
Tradeoff: the view is computed on every page load; the dashboard container now holds credentials that can read and write message text (the Ask session is set read-only, which the page's own code could undo); a retrieval method change between two grading sessions would mix methods in one file unnoticed.

## D125 — 2026-11-25 — Fixes from the burst-finish review: local-only host names, grading keys, reply time
Decision:
- Streamlit's server.allowedHosts is localhost and 127.0.0.1, so the dashboard refuses a browser connection under any other host name (DNS rebinding). Reaching it by another name needs a change to .streamlit/config.toml.
- grade-answers reads one unbuffered keypress per prompt on a real terminal, so an escape sequence or a paste matches no choice, and each save re-reads the grades file, so a row consent-prune removed during the session stays removed.
- mart_member_wrapped's median reply time is the existing reply-time model's (D123) over the member's eligible replies. The previous turn's timestamp, and the session and turn boundaries, come from all messages, so a sender without consent can affect another member's median through timestamps only, never text.
Alternative: count only replies whose previous message is also eligible; one key per buffered character, as labeling does.
Why: the dashboard now shows chat text, and grades are a measured result with no undo. Reply time is defined by the existing model; narrowing it needs a change to the intermediate model, which is left for planning.
Tradeoff: a reply time is partly derived from a message its sender did not opt in to; labeling and grading read the keyboard differently.

## D126 — 2026-11-25 — Eval passes run only on a clean, isolated machine against the expected model
Decision: (1) After starting the llm or embed service, and before every eval pass, ask, grade-answers, tag (including --no-start) and each Ask page question, the client reads /v1/models and fails closed unless the served model is the expected one. (2) A command refuses to start or use model services while llm or embed containers of another GC Wrapped compose project are running (dev vs gc-wrapped-real), judged from docker ps names and compose-project labels only. (3) Validity guard for the D104 and D121 comparisons: before each answer-eval pass the llm service is restarted; every retrieval-eval and answer-eval pass refuses to start if swap in use exceeds 256 MiB, prints available memory and swap in use at start and end, and fails if swap in use grew by more than 256 MiB during the pass, so eval-all stops before any compare or promote and the whole run is repeated. (4) Both llama.cpp services run with the slots endpoint disabled. (5) answer-eval and ask print counts of invalid outputs per fixed reason; this is a diagnostic and changes neither prompt B nor the D121 rule.
Alternative: interleave A and B per question in one pass; trust whatever answers on the configured port; leave the slots endpoint on.
Why: on 2026-11-25 the real stack's model servers ran beside the dev stack's; swap filled, one pass's p95 grew several-fold, and a client could have talked to whichever server held its port. On a clean machine a synthetic run gave nearly identical A and B p95, so a checked, clean machine is enough for paired latency, without interleaving's extra complexity and production-unlike absolute latency. The slots endpoint can expose the prompt a server last processed to anything on localhost.
Tradeoff: slow drift such as thermal throttling over a long run is not controlled; a tripped guard costs a full rerun; restarting llm adds load time and starts each answer pass with a cold prompt cache (the same for both arms). Correction to D119: consent-prune removes everything derived from a revoked sender's messages except rows in dbt tables, which go at the next dbt build (README, Deleting data).

## D127 — 2026-11-25 — The Ask page connects as gcw_ask, a read-only role with only the grants answering needs
Decision: The dashboard container no longer receives POSTGRES_USER or POSTGRES_PASSWORD. The Ask page connects as gcw_ask (password GCW_ASK_PASSWORD): a LOGIN role with default_transaction_read_only on, USAGE on the needed schemas and SELECT on exactly the relations the answer pipeline reads, and nothing else. gcw dashboard-services creates or updates the role and its grants on the host, through the superuser connection from the env file, before the dashboard starts; grants on dbt-managed relations are also set in dbt so a rebuild keeps them. Supersedes D124's credentials paragraph.
Alternative: the pipeline's credentials in a session set read-only (D124); a role created by the Postgres init script.
Why: read-only is enforced by grants rather than by the page's own code, so a compromised page can read what answering reads and nothing more. Creating the role from dashboard-services also works on existing volumes, where init scripts do not rerun.
Tradeoff: one more secret in every env file; the grant list must follow the pipeline (a test fails if answering needs a relation the role cannot read).

## D128 — 2026-11-25 — Reader-visible marts count and name only opted-in senders
Decision: No relation gcw_reader can read names, counts or otherwise includes a sender or message that is not local_ok (D88). The Activity marts count only local_ok messages and list only local_ok senders, like mart_member_wrapped. Session boundaries, turns, the reply-time definition, embedding windows, the embed version and the corpus hash are unchanged: as in D125, timestamps of other messages can shape sessions and reply times, never counts or names. Relations that must keep every message for the pipeline lose their reader grant. A revocation reaches table marts at the next dbt build (the README's deletion flow already rebuilds).
Alternative: a two-tier rule (the thread allowlist covers metadata, the ledger covers content).
Why: Ryan's rule since November 2026 is that no one's messages are used on silence, including local-only work, and CLAUDE.md already says non-consented senders are excluded from everything. One rule then holds across the warehouse.
Tradeoff: thread totals on the Activity page are opted-in totals; a revocation shows in Activity only after a rebuild.

## D129 — 2026-11-26 — Implementation details D127 and D128 leave open
Decision:
- Migration 009 creates gcw_ask with no login, password or grant, so that dbt's grants work on any migrated warehouse before dashboard-up has run. gcw dashboard-services (gcwrapped.dashboard.ask_role) sets the rest in one transaction: it revokes the role's table, sequence and schema rights in the database, then grants. It refuses an unset GCW_ASK_PASSWORD, or one equal to the superuser's or the reader's (URL passwords included), before starting anything, and needs a built warehouse. The server receives only the SCRAM verifier, computed on the client.
- So that nothing can be written even with read-only turned off, it also revokes from PUBLIC, in the target database, TEMPORARY and EXECUTE on the large-object creation functions.
- The consent gate of the Activity marts is one table, fct_consented_messages (fct_messages rows whose sender is local_ok, without turn_id), which agg_activity_daily, agg_member_activity, fct_sessions and the page's own queries read. fct_messages, dim_members and fct_reactions lose the reader grant.
- A reaction is counted only when the reactor sent an opted-in message in the thread and the reacted-to message is opted in. A session is listed if it has an opted-in message; its times, counts and the day it counts on use those messages; started_by_member_key is null when the sender of its opening message has not opted in.
Alternative: create the role in a dbt on-run-start hook; gate each mart separately and add aggregate tables for the page's hour and reply-time queries; renumber sessions for the reader.
Why: migrations already run before every dbt build; one gate is one place to read and to test; renumbering would change session_id.
Tradeoff: session_index and session_id keep their numbers over all messages, so gaps show that sessions with no opted-in message exist. gcw_ask can read all of raw.messages, which answering reads, so consent on answers rests on the query. Its PUBLIC rights in other databases of the same cluster are untouched.

## D130 — 2026-11-26 — The dashboard makes no outbound network requests
Decision: Streamlit's startup external-IP lookup is disabled by setting browser.serverAddress = "localhost" in .streamlit/config.toml, and usage statistics stay off (browser.gatherUsageStats = false). A static test pins both; CI checks the dashboard log has no "External URL" line.
Alternative: an internal-only Docker network for the dashboard (breaks the published port); leave it.
Why: the dashboard shows chat text on the Ask page and has no reason to contact anything outside the compose network; the lookup tells an outside service this machine's public IP at every start.
Tradeoff: depends on current Streamlit behavior; an upgrade could add a new call, which the log check only partly covers.

## D131 — 2026-11-26 — gcw_ask reads only consent-filtered views
Decision: The answer pipeline (retrieval and excerpts, for make ask, the evals and the Ask page alike) reads messages and vectors through dbt views in analytics that return only messages passing the local-work rule (local_ok AND has_user_text, D88, D95). The views are owned by the dbt user and run with its rights (not security_invoker), and are marked security_barrier. gcw_ask is granted SELECT on these views and on the default tables the pipeline reads, and nothing else: its grants on raw.messages, enrich.message_embeddings, fct_messages, fct_message_eligibility and fct_message_text are removed. The views are live over the eligibility view, so a revocation reaches gcw_ask when consent-load commits. Supersedes D129's statement that consent on answers rests on the query.
Alternative: row-level security on raw.messages and enrich.message_embeddings with a policy for gcw_ask; keep the grants and document the gap (D129).
Why: the database, not application code, enforces consent for the one role whose password sits in a container. One code path keeps make ask, the evals and the page identical. Views are easier to read and test than policies, and policies on dbt-rebuilt tables would need recreating after every build.
Tradeoff: the pipeline's queries change, so identical retrieval and prompts had to be shown. A vector for an eligible message whose window included a since-revoked neighbor still ranks until consent-prune (D105) removes it.

## D132 — 2026-11-26 — Correction to D130: the lookup host resolves inside the container
Decision: D130 removes the lookup at startup only: Streamlit 1.66.1 still calls get_external_ip when a websocket arrives with an allowed Host and a foreign Origin. The dashboard service maps checkip.amazonaws.com to 127.0.0.1 (compose extra_hosts), so that call fails inside the container and nothing leaves the machine. A test lists every URL in the installed streamlit/net_util.py and fails if one uses another host.
Alternative: patch Streamlit's private IP cache; an internal-only network (breaks the published port); accept and only correct D130's wording.
Why: a web page open in the browser could otherwise make the dashboard tell an outside service this machine's public IP.
Tradeoff: tied to one hostname; an upgrade that calls another host from elsewhere in Streamlit would not be caught by the net_util.py test.

## D133 — 2026-11-26 — Implementation details D131 and D132 leave open, and two corrections
Decision:
- The views are analytics.ask_messages and analytics.ask_message_embeddings, dbt view models with a post-hook that sets security_barrier. ask_message_embeddings holds the rows, of every embed version, whose centre message is in ask_messages; it carries the window IDs, the vector and the lexemes.
- Correction to D131's tradeoff: a vector whose window included a since-revoked neighbor does not rank. Search still requires every window member to be in ask_messages (D75, D103), as before. What remains until consent-prune is that gcw_ask can read that row, whose vector and lexemes were made from the neighbor's text too. Excerpt loading reads its window IDs and takes only the members ask_messages holds.
- consent-prune is unchanged: it reads the tables as the pipeline user, and a test compares what it leaves with what search returns.
- gcw_ask has no USAGE on raw. It keeps enrich.tag_default, enrich.retrieval_default and enrich.answer_prompt_default, all of which the pipeline reads.
- Correction to D132's test: net_util.py also holds three URLs that nothing requests (a licence comment, a help link that a warning prints and a source credit in a docstring). The test excepts exactly those three and asserts that only the two checkip URLs are passed to the request function.
Alternative: a vector view that also requires the whole window (tried: a revocation between search and excerpt loading then drops the whole result, where today its other eligible members are still shown); null the vector and lexemes of such rows in the view and keep their IDs.
Why: the centre filter is the rule D131 states and changes no behavior; search and excerpts return what they did before.
Tradeoff: until consent-prune, gcw_ask can read the words (as lexemes) of a revoked sender inside a neighbor's row. A dbt rebuild drops and recreates the views, so between those two steps, or after a failed build, answering fails instead of reading anything unfiltered.
