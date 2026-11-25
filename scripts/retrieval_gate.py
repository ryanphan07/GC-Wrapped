"""The CI retrieval gate (D108): compare a `make retrieval-eval ... SYNTHETIC=1` log with the
committed floor.

Usage: python scripts/retrieval_gate.py <retrieval-eval log>

Reads the fixed output lines of retrieval-eval and tests/fixtures/eval/retrieval_floors.json.
Prints fixed lines only and exits 1 if the log is not a complete synthetic eval of the floor's
method with no question dropped, or recall@5 is below the floor. A dropped question would leave an
easier set to score, so it fails the gate too. Changing the floor requires a new decisions entry.
"""

import json
import re
import sys
from pathlib import Path

FLOORS = (
    Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "eval" / "retrieval_floors.json"
)


class GateInputError(Exception):
    """The log or the floors file is not in the expected shape."""


def parse_log(text: str) -> tuple[str, int, float]:
    """(method, questions dropped, recall@5) from a retrieval-eval log; each must appear once."""
    lines = text.splitlines()
    if "retrieval-eval: ok" not in lines or "questions source: synthetic" not in lines:
        raise GateInputError
    found: list[str] = []
    for pattern in (
        r"method: (vector|hybrid)",
        r"questions dropped \(gold not embedded or ineligible\): (\d+)",
        r"recall@5: (\d+\.\d+)",
    ):
        hits = [m.group(1) for line in lines if (m := re.fullmatch(pattern, line))]
        if len(hits) != 1:
            raise GateInputError
        found.append(hits[0])
    return found[0], int(found[1]), float(found[2])


def load_floors(path: Path = FLOORS) -> tuple[str, float]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if set(data) != {"method", "recall_at_5"}:
        raise GateInputError
    return str(data["method"]), float(data["recall_at_5"])


def check(measured: tuple[str, int, float], floors: tuple[str, float]) -> list[str]:
    """Fixed lines; the last is `retrieval-gate: ok` only if every check passed."""
    method, dropped, recall = measured
    floor_method, floor = floors
    checks = (
        ("method is the floor's method", method == floor_method),
        ("no question dropped", dropped == 0),
        ("recall@5 at least its floor", recall >= floor),
    )
    return [
        f"method: {method}",
        f"questions dropped: {dropped}",
        f"recall@5: {recall:.3f} (floor {floor:.2f})",
        *(f"check {name}: {'pass' if ok else 'fail'}" for name, ok in checks),
        "retrieval-gate: ok" if all(ok for _, ok in checks) else "retrieval-gate: failed",
    ]


def main(argv: list[str]) -> int:
    try:
        if len(argv) != 2:
            raise GateInputError
        lines = check(parse_log(Path(argv[1]).read_text(encoding="utf-8")), load_floors())
    except (OSError, ValueError, KeyError, GateInputError) as exc:
        print(f"retrieval-gate: failed ({type(exc).__name__})")
        return 1
    print(*lines, sep="\n")
    return 0 if lines[-1] == "retrieval-gate: ok" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
