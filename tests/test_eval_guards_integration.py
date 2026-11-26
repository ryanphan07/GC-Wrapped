"""The D126 checks inside the commands, against gcw_test and the stub servers: a server with
another model gets no text, and a swapping machine stores no eval result.

Docker and the host's memory are the external boundaries (conftest's fakes); everything else runs
for real, on test_retrieval_integration's warehouse and questions.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from conftest import meminfo_text
from embedding_stub import StubEmbed
from gcwrapped import eval_all, memory
from gcwrapped.answers import commands as answers
from gcwrapped.dashboard import ask
from gcwrapped.dashboard import services as dashboard_services
from gcwrapped.retrieval import commands as retrieval
from gcwrapped.tagging import commands as tagging
from gcwrapped.tagging import model
from gcwrapped.tagging.llamacpp import ServedModelMismatch
from tagging_stub import StubLlm
from test_answers import ScriptedTerminal
from test_answers_integration import NO_SERVICES, embedded, llm
from test_grading import KeyTerminal
from test_retrieval_integration import label_dir, stub, warehouse

__all__ = ["embedded", "label_dir", "llm", "stub", "warehouse"]  # fixtures, used by name

pytestmark = pytest.mark.integration

LIMIT = 256 * 1024
Command = Callable[[], int]


def _ask_command() -> int:
    return answers.run_ask(terminal=ScriptedTerminal("when is the midterm"), services=NO_SERVICES)


LLM_COMMANDS: list[tuple[str, Command]] = [
    ("tag", lambda: tagging.run_tag(start_db=None, start_llm=None)),
    ("answer-eval", lambda: answers.run_answer_eval(services=NO_SERVICES)),
    ("ask", _ask_command),
    (
        "grade-answers",
        lambda: answers.run_grade_answers(terminal=KeyTerminal(), services=NO_SERVICES),
    ),
    ("dashboard-up", lambda: dashboard_services.run(NO_SERVICES)),
]
EMBED_COMMANDS: list[tuple[str, Command]] = [
    ("embed", lambda: retrieval.run_embed(start_db=None, start_service=None)),
    (
        "retrieval-eval",
        lambda: retrieval.run_retrieval_eval("vector", start_db=None, start_service=None),
    ),
    *LLM_COMMANDS[1:],
]


def _stored(directory: Path) -> set[str]:
    return {p.name for p in directory.rglob("*") if p.is_file()}


@pytest.mark.parametrize(("command", "run"), LLM_COMMANDS)
def test_another_model_on_the_llm_port_gets_nothing(
    embedded: str,
    label_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    command: str,
    run: Command,
) -> None:
    before = _stored(label_dir)
    with StubLlm(served=model.SMALL_MODEL_ID) as other:
        monkeypatch.setenv("GCW_LLM_URL", other.url)
        assert run() == 1
    assert capsys.readouterr().out == f"{command}: failed at model (ServedModelMismatch)\n"
    assert other.requests == [] and _stored(label_dir) == before


@pytest.mark.parametrize(("command", "run"), EMBED_COMMANDS)
def test_another_model_on_the_embed_port_gets_nothing(
    embedded: str,
    llm: StubLlm,
    stub: StubEmbed,
    label_dir: Path,
    capsys: pytest.CaptureFixture[str],
    command: str,
    run: Command,
) -> None:
    before = _stored(label_dir)
    stub.served, sent = "another-model", len(stub.requests)
    assert run() == 1
    assert capsys.readouterr().out == f"{command}: failed at model (ServedModelMismatch)\n"
    assert len(stub.requests) == sent and llm.requests == [] and _stored(label_dir) == before


@pytest.mark.parametrize("service", ["llm", "embed"])
def test_every_ask_page_question_checks_both_servers(
    embedded: str, llm: StubLlm, stub: StubEmbed, service: str
) -> None:
    assert ask.answer_question("when is the midterm").outcome == "answered"
    asked, embeds = len(llm.requests), len(stub.requests)
    server: Any = llm if service == "llm" else stub
    server.served = "another-model"  # the server changed under a running page
    with pytest.raises(ServedModelMismatch):
        ask.answer_question("when is the midterm")
    assert (len(llm.requests), len(stub.requests)) == (asked, embeds)


# --- the memory guard --------------------------------------------------------------------------


class Meminfo:
    """/proc/meminfo as a sequence of readings, one per read; the last one repeats."""

    def __init__(self, *swap_used_kib: int) -> None:
        self._left = list(swap_used_kib)

    def read_text(self, encoding: str) -> str:
        return meminfo_text(self._left.pop(0) if len(self._left) > 1 else self._left[0])


def _swap(monkeypatch: pytest.MonkeyPatch, *swap_used_kib: int) -> None:
    monkeypatch.setattr(memory, "MEMINFO", Meminfo(*swap_used_kib))


EVALS: list[tuple[str, Command]] = [
    ("answer-eval", lambda: answers.run_answer_eval(services=NO_SERVICES)),
    (
        "retrieval-eval",
        lambda: retrieval.run_retrieval_eval("vector", start_db=None, start_service=None),
    ),
]


@pytest.mark.parametrize(("command", "run"), EVALS)
def test_a_pass_on_a_swapping_machine_is_refused_before_any_question(
    embedded: str,
    llm: StubLlm,
    stub: StubEmbed,
    label_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    command: str,
    run: Command,
) -> None:
    _swap(monkeypatch, LIMIT + 1)
    before, sent = _stored(label_dir), len(stub.requests)
    assert run() == 1
    assert capsys.readouterr().out.splitlines() == [
        "available memory at start (MiB): 4096",
        "swap in use at start (MiB): 257",
        f"{command}: failed at memory (SwapInUse)",
    ]
    assert (llm.requests, len(stub.requests), _stored(label_dir)) == ([], sent, before)


@pytest.mark.parametrize(("command", "run"), EVALS)
def test_a_pass_during_which_swap_grew_stores_nothing(
    embedded: str,
    llm: StubLlm,
    label_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    command: str,
    run: Command,
) -> None:
    _swap(monkeypatch, 0, LIMIT + 1)
    before = _stored(label_dir)
    assert run() == 1
    lines = capsys.readouterr().out.splitlines()
    assert lines[-3:] == [
        "available memory at end (MiB): 4096",
        "swap in use at end (MiB): 257",
        f"{command}: failed at memory (SwapGrew)",
    ]
    assert len(lines) == 5 and _stored(label_dir) == before


@pytest.mark.parametrize(
    ("readings", "failed_step", "failure"),
    [
        # one start and one end reading per pass: the two retrieval passes, then answer a and b
        ((0, LIMIT + 1), "retrieval-eval vector", "retrieval-eval: failed at memory (SwapGrew)"),
        ((0,) * 7 + (LIMIT + 1,), "answer-eval b", "answer-eval: failed at memory (SwapGrew)"),
        ((0,) * 6 + (LIMIT + 1,), "answer-eval b", "answer-eval: failed at memory (SwapInUse)"),
    ],
)
def test_eval_all_stops_before_any_compare_or_promote_of_a_tripped_pass(
    warehouse: str,
    stub: StubEmbed,
    llm: StubLlm,
    label_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    readings: tuple[int, ...],
    failed_step: str,
    failure: str,
) -> None:
    _swap(monkeypatch, *readings)
    assert eval_all.run(services=NO_SERVICES) == 1
    lines = capsys.readouterr().out.splitlines()
    assert lines[-1] == failure
    assert [line for line in lines if line.startswith("== ")][-1] == f"== {failed_step} =="
    verdicts = _stored(label_dir)
    assert not any(name.startswith("answer-compare") for name in verdicts)
    if failed_step.startswith("retrieval"):
        assert not any(name.startswith("retrieval-compare") for name in verdicts)


def test_every_answer_eval_pass_gets_a_new_llm_server_and_ask_does_not(
    embedded: str, llm: StubLlm, label_dir: Path
) -> None:
    started: list[bool] = []

    def start(model_id: str, *, fresh: bool = False) -> None:
        started.append(fresh)

    services = answers.Services(db=None, llm=start, embed=None)
    for variant in ("a", "b"):
        assert answers.run_answer_eval(services=services, variant=variant) == 0
    assert answers.run_ask(terminal=ScriptedTerminal(), services=services) == 0
    assert started == [True, True, False]
