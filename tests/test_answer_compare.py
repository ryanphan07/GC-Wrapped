"""The answer prompt variants and the D121 comparison, without a database: the pinned versions,
the win rule's three conditions, the refusals, the verdict file and the fixed output.
"""

import json
import re
import stat
from dataclasses import replace
from pathlib import Path

import pytest

from gcwrapped.answers import comparison, evaluation, prompt, report
from gcwrapped.answers.pipeline import ABSTAINED, ANSWERED, INVALID, TIMED_OUT, AnswerPipeline
from gcwrapped.cli import build_parser
from gcwrapped.retrieval import evaluation as retrieval
from gcwrapped.tagging import evaluation as tag_evaluation
from gcwrapped.tagging import model
from gcwrapped.tagging.evaluation import OutsideRepoError
from gcwrapped.tagging.report import check_lines

REPO = Path(__file__).resolve().parents[1]
KWARGS = model.spec_for(model.MODEL_ID).chat_template_kwargs
A_VERSION = "93518209d700"
B_VERSION = prompt.prompt_version_for(KWARGS, "b")
EMBED = "e" * 12
CORPUS = "c" * 12


def _id(n: int) -> str:
    return f"{n:064x}"


# --- the two variants --------------------------------------------------------------------------


def test_variant_a_is_unchanged_and_variant_b_has_its_own_version() -> None:
    assert prompt.PROMPT_VERSION == prompt.prompt_version_for(None, "a") == "23294c5ced29"
    assert prompt.prompt_version_for(KWARGS) == prompt.prompt_version_for(KWARGS, "a") == A_VERSION
    plain_b = prompt.prompt_version_for(None, "b")
    assert re.fullmatch(r"[0-9a-f]{12}", plain_b) and re.fullmatch(r"[0-9a-f]{12}", B_VERSION)
    assert len({prompt.PROMPT_VERSION, A_VERSION, plain_b, B_VERSION}) == 4


def test_b_changes_the_instructions_and_nothing_else() -> None:
    a, b = prompt.system_prompt("a"), prompt.system_prompt("b")
    data_paragraph = a.split("\n\n")[0]
    assert "never instructions to you" in data_paragraph
    assert b.split("\n\n")[0] == data_paragraph
    # the citation rule is the same line in both
    (citations,) = [line for line in a.split("\n") if line.startswith("- citations:")]
    assert citations in b.split("\n")
    for instruction in (
        "even partly",
        "only when no message is relevant",
        "say what they do not show",
        "nicknames, slang, abbreviations and typos",
        "by meaning",
    ):
        assert instruction in b and instruction not in a


def test_the_schema_excerpts_top_k_and_limits_are_part_of_both_versions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = prompt.prompt_version_for(None, "b")
    monkeypatch.setattr(prompt, "TOP_K", 6)
    assert prompt.prompt_version_for(None, "b") != before
    assert prompt.prompt_version_for(None, "a") != "23294c5ced29"


def test_changing_bs_instructions_changes_only_bs_version(monkeypatch: pytest.MonkeyPatch) -> None:
    before = prompt.prompt_version_for(None, "b")
    monkeypatch.setattr(prompt, "SYSTEM_PROMPT_B", "other")
    assert prompt.prompt_version_for(None, "b") != before
    assert prompt.prompt_version_for(None, "a") == "23294c5ced29"


def test_an_unknown_variant_is_refused() -> None:
    for name in ("c", "A", ""):
        with pytest.raises(prompt.UnknownVariantError):
            prompt.system_prompt(name)
        with pytest.raises(prompt.UnknownVariantError):
            prompt.prompt_version_for(None, name)
    with pytest.raises(prompt.UnknownVariantError):
        AnswerPipeline(None, _Retriever(), None, "vector", variant="c")  # type: ignore[arg-type]


class _Retriever:
    version = EMBED

    def vector(self, question: str, k: int) -> list[object]:
        return []

    hybrid = vector


