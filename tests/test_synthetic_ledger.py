"""`make synth` writes a synthetic consent ledger that fits the synthetic exports."""

import stat
from pathlib import Path

import pytest

from gcwrapped import ledger
from gcwrapped.parsers.instagram import parse_export
from synthetic.instagram import (
    CONSENT_BY_SENDER,
    SENTINEL_BLANK,
    SENTINEL_NO,
    SYNTHETIC_THREAD_IDS,
    consent_ledger_rows,
    generate_pair,
    resolve_ledger_target,
    write_consent_ledger,
)

REPO = Path(__file__).resolve().parents[1]


def test_the_ledger_has_a_mix_and_the_sentinel_senders() -> None:
    rows = consent_ledger_rows()
    by_name = {r.sender_name: r.consent for r in rows}

    assert {"yes", "no", ""} <= {r.consent for r in rows}
    assert by_name[SENTINEL_NO] == "no"
    assert by_name[SENTINEL_BLANK] == ""
    assert "sentinel" in SENTINEL_NO.lower() and "sentinel" in SENTINEL_BLANK.lower()
    assert "," in SENTINEL_NO and "🎉" in SENTINEL_NO  # names have commas and emoji in practice
    assert {r.thread_id for r in rows} == set(SYNTHETIC_THREAD_IDS)


def test_every_ledger_sender_is_a_sender_in_both_exports(tmp_path: Path) -> None:
    root_a, root_b = generate_pair(tmp_path)
    senders_a = {(m.thread_id, m.sender_name) for m in parse_export([root_a])}
    senders_b = {(m.thread_id, m.sender_name) for m in parse_export([root_b])}

    for row in consent_ledger_rows():
        assert row.key in senders_a and row.key in senders_b


def test_the_ledger_is_valid_sorted_and_uses_only_known_consent_values(tmp_path: Path) -> None:
    path = tmp_path / "senders.csv"

    count = write_consent_ledger(path)
    check = ledger.check_ledger(path, allowlist=frozenset(SYNTHETIC_THREAD_IDS))

    assert check.problems == [] and len(check.rows) == count
    assert set(CONSENT_BY_SENDER.values()) <= {"yes", "no", ""}
    assert check.rows == sorted(
        check.rows, key=lambda r: (r.thread_id, -int(r.messages), r.sender_name)
    )
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_the_ledger_is_deterministic() -> None:
    assert consent_ledger_rows(7) == consent_ledger_rows(7)


def test_exports_are_unchanged_by_the_ledger_helper(tmp_path: Path) -> None:
    a = tmp_path / "a"
    b = tmp_path / "b"
    generate_pair(a)
    consent_ledger_rows()  # builds its own rng; must not disturb generate_pair
    generate_pair(b)

    files_a = sorted(p.relative_to(a) for p in a.rglob("*.json"))
    assert files_a == sorted(p.relative_to(b) for p in b.rglob("*.json"))
    for rel in files_a:
        assert (a / rel).read_bytes() == (b / rel).read_bytes()


# --- Where the synthetic ledger may be written ---------------------------------------------------


def test_no_path_means_no_ledger() -> None:
    assert resolve_ledger_target(None, "") is None
    assert resolve_ledger_target(None, "  ") is None


def test_an_environment_path_must_be_inside_the_repo(tmp_path: Path) -> None:
    inside = REPO / "tests" / "fixtures" / "synthetic_consent" / "senders.csv"

    assert resolve_ledger_target(None, str(inside)) == inside
    with pytest.raises(SystemExit, match="outside the repo"):
        resolve_ledger_target(None, str(tmp_path / "senders.csv"))


def test_an_explicit_path_may_be_anywhere_but_the_private_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    (home / "gcw-private").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))

    assert resolve_ledger_target(tmp_path / "ci" / "s.csv", "") == tmp_path / "ci" / "s.csv"
    for target in (home / "gcw-private" / "s.csv", Path("~/gcw-private/consent/s.csv")):
        with pytest.raises(SystemExit, match="private directory"):
            resolve_ledger_target(target, "")
    with pytest.raises(SystemExit, match="private directory"):
        resolve_ledger_target(None, str(home / "gcw-private" / "s.csv"))


def test_a_symlink_into_the_private_dir_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    (home / "gcw-private").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    link = tmp_path / "innocent"
    link.symlink_to(home / "gcw-private")

    with pytest.raises(SystemExit, match="private directory"):
        resolve_ledger_target(link / "s.csv", "")
