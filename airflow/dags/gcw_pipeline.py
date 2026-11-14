"""Daily pipeline: check landing dir -> migrate -> ingest -> dbt build -> summarize (+ tag).

Tasks only shell out to the gcw CLI and dbt in the isolated /opt/gcw-venv, so Airflow's own
environment never imports project code or dbt. Both print counts only, never message content.
Configuration comes from the environment (GCW_LANDING_DIR, GCW_DB_NAME, POSTGRES_*, and
CHAT_TIMEZONE, which dbt's gcw_timezone var reads; tasks inherit the container env), not from
Airflow connections. GCW_THREAD_ALLOWLIST (compose passes it in) is the consent allowlist: check
and ingest read it, and ingest fails without it. The DAG has no way around it.
Ingest is idempotent, so every run re-ingests the whole landing directory.
The optional `tag` task (D89) follows dbt build as a leaf: it tags only new local-eligible messages
and is skipped (exit 99) when GCW_LLM_URL is unset, so a stack without the llm service is unchanged.
It talks only to the local llama.cpp server; the client refuses any other host.
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

    @task.bash
    def tag() -> str:
        # Exit 99 is Airflow's "skip this task" code; the URL itself is validated by the client.
        return f'if [ -z "${{GCW_LLM_URL:-}}" ]; then exit 99; fi\n{GCW} tag --no-start'

    build = dbt_deps_and_build()
    build >> tag()
    check_landing() >> migrate() >> ingest() >> build >> summarize()


gcw_pipeline()
