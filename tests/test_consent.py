"""Consent allowlist: parsing, thread selection before any file is opened, ingest and check-landing.

Sentinels (SENTINELNAME...) stand in for a person's name wherever the code must not print one.
"""

import json
import re
from pathlib import Path
from typing import Any

import pytest

from gcwrapped.cli import main
from gcwrapped.config import get_settings
from gcwrapped.consent import AllowlistError, parse_allowlist, require_allowlist
from gcwrapped.parsers import instagram_format as fmt
from gcwrapped.parsers.instagram import ParseStats, parse_export, parse_export_consented
from synthetic import sentinel
from synthetic.instagram import SYNTHETIC_THREAD_IDS, generate_pair

REPO = Path(__file__).resolve().parents[1]
FIRST_ID, SECOND_ID, THIRD_ID = SYNTHETIC_THREAD_IDS
MALFORMED = "{ this is not json SENTINELTEXT"


@pytest.fixture(autouse=True)
def isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory) -> None:
    """No DB env, no allowlist, and a cwd without a .env: each test sets exactly what it needs."""
    for var in (
        "POSTGRES_USER",
        "POSTGRES_PASSWORD",
        "POSTGRES_PORT",
        "DATABASE_URL",
        "GCW_DB_NAME",
        "GCW_THREAD_ALLOWLIST",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path_factory.mktemp("cwd"))
    get_settings.cache_clear()


def _inbox(root: Path) -> Path:
    return root / fmt.MESSAGES_DIR


def _add_folder(root: Path, name: str, payload: str = MALFORMED) -> Path:
    folder = _inbox(root) / name
    folder.mkdir(parents=True)
    (folder / "message_1.json").write_text(payload, encoding="utf-8")
    return folder


# --- Parsing -------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("123", {"123"}),
        ("123,456", {"123", "456"}),
        (" 123 , 456 ,\t789 ", {"123", "456", "789"}),
        ("123,123", {"123"}),
        ("007", {"007"}),
        ("", set()),
        ("   ", set()),
    ],
)
def test_parse_allowlist_valid_and_empty(raw: str, expected: set[str]) -> None:
    assert parse_allowlist(raw) == expected


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("123,SENTINELNAME,456", "entry 2 of 3 is not numeric"),
        ("SENTINELNAME", "entry 1 of 1 is not numeric"),
        ("123,,456", "entry 2 of 3 is not numeric"),
        ("123,456,", "entry 3 of 3 is not numeric"),
        ("12 3", "entry 1 of 1 is not numeric"),
        ("-5", "entry 1 of 1 is not numeric"),
        ("12²", "entry 1 of 1 is not numeric"),  # str.isdigit() would accept this
    ],
)
def test_parse_allowlist_invalid_entry_error_names_position_not_value(
    raw: str, message: str
) -> None:
    with pytest.raises(AllowlistError) as info:
        parse_allowlist(raw)

    text = str(info.value)
    assert message in text
    assert "SENTINELNAME" not in text
    assert info.value.__cause__ is None
    assert info.value.__context__ is None


def test_require_allowlist_fails_when_missing_or_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(AllowlistError, match="unset or empty"):
        require_allowlist()
    monkeypatch.setenv("GCW_THREAD_ALLOWLIST", "  ")
    with pytest.raises(AllowlistError, match="unset or empty"):
        require_allowlist()


def test_require_allowlist_reads_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GCW_THREAD_ALLOWLIST", " 111 , 222 ")
    assert require_allowlist() == {"111", "222"}


# The real-ingest-synthetic CI job: sentinel threads A, C, D, plus the folder its third run adds.
SENTINEL_JOB_IDS = {
    sentinel.THREAD_A.rsplit("_", 1)[-1],
    sentinel.THREAD_C,
    sentinel.THREAD_D,
    "99999999999999",
}


def test_env_example_and_ci_list_the_synthetic_ids() -> None:
    """One code path in dev, CI and real: synthetic IDs are listed explicitly, never bypassed."""
    for name in (".env.example", ".github/workflows/ci.yml"):
        text = (REPO / name).read_text(encoding="utf-8")
        uncommented = "\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("#"))
        values = re.findall(r"GCW_THREAD_ALLOWLIST[:=]\s*(\S+)", uncommented)
        assert values, name
        for value in values:
            # CI has two synthetic landings: the generator's and the real-ingest-synthetic job's.
            assert parse_allowlist(value) in (set(SYNTHETIC_THREAD_IDS), SENTINEL_JOB_IDS), name


