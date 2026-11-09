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
