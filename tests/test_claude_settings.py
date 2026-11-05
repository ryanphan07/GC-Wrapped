"""The assistant's permission rules are the tool-enforced half of the real-data boundary.

If one of these entries is removed, the rule in CLAUDE.md becomes prose only. Deny beats allow, so
nothing else can restore it.
"""

import json
from pathlib import Path

import pytest

SETTINGS = Path(__file__).resolve().parents[1] / ".claude" / "settings.json"

REQUIRED_DENY = [
    "Read(./data/**)",
    "Edit(./data/**)",
    "Read(./real.env)",
    "Edit(./real.env)",
    "Write(./real.env)",
    "Read(~/gcw-private/**)",
    "Edit(~/gcw-private/**)",
    "Write(~/gcw-private/**)",
    "Bash(*gcw-private*)",
    "Bash(*gcw_real*)",
    "Bash(*gc-wrapped-real*)",
    "Bash(*real.env*)",
    "Bash(*make real-ingest*)",
    "Bash(*gcw real-ingest*)",
    "Bash(*gcwrapped.real_ingest*)",
    "Bash(*5433*)",
    "Bash(*8502*)",
]


def _deny() -> list[str]:
    data = json.loads(SETTINGS.read_text(encoding="utf-8"))
    return list(data["permissions"]["deny"])


@pytest.mark.parametrize("entry", REQUIRED_DENY)
def test_required_deny_rule_is_present(entry: str) -> None:
    assert entry in _deny()