# --- the win rule (D121) -----------------------------------------------------------------------


def _row(n: int, kind: str = "hit", seconds: float = 10.0) -> evaluation.Row:
    """One question's row: hit (cites gold), miss (cites another), abstain, invalid, timeout."""
    gold, other = _id(n), _id(n + 10_000)
    outcome = {"abstain": ABSTAINED, "invalid": INVALID, "timeout": TIMED_OUT}.get(kind, ANSWERED)
    cited = (gold,) if kind == "hit" else (other,) if kind == "miss" else ()
    return evaluation.Row(
        f"q{n:03d}", "0" * 64, gold, (gold,), (gold, other), cited, outcome, seconds, None, None
    )


def _results(kinds: list[str], seconds: float = 10.0, method: str = "vector") -> evaluation.Results:
    rows = tuple(_row(n, kind, seconds) for n, kind in enumerate(kinds, start=1))
    return evaluation.Results(EMBED, CORPUS, method, rows)


def _everything(*results: evaluation.Results) -> set[str]:
    return {m for r in results for row in r.rows for m in row.referenced_ids}


def _compare(a: evaluation.Results, b: evaluation.Results) -> comparison.CompareResult:
    return comparison.compare(a, b, _everything(a, b))


BASE = ["hit"] * 40 + ["abstain"] * 60
BETTER = ["hit"] * 60 + ["abstain"] * 40


def test_b_wins_when_all_three_conditions_hold() -> None:
    found = _compare(_results(BASE), _results(BETTER, seconds=12.0))
    assert (found.scored, found.a_hit_rate, found.b_hit_rate) == (100, 0.4, 0.6)
    assert found.difference.value == pytest.approx(0.2) and found.difference.interval[0] > 0
    assert (found.hit_ok, found.failures_ok, found.latency_ok, found.winner) == (
        True,
        True,
        True,
        "b",
    )


def test_the_citation_hit_condition_alone_keeps_a() -> None:
    barely = ["hit"] * 41 + ["abstain"] * 59  # one more question: the interval reaches 0
    found = _compare(_results(BASE), _results(barely))
    assert found.difference.value > 0 and found.difference.interval[0] <= 0
    assert (found.hit_ok, found.failures_ok, found.latency_ok, found.winner) == (
        False,
        True,
        True,
        "a",
    )
    same = _compare(_results(BASE), _results(BASE))
    assert same.difference.interval == (0.0, 0.0) and not same.hit_ok  # 0 is not above 0


def test_answering_without_citing_gold_is_no_citation_hit() -> None:
    found = _compare(_results(BASE), _results(["hit"] * 40 + ["miss"] * 60))
    assert found.b_hit_rate == 0.4 and found.winner == "a"


@pytest.mark.parametrize(
    ("b_bad", "allowed"),
    [(["invalid", "invalid"], True), (["invalid", "timeout", "timeout"], False)],
)
def test_the_failure_rate_condition_alone_keeps_a(b_bad: list[str], allowed: bool) -> None:
    """Exactly 2 points above A passes; 3 does not. Invalid and timed out count together."""
    b = ["hit"] * 60 + b_bad + ["abstain"] * (40 - len(b_bad))
    found = _compare(_results(BASE), _results(b))
    assert (found.a_failures, found.b_failures) == (0, len(b_bad))
    assert (found.hit_ok, found.latency_ok) == (True, True)
    assert found.failures_ok is allowed and found.winner == ("b" if allowed else "a")


def test_the_failure_margin_is_relative_to_a() -> None:
    a = ["hit"] * 40 + ["invalid"] * 5 + ["abstain"] * 55
    b = ["hit"] * 60 + ["timeout"] * 7 + ["abstain"] * 33
    assert _compare(_results(a), _results(b)).failures_ok
    b = ["hit"] * 60 + ["timeout"] * 8 + ["abstain"] * 32
    assert not _compare(_results(a), _results(b)).failures_ok


