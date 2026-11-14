"""Per-sender consent ledger (D69): validation, merge, atomic write, path rules, output shapes.

Sentinels (SENTINELNAME...) stand in for sender names and paths wherever nothing may be printed.
The database and docker are the external boundaries: `warehouse_members`, `migrate`,
`replace_consent` and the db start are replaced here; tests/test_ledger_integration.py runs them
for real against gcw_test.
"""

import csv
import os
import re
import stat
import subprocess
import sys
from collections.abc import Callable, Iterator
from io import StringIO
from pathlib import Path

import pytest

from gcwrapped import ledger
from gcwrapped.cli import main
from gcwrapped.config import get_database_settings, get_settings
from gcwrapped.ledger import HEADER, LedgerRow, check_ledger, merge, write_ledger

REPO = Path(__file__).resolve().parents[1]
HEADER_LINE = ",".join(HEADER)
ALLOWLIST = frozenset({"111", "222"})
MEMBERS = {
    ("111", "SENTINELNAME a"): 5,
    ("111", "SENTINELNAME b"): 9,
    ("222", "SENTINELNAME c"): 2,
}
Run = Callable[[Callable[[], None]], int]


@pytest.fixture(autouse=True)
def env(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[None]:
    """A cwd without a .env, dev database settings, the allowlist 111,222 and no ledger path."""
    for var in ("DATABASE_URL", "ENV_FILE", "GCW_CONSENT_LEDGER", "POSTGRES_HOST_PORT"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path_factory.mktemp("cwd"))
    monkeypatch.setenv("GCW_DB_NAME", "gcw_dev")
    monkeypatch.setenv("POSTGRES_USER", "u")
    monkeypatch.setenv("POSTGRES_PASSWORD", "p")
    monkeypatch.setenv("GCW_THREAD_ALLOWLIST", "111,222")
    get_settings.cache_clear()
    get_database_settings.cache_clear()
    yield
    get_database_settings.cache_clear()


def _write(path: Path, *lines: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _csv_line(*fields: str) -> str:
    buffer = StringIO()
    csv.writer(buffer, lineterminator="").writerow(fields)
    return buffer.getvalue()


def _problems(
    path: Path,
    allowlist: frozenset[str] | None = None,
    members: dict[tuple[str, str], int] | None = None,
) -> list[str]:
    return [p.render() for p in check_ledger(path, allowlist=allowlist, members=members).problems]


# --- Validation: every error kind ------------------------------------------------------------


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"consent,thread_id,sender_name\n",  # a column missing
        b"thread_id,consent,sender_name,messages\n",  # reordered
        b"Consent,thread_id,sender_name,messages\n",  # wrong case
        b"\xef\xbb\xbfconsent,thread_id,sender_name,messages\n",  # byte-order mark
        b"consent,thread_id,sender_name,messages,extra\n",
        b"\xff\xfe not utf-8 \x00\n",
    ],
)
def test_bad_header_is_one_error_at_line_one(tmp_path: Path, content: bytes) -> None:
    path = tmp_path / "l.csv"
    path.write_bytes(content)

    assert _problems(path) == ["ledger error: bad_header at line 1"]


@pytest.mark.parametrize(
    "row",
    [
        _csv_line("Yes", "111", "a", "1"),
        _csv_line("YES", "111", "a", "1"),
        _csv_line(" yes", "111", "a", "1"),
        _csv_line("maybe", "111", "a", "1"),
        _csv_line("y", "111", "a", "1"),
        _csv_line("yes", "", "a", "1"),  # no thread
        _csv_line("yes", "111", "", "1"),  # no sender
        "yes,111,a",  # too few fields
        "yes,111,a,1,extra",  # too many fields
    ],
)
def test_bad_value(tmp_path: Path, row: str) -> None:
    path = _write(tmp_path / "l.csv", HEADER_LINE, row)

    assert _problems(path) == ["ledger error: bad_value at line 2"]


def test_duplicate_reports_the_second_occurrence(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "l.csv",
        HEADER_LINE,
        "yes,111,a,3",
        "no,111,b,2",
        "no,111,a,1",  # same (thread, sender) as line 2, different consent
        "no,222,a,1",  # same name, other thread: fine
    )

    assert _problems(path) == ["ledger error: duplicate at line 4"]


def test_unknown_thread_needs_the_allowlist(tmp_path: Path) -> None:
    path = _write(tmp_path / "l.csv", HEADER_LINE, "yes,333,a,1", "yes,111,a,1")

    assert _problems(path, allowlist=ALLOWLIST) == ["ledger error: unknown_thread at line 2"]
    assert _problems(path) == []  # not checked without an allowlist (consent-template)


