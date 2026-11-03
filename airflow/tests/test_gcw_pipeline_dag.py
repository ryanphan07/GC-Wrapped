"""DAG structure tests. Run inside the built image: `docker run --rm <image>:test`."""

from pathlib import Path

import pytest
from airflow.models.dagbag import DagBag
from airflow.sdk import DAG

DAGS_DIR = Path(__file__).resolve().parents[1] / "dags"
ORDER = ["check_landing", "migrate", "ingest", "dbt_deps_and_build", "summarize"]


@pytest.fixture(scope="module")
def dagbag() -> DagBag:
    return DagBag(dag_folder=str(DAGS_DIR), include_examples=False)


@pytest.fixture(scope="module")
def dag(dagbag: DagBag) -> DAG:
    found = dagbag.get_dag("gcw_pipeline")
    assert found is not None
    return found


def test_dags_import_without_errors(dagbag: DagBag) -> None:
    assert dagbag.import_errors == {}


def test_tasks_run_in_the_expected_order(dag: DAG) -> None:
    assert sorted(dag.task_ids) == sorted(ORDER)
    for upstream, downstream in zip(ORDER, ORDER[1:], strict=False):
        assert dag.get_task(downstream).upstream_task_ids == {upstream}
    assert dag.get_task(ORDER[0]).upstream_task_ids == set()


def test_schedule_and_run_settings(dag: DAG) -> None:
    assert dag.timetable.summary == "@daily"
    assert dag.catchup is False
    assert dag.max_active_runs == 1


def test_every_task_retries_once(dag: DAG) -> None:
    assert {t.task_id: t.retries for t in dag.tasks} == dict.fromkeys(ORDER, 1)
