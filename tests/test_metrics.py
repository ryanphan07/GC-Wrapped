"""`gcw metrics` output shapes, error path and counting helpers. Synthetic values only."""

import re
import subprocess
from pathlib import Path

import pytest

from gcwrapped import metrics
from gcwrapped.metrics import (
    LINE_SHAPES,
    DbtCounts,
    PytestCounts,
    Steps,
    WarehouseStats,
    count_manifest,
    parse_collected,
)
from gcwrapped.runs import RunSummary

WAREHOUSE = WarehouseStats(
    database="gcw_dev",
    raw_messages=10,
    modeled_messages=9,
    threads=3,
    distinct_senders=4,
    load_id=7,
    load_status="succeeded",
    rows_parsed=10,
    rows_inserted=10,
    load_seconds=1.2,
)
DBT = DbtCounts(
    models=9,
    data_tests=111,
    generic_tests=109,
    singular_tests=2,
    unit_tests=4,
    sources=2,
    seeds=0,
    hooks=1,
)
PYTEST = PytestCounts(unit=228, non_unit=40)


def _steps(**overrides: object) -> Steps:
    base = {
        "database": lambda: None,
        "warehouse": lambda: WAREHOUSE,
        "dbt": lambda: DBT,
        "pytest": lambda: PYTEST,
    }
    base.update(overrides)  # type: ignore[arg-type]
    return Steps(**base)  # type: ignore[arg-type]


def test_every_output_line_matches_exactly_its_allowed_shape(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert metrics.run(_steps()) == 0

    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == len(LINE_SHAPES)
    for line, shape in zip(lines, LINE_SHAPES, strict=True):
        assert re.fullmatch(shape, line), "a line does not match its allowed shape"
    assert lines[-1] == "metrics: ok"


def test_values_are_rendered_from_the_counts(capsys: pytest.CaptureFixture[str]) -> None:
    metrics.run(_steps())

    out = capsys.readouterr().out
    assert "warehouse distinct senders: 4\n" in out
    assert "latest load seconds: 1.20\n" in out
    assert "dbt singular tests: 2\n" in out
    assert "pytest non-unit tests collected: 40\n" in out


@pytest.mark.parametrize("step", ["database", "warehouse", "dbt", "pytest"])
def test_failure_prints_step_and_type_name_only(
    step: str, capsys: pytest.CaptureFixture[str]
) -> None:
    class BoomError(Exception):
        pass

    def boom() -> None:
        raise BoomError("SENTINELTEXT in the message")

    assert metrics.run(_steps(**{step: boom})) == 1

    out = capsys.readouterr().out
    assert out == f"metrics: failed at {step} (BoomError)\n"
    assert "SENTINELTEXT" not in out


def test_failure_message_never_reaches_the_output(capsys: pytest.CaptureFixture[str]) -> None:
    def boom() -> WarehouseStats:
        raise ValueError("SENTINELTEXT")

    assert metrics.run(_steps(warehouse=boom)) == 1

    out = capsys.readouterr()
    assert "metrics: failed at warehouse" in out.out
    assert "ValueError" in out.out
    assert "sentinel" not in (out.out + out.err).lower()


def test_nothing_is_printed_before_every_step_succeeds(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def boom() -> PytestCounts:
        raise RuntimeError

    metrics.run(_steps(pytest=boom))

    assert capsys.readouterr().out == "metrics: failed at pytest (RuntimeError)\n"


def test_unknown_database_name_is_refused_before_connecting() -> None:
    with pytest.raises(metrics.UnexpectedDatabaseError):
        metrics.warehouse_stats("postgresql://u:p@localhost:1/some_other_db")


def test_count_manifest_splits_by_resource_type_and_ignores_packages() -> None:
    def node(kind: str, package: str = "gcwrapped", **extra: object) -> dict[str, object]:
        return {"resource_type": kind, "package_name": package, **extra}

    manifest = {
        "nodes": {
            "m1": node("model"),
            "m2": node("model"),
            "m3": node("model", package="dbt_utils"),
            "t1": node("test", test_metadata={"name": "unique"}),
            "t2": node("test", test_metadata={"name": "not_null"}),
            "t3": node("test"),
            "t4": node("test", package="dbt_utils", test_metadata={"name": "x"}),
            "s1": node("seed"),
            "h1": node("operation"),
            "h2": node("operation", package="dbt_utils"),
        },
        "unit_tests": {"u1": node("unit_test"), "u2": node("unit_test", package="other")},
        "sources": {"src1": node("source"), "src2": node("source")},
    }

    assert count_manifest(manifest) == DbtCounts(
        models=2,
        data_tests=3,
        generic_tests=2,
        singular_tests=1,
        unit_tests=1,
        sources=2,
        seeds=1,
        hooks=1,
    )


def test_buildable_nodes_is_what_dbt_build_reports() -> None:
    assert DBT.buildable == 125  # 9 models + 111 data tests + 4 unit tests + 1 hook


def test_count_manifest_without_unit_tests_is_zero() -> None:
    assert count_manifest({"nodes": {}, "sources": {}}).unit_tests == 0


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("tests/a.py::t\n\n248 tests collected in 0.40s\n", 248),
        ("tests/a.py::t\n\n228/248 tests collected (20 deselected) in 0.40s\n", 228),
        ("1 test collected in 0.01s\n", 1),
    ],
)
def test_parse_collected(output: str, expected: int) -> None:
    assert parse_collected(output) == expected