def test_unknown_sender_needs_the_warehouse(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "l.csv", HEADER_LINE, "yes,111,SENTINELNAME a,1", "yes,111,SENTINELNAME z,1"
    )

    assert _problems(path, ALLOWLIST, MEMBERS) == ["ledger error: unknown_sender at line 3"]
    assert _problems(path) == []


def test_a_sender_known_only_in_another_thread_is_unknown(tmp_path: Path) -> None:
    path = _write(tmp_path / "l.csv", HEADER_LINE, "yes,222,SENTINELNAME a,1")

    assert _problems(path, ALLOWLIST, MEMBERS) == ["ledger error: unknown_sender at line 2"]


def test_a_row_gets_one_error_in_priority_order(tmp_path: Path) -> None:
    path = _write(tmp_path / "l.csv", HEADER_LINE, "maybe,333,SENTINELNAME q,1")

    assert _problems(path, ALLOWLIST, MEMBERS) == ["ledger error: bad_value at line 2"]


def test_every_problem_is_reported_in_file_order_and_blank_lines_are_skipped(
    tmp_path: Path,
) -> None:
    path = _write(
        tmp_path / "l.csv",
        HEADER_LINE,
        "",
        "bad,111,SENTINELNAME a,1",
        "yes,111,SENTINELNAME a,1",
        "yes,333,SENTINELNAME a,1",
        "yes,111,SENTINELNAME zz,1",
        "yes,111,SENTINELNAME a,1",
    )

    assert _problems(path, ALLOWLIST, MEMBERS) == [
        "ledger error: bad_value at line 3",
        "ledger error: unknown_thread at line 5",
        "ledger error: unknown_sender at line 6",
        "ledger error: duplicate at line 7",
    ]


def test_names_with_commas_quotes_emoji_and_newlines_round_trip(tmp_path: Path) -> None:
    names = ["SENTINELNAME Doe, Jr. 🎉", 'SENTINELNAME "Q" Ríos', "SENTINELNAME two\nlines"]
    path = tmp_path / "l.csv"
    write_ledger(path, [LedgerRow("yes", "111", name, "1") for name in names])

    check = check_ledger(path)

    assert check.problems == []
    assert [row.sender_name for row in check.rows] == names


def test_consent_values_are_exactly_yes_local_no_blank(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "l.csv",
        HEADER_LINE,
        "yes,111,a,1",
        "no,111,b,1",
        ",111,c,1",
        "yes,111,d,",
        "local,111,e,1",
    )

    check = check_ledger(path)

    assert check.problems == []
    assert [r.consent for r in check.rows] == ["yes", "no", "", "yes", "local"]


def test_count_consent_counts_each_value_separately() -> None:
    rows = [
        LedgerRow(c, "111", f"n{i}", "1") for i, c in enumerate(["yes", "local", "local", "no", ""])
    ]

    counts = ledger.count_consent(rows)

    assert (counts.rows, counts.yes, counts.local, counts.no, counts.blank) == (5, 1, 2, 1, 1)


@pytest.mark.parametrize("value", ["Local", "LOCAL", " local", "local ", "maybe", "y", "true"])
def test_near_misses_of_local_are_bad_values(tmp_path: Path, value: str) -> None:
    path = _write(tmp_path / "l.csv", HEADER_LINE, f"{value},111,a,1")

    assert _problems(path) == ["ledger error: bad_value at line 2"]


# --- Merge -----------------------------------------------------------------------------------


def test_merge_keeps_decisions_adds_blank_rows_and_counts_stale() -> None:
    existing = [
        LedgerRow("yes", "111", "SENTINELNAME a", "1"),
        LedgerRow("no", "111", "SENTINELNAME gone", "7"),  # no longer in the warehouse
        LedgerRow("", "222", "SENTINELNAME c", "1"),  # undecided, still there
    ]

    result = merge(existing, MEMBERS)

    by_key = {row.key: row for row in result.rows}
    assert by_key[("111", "SENTINELNAME a")].consent == "yes"
    assert by_key[("111", "SENTINELNAME gone")].consent == "no"
    assert by_key[("222", "SENTINELNAME c")].consent == ""
    assert by_key[("111", "SENTINELNAME b")].consent == ""  # new: blank
    assert (result.new, result.decisions_kept, result.stale) == (1, 2, 1)
    assert len(result.rows) == 4  # nothing deleted


