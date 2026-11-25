"""`make eval-all [SYNTHETIC=1]` (D122): every eval, in order, stopping at the first failure.

This module only orders the commands. Each step is the command's own `run_*` function, so its
guards, its output and its failure line are the ones it has when run alone; a header line names
each step. The promote steps take the winner straight from the comparison that just ran, and a
synthetic run has none (no synthetic promotion exists).
"""

from collections.abc import Callable

from gcwrapped.answers import commands as answers
from gcwrapped.answers.prompt import VARIANT_A, VARIANT_B
from gcwrapped.retrieval import commands as retrieval_commands
from gcwrapped.retrieval.evaluation import HYBRID, VECTOR


def header(step: str) -> str:
    return f"== {step} =="


def run(synthetic: bool = False, services: answers.Services | None = None) -> int:
    """0 if every step succeeded; else the failing step's status, with nothing run after it."""
    services = services or answers.Services()
    db, embed = services.db, services.embed
    method: list[str] = []  # the winner of retrieval-compare, once it has run
    variant: list[str] = []  # the winner of answer-compare

    def retrieval_eval(name: str) -> int:
        return retrieval_commands.run_retrieval_eval(name, synthetic, db, embed)

    def answer_eval(name: str) -> int:
        return answers.run_answer_eval(synthetic, services, name)

    steps: list[tuple[str, Callable[[], int]]] = [
        ("embed", lambda: retrieval_commands.run_embed(synthetic, db, embed)),
        (f"retrieval-eval {VECTOR}", lambda: retrieval_eval(VECTOR)),
        (f"retrieval-eval {HYBRID}", lambda: retrieval_eval(HYBRID)),
        (
            "retrieval-compare",
            lambda: retrieval_commands.run_retrieval_compare(
                VECTOR, HYBRID, synthetic, db, on_verdict=method.append
            ),
        ),
    ]
    if not synthetic:
        steps.append(
            ("retrieval-promote", lambda: retrieval_commands.run_retrieval_promote(method[0], db))
        )
    steps += [
        (f"answer-eval {VARIANT_A}", lambda: answer_eval(VARIANT_A)),
        (f"answer-eval {VARIANT_B}", lambda: answer_eval(VARIANT_B)),
        (
            "answer-compare",
            lambda: answers.run_answer_compare(synthetic, db, on_verdict=variant.append),
        ),
    ]
    if not synthetic:
        steps.append(("answer-promote", lambda: answers.run_answer_promote(variant[0], db)))

    for name, step in steps:
        print(header(name), flush=True)  # before a step that can take most of an hour
        status = step()
        if status != 0:
            return status
    return 0
