"""The memory validity guard of an eval pass (D126): a pass counts only on a machine that is not
swapping.

Each retrieval-eval and answer-eval pass reads the host's /proc/meminfo at its start and end and
prints available memory and swap in use. It is refused if more than 256 MiB of swap is in use at
the start, and fails if swap in use grew by more than 256 MiB during it, so its timings never reach
a comparison. The limits are fixed by D126: they are not tuned on a run.
"""

from dataclasses import dataclass
from pathlib import Path

from gcwrapped.tagging.report import check_lines

MEMINFO = Path("/proc/meminfo")
MAX_SWAP_IN_USE_KIB = 256 * 1024
MAX_SWAP_GROWTH_KIB = 256 * 1024
SHAPES = (
    r"available memory at (start|end) \(MiB\): \d+",
    r"swap in use at (start|end) \(MiB\): \d+",
)


class MemInfoError(Exception):
    """/proc/meminfo cannot be read or lacks a field: the guard fails closed."""


class SwapInUse(Exception):
    """Too much swap is in use to start a pass."""


class SwapGrew(Exception):
    """Swap in use grew too much during a pass."""


@dataclass(frozen=True)
class Memory:
    available_kib: int
    swap_used_kib: int


def read() -> Memory:
    try:
        fields = dict(
            line.split(":", 1) for line in MEMINFO.read_text(encoding="ascii").splitlines()
        )
        available, total, free = (
            int(fields[name].split()[0]) for name in ("MemAvailable", "SwapTotal", "SwapFree")
        )
    except (OSError, ValueError, KeyError, IndexError):
        raise MemInfoError from None
    return Memory(available, total - free)


def _report(when: str) -> Memory:
    """Read and print one measurement at once, since a pass can take most of an hour. Swap is
    rounded up, so a refused value never prints as the limit itself."""
    found = read()
    lines = [
        f"available memory at {when} (MiB): {found.available_kib // 1024}",
        f"swap in use at {when} (MiB): {-(-found.swap_used_kib // 1024)}",
    ]
    print(*check_lines(lines, SHAPES), sep="\n", flush=True)
    return found


def start_pass() -> Memory:
    found = _report("start")
    if found.swap_used_kib > MAX_SWAP_IN_USE_KIB:
        raise SwapInUse
    return found


def end_pass(start: Memory) -> None:
    if _report("end").swap_used_kib - start.swap_used_kib > MAX_SWAP_GROWTH_KIB:
        raise SwapGrew
