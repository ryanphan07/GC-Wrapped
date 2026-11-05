"""GitHub Actions run blocks use bash errexit, which ignores commands negated with `!`.

A line like `! grep -q x file` therefore never fails the step unless it is the last line. Use
`if grep ...; then echo "<fixed reason>"; exit 1; fi` instead.
"""

import re
from pathlib import Path

import pytest

WORKFLOWS = sorted((Path(__file__).resolve().parents[1] / ".github" / "workflows").glob("*.yml"))
NEGATED = re.compile(r"^\s*!\s")


def test_workflows_exist() -> None:
    assert WORKFLOWS


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_no_line_starts_with_a_negation(path: Path) -> None:
    bad = [
        number
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if NEGATED.match(line)
    ]
    assert not bad, f"{path.name}: negated command on lines {bad}"
