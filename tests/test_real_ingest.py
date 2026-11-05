"""real-ingest: guards, the output summarizer, the runner and the Makefile wiring.

Everything uses temporary env files and synthetic output. SENTINELNAME / SENTINELTEXT stand in for
names and message text that must never reach the terminal.
"""

import os
import re
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from gcwrapped import real_ingest
from gcwrapped.real_ingest import (
    ALLOWED_LINE_PATTERNS,
    GuardError,
    Plan,
    Step,
    check_guards,
    run,
    summarize,
    summarize_line,
)

REPO = Path(__file__).resolve().parents[1]
GOOD_ENV = {
    "COMPOSE_PROJECT_NAME": "gcw-synthetic-realtest",
    "POSTGRES_HOST_PORT": "5544",
    "DASHBOARD_PORT": "8599",
    "GCW_DB_NAME": "gcw_synthetic_realtest",
    "GCW_THREAD_ALLOWLIST": "111,222",
}


def write_env(tmp_path: Path, **overrides: str | None) -> str:
    """A temporary env file (valid unless overridden; None removes a key). Returns its path."""
    values: dict[str, str | None] = {**GOOD_ENV, "GCW_LOG_DIR": str(tmp_path / "logs")}
    values.update(overrides)
    (tmp_path / "logs").mkdir(exist_ok=True)
    path = tmp_path / "real-test.env"
    path.write_text(
        "# synthetic test env\n"
        + "".join(f"{k}={v}\n" for k, v in values.items() if v is not None),
        encoding="utf-8",
    )
    return str(path)


# --- Guards --------------------------------------------------------------------------------------


def test_valid_env_file_passes_the_guards(tmp_path: Path) -> None:
    plan = check_guards(write_env(tmp_path))

    assert plan.allowlist_size == 2
    assert plan.log_dir == tmp_path / "logs"
    assert plan.landing == "./data/landing"  # the compose default when the env file omits it


def test_refuses_when_env_file_is_not_set() -> None:
    with pytest.raises(GuardError, match="ENV_FILE is not set"):
        check_guards("")


@pytest.mark.parametrize("name", [".env", ".env.example"])
def test_refuses_the_dev_env_files(tmp_path: Path, name: str) -> None:
    (tmp_path / "elsewhere").mkdir()
    dev_named = Path(write_env(tmp_path)).rename(tmp_path / "elsewhere" / name)
    with pytest.raises(GuardError, match="dev env file"):
        check_guards(str(dev_named))


def test_refuses_a_missing_env_file(tmp_path: Path) -> None:
    with pytest.raises(GuardError, match="does not exist"):
        check_guards(str(tmp_path / "nope.env"))


def test_refuses_the_dev_project_name_or_none(tmp_path: Path) -> None:
    with pytest.raises(GuardError, match="COMPOSE_PROJECT_NAME is not set"):
        check_guards(write_env(tmp_path, COMPOSE_PROJECT_NAME=None))
    with pytest.raises(GuardError, match="dev project name"):
        check_guards(write_env(tmp_path, COMPOSE_PROJECT_NAME=real_ingest.DEV_COMPOSE_PROJECT))


@pytest.mark.parametrize(
    ("var", "dev_port"), [("POSTGRES_HOST_PORT", "5432"), ("DASHBOARD_PORT", "8501")]
)
def test_refuses_missing_or_dev_ports(tmp_path: Path, var: str, dev_port: str) -> None:
    with pytest.raises(GuardError, match=f"{var} is not set"):
        check_guards(write_env(tmp_path, **{var: None}))
    with pytest.raises(GuardError, match=f"{var} .*dev stack's port"):
        check_guards(write_env(tmp_path, **{var: dev_port}))
    with pytest.raises(GuardError, match=f"{var} .*not a number"):
        check_guards(write_env(tmp_path, **{var: "SENTINELNAME"}))


def test_port_must_be_ascii_digits(tmp_path: Path) -> None:
    for bad in ("5²", "٣٣٣٣", "55 44", "-5544", "55.4"):
        with pytest.raises(GuardError, match=r"POSTGRES_HOST_PORT .*not a number"):
            check_guards(write_env(tmp_path, POSTGRES_HOST_PORT=bad))


def test_refuses_a_missing_or_dev_database_name(tmp_path: Path) -> None:
    with pytest.raises(GuardError, match="GCW_DB_NAME is not set"):
        check_guards(write_env(tmp_path, GCW_DB_NAME=None))
    with pytest.raises(GuardError) as info:
        check_guards(write_env(tmp_path, GCW_DB_NAME="gcw_dev"))
    assert str(info.value) == "GCW_DB_NAME in the env file is the dev database"


