"""The dev landing cannot go stale because it is regenerated, not compared (P2-3a, task 7).

`data/` is never read by any test or tool (CLAUDE.md), so there is deliberately no test that diffs
data/landing against the generator. Instead `make synth` replaces data/landing wholesale from
tests/synthetic/instagram.py, and a local `make e2e` does the same before it ingests anything.
These tests read the Makefile's dry run (`make -n`), which prints the recipe without running it
and opens nothing under data/.
"""

import os
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
GENERATE_LANDING = "uv run python tests/synthetic/instagram.py --out data/landing"


def _dry_run(target: str, **variables: str) -> list[str]:
    """`make -n target`: the recipe lines it would run, in order. Nothing is executed."""
    # Not ENV_FILE (each test sets what it needs), and not make's own variables: under `make test`
    # a nested make would otherwise print "Entering directory" lines among the recipe.
    drop = {"ENV_FILE", "MAKEFLAGS", "MFLAGS", "MAKELEVEL", "MAKEOVERRIDES"}
    env = {k: v for k, v in os.environ.items() if k not in drop}
    result = subprocess.run(
        ["make", "-n", target, *(f"{k}={v}" for k, v in variables.items())],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    return [
        line for line in result.stdout.splitlines() if line.strip() and not line.startswith("make[")
    ]


def test_make_synth_replaces_data_landing_with_the_default_seed() -> None:
    lines = _dry_run("synth")
    assert GENERATE_LANDING in lines
    assert "uv run python tests/synthetic/instagram.py" in lines  # the test fixtures, as before
    assert not any("--seed" in line for line in lines)  # the default seed, so it matches the tests


def test_make_e2e_regenerates_the_landing_before_anything_else_in_the_default_dev_run() -> None:
    lines = _dry_run("e2e")
    assert lines[0] == GENERATE_LANDING
    ingest = next(i for i, line in enumerate(lines) if "airflow dags test gcw_pipeline" in line)
    first_docker = next(i for i, line in enumerate(lines) if line.startswith("docker compose"))
    assert 0 < first_docker < ingest  # regenerated before compose starts and before ingest runs
    assert not any("--seed" in line for line in lines)


def test_an_explicit_dot_env_is_the_same_dev_run() -> None:
    assert _dry_run("e2e", ENV_FILE=".env")[0] == GENERATE_LANDING


@pytest.mark.parametrize(
    "env_file",
    ["/nonexistent/real.env", "../outside.env", "other.env", "./.env", "/tmp/.env"],
)
def test_any_other_env_file_never_regenerates_or_deletes_a_landing(env_file: str) -> None:
    """A real run (or CI's) names another env file: data/landing is not touched by it."""
    lines = _dry_run("e2e", ENV_FILE=env_file)
    assert not any("instagram.py" in line or "data/landing" in line for line in lines)
    assert any(line.startswith("docker compose") for line in lines)  # the e2e recipe is intact


def test_the_regeneration_empties_the_landing_before_writing() -> None:
    """The generator, not a copy of its output, is the only writer; it clears its --out first."""
    source = (REPO / "tests" / "synthetic" / "instagram.py").read_text(encoding="utf-8")
    body = source.split("def main()", 1)[1]
    assert re.search(r"clear_output_dir\(args\.out\)\s+a, b = generate_pair\(args\.out", body)
    assert "--seed" in body and "default=1234" in body


def test_no_make_target_or_test_reads_data() -> None:
    """The recipes only write into data/: no cat, diff, grep, find or ls on it."""
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")
    for line in makefile.splitlines():
        if "data/" in line and not line.lstrip().startswith("#"):
            assert not re.search(r"\b(cat|diff|grep|find|ls|head|tail|wc|sha\w*sum)\b", line), line
