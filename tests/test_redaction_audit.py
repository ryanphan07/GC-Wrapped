"""`gcw redaction-audit` output rules, with the scan itself replaced (no database, no model).

SENTINELNAME and SENTINELTEXT stand for real names and message bodies: neither may reach the
output, on success or on any failure.
"""

import subprocess
import sys

import pytest

from gcwrapped import redaction_audit
from gcwrapped.redaction import PLACEHOLDER_TYPES
from gcwrapped.redaction_audit import AuditReport, Steps


def _report(residual: int = 0, database: str = "gcw_dev") -> AuditReport:
    return AuditReport(
        database=database,
        scanned=10,
        with_text=7,
        changed=5,
        placeholders={t: i for i, t in enumerate(PLACEHOLDER_TYPES)},
        residual=residual,
        seconds=1.234,
    )


def _steps(report: AuditReport | Exception, docker: Exception | None = None) -> Steps:
    def start() -> None:
        if docker is not None:
            raise docker

    def audit(set_step: object) -> AuditReport:
        if isinstance(report, Exception):
            raise report
        return report

    return Steps(docker=start, audit=audit)


def test_success_prints_fixed_lines_in_order(capsys: pytest.CaptureFixture[str]) -> None:
    assert redaction_audit.run(_steps(_report())) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.splitlines() == [
        "target database: gcw_dev",
        "messages scanned: 10",
        "messages with text: 7",
        "messages changed: 5",
        "placeholders person: 0",
        "placeholders phone: 1",
        "placeholders email: 2",
        "placeholders url: 3",
        "placeholders handle: 4",
        "placeholders address: 5",
        "placeholders card: 6",
        "placeholders ssn: 7",
        "placeholders ip: 8",
        "placeholders iban: 9",
        "member-name tokens remaining: 0",
        "redaction seconds: 1.23",
        "redaction-audit: ok",
    ]


def test_residual_tokens_print_counts_then_fail(capsys: pytest.CaptureFixture[str]) -> None:
    assert redaction_audit.run(_steps(_report(residual=3))) == 1
    captured = capsys.readouterr()
    assert captured.err == ""
    lines = captured.out.splitlines()
    assert "member-name tokens remaining: 3" in lines
    assert "redaction-audit: ok" not in lines
    assert lines[-1] == "redaction-audit: failed (residual member tokens)"
    assert len(lines) == len(redaction_audit.LINE_SHAPES) + 1


@pytest.mark.parametrize("failure", [ValueError, RuntimeError, KeyError])
def test_failure_prints_step_and_type_name_only(
    failure: type[Exception], capsys: pytest.CaptureFixture[str]
) -> None:
    """An audit failure that names no step of its own is attributed to database, never docker."""
    assert redaction_audit.run(_steps(failure("SENTINELNAME SENTINELTEXT"))) == 1
    captured = capsys.readouterr()
    assert captured.out == f"redaction-audit: failed at database ({failure.__name__})\n"
    assert "sentinel" not in (captured.out + captured.err).lower()


def test_failure_names_the_step_that_set_it(capsys: pytest.CaptureFixture[str]) -> None:
    def audit(set_step: object) -> AuditReport:
        assert callable(set_step)
        set_step(redaction_audit.STEP_SCAN)
        raise RuntimeError("SENTINELTEXT")

    assert redaction_audit.run(Steps(docker=lambda: None, audit=audit)) == 1
    captured = capsys.readouterr()
    assert captured.out == "redaction-audit: failed at scan (RuntimeError)\n"
    assert "sentinel" not in (captured.out + captured.err).lower()


def test_docker_failure_is_its_own_step(capsys: pytest.CaptureFixture[str]) -> None:
    assert redaction_audit.run(_steps(_report(), docker=OSError("SENTINELNAME"))) == 1
    captured = capsys.readouterr()
    assert captured.out == "redaction-audit: failed at docker (OSError)\n"
    assert "sentinel" not in (captured.out + captured.err).lower()


def test_a_line_that_does_not_match_its_shape_prints_nothing_else(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert redaction_audit.run(_steps(_report(database="SENTINELNAME"))) == 1
    captured = capsys.readouterr()
    assert captured.out == "redaction-audit: failed at render (ShapeError)\n"
    assert "sentinel" not in (captured.out + captured.err).lower()


def test_wrong_line_count_is_a_shape_error() -> None:
    with pytest.raises(redaction_audit.ShapeError):
        redaction_audit.check_shapes(["target database: gcw_dev"])


def test_unusual_exception_type_name_cannot_break_the_failure_shape() -> None:
    odd = type("Odd\nName SENTINELNAME", (Exception,), {})
    line = redaction_audit.failure_line("scan", odd())
    assert line == "redaction-audit: failed at scan (Exception)"


def test_importing_the_cli_does_not_load_the_redaction_dependencies() -> None:
    """The Airflow image's venv has no Presidio or spaCy; only the audit command may need them."""
    code = (
        "import sys, gcwrapped.cli; "
        "mods = ('presidio_analyzer', 'spacy', 'gcwrapped.redaction'); "
        "bad = [m for m in mods if m in sys.modules]; "
        "sys.exit(1 if bad else 0)"
    )
    assert subprocess.run([sys.executable, "-c", code], timeout=60).returncode == 0


def test_setup_logging_silences_presidio_loggers() -> None:
    import logging

    from gcwrapped.log import QUIET_LOGGERS, setup_logging

    setup_logging("INFO")
    for name in QUIET_LOGGERS:
        assert not logging.getLogger(name).isEnabledFor(logging.INFO)
