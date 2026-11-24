"""The CI eval gate (D100): compare a `make tag-eval ... SYNTHETIC=1` log with the committed floors.

Usage: python scripts/eval_gate.py <tag-eval log>

Reads the fixed output lines of tag-eval (model id, topic and sentiment accuracy) and the
floors in tests/fixtures/eval/gate_floors.json. Prints fixed lines only and exits 1 if the log is
not a complete tag-eval of the floors' model or either accuracy is below its floor. Changing a
floor requires a new decisions entry (D100).
"""

import json
import re
import sys
from pathlib import Path

FLOORS = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "eval" / "gate_floors.json"
METRICS = ("topic", "sentiment")


class GateInputError(Exception):
    """The log or the floors file is not in the expected shape."""


def parse_log(text: str) -> dict[str, float | str]:
    """model id and the two accuracies from a tag-eval log; each must appear once."""
    found: dict[str, float | str] = {}
    patterns = {
        "model": r"model id: ([A-Za-z0-9._-]+)",
        "topic": r"topic accuracy: (\d+\.\d+)",
        "sentiment": r"sentiment accuracy: (\d+\.\d+)",
    }
    lines = text.splitlines()
    if "tag-eval: ok" not in lines:
        raise GateInputError
    for key, pattern in patterns.items():
        hits = [m.group(1) for line in lines if (m := re.fullmatch(pattern, line))]
        if len(hits) != 1:
            raise GateInputError
        found[key] = hits[0] if key == "model" else float(hits[0])
    return found


def load_floors(path: Path = FLOORS) -> dict[str, float | str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if set(data) != {"model", "topic_accuracy", "sentiment_accuracy"}:
        raise GateInputError
    return {
        "model": str(data["model"]),
        "topic": float(data["topic_accuracy"]),
        "sentiment": float(data["sentiment_accuracy"]),
    }


def check(measured: dict[str, float | str], floors: dict[str, float | str]) -> list[str]:
    """Fixed lines; the last is `eval-gate: ok` only if every check passed."""
    lines = [f"model id: {measured['model']}"]
    ok = measured["model"] == floors["model"]
    lines.append(f"check model is the floors' model: {'pass' if ok else 'fail'}")
    for metric in METRICS:
        value, floor = float(measured[metric]), float(floors[metric])
        passed = value >= floor
        ok = ok and passed
        lines.append(f"{metric} accuracy: {value:.3f} (floor {floor:.2f})")
        lines.append(f"check {metric} accuracy at least its floor: {'pass' if passed else 'fail'}")
    lines.append("eval-gate: ok" if ok else "eval-gate: failed")
    return lines


def main(argv: list[str]) -> int:
    try:
        if len(argv) != 2:
            raise GateInputError
        lines = check(parse_log(Path(argv[1]).read_text(encoding="utf-8")), load_floors())
    except (OSError, ValueError, KeyError, GateInputError) as exc:
        print(f"eval-gate: failed ({type(exc).__name__})")
        return 1
    print(*lines, sep="\n")
    return 0 if lines[-1] == "eval-gate: ok" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
