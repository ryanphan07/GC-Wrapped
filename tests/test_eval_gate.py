"""The CI eval gate script (D100): floors, shape checks and the committed floors file."""

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from gcwrapped.tagging import model

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "eval_gate.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("eval_gate", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gate = _load_script()
TINY = model.TINY_MODEL_ID


def _log(topic: str, sentiment: str, model_id: str = TINY, ok: bool = True) -> str:
    lines = [
        "target database: gcw_dev",
        f"model id: {model_id}",
        f"topic accuracy: {topic}",
        "topic accuracy 95% interval: [0.500, 0.700]",
        f"sentiment accuracy: {sentiment}",
        "sentiment accuracy 95% interval: [0.500, 0.700]",
    ]
    return "\n".join([*lines, "tag-eval: ok"] if ok else lines) + "\n"


def _run(tmp_path: Path, text: str, capsys: pytest.CaptureFixture[str]) -> tuple[int, str]:
    log = tmp_path / "eval.log"
    log.write_text(text, encoding="utf-8")
    code = gate.main(["eval_gate.py", str(log)])
    return code, capsys.readouterr().out


def test_the_committed_floors_name_the_tiny_model_and_sit_above_chance() -> None:
    floors = gate.load_floors()
    assert floors["model"] == TINY
    assert float(floors["topic"]) >= 0.10 + 0.15  # D100: a gate at chance is meaningless
    assert float(floors["sentiment"]) >= 0.33 + 0.15
    for value in (floors["topic"], floors["sentiment"]):
        assert round(float(value) * 20, 9) == round(float(value) * 20)  # a multiple of 0.05


def test_a_run_at_or_above_both_floors_passes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    floors = gate.load_floors()
    at_floor = _log(f"{floors['topic']:.3f}", f"{floors['sentiment']:.3f}")
    code, out = _run(tmp_path, at_floor, capsys)
    assert code == 0 and out.splitlines()[-1] == "eval-gate: ok"


@pytest.mark.parametrize(("topic", "sentiment"), [("0.100", "0.900"), ("0.900", "0.100")])
def test_a_run_below_either_floor_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], topic: str, sentiment: str
) -> None:
    code, out = _run(tmp_path, _log(topic, sentiment), capsys)
    assert code == 1
    assert out.splitlines()[-1] == "eval-gate: failed"
    assert "fail" in out


def test_another_model_or_an_incomplete_log_fails(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out = _run(tmp_path, _log("0.900", "0.900", model_id=model.MODEL_ID), capsys)
    assert code == 1 and "check model is the floors' model: fail" in out
    code, out = _run(tmp_path, _log("0.900", "0.900", ok=False), capsys)
    assert code == 1 and out == "eval-gate: failed (GateInputError)\n"
    code, out = _run(tmp_path, "", capsys)
    assert code == 1 and out == "eval-gate: failed (GateInputError)\n"


def test_a_missing_log_fails_with_the_class_name_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = gate.main(["eval_gate.py", str(tmp_path / "missing.log")])
    assert code == 1
    assert capsys.readouterr().out == "eval-gate: failed (FileNotFoundError)\n"


def test_the_floors_file_has_exactly_the_expected_keys(tmp_path: Path) -> None:
    bad = tmp_path / "floors.json"
    bad.write_text(json.dumps({"model": TINY, "topic_accuracy": 0.5}), encoding="utf-8")
    with pytest.raises(gate.GateInputError):
        gate.load_floors(bad)