def test_parse_collected_without_a_summary_line_raises() -> None:
    with pytest.raises(metrics.PytestCountError):
        parse_collected("ERROR collecting tests\n")


def test_makefile_test_target_uses_the_selection_metrics_counts() -> None:
    makefile = (Path(__file__).resolve().parents[1] / "Makefile").read_text(encoding="utf-8")
    marker, ignore = metrics.UNIT_PYTEST_ARGS[1], metrics.UNIT_PYTEST_ARGS[2]

    assert f'-m "{marker}"' in makefile
    assert ignore.removeprefix("--ignore=") in makefile
    assert "IGNORE_WAREHOUSE = " + ignore in makefile


def test_unexpected_load_status_never_reaches_the_output(
    capsys: pytest.CaptureFixture[str],
) -> None:
    bad = WarehouseStats(**{**WAREHOUSE.__dict__, "load_status": "SENTINELTEXT"})

    assert metrics.run(_steps(warehouse=lambda: bad)) == 1

    out = capsys.readouterr().out
    assert out == "metrics: failed at render (ShapeError)\n"
    assert "sentinel" not in out.lower()


def test_stats_reject_a_status_other_than_succeeded_or_failed() -> None:
    run = RunSummary(
        load_id=1,
        status="SENTINELTEXT",
        rows_parsed=1,
        rows_inserted=1,
        elapsed_seconds=0.1,
    )
    with pytest.raises(metrics.NoLoadRunError):
        metrics._stats("gcw_dev", 1, (1, 1, 1), run)


def test_wrong_line_count_or_order_fails_with_shape_error() -> None:
    good = metrics.render(WAREHOUSE, DBT, PYTEST)
    metrics.check_shapes(good)

    with pytest.raises(metrics.ShapeError):
        metrics.check_shapes(good[:-1])
    with pytest.raises(metrics.ShapeError):
        metrics.check_shapes([good[1], good[0], *good[2:]])


def test_wrong_render_fails_the_run_with_shape_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(metrics, "render", lambda w, d, p: ["target database: gcw_dev"])

    assert metrics.run(_steps()) == 1
    assert capsys.readouterr().out == "metrics: failed at render (ShapeError)\n"


def test_dbt_step_uses_no_env_file_values_and_writes_outside_the_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / "sentinel.env"
    env_file.write_text("POSTGRES_PASSWORD=SENTINELPASSWORD\nGCW_DB_NAME=SENTINELDB\n")
    monkeypatch.setenv("ENV_FILE", str(env_file))
    project = tmp_path / "dbt"
    (project / "dbt_packages").mkdir(parents=True)
    calls: list[tuple[list[str], dict[str, str]]] = []

    def fake_run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        env = kwargs["env"]
        assert isinstance(env, dict)
        calls.append((argv, env))
        target = Path(argv[argv.index("--target-path") + 1])
        target.mkdir(parents=True)
        (target / "manifest.json").write_text('{"nodes": {}}')
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert metrics.dbt_counts(project).models == 0

    assert calls
    repo = Path(__file__).resolve().parents[1]
    for argv, env in calls:
        assert not any("SENTINEL" in a for a in argv)
        assert not any("SENTINEL" in v for v in env.values())
        for flag in ("--target-path", "--log-path"):
            path = Path(argv[argv.index(flag) + 1]).resolve()
            assert not path.is_relative_to(repo)
            assert not path.is_relative_to(project)
    assert not (project / "target").exists()
    assert not (project / "logs").exists()