def test_no_bypass_switch_exists_in_source() -> None:
    sources = "\n".join(p.read_text(encoding="utf-8") for p in (REPO / "src").rglob("*.py"))
    assert "GCW_SYNTHETIC" not in sources
    assert "ALLOWLIST_DISABLED" not in sources.upper()


# --- Selection happens before any file is opened --------------------------------------------------


def test_select_threads_counts_shapes_and_skips_the_rest(tmp_path: Path) -> None:
    a, _ = generate_pair(tmp_path)
    _add_folder(a, "SENTINELNAME_999")
    _add_folder(a, "SENTINELNAME")
    (_inbox(a) / "stray.txt").write_text("x", encoding="utf-8")  # files are not thread folders

    sel = fmt.select_threads(a, {FIRST_ID, SECOND_ID})

    assert {fmt.thread_id(d) for d in sel.threads} == {FIRST_ID, SECOND_ID}
    assert sel.shapes == {"<name>_<digits>": 3, "<digits>": 1, "other": 1}
    assert sel.not_allowlisted == 2  # the sentinel thread and the third synthetic thread
    assert sel.other_shape == 1
    assert sel.allowlisted_found == {FIRST_ID, SECOND_ID}


def test_empty_allowlist_selects_nothing(tmp_path: Path) -> None:
    a, _ = generate_pair(tmp_path)
    assert fmt.select_threads(a, frozenset()).threads == []
    assert list(parse_export_consented([a], frozenset())) == []