def test_merge_refreshes_counts_but_keeps_a_stale_rows_text_as_written() -> None:
    existing = [
        LedgerRow("yes", "111", "SENTINELNAME a", "1"),
        LedgerRow("no", "111", "SENTINELNAME gone", "not a number"),
    ]

    rows = {r.key: r for r in merge(existing, MEMBERS).rows}

    assert rows[("111", "SENTINELNAME a")].messages == "5"
    assert rows[("111", "SENTINELNAME gone")].messages == "not a number"


def test_merge_sorts_by_thread_then_messages_descending() -> None:
    members = {("222", "b"): 1, ("111", "x"): 3, ("111", "y"): 10, ("111", "z"): 3}

    rows = merge([], members).rows

    assert [(r.thread_id, r.sender_name) for r in rows] == [
        ("111", "y"),
        ("111", "x"),
        ("111", "z"),
        ("222", "b"),
    ]


def test_merge_is_idempotent() -> None:
    first = merge([], MEMBERS)
    second = merge(first.rows, MEMBERS)

    assert second.rows == first.rows
    assert (second.new, second.stale) == (0, 0)


# --- Atomic write, mode 0600 -----------------------------------------------------------------


def test_write_is_mode_0600_and_replaces_a_loose_file(tmp_path: Path) -> None:
    path = _write(tmp_path / "l.csv", "old")
    path.chmod(0o644)

    write_ledger(path, [LedgerRow("yes", "111", "a", "1")])

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert path.read_text(encoding="utf-8") == f"{HEADER_LINE}\nyes,111,a,1\n"


def test_write_creates_the_directory_privately_and_leaves_no_temp_file(tmp_path: Path) -> None:
    path = tmp_path / "new" / "dir" / "l.csv"

    write_ledger(path, [LedgerRow("", "111", "a", "1")])

    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert [p.name for p in path.parent.iterdir()] == ["l.csv"]


def test_a_failed_write_keeps_the_old_ledger_and_leaves_no_temp_file(tmp_path: Path) -> None:
    path = _write(tmp_path / "l.csv", HEADER_LINE, "yes,111,a,1")
    before = path.read_bytes()

    def rows() -> Iterator[LedgerRow]:
        yield LedgerRow("no", "111", "b", "1")
        raise RuntimeError

    with pytest.raises(RuntimeError):
        write_ledger(path, rows())

    assert path.read_bytes() == before
    assert [p.name for p in tmp_path.iterdir()] == ["l.csv"]


# --- Path rules ------------------------------------------------------------------------------


@pytest.mark.parametrize("raw", ["", "   "])
def test_an_empty_path_is_refused(raw: str) -> None:
    with pytest.raises(ledger.LedgerPathError):
        ledger.resolve_ledger_path(raw, "gcw_dev")


def test_real_database_refuses_a_path_inside_the_repo(tmp_path: Path) -> None:
    for inside in (REPO / "senders.csv", REPO / "tests" / "fixtures" / "senders.csv"):
        with pytest.raises(ledger.LedgerPathError):
            ledger.resolve_ledger_path(str(inside), "gcw_real")
    assert ledger.resolve_ledger_path(str(tmp_path / "s.csv"), "gcw_real") == tmp_path / "s.csv"


def test_a_relative_path_resolves_against_the_working_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(REPO)

    with pytest.raises(ledger.LedgerPathError):
        ledger.resolve_ledger_path("./senders.csv", "gcw_real")


def test_a_symlink_into_the_repo_counts_as_inside(tmp_path: Path) -> None:
    link = tmp_path / "link"
    link.symlink_to(REPO / "tests")

    with pytest.raises(ledger.LedgerPathError):
        ledger.resolve_ledger_path(str(link / "s.csv"), "gcw_real")


def test_dev_databases_may_keep_the_ledger_inside_the_repo() -> None:
    inside = REPO / "tests" / "fixtures" / "synthetic_consent" / "s.csv"
    for database in ("gcw_dev", "gcw_test", "gcw_ci"):
        assert ledger.resolve_ledger_path(str(inside), database) == inside


# --- Commands: output shapes, failure paths, sentinel leaks ----------------------------------