def test_refuses_a_missing_empty_or_invalid_allowlist(tmp_path: Path) -> None:
    for value in (None, "", "  "):
        with pytest.raises(GuardError, match="GCW_THREAD_ALLOWLIST is not set or is empty"):
            check_guards(write_env(tmp_path, GCW_THREAD_ALLOWLIST=value))
    with pytest.raises(GuardError) as info:
        check_guards(write_env(tmp_path, GCW_THREAD_ALLOWLIST="111,SENTINELNAME"))
    assert "entry 2 of 2 is not numeric" in str(info.value)
    assert "SENTINELNAME" not in str(info.value)


def test_refuses_a_missing_or_nonexistent_log_dir(tmp_path: Path) -> None:
    with pytest.raises(GuardError, match="GCW_LOG_DIR is not set"):
        check_guards(write_env(tmp_path, GCW_LOG_DIR=None))
    with pytest.raises(GuardError, match="GCW_LOG_DIR does not exist"):
        check_guards(write_env(tmp_path, GCW_LOG_DIR=str(tmp_path / "missing")))


def test_refuses_a_log_dir_inside_the_repo(tmp_path: Path) -> None:
    with pytest.raises(GuardError, match="inside the repo"):
        check_guards(write_env(tmp_path, GCW_LOG_DIR=str(REPO / "tests")))


def test_guard_messages_never_include_env_values(tmp_path: Path) -> None:
    cases = [
        {"COMPOSE_PROJECT_NAME": "SENTINELNAME"},  # fine value, but must not be echoed anyway
        {"POSTGRES_HOST_PORT": "5432"},
        {"GCW_LOG_DIR": str(tmp_path / "SENTINELNAME")},
    ]
    for overrides in cases:
        try:
            check_guards(write_env(tmp_path, **overrides))
        except GuardError as exc:
            assert "SENTINELNAME" not in str(exc)


def test_dev_constants_match_docker_compose() -> None:
    """The guards compare against these; they must be what the dev stack really uses."""
    compose = (REPO / "docker-compose.yml").read_text(encoding="utf-8")
    assert re.search(rf"^name: {re.escape(real_ingest.DEV_COMPOSE_PROJECT)}$", compose, re.M)
    assert (
        f"${{POSTGRES_HOST_PORT:-${{POSTGRES_PORT:-{real_ingest.DEV_POSTGRES_PORT}}}}}" in compose
    )
    assert f"${{DASHBOARD_PORT:-{real_ingest.DEV_DASHBOARD_PORT}}}" in compose


def test_refusal_prints_a_clear_message_and_starts_nothing(tmp_path: Path) -> None:
    shown: list[str] = []

    def must_not_run(_plan: Plan) -> list[Step]:
        raise AssertionError("a refused run must not build or start any step")

    code = run(
        write_env(tmp_path, POSTGRES_HOST_PORT="5432"), steps_for=must_not_run, show=shown.append
    )

    assert code == 2
    assert shown == [
        "real-ingest: refused: POSTGRES_HOST_PORT in the env file equals the dev stack's port"
    ]
    assert list((tmp_path / "logs").iterdir()) == []  # no log was even created


# --- Makefile wiring (guards only: nothing is started) -------------------------------------------


