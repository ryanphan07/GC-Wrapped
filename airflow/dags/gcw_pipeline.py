"""Daily pipeline: check landing dir -> migrate -> ingest -> dbt build -> summarize.

Tasks only shell out to the gcw CLI and dbt in the isolated /opt/gcw-venv, so Airflow's own
environment never imports project code or dbt. Both print counts only, never message content.
Configuration comes from the environment (GCW_LANDING_DIR, GCW_DB_NAME, POSTGRES_*), not from
Airflow connections. Ingest is idempotent, so every run re-ingests the whole landing directory.
"""

from datetime import UTC, datetime

from airflow.sdk import dag, task

GCW = "/opt/gcw-venv/bin/gcw"
DBT = "/opt/gcw-venv/bin/dbt"
DBT_DIRS = "--project-dir /opt/gcw/dbt --profiles-dir /opt/gcw/dbt"
LANDING = '"${GCW_LANDING_DIR:?GCW_LANDING_DIR is not set}"'


@dag(
    dag_id="gcw_pipeline",
    schedule="@daily",
    start_date=datetime(2026, 11, 1, tzinfo=UTC),
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 1},
    tags=["gcw"],
)
def gcw_pipeline() -> None:
    @task.bash
    def check_landing() -> str:
        return f"{GCW} check-landing {LANDING}"

    @task.bash
    def migrate() -> str:
        return f"{GCW} migrate"

    @task.bash
    def ingest() -> str:
        return f"{GCW} ingest --landing {LANDING}"

    @task.bash
    def dbt_deps_and_build() -> str:
        # No --store-failures anywhere: failing rows would land in the database and the logs.
        return f"set -e\n{DBT} deps {DBT_DIRS}\n{DBT} build {DBT_DIRS}"

    @task.bash
    def summarize() -> str:
        return f"{GCW} last-run"

    check_landing() >> migrate() >> ingest() >> dbt_deps_and_build() >> summarize()


gcw_pipeline()