class Calls:
    """What the replaced boundaries were asked to do."""

    def __init__(self) -> None:
        self.started = 0
        self.migrated = 0
        self.loaded: list[list[LedgerRow]] = []

    def start(self) -> None:
        self.started += 1


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> Calls:
    """Replace the database boundary: the warehouse read, migrate and the load."""
    recorded = Calls()

    def fake_migrate(url: str) -> list[str]:
        recorded.migrated += 1
        return []

    monkeypatch.setattr(ledger, "warehouse_members", lambda url: dict(MEMBERS))
    monkeypatch.setattr(ledger, "migrate", fake_migrate)
    monkeypatch.setattr(ledger, "replace_consent", lambda url, rows: recorded.loaded.append(rows))
    return recorded


def _use_ledger(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    monkeypatch.setenv("GCW_CONSENT_LEDGER", str(path))


def _runner(command: str) -> Run:
    return ledger.run_template if command == "template" else ledger.run_load


def _no_sentinel(out: str) -> bool:
    return "sentinel" not in out.lower()


def test_template_creates_a_ledger_and_prints_only_allowed_lines(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    calls: Calls,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _use_ledger(monkeypatch, tmp_path / "SENTINELDIR" / "senders.csv")

    assert ledger.run_template(calls.start) == 0

    out = capsys.readouterr().out
    assert out.splitlines() == [
        "target database: gcw_dev",
        "ledger rows: 3",
        "new rows added: 3",
        "rows with a decision kept: 0",
        "stale rows: 0",
        "consent-template: ok",
    ]
    for line, shape in zip(out.splitlines(), ledger.TEMPLATE_SHAPES, strict=True):
        assert re.fullmatch(shape, line)
    assert _no_sentinel(out)
    assert calls.started == 1


def test_template_twice_adds_nothing_and_keeps_every_decision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    calls: Calls,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "senders.csv"
    _use_ledger(monkeypatch, path)
    assert ledger.run_template(calls.start) == 0
    rows = check_ledger(path).rows
    decided = [
        LedgerRow("yes", r.thread_id, r.sender_name, r.messages) if i == 0 else r
        for i, r in enumerate(rows)
    ]
    write_ledger(path, decided)
    capsys.readouterr()

    assert ledger.run_template(calls.start) == 0

    lines = capsys.readouterr().out.splitlines()
    assert "new rows added: 0" in lines
    assert "rows with a decision kept: 1" in lines
    assert check_ledger(path).rows == decided


def test_template_counts_stale_rows_and_keeps_them(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    calls: Calls,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = _write(tmp_path / "l.csv", HEADER_LINE, "no,111,SENTINELNAME gone,4")
    _use_ledger(monkeypatch, path)

    assert ledger.run_template(calls.start) == 0

    lines = capsys.readouterr().out.splitlines()
    assert "stale rows: 1" in lines
    assert "rows with a decision kept: 1" in lines
    assert "new rows added: 3" in lines
    assert any(r.sender_name == "SENTINELNAME gone" for r in check_ledger(path).rows)


def test_template_refuses_to_touch_a_malformed_ledger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    calls: Calls,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = _write(tmp_path / "l.csv", HEADER_LINE, "Yes,111,SENTINELNAME a,1")
    before = path.read_bytes()
    _use_ledger(monkeypatch, path)

    assert ledger.run_template(calls.start) == 1

    out = capsys.readouterr().out
    assert out.splitlines() == [
        "ledger error: bad_value at line 2",
        "ledger errors: 1",
        "consent-template: failed",
    ]
    assert path.read_bytes() == before
    assert _no_sentinel(out)


def test_load_prints_counts_and_loads_the_checked_rows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    calls: Calls,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = _write(
        tmp_path / "SENTINELDIR" / "l.csv",
        HEADER_LINE,
        "yes,111,SENTINELNAME a,5",
        "no,111,SENTINELNAME b,9",
        "local,222,SENTINELNAME c,2",
    )
    _use_ledger(monkeypatch, path)

    assert ledger.run_load(calls.start) == 0

    out = capsys.readouterr().out
    assert out.splitlines() == [
        "target database: gcw_dev",
        "ledger rows: 3",
        "consented (yes): 1",
        "consented (local): 1",
        "declined (no): 1",
        "undecided (blank, treated as no): 0",
        "consent-load: ok",
    ]
    for line, shape in zip(out.splitlines(), ledger.LOAD_SHAPES, strict=True):
        assert re.fullmatch(shape, line)
    assert _no_sentinel(out)
    assert [len(rows) for rows in calls.loaded] == [3]


@pytest.mark.parametrize(
    ("rows", "expected"),
    [
        (["Yes,111,SENTINELNAME a,1"], "bad_value at line 2"),
        (["yes,111,SENTINELNAME a,1", "no,111,SENTINELNAME a,1"], "duplicate at line 3"),
        (["yes,999,SENTINELNAME a,1"], "unknown_thread at line 2"),
        (["yes,111,SENTINELNAME nobody,1"], "unknown_sender at line 2"),
    ],
)
def test_load_with_a_bad_ledger_loads_nothing_and_leaks_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    calls: Calls,
    capsys: pytest.CaptureFixture[str],
    rows: list[str],
    expected: str,
) -> None:
    _use_ledger(monkeypatch, _write(tmp_path / "SENTINELDIR" / "l.csv", HEADER_LINE, *rows))

    assert ledger.run_load(calls.start) == 1

    out = capsys.readouterr().out
    assert out.splitlines() == [
        f"ledger error: {expected}",
        "ledger errors: 1",
        "consent-load: failed",
    ]
    assert calls.loaded == [] and calls.migrated == 0
    assert _no_sentinel(out)


def test_a_bad_header_loads_nothing_and_leaks_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    calls: Calls,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _use_ledger(monkeypatch, _write(tmp_path / "SENTINELDIR" / "l.csv", "SENTINELTEXT,x"))

    assert ledger.run_load(calls.start) == 1

    out = capsys.readouterr().out
    assert out.splitlines() == [
        "ledger error: bad_header at line 1",
        "ledger errors: 1",
        "consent-load: failed",
    ]
    assert calls.loaded == [] and _no_sentinel(out)


def test_a_missing_ledger_file_fails_by_type_name_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    calls: Calls,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _use_ledger(monkeypatch, tmp_path / "SENTINELDIR" / "nope.csv")

    assert ledger.run_load(calls.start) == 1

    out = capsys.readouterr().out
    assert out == "consent-load: failed at ledger (FileNotFoundError)\n"
    assert calls.loaded == [] and _no_sentinel(out)


def test_one_good_row_among_bad_ones_still_loads_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, calls: Calls
) -> None:
    rows = ["yes,111,SENTINELNAME a,5", "maybe,111,SENTINELNAME b,9"]
    _use_ledger(monkeypatch, _write(tmp_path / "l.csv", HEADER_LINE, *rows))

    assert ledger.run_load(calls.start) == 1
    assert calls.loaded == []


def test_at_most_twenty_error_lines_then_the_total(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    calls: Calls,
    capsys: pytest.CaptureFixture[str],
) -> None:
    bad = [f"maybe,111,SENTINELNAME n{i},1" for i in range(25)]
    _use_ledger(monkeypatch, _write(tmp_path / "l.csv", HEADER_LINE, *bad))

    assert ledger.run_load(calls.start) == 1

    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 22
    assert lines[0] == "ledger error: bad_value at line 2"
    assert lines[19] == "ledger error: bad_value at line 21"
    assert lines[20:] == ["ledger errors: 25", "consent-load: failed"]


def test_every_failure_line_matches_an_allowed_shape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    calls: Calls,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rows = ["bad,111,a,1", "yes,111,SENTINELNAME a,1", "no,111,SENTINELNAME a,1", "yes,9,x,1"]
    printed: list[str] = []
    for header in ("wrong header", HEADER_LINE):
        _use_ledger(monkeypatch, _write(tmp_path / f"{len(printed)}.csv", header, *rows))
        assert ledger.run_load(calls.start) == 1
        printed += capsys.readouterr().out.splitlines()

    allowed = (ledger.ERROR_LINE_SHAPE, ledger.ERROR_COUNT_SHAPE, ledger.FAILED_SHAPE)
    assert printed
    for line in printed:
        assert any(re.fullmatch(shape, line) for shape in allowed)


@pytest.mark.parametrize("command", ["template", "load"])
def test_a_missing_or_empty_ledger_variable_fails_before_starting_anything(
    monkeypatch: pytest.MonkeyPatch,
    calls: Calls,
    capsys: pytest.CaptureFixture[str],
    command: str,
) -> None:
    for value in (None, "", "  "):
        if value is not None:
            monkeypatch.setenv("GCW_CONSENT_LEDGER", value)

        assert _runner(command)(calls.start) == 1

        assert capsys.readouterr().out == (
            f"consent-{command}: failed at ledger path (LedgerPathError)\n"
        )
    assert calls.started == 0


@pytest.mark.parametrize("command", ["template", "load"])
def test_the_real_database_refuses_a_ledger_inside_the_repo_before_starting(
    monkeypatch: pytest.MonkeyPatch,
    calls: Calls,
    capsys: pytest.CaptureFixture[str],
    command: str,
) -> None:
    monkeypatch.setenv("GCW_DB_NAME", "gcw_real")
    _use_ledger(monkeypatch, REPO / "tests" / "fixtures" / "senders.csv")

    assert _runner(command)(calls.start) == 1

    assert capsys.readouterr().out == (
        f"consent-{command}: failed at ledger path (LedgerPathError)\n"
    )
    assert calls.started == 0


@pytest.mark.parametrize("command", ["consent-template", "consent-load"])
def test_the_real_database_refusal_through_a_subprocess_and_a_temporary_env_file(
    tmp_path: Path, command: str
) -> None:
    """`gcw <command>` with an env file naming gcw_real and an in-repo ledger path.

    The env file is a temporary stand-in with fake credentials. Nothing connects: the refusal
    happens before docker and before any database work.
    """
    env_file = tmp_path / "refusal.env"
    env_file.write_text(
        "GCW_DB_NAME=gcw_real\nPOSTGRES_USER=u\nPOSTGRES_PASSWORD=p\nPOSTGRES_PORT=1\n"
        f"GCW_THREAD_ALLOWLIST=111\nGCW_CONSENT_LEDGER={REPO}/tests/fixtures/senders.csv\n",
        encoding="utf-8",
    )
    clean_env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("GCW_", "POSTGRES_", "DATABASE_URL", "DASHBOARD_"))
    }

    result = subprocess.run(
        [sys.executable, "-m", "gcwrapped.cli", command],
        cwd=tmp_path,
        env={**clean_env, "ENV_FILE": str(env_file)},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )

    assert result.returncode == 1
    assert result.stdout == f"{command}: failed at ledger path (LedgerPathError)\n"
    assert result.stderr == ""