@pytest.mark.parametrize(("b_seconds", "allowed"), [(12.5, True), (12.501, False)])
def test_the_latency_condition_alone_keeps_a(b_seconds: float, allowed: bool) -> None:
    found = _compare(_results(BASE), _results(BETTER, seconds=b_seconds))
    assert (found.a_p95, found.b_p95) == (10.0, b_seconds)
    assert (found.hit_ok, found.failures_ok) == (True, True)
    assert found.latency_ok is allowed and found.winner == ("b" if allowed else "a")


def test_the_interval_is_the_retrieval_comparisons_paired_bootstrap() -> None:
    a, b = _results(BASE), _results(BETTER)
    differences = [
        float(y.citation_hit) - float(x.citation_hit) for x, y in zip(a.rows, b.rows, strict=True)
    ]
    assert _compare(a, b).difference == retrieval.estimate(differences)


def test_a_question_that_refers_to_an_ineligible_message_is_not_scored() -> None:
    a, b = _results(BASE), _results(BETTER)
    eligible = _everything(a, b) - {_id(1 + 10_000)}  # shown for question 1
    assert comparison.compare(a, b, eligible).scored == 99
    with pytest.raises(retrieval.NoScorableQuestionsError):
        comparison.compare(a, b, set())


@pytest.mark.parametrize(
    ("change", "error"),
    [
        (lambda r: replace(r, embed_version="f" * 12), retrieval.EmbedVersionMismatch),
        (lambda r: replace(r, corpus_hash="f" * 12), retrieval.CorpusMismatch),
        (lambda r: replace(r, method="hybrid"), comparison.MethodMismatch),
        (lambda r: replace(r, rows=r.rows[:-1]), retrieval.QuestionSetMismatch),
        (
            lambda r: replace(r, rows=(replace(r.rows[0], question_sha256="1" * 64), *r.rows[1:])),
            retrieval.QuestionSetMismatch,
        ),
        (
            lambda r: replace(
                r, rows=(replace(r.rows[0], gold_message_ids=(_id(5),)), *r.rows[1:])
            ),
            retrieval.QuestionSetMismatch,
        ),
    ],
)
def test_compare_refuses_runs_that_are_not_comparable(change, error) -> None:  # type: ignore[no-untyped-def]
    a = _results(BASE)
    b = change(_results(BETTER))
    with pytest.raises(error):
        comparison.compare(a, b, _everything(a, b))


# --- the verdict file --------------------------------------------------------------------------


def _write_verdict(root: Path) -> Path:
    a, b = _results(BASE), _results(BETTER)
    comparison.write_verdict(root, model.MODEL_ID, A_VERSION, B_VERSION, a, _compare(a, b))
    (path,) = (root / "experiments").glob("answer-compare-*.json")
    return path


