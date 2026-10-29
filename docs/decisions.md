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