class BoomError(RuntimeError):
    """Raised by a replaced boundary with a message that must never be printed."""


@pytest.mark.parametrize(
    ("command", "step", "boundary"),
    [
        ("template", "database", "start"),
        ("template", "warehouse", "warehouse_members"),
        ("template", "write", "write_ledger"),
        ("load", "database", "start"),
        ("load", "warehouse", "warehouse_members"),
        ("load", "migrate", "migrate"),
        ("load", "load", "replace_consent"),
    ],
)
def test_unexpected_failures_print_the_step_and_type_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    calls: Calls,
    capsys: pytest.CaptureFixture[str],
    command: str,
    step: str,
    boundary: str,
) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise BoomError("SENTINELTEXT SENTINELNAME")

    _use_ledger(
        monkeypatch,
        _write(tmp_path / "SENTINELDIR" / "l.csv", HEADER_LINE, "yes,111,SENTINELNAME a,1"),
    )
    start = calls.start
    if boundary == "start":
        start = boom
    else:
        monkeypatch.setattr(ledger, boundary, boom)

    assert _runner(command)(start) == 1

    out = capsys.readouterr().out
    assert out == f"consent-{command}: failed at {step} (BoomError)\n"
    assert _no_sentinel(out)


@pytest.mark.parametrize("command", ["template", "load"])
def test_docker_down_is_its_own_failure_line(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    command: str,
) -> None:
    """Docker not running shows as DockerUnavailableError, not a bare CalledProcessError."""
    from gcwrapped import docker

    def docker_down(argv: list[str], **_kwargs: object) -> None:
        assert argv[:2] == ["docker", "info"]  # compose is never reached
        raise subprocess.CalledProcessError(1, argv)

    monkeypatch.setattr(subprocess, "run", docker_down)
    _use_ledger(monkeypatch, _write(tmp_path / "l.csv", HEADER_LINE, "yes,111,Sam Lee,1"))

    assert _runner(command)(docker.start_database) == 1

    assert capsys.readouterr().out == (
        f"consent-{command}: failed at database (DockerUnavailableError)\n"
    )


def test_cli_subcommands_call_the_matching_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []

    def fake(name: str) -> Callable[[], int]:
        def run() -> int:
            seen.append(name)
            return 0

        return run

    monkeypatch.setattr(ledger, "run_template", fake("template"))
    monkeypatch.setattr(ledger, "run_load", fake("load"))

    assert main(["consent-template"]) == 0
    assert main(["consent-load"]) == 0
    assert seen == ["template", "load"]


def test_makefile_targets_run_the_cli_and_are_phony() -> None:
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")

    for command in ("consent-template", "consent-load"):
        assert f"\n{command}:\n\t@uv run gcw {command}\n" in makefile
        assert re.search(rf"^\.PHONY:.*\b{command}\b", makefile, re.MULTILINE)