def _make_real_ingest(*args: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k != "ENV_FILE"}
    return subprocess.run(
        ["make", "real-ingest", *args],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_make_real_ingest_without_env_file_is_refused() -> None:
    result = _make_real_ingest()

    assert result.returncode != 0
    assert "ENV_FILE is not set" in result.stdout
    assert "docker" not in (result.stdout + result.stderr).lower()


def test_make_real_ingest_with_the_dev_env_file_is_refused() -> None:
    result = _make_real_ingest("ENV_FILE=.env")

    assert result.returncode != 0
    assert "dev env file" in result.stdout


# --- Summarizer ----------------------------------------------------------------------------------


def test_allowed_patterns_are_fixed_labels_and_numbers_only() -> None:
    """Guards against someone adding a loose pattern (anything that could match free text)."""
    loose = (".*", ".+", r"\S", r"\w", r"\D", r"\s", "[^")
    for pattern in ALLOWED_LINE_PATTERNS:
        for fragment in loose:
            assert fragment not in pattern.pattern, pattern.pattern


AIRFLOW_TAIL = "[airflow.task.hooks.x.SubprocessHook] loc=subprocess.py:99"
ALLOWED_RAW = [
    "export roots: 2",
    "root 1 folders <name>_<digits>: 2",
    "root 2 folders <digits>: 1",
    "root 1 folders other: 0",
    "root 1 allowlisted IDs found: 3",
    "root 1 allowlisted threads to ingest: 3",
    "total folders <digits>: 2",
    "allowlisted IDs configured: 3",
    "allowlisted IDs found: 3",
    "allowlisted IDs not found: 0",
    "allowlisted threads to ingest: 3",
    "target database: gcw_dev",
    "target database: gcw_real",
    "target database: gcw_ci",
    "migrations applied: 0",
    "threads allowlisted: 3",
    "folders skipped (not allowlisted): 0",
    "folders skipped (unrecognised shape): 0",
    "rows parsed: 170",
    "rows inserted: 0",
    "duplicates skipped: 4",
    "elapsed seconds: 1.23",
    "load id: 7",
    "status: succeeded",
    "Done. PASS=124 WARN=0 ERROR=0 SKIP=0 TOTAL=124",
    "Done. PASS=125 WARN=0 ERROR=0 SKIP=0 NO-OP=0 REUSED=0 TOTAL=125",
    "pipeline seconds: 12.34",
]

NOISE = [
    "Traceback (most recent call last):",
    '  File "/opt/gcw/src/gcwrapped/load.py", line 88, in load',
    "psycopg.errors.UniqueViolation: duplicate key value violates unique constraint",
    "DETAIL:  Key (sender_name, content)=(SENTINELNAME, SENTINELTEXT) already exists.",
    "ValueError: bad value SENTINELTEXT for sender SENTINELNAME",
    f"2026-11-05T08:23:49.59Z [info     ] Task SENTINELNAME is running {AIRFLOW_TAIL}",
    f"2026-11-05T08:23:49.59Z [info     ] Output: SENTINELTEXT {AIRFLOW_TAIL}",
    f"2026-11-05T08:23:49.59Z [error    ] rows inserted: 5 SENTINELNAME {AIRFLOW_TAIL}",
    "2026-11-05T08:23:49.59Z [info     ] SENTINELNAME [airflow.x] loc=a.py:1 rows inserted: 5",
    "12:00:02  1 of 9 OK created sql table model analytics.agg_member_activity [SELECT 5 in 0.1s]",
    "12:00:02  Failure in test not_null_stg_messages_sender (models/staging/x.yml) SENTINELNAME",
    "values: ('SENTINELNAME', 'SENTINELTEXT', 1730000000000)",
    "sender_name: SENTINELNAME",
    "rows inserted: SENTINELTEXT",
    "rows inserted: 5 SENTINELNAME",
    "SENTINELNAME rows inserted: 5",
    "rows inserted: 5\\nSENTINELTEXT",
    "status: SENTINELNAME",
    "target database: SENTINELNAME",
    "target database: gcw_ci_extra",
    "target database: gcw_prod",
    "root SENTINELNAME folders other: 1",
    "folders skipped (not allowlisted): SENTINELNAME",
    "[SENTINELNAME] rows inserted: 5",
    "elapsed seconds: 1.2",  # wrong shape on purpose
    "",
    "   ",
]


def test_each_allowed_line_is_shown_exactly() -> None:
    for line in ALLOWED_RAW:
        assert summarize_line(line) == line
        assert summarize_line(f"  {line}  \n") == line


def test_wrappers_are_stripped_not_printed() -> None:
    done = "Done. PASS=125 WARN=0 ERROR=0 SKIP=0 NO-OP=0 REUSED=0 TOTAL=125"
    assert summarize_line(f"\x1b[0m12:00:01  {done}") == done
    airflow = "2026-11-05T08:23:49.594030Z [info     ] {} " + AIRFLOW_TAIL
    assert summarize_line(airflow.format("rows inserted: 0       ")) == "rows inserted: 0"
    assert summarize_line(airflow.format("\x1b[0m08:24:21  " + done)) == done
    assert summarize_line(airflow.format("SENTINELNAME")) is None


def test_leak_log_prints_only_allowlisted_lines() -> None:
    log: list[str] = []
    for index, allowed in enumerate(ALLOWED_RAW):
        log += [*NOISE[index % len(NOISE) :][:3], allowed]
    log += NOISE

    shown = summarize(log)

    assert shown == ALLOWED_RAW
    assert "SENTINEL" not in "\n".join(shown)


def test_leak_log_with_only_noise_prints_nothing() -> None:
    assert summarize(NOISE) == []


# --- Runner --------------------------------------------------------------------------------------


def _python_step(name: str, body: str) -> Step:
    return Step(name, [sys.executable, "-c", body])


def _run_with(tmp_path: Path, steps: list[Step]) -> tuple[int, list[str], str]:
    shown: list[str] = []
    code = run(write_env(tmp_path), steps_for=lambda _plan: steps, show=shown.append)
    (log,) = (tmp_path / "logs").iterdir()
    return code, shown, log.read_text(encoding="utf-8")


NOISY_OK = (
    "import sys\n"
    "print('SENTINELNAME wrote SENTINELTEXT')\n"
    "print('rows inserted: 0')\n"
    "print('Traceback: SENTINELNAME', file=sys.stderr)\n"
)


def test_successful_run_logs_everything_privately_and_prints_allowlisted_lines(
    tmp_path: Path,
) -> None:
    code, shown, log_text = _run_with(
        tmp_path, [_python_step("check-landing", NOISY_OK), _python_step("e2e", NOISY_OK)]
    )

    assert code == 0
    assert shown[:4] == [
        "== step: check-landing ==",
        "rows inserted: 0",
        "== step: e2e ==",
        "rows inserted: 0",
    ]
    assert re.fullmatch(r"pipeline seconds: \d+\.\d{2}", shown[4])
    assert shown[-1] == "real-ingest: ok"
    assert len(shown) == 6
    assert "SENTINEL" not in "\n".join(shown)
    # The full output is in the private log, stderr included.
    assert log_text.count("SENTINELNAME wrote SENTINELTEXT") == 2
    assert "Traceback: SENTINELNAME" in log_text


def test_log_file_is_private_and_named_with_a_utc_timestamp(tmp_path: Path) -> None:
    _run_with(tmp_path, [_python_step("e2e", NOISY_OK)])

    (log,) = (tmp_path / "logs").iterdir()
    assert re.fullmatch(r"real-ingest-\d{8}T\d{6}Z\.log", log.name)
    assert stat.S_IMODE(log.stat().st_mode) == 0o600


def test_failure_stops_at_the_failing_step_and_keeps_its_exit_status(tmp_path: Path) -> None:
    marker = tmp_path / "third-ran"
    ran_after = f"open({str(marker)!r}, 'w').write('x')"
    code, shown, log_text = _run_with(
        tmp_path,
        [
            _python_step("check-landing", "print('allowlisted IDs found: 1')"),
            _python_step("e2e", "import sys; print('boom SENTINELTEXT'); sys.exit(3)"),
            _python_step("never", ran_after),
        ],
    )

    assert code == 3
    assert shown[:3] == [
        "== step: check-landing ==",
        "allowlisted IDs found: 1",
        "== step: e2e ==",
    ]
    assert shown[-1] == "real-ingest: failed at e2e; full log in GCW_LOG_DIR"
    assert "real-ingest: ok" not in shown
    assert "SENTINEL" not in "\n".join(shown)
    assert "boom SENTINELTEXT" in log_text
    assert not marker.exists()


def test_failure_at_the_first_step_never_starts_the_second(tmp_path: Path) -> None:
    marker = tmp_path / "second-ran"
    code, shown, _ = _run_with(
        tmp_path,
        [
            _python_step("check-landing", "import sys; sys.exit(1)"),
            _python_step("e2e", f"open({str(marker)!r}, 'w').write('x')"),
        ],
    )

    assert code == 1
    assert shown[-1] == "real-ingest: failed at check-landing; full log in GCW_LOG_DIR"
    assert not marker.exists()


def test_every_step_gets_the_env_file_in_its_environment(tmp_path: Path) -> None:
    env_file = write_env(tmp_path)
    body = "import os; print('rows parsed: ' + str(len(os.environ['ENV_FILE'])))"
    shown: list[str] = []

    code = run(env_file, steps_for=lambda _p: [_python_step("e2e", body)], show=shown.append)

    assert code == 0
    assert f"rows parsed: {len(env_file)}" in shown


def test_the_runner_overrides_an_inherited_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ENV_FILE", ".env")
    env_file = write_env(tmp_path)
    body = "import os; print('rows parsed: ' + str(int(os.environ['ENV_FILE'] != '.env')))"
    shown: list[str] = []

    run(env_file, steps_for=lambda _p: [_python_step("e2e", body)], show=shown.append)

    assert "rows parsed: 1" in shown


def test_default_steps_have_the_documented_headers(tmp_path: Path) -> None:
    plan = check_guards(write_env(tmp_path))

    headers = [real_ingest.step_header(step) for step in real_ingest.build_steps(plan)]

    assert headers == ["== step: check-landing ==", "== step: e2e =="]