def test_non_allowlisted_and_nonconforming_folders_are_never_opened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Malformed JSON in folders we have no consent for must not break ingest or be counted."""
    a, _ = generate_pair(tmp_path)
    allowlist = frozenset(SYNTHETIC_THREAD_IDS)
    baseline = [m.message_key for m in parse_export_consented([a], allowlist)]
    _add_folder(a, "SENTINELNAME_999")  # recognised shape, not on the list
    _add_folder(a, "SENTINELNAME")  # not a thread-ID shape at all

    touched: list[Path] = []
    original_iterdir, original_read_text = Path.iterdir, Path.read_text

    def spy_iterdir(self: Path) -> Any:
        touched.append(self)
        return original_iterdir(self)

    def spy_read_text(self: Path, *args: Any, **kwargs: Any) -> str:
        touched.append(self)
        return original_read_text(self, *args, **kwargs)

    stats = ParseStats()
    with monkeypatch.context() as spy:
        spy.setattr(Path, "iterdir", spy_iterdir)
        spy.setattr(Path, "read_text", spy_read_text)
        keys = [m.message_key for m in parse_export_consented([a], allowlist, stats)]

    assert keys == baseline
    assert stats.thread_ids == allowlist
    assert stats.folders_not_allowlisted == 1
    assert stats.folders_other_shape == 1
    assert not [p for p in touched if "SENTINELNAME" in str(p)], "a skipped folder was touched"


def test_the_ungated_survey_path_does_read_every_folder(tmp_path: Path) -> None:
    """Control for the test above: parse_export (survey) hits the malformed folder and fails."""
    a, _ = generate_pair(tmp_path)
    _add_folder(a, "SENTINELNAME_999")

    with pytest.raises(ValueError):  # ParseError, from the malformed message file
        list(parse_export([a]))


def test_only_allowlisted_threads_are_parsed(tmp_path: Path) -> None:
    a, _ = generate_pair(tmp_path)
    everything = list(parse_export([a]))

    only_first = list(parse_export_consented([a], {FIRST_ID}))

    assert {m.thread_id for m in only_first} == {FIRST_ID}
    assert [m.message_key for m in only_first] == [
        m.message_key for m in everything if m.thread_id == FIRST_ID
    ]
    assert len(only_first) < len(everything)


# --- gcw ingest ----------------------------------------------------------------------------------


def test_ingest_without_an_allowlist_fails_before_anything_else(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    a, _ = generate_pair(tmp_path)

    assert main(["ingest", str(a)]) == 1

    out = capsys.readouterr().out
    assert "GCW_THREAD_ALLOWLIST is unset or empty" in out
    assert "database not configured" not in out  # stopped before the DB was even considered


def test_ingest_with_an_invalid_allowlist_names_position_not_value(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    a, _ = generate_pair(tmp_path)
    monkeypatch.setenv("GCW_THREAD_ALLOWLIST", f"{FIRST_ID},SENTINELNAME")

    assert main(["ingest", str(a)]) == 1

    out = capsys.readouterr().out
    assert "entry 2 of 2 is not numeric" in out
    assert "SENTINELNAME" not in out


def test_ingest_landing_with_no_allowlisted_thread_fails_clearly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    generate_pair(tmp_path)
    monkeypatch.setenv("GCW_THREAD_ALLOWLIST", "999")  # a mistyped ID allowlists nothing

    assert main(["ingest", "--landing", str(tmp_path)]) == 1

    out = capsys.readouterr().out
    assert "no allowlisted message threads" in out
    assert "database not configured" not in out


# --- gcw check-landing ---------------------------------------------------------------------------


def _check(
    landing: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch, allow: str
) -> tuple[int, str]:
    monkeypatch.setenv("GCW_THREAD_ALLOWLIST", allow)
    code = main(["check-landing", str(landing)])
    return code, capsys.readouterr().out


def test_check_landing_reports_counts_per_root_and_in_total(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    generate_pair(tmp_path)

    code, out = _check(tmp_path, capsys, monkeypatch, ",".join(SYNTHETIC_THREAD_IDS))

    assert code == 0
    lines = out.splitlines()
    assert "export roots: 2" in lines
    for number in (1, 2):
        assert f"root {number} folders <name>_<digits>: 2" in lines
        assert f"root {number} folders <digits>: 1" in lines
        assert f"root {number} folders other: 0" in lines
        assert f"root {number} allowlisted IDs found: 3" in lines
        assert f"root {number} allowlisted threads to ingest: 3" in lines
    assert "total folders <name>_<digits>: 4" in lines
    assert "total folders <digits>: 2" in lines
    assert "allowlisted IDs configured: 3" in lines
    assert "allowlisted IDs found: 3" in lines
    assert "allowlisted IDs not found: 0" in lines
    assert "allowlisted threads to ingest: 3" in lines


def test_check_landing_counts_configured_ids_that_were_not_found(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    generate_pair(tmp_path)

    code, out = _check(tmp_path, capsys, monkeypatch, f"{FIRST_ID}, 999, 998")

    assert code == 0  # something matched, so the run can proceed
    lines = out.splitlines()
    assert "allowlisted IDs configured: 3" in lines
    assert "allowlisted IDs found: 1" in lines
    assert "allowlisted IDs not found: 2" in lines
    assert "allowlisted threads to ingest: 1" in lines
    assert "999" not in out and "998" not in out  # counts only, never the IDs


def test_check_landing_fails_when_no_allowlisted_id_is_found(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    generate_pair(tmp_path)

    code, out = _check(tmp_path, capsys, monkeypatch, "999")

    assert code == 1
    assert "allowlisted IDs found: 0" in out
    assert "none of the allowlisted IDs were found" in out


def test_check_landing_fails_with_an_empty_allowlist(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    generate_pair(tmp_path)

    code, out = _check(tmp_path, capsys, monkeypatch, "")

    assert code == 1
    assert "allowlisted IDs configured: 0" in out


def test_check_landing_fails_when_there_are_no_thread_folders(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    _inbox(tmp_path / "export_empty").mkdir(parents=True)
    _add_folder(tmp_path / "export_empty", "SENTINELNAME", "{}")  # "other" shape, not a thread

    code, out = _check(tmp_path, capsys, monkeypatch, FIRST_ID)

    assert code == 1
    assert "no thread folders found" in out
    assert "root 1 folders other: 1" in out


def test_check_landing_fails_when_there_are_no_export_roots(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    code, out = _check(tmp_path, capsys, monkeypatch, FIRST_ID)

    assert code == 1
    assert "no export roots" in out


def test_check_landing_rejects_a_malformed_allowlist_without_echoing_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    generate_pair(tmp_path)

    code, out = _check(tmp_path, capsys, monkeypatch, "SENTINELNAME")

    assert code == 1
    assert "entry 1 of 1 is not numeric" in out
    assert "SENTINELNAME" not in out


def test_check_landing_output_has_no_names_ids_or_paths(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    generate_pair(tmp_path)
    a = tmp_path / "export_a"
    _add_folder(a, "SENTINELNAME_555", json.dumps({"messages": []}))
    _add_folder(a, "SENTINELNAME", "{}")

    code, out = _check(tmp_path, capsys, monkeypatch, ",".join(SYNTHETIC_THREAD_IDS))

    assert code == 0
    forbidden = ["SENTINEL", "weekendcrew", "samantha", "export_a", "export_b", str(tmp_path)]
    forbidden += [*SYNTHETIC_THREAD_IDS, "555"]
    for text in forbidden:
        assert text not in out, text
    # Every line is a fixed label, then a colon and one integer.
    for line in out.splitlines():
        assert re.fullmatch(r"[A-Za-z0-9 <>_]+: \d+", line), line
