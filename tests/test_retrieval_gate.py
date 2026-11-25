"""The CI retrieval gate script (D108): the floor, shape checks and the committed floor file."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from gcwrapped.retrieval import evaluation, report

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "retrieval_gate.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("retrieval_gate", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gate = _load_script()


def _log(hits: int, total: int = 20, dropped: int = 0, method: str = "vector") -> str:
    """The real renderer's lines for `hits` of `total` questions found at rank 1."""
    rows = [
        evaluation.Row(
            f"q{n:03d}",
            "a" * 64,
            "b" * 64,
            ("b" * 64,),
            ("b" * 64,) if n < hits else (),
            0.1,
        )
        for n in range(total)
    ]
    result = evaluation.score(rows, total + dropped)
    return "\n".join(report.render_eval("gcw_dev", "synthetic", method, "0" * 12, result)) + "\n"


def _run(tmp_path: Path, text: str, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    log = tmp_path / "eval.log"
    log.write_text(text, encoding="utf-8")
    code = gate.main(["retrieval_gate.py", str(log)])
    return code, capsys.readouterr().out


def test_the_committed_floor_is_for_vector_search_and_a_multiple_of_five_hundredths() -> None:
    method, floor = gate.load_floors()
    assert method == evaluation.VECTOR
    assert 0.5 < floor < 1
    assert round(floor * 20, 9) == round(floor * 20)


@pytest.mark.parametrize("hits", [17, 20])
def test_a_run_at_or_above_the_floor_passes(
    hits: int, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = _run(tmp_path, _log(hits), capsys)
    assert code == 0 and out.splitlines()[-1] == "retrieval-gate: ok"


@pytest.mark.parametrize(
    "text",
    [_log(16), _log(20, dropped=1), _log(20, method="hybrid")],
    ids=["below the floor", "a question dropped", "another method"],
)
def test_a_run_that_misses_a_check_fails(
    text: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = _run(tmp_path, text, capsys)
    assert code == 1 and out.splitlines()[-1] == "retrieval-gate: failed"


def test_an_incomplete_or_real_data_log_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    good = _log(20)
    for bad in (
        "",
        good.replace("retrieval-eval: ok\n", ""),
        good.replace("questions source: synthetic", "questions source: real"),
        good + "recall@5: 1.000\n",
    ):
        code, out = _run(tmp_path, bad, capsys)
        assert (code, out) == (1, "retrieval-gate: failed (GateInputError)\n")
