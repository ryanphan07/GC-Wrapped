"""`gcw redaction-audit` output rules, with the scan itself replaced (no database, no model).

SENTINELNAME and SENTINELTEXT stand for real names and message bodies: neither may reach the
output, on success or on any failure.
"""

import subprocess
import sys
from collections import Counter

import pytest

from gcwrapped import redaction_audit
from gcwrapped.redaction import FOLDED_ONLY_CATEGORIES, PLACEHOLDER_TYPES, MemberDirectory
from gcwrapped.redaction_audit import AuditReport, Steps, folded_only_matches, stop_word_matches


def _report(residual: int = 0, database: str = "gcw_dev") -> AuditReport:
    return AuditReport(
        database=database,
        scanned=10,
        with_text=7,
        changed=5,
        placeholders={t: i for i, t in enumerate(PLACEHOLDER_TYPES)},
        residual=residual,
        seconds=1.234,
        overlap_merges=11,
        boundary_extensions=12,
        backstop_adjacent=2,
        backstop_standalone=3,
        directory_tokens=100,
        stop_word_tokens=4,
        stop_word_matches=55,
        raw_only_tokens=60,
        folded_only_tokens=10,
        folded_only_by_category={
            "one_symbol": 1,
            "several_symbols": 2,
            "mixed": 3,
            "folded_break": 4,
        },
        folded_only_matches={"one_symbol": 5, "several_symbols": 6, "mixed": 7, "folded_break": 8},
        messages_with_folded_only_match=9,
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
        "overlap merges: 11",
        "boundary extensions: 12",
        "backstop replacements: 5",
        "backstop next to a placeholder: 2",
        "backstop standalone: 3",
        "directory tokens: 100",
        "directory tokens that are stop words: 4",
        "member matches on stop-word tokens: 55",
        "directory tokens raw-only: 60",
        "directory tokens folded-only: 10",
        "folded-only tokens from one symbol character: 1",
        "folded-only tokens from several symbol characters: 2",
        "folded-only tokens mixing symbol and regular characters: 3",
        "folded-only tokens split by a folded break: 4",
        "matches on folded-only tokens from one symbol character: 5",
        "matches on folded-only tokens from several symbol characters: 6",
        "matches on folded-only tokens mixing symbol and regular characters: 7",
        "matches on folded-only tokens split by a folded break: 8",
        "messages with a match on a folded-only token: 9",
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


def test_backstop_replacements_are_reported_not_failed(capsys: pytest.CaptureFixture[str]) -> None:
    """On real data the backstop count is a signal to read; only residual tokens fail the audit."""
    assert redaction_audit.run(_steps(_report())) == 0
    assert "backstop replacements: 5" in capsys.readouterr().out.splitlines()


def test_new_lines_have_fixed_shapes() -> None:
    lines = redaction_audit.render(_report())
    redaction_audit.check_shapes(lines)
    assert lines[-19:-11] == [
        "overlap merges: 11",
        "boundary extensions: 12",
        "backstop replacements: 5",
        "backstop next to a placeholder: 2",
        "backstop standalone: 3",
        "directory tokens: 100",
        "directory tokens that are stop words: 4",
        "member matches on stop-word tokens: 55",
    ]
    lines[-13] = "directory tokens: SENTINELNAME"
    with pytest.raises(redaction_audit.ShapeError):
        redaction_audit.check_shapes(lines)


def test_stop_word_matches_counts_single_stop_word_tokens_only() -> None:
    directory = MemberDirectory([("t", "Will Smith"), ("t", "May Chen"), ("t", "Ana Ruiz")])
    assert directory.tokens >= {"will", "may", "smith", "chen", "ana", "ruiz"}
    # "will" and "May" match as single stop-word tokens; "smith" and "Ruiz" are not stop words,
    # and the full name "Will Smith" is one multi-word match.
    assert stop_word_matches("I will see May, and smith or Ruiz", directory) == 2
    assert stop_word_matches("Will Smith is here", directory) == 0


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


def _enclosed(text: str) -> str:
    return "".join(chr(0x24D0 + ord(c) - 97) for c in text)


def test_d83_every_folded_only_token_falls_in_exactly_one_category() -> None:
    directory = MemberDirectory(
        [
            ("t", _enclosed("sentinelnamea")),  # several symbol characters
            (
                "t",
                "SENTINELNAMEB\u2122",
            ),  # raw word "sentinelnameb(tm)": folded "sentinelnameb" ... see below
            ("t", "\u24d9sentinelnamec"),  # one symbol character glued to regular ones: mixed
            ("t", "SENTINELNAMED\u2475"),  # split by a folded break: no symbol characters
            ("t", "SENTINELNAMEE"),  # raw and folded: neither
        ]
    )
    folded_only = directory.folded_only_tokens
    assert folded_only["sentinelnamea"] == "several_symbols"
    assert folded_only["sentinelnamebtm"] == "mixed"
    assert folded_only["jsentinelnamec"] == "mixed"
    assert folded_only["sentinelnamed"] == "folded_break"
    assert "sentinelnamee" not in folded_only
    assert "sentinelnamee" not in directory.raw_only_tokens
    assert set(folded_only.values()) <= set(FOLDED_ONLY_CATEGORIES)
    both = directory.tokens - directory.raw_only_tokens - set(folded_only)
    assert "sentinelnamee" in both
    assert len(directory.tokens) == len(directory.raw_only_tokens) + len(folded_only) + len(both)


def test_d83_one_symbol_character_category() -> None:
    directory = MemberDirectory([("t", "\u338f"), ("t", "\u2122\u2122")])
    # "kg" from one symbol character; "tmtm" from two symbols, one word
    assert directory.folded_only_tokens == {"kg": "one_symbol", "tmtm": "several_symbols"}


def test_d83_folded_only_matches_are_counted_per_category() -> None:
    directory = MemberDirectory([("t", _enclosed("sentinelnamea")), ("t", "\u338f")])
    folded_only = directory.folded_only_tokens
    hits = folded_only_matches("hi sentinelnamea and kg, SENTINELNAMEA", directory, folded_only)
    assert hits == {"several_symbols": 2, "one_symbol": 1}
    assert not folded_only_matches("nothing here", directory, folded_only)


def test_d83_audit_lines_carry_no_name_or_text() -> None:
    sentinel = "SENTINELNAMEZ"
    directory = MemberDirectory([("t", _enclosed(sentinel.lower()))])
    hits = folded_only_matches(f"SENTINELTEXT {sentinel}", directory, directory.folded_only_tokens)
    report = AuditReport(
        "gcw_dev",
        1,
        1,
        1,
        dict.fromkeys(PLACEHOLDER_TYPES, 0),
        0,
        0.0,
        folded_only_tokens=len(directory.folded_only_tokens),
        folded_only_by_category=dict(Counter(directory.folded_only_tokens.values())),
        folded_only_matches=dict(hits),
        messages_with_folded_only_match=1,
    )
    lines = redaction_audit.render(report)
    redaction_audit.check_shapes(lines)
    assert len(lines) == 35
    assert not any(sentinel.lower() in ln.lower() or "SENTINELTEXT" in ln for ln in lines)