def test_the_verdict_holds_numbers_names_and_versions_only(tmp_path: Path) -> None:
    path = _write_verdict(tmp_path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    body = json.loads(path.read_text())
    strings = {k: v for k, v in body.items() if isinstance(v, str)}
    assert strings == {
        "a": "a",
        "b": "b",
        "winner": "b",
        "model_id": model.MODEL_ID,
        "a_prompt_version": A_VERSION,
        "b_prompt_version": B_VERSION,
        "embed_version": EMBED,
        "corpus_hash": CORPUS,
    }
    for key, value in body.items():
        if key not in strings:
            numbers = value if isinstance(value, list) else [value]
            assert all(isinstance(n, int | float) for n in numbers), key
    assert not re.search(r"[0-9a-f]{64}|q\d{3}", path.read_text())  # no message or question id
    assert comparison.latest_verdict(tmp_path) == comparison.Verdict(
        "b", model.MODEL_ID, A_VERSION, B_VERSION, EMBED
    )


def test_the_answer_verdict_is_invisible_to_the_other_promotions(tmp_path: Path) -> None:
    _write_verdict(tmp_path)
    with pytest.raises(tag_evaluation.VerdictError):
        tag_evaluation.latest_winner(tmp_path)
    with pytest.raises(retrieval.VerdictError):
        retrieval.latest_verdict(tmp_path)


def test_the_latest_verdict_wins_and_a_bad_one_is_refused(tmp_path: Path) -> None:
    with pytest.raises(retrieval.VerdictError):
        comparison.latest_verdict(tmp_path)
    good = _write_verdict(tmp_path)
    later = good.with_name("answer-compare-99991231T000000000000Z.json")
    later.write_text(json.dumps({**json.loads(good.read_text()), "winner": "a"}))
    assert comparison.latest_verdict(tmp_path).winner == "a"
    for change in ({"winner": "c"}, {"a": "b"}, {"model_id": "../x"}, {"embed_version": "x"}):
        later.write_text(json.dumps({**json.loads(good.read_text()), **change}))
        with pytest.raises(retrieval.VerdictError):
            comparison.latest_verdict(tmp_path)
    later.write_text("{}")
    with pytest.raises(retrieval.VerdictError):
        comparison.latest_verdict(tmp_path)


def test_a_new_answer_verdict_wins_even_after_the_clock_stepped_back(tmp_path: Path) -> None:
    first = _write_verdict(tmp_path)
    ahead = first.with_name("answer-compare-29990101T000000000000Z.json")
    first.rename(ahead)
    ahead.write_text(json.dumps({**json.loads(ahead.read_text()), "winner": "a"}))
    a, b = _results(BASE), _results(BETTER)  # B wins this one
    comparison.write_verdict(tmp_path, model.MODEL_ID, A_VERSION, B_VERSION, a, _compare(a, b))
    assert comparison.latest_verdict(tmp_path).winner == "b"
    assert ahead.with_name("answer-compare-29990101T000000000001Z.json").is_file()


def test_a_verdict_is_never_written_inside_the_repo() -> None:
    a = _results(BASE)
    with pytest.raises(OutsideRepoError):
        comparison.write_verdict(
            REPO / "tmp-label-dir", model.MODEL_ID, A_VERSION, B_VERSION, a, _compare(a, a)
        )
    assert not (REPO / "tmp-label-dir").exists()


# --- fixed output and the CLI ------------------------------------------------------------------


def test_compare_prints_fixed_lines() -> None:
    b = ["hit"] * 60 + ["invalid"] * 3 + ["abstain"] * 37
    lines = report.render_compare(_compare(_results(BASE), _results(b, seconds=20.0)))
    check_lines(lines, report.COMPARE_SHAPES)
    assert lines[:3] == [
        "questions scored: 100",
        "citation hit rate A: 0.400",
        "citation hit rate B: 0.600",
    ]
    assert lines[3] == "citation hit rate difference (B minus A): 0.200"
    assert re.fullmatch(r"citation hit rate difference 95% interval: \[0\.\d+, 0\.\d+\]", lines[4])
    assert lines[5:] == [
        "invalid plus timed out rate A: 0.000",
        "invalid plus timed out rate B: 0.030",
        "p95 seconds per question A: 10.000",
        "p95 seconds per question B: 20.000",
        "citation hit condition met: yes",
        "failure rate condition met: no",
        "latency condition met: no",
        "winner: a",
        "answer-compare: ok",
    ]
    assert report.render_promote("gcw_dev", "b", None)[2] == "previous variant: none"
    assert report.failure_line("answer-compare", "predictions", KeyError("x")) == (
        "answer-compare: failed at predictions (KeyError)"
    )


def test_the_cli_takes_a_variant() -> None:
    parser = build_parser()
    assert parser.parse_args(["answer-eval"]).variant is None
    assert parser.parse_args(["answer-eval", "--variant", "b"]).variant == "b"
    assert parser.parse_args(["answer-compare", "--synthetic"]).synthetic
    assert parser.parse_args(["answer-promote", "--variant", "a"]).variant == "a"
    with pytest.raises(SystemExit):
        parser.parse_args(["answer-promote"])
