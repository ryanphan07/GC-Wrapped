"""make text-audit: fixed lines, shape checks, one failure line, nothing else printed (D95)."""

import pytest

from gcwrapped import text_audit as ta
from gcwrapped.metrics import UnexpectedDatabaseError

SENTINEL = "SENTINELTEXT"
REPORT = ta.TextReport(
    database="gcw_dev",
    messages=100,
    non_blank=90,
    placeholders=30,
    by_pattern={"liked_message": 20, "group_member_left": 10, "poll_voted": 0},
    user_text=60,
    short=10,
    medium=20,
    long=30,
    emoji_only=4,
    url_only=2,
)


def _noop() -> None:
    return None


def test_the_report_renders_the_fixed_lines_in_order() -> None:
    assert ta.render(REPORT) == [
        "target database: gcw_dev",
        "messages: 100",
        "non-blank content: 90",
        "placeholders total: 30",
        "placeholder liked_message: 20",
        "placeholder group_member_left: 10",
        "placeholder poll_voted: 0",
        "user text messages: 60",
        "user text 1-3 characters: 10",
        "user text 4-10 characters: 20",
        "user text 11+ characters: 30",
        "emoji-only user text: 4",
        "URL-only user text: 2",
    ]
    ta.check_shapes(REPORT, ta.render(REPORT))  # no exception


def test_run_prints_the_lines_and_the_ok_line(capsys: pytest.CaptureFixture[str]) -> None:
    assert ta.run(_noop, lambda set_step: REPORT) == 0
    out = capsys.readouterr().out.splitlines()
    assert out == [*ta.render(REPORT), "text-audit: ok"]


def test_a_line_that_does_not_fit_its_shape_prints_nothing_but_the_failure(
    capsys: pytest.CaptureFixture[str],
) -> None:
    bad = ta.TextReport("gcw_dev", 1, 1, 0, {f"{SENTINEL} Ana": 1}, 1, 1, 0, 0, 0, 0)
    assert ta.run(_noop, lambda set_step: bad) == 1
    out = capsys.readouterr().out
    assert out == "text-audit: failed at render (ShapeError)\n"
    assert SENTINEL not in out and "Ana" not in out


def test_a_database_error_prints_the_step_and_the_type_name_only(
    capsys: pytest.CaptureFixture[str],
) -> None:
    def boom(set_step: object) -> ta.TextReport:
        raise RuntimeError(f"{SENTINEL} Ana said hi")

    assert ta.run(_noop, boom) == 1
    assert capsys.readouterr().out == "text-audit: failed at database (RuntimeError)\n"


def test_a_docker_failure_is_reported_at_docker(capsys: pytest.CaptureFixture[str]) -> None:
    def down() -> None:
        raise OSError(SENTINEL)

    assert ta.run(down, lambda set_step: REPORT) == 1
    assert capsys.readouterr().out == "text-audit: failed at docker (OSError)\n"


def test_a_database_outside_the_allowed_list_is_refused_before_any_query(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ta, "database_url", lambda: "postgresql://u:p@localhost:5432/prod")
    with pytest.raises(UnexpectedDatabaseError):
        ta.audit_database("postgresql://u:p@localhost:5432/prod", lambda step: None)
    assert ta.run(_noop) == 1
    assert capsys.readouterr().out == "text-audit: failed at database (UnexpectedDatabaseError)\n"
