# GC Wrapped

Group chat analytics platform: chat exports go into a Postgres warehouse (dbt), get enriched by an LLM, power RAG Q&A with citations, and produce a "Wrapped" profile per member.

Status: Phase 1 (pipeline). Parser, Postgres loader, dbt models and the Airflow DAG work; the dashboard is in; one-command e2e is next. See [docs/STATUS.md](docs/STATUS.md).

## Architecture

TBD. Planned flow: export file → parser → Postgres `raw` schema → dbt models → Airflow landing-zone DAG → dashboard. Design rationale lives in [docs/decisions.md](docs/decisions.md).

## Quickstart

Requires [uv](https://docs.astral.sh/uv/), Docker, and make.

```
cp .env.example .env        # then set POSTGRES_PASSWORD (and the same password in DATABASE_URL)
uv sync --all-groups
make up                     # Postgres 17 + pgvector, databases gcw_dev and gcw_real
make lint test
make test-integration       # needs the db container up
make dbt-debug
```

## Usage

Both commands connect with `DATABASE_URL` (plus `POSTGRES_USER` / `POSTGRES_PASSWORD`) from the environment or `.env`, and print the target database name first so you can see where you are writing.

```
gcw migrate                 # create/upgrade the raw schema (idempotent)
gcw ingest PATH [PATH ...]  # parse export root(s) and load them into raw.messages
gcw parse PATH [PATH ...]   # parse only, print counts; no database needed
```

- **`gcw migrate`** applies the numbered files in `migrations/` that are not yet recorded in `schema_migrations`, in order, each in its own transaction. Running it again applies nothing. Run it once per database before the first `ingest`.
- **`gcw ingest`** parses one or more export roots, then loads them in a single transaction. It prints counts only: rows parsed, rows inserted, duplicates skipped, elapsed seconds. Re-ingesting a file, or an export that overlaps an earlier one, inserts only the new messages (first write wins). Every run is recorded in `raw.load_runs` with its status and counts; a failed run leaves no partial messages.

## Airflow

A daily DAG, `gcw_pipeline`, runs `check_landing → migrate → ingest → dbt_deps_and_build → summarize` over a landing directory. Each export is one subfolder of that directory. Tasks only call the `gcw` CLI and dbt, and both log counts, never message content.

Run it locally (Docker needed; the DAG itself is verified in CI):

```
mkdir -p data/landing                    # drop export folders in here, one subfolder per export
make airflow-up                          # builds airflow/Dockerfile, starts db + Airflow
# open http://localhost:8080, unpause gcw_pipeline, trigger it
```

- `GCW_LANDING_HOST_DIR` chooses which host folder is mounted (read-only) at `GCW_LANDING_DIR` in the container; it defaults to `data/landing`.
- The database comes from `DATABASE_URL` in the container's environment, not from Airflow connections. It defaults to `gcw_dev`; pointing it elsewhere (`GCW_DBNAME`) is a deliberate manual step.
- To run it once without the UI: `docker compose exec airflow airflow dags test gcw_pipeline`.
- CI runs the same thing: it builds the image, runs the DAG tests inside it, then `airflow dags test gcw_pipeline` against a Postgres service and synthetic exports.

**Why Airflow and dbt use separate environments.** Airflow pins a large set of dependencies with a constraints file, and dbt's own dependencies often conflict with those pins. The image therefore keeps Airflow in its own Python environment and installs `gcwrapped` and dbt into a second virtualenv, `/opt/gcw-venv`, built from `uv.lock`. Tasks run that venv's `gcw` and `dbt` binaries as subprocesses, so neither side can break the other when it upgrades.

## Dashboard

A Streamlit app shows activity from the `analytics` marts: KPIs, messages per day, a member leaderboard and an hour-of-day chart, per thread. It connects as `gcw_reader`, a read-only role that can read mart tables and nothing in `raw`, staging or intermediate, so message text cannot reach it.

```
# .env needs GCW_READER_PASSWORD (the role is created on first `make up`, from docker/postgres/init/02-reader-role.sh)
# Run the pipeline first so the marts exist (ingest + dbt build); dbt grants the role its access.
make dashboard-up           # http://localhost:8501
make test-warehouse         # query tests, permission-denied checks, Streamlit AppTest (after dbt build)
```

If your Postgres volume already existed before this change, the init script will not rerun: run `bash docker/postgres/init/02-reader-role.sh` inside the db container once.

## Privacy

Real chat data is sensitive, and the AI coding assistant used on this repo is a third-party API. The rules:

- The assistant never reads, prints, searches, or lists anything under `data/`. It works only with synthetic fixtures in `tests/fixtures/`.
- The assistant only connects to the `gcw_dev` database (synthetic data). The `gcw_real` database is for the repo owner alone.
- Real-data commands are run by the repo owner in their own terminal, and only aggregate numbers are shared back.
- Logs and error messages never include message text, sender names, phone numbers, or emails. They carry counts, IDs, durations, and file hashes.
- Chat data, media, `.env` files, and `members.yaml` are never committed. `data/` is git-ignored.
- Phase 1 pipeline code makes no external API calls.

## Metrics

See [METRICS.md](METRICS.md). Every number there comes from a recorded run, with its source and date.
