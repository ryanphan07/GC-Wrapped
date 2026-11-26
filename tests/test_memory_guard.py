"""The memory validity guard (D126) over a fake /proc/meminfo: the limits, the fixed lines and
failing closed. Its place in the eval commands is tested in test_eval_guards_integration.py."""

from pathlib import Path

import pytest

from conftest import meminfo_text
from gcwrapped import memory
from gcwrapped.tagging.report import check_lines

REAL_MEMINFO = memory.MEMINFO  # conftest's autouse fixture replaces it in every test
LIMIT = 256 * 1024  # KiB, fixed by D126


@pytest.fixture
def meminfo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "meminfo"
    monkeypatch.setattr(memory, "MEMINFO", path)
    return path


def _set(path: Path, swap_used_kib: int, available_kib: int = 5 * 1024 * 1024) -> None:
    path.write_text(meminfo_text(swap_used_kib, available_kib), encoding="ascii")


def test_the_guard_reads_the_hosts_meminfo() -> None:
    assert Path("/proc/meminfo") == REAL_MEMINFO


def test_available_memory_and_swap_in_use_are_read(meminfo: Path) -> None:
    _set(meminfo, swap_used_kib=2048, available_kib=1_000_000)
    assert memory.read() == memory.Memory(available_kib=1_000_000, swap_used_kib=2048)


def test_a_pass_prints_both_values_at_its_start_and_end(
    meminfo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _set(meminfo, swap_used_kib=100 * 1024)
    before = memory.start_pass()
    _set(meminfo, swap_used_kib=101 * 1024 + 1, available_kib=2048 * 1024 + 1023)
    memory.end_pass(before)
    lines = capsys.readouterr().out.splitlines()
    assert lines == [
        "available memory at start (MiB): 5120",
        "swap in use at start (MiB): 100",
        "available memory at end (MiB): 2048",
        "swap in use at end (MiB): 102",
    ]
    check_lines(lines, memory.SHAPES * 2)


def test_a_pass_may_start_at_the_swap_limit_and_not_above_it(
    meminfo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _set(meminfo, swap_used_kib=LIMIT)
    memory.start_pass()
    capsys.readouterr()
    _set(meminfo, swap_used_kib=LIMIT + 1)
    with pytest.raises(memory.SwapInUse):
        memory.start_pass()
    # the values are printed before the refusal, and a refused one never prints as the limit
    assert "swap in use at start (MiB): 257" in capsys.readouterr().out.splitlines()


@pytest.mark.parametrize(("growth", "grew"), [(LIMIT, False), (LIMIT + 1, True), (-LIMIT, False)])
def test_swap_may_grow_by_the_limit_and_no_more(
    meminfo: Path, capsys: pytest.CaptureFixture[str], growth: int, grew: bool
) -> None:
    start = memory.Memory(available_kib=1, swap_used_kib=LIMIT)
    _set(meminfo, swap_used_kib=start.swap_used_kib + growth)
    if grew:
        with pytest.raises(memory.SwapGrew):
            memory.end_pass(start)
    else:
        memory.end_pass(start)
    assert "swap in use at end (MiB): " in capsys.readouterr().out


@pytest.mark.parametrize(
    "content",
    ["", "MemAvailable: 1 kB\nSwapTotal: 2 kB\n", "MemAvailable: x kB\n", "no colon\n"],
)
def test_a_meminfo_without_the_fields_fails_closed(meminfo: Path, content: str) -> None:
    meminfo.write_text(content, encoding="ascii")
    with pytest.raises(memory.MemInfoError):
        memory.start_pass()


def test_a_missing_meminfo_fails_closed(meminfo: Path) -> None:
    with pytest.raises(memory.MemInfoError):
        memory.start_pass()
