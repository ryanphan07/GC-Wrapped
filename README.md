# GC Wrapped

Group chat analytics platform: chat exports go into a Postgres warehouse (dbt), get enriched by an LLM, power RAG Q&A with citations, and produce a "Wrapped" profile per member.

Status: Phase 1 (pipeline) scaffold. No pipeline logic yet. See [docs/STATUS.md](docs/STATUS.md).

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
