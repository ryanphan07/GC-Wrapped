"""The tagging eval (D92, D93): scoring, bootstrap, the win rule, files and fixed output.

Pure and file tests only; nothing here needs Postgres, docker or a model. The command-level tests
against a database are in test_tag_eval_integration.py. All data is synthetic.
"""

import hashlib
import json
import math
import re
import stat
import time
from collections.abc import Sequence
from pathlib import Path

import pytest

from gcwrapped.labeling.schema import SENTIMENTS, TOPICS
from gcwrapped.ledger import REPO_ROOT
from gcwrapped.tagging import commands, evaluation, model, report, scoring
from gcwrapped.tagging.evaluation import PredictionRow

REPO = Path(__file__).resolve().parents[1]
PROMPT = "0123456789ab"
A_ID = model.MODEL_ID
B_ID = model.SMALL_MODEL_ID


def _id(n: int) -> str:
    return hashlib.sha256(f"msg{n}".encode()).hexdigest()


# --- accuracy and macro-F1 on a hand-computed case ---------------------------------------------

GOLD = ["a", "a", "b", "b", "c"]
PRED: list[str | None] = ["a", "b", "b", None, "c"]


def test_accuracy_counts_a_failure_as_wrong() -> None:
    assert scoring.accuracy(GOLD, PRED) == pytest.approx(3 / 5)


def test_per_class_f1_on_a_known_case() -> None:
    # a: tp 1, fn 1 -> 2/3. b: tp 1, fn 1 (the failure), fp 1 -> 1/2. c: tp 1 -> 1.
    scores = scoring.class_f1(GOLD, PRED)
    assert scores == pytest.approx({"a": 2 / 3, "b": 1 / 2, "c": 1.0})
    assert scoring.macro_f1(GOLD, PRED) == pytest.approx((2 / 3 + 1 / 2 + 1) / 3)


def test_a_predicted_class_with_no_gold_item_scores_zero_and_counts_in_the_macro() -> None:
    assert scoring.class_f1(["a"], ["z"]) == {"a": 0.0, "z": 0.0}
    assert scoring.macro_f1(["a"], ["z"]) == 0.0


def test_a_failure_adds_no_false_positive() -> None:
    assert scoring.class_f1(["a", "b"], [None, "b"]) == pytest.approx({"a": 0.0, "b": 1.0})


def test_perfect_predictions_score_one_everywhere() -> None:
    gold = ["a", "b", "a"]
    assert scoring.accuracy(gold, gold) == 1.0
    assert scoring.macro_f1(gold, gold) == 1.0


def test_metrics_respect_the_selected_indices() -> None:
    assert scoring.accuracy(GOLD, PRED, [0, 0, 2]) == 1.0
    assert scoring.accuracy(GOLD, PRED, [1, 3]) == 0.0


def test_messages_per_second_is_attempted_over_summed_seconds() -> None:
    assert scoring.messages_per_second([0.5, 0.5, 1.0]) == 1.5
    assert scoring.messages_per_second([]) == 0.0
    assert scoring.messages_per_second([0.0]) == 0.0


# --- bootstrap ---------------------------------------------------------------------------------


def test_the_bootstrap_resamples_the_fixed_number_of_times_with_n_indices_each() -> None:
    sizes: list[int] = []

    def stat_fn(indices: Sequence[int]) -> float:
        sizes.append(len(indices))
        return 0.0

    scoring.bootstrap_interval(stat_fn, 7)
    assert sizes == [7] * scoring.RESAMPLES == [7] * 2000


def test_a_fixed_seed_gives_identical_bounds_twice() -> None:
    values = [math.sqrt(n) for n in range(30)]  # distinct floats, so a different seed shows

    def mean(i: Sequence[int]) -> float:
        return sum(values[j] for j in i) / len(i)

    first = scoring.bootstrap_interval(mean, len(values))
    assert scoring.bootstrap_interval(mean, len(values)) == first
    assert first != scoring.bootstrap_interval(mean, len(values), seed=scoring.SEED + 1)


def test_an_interval_brackets_the_point_estimate() -> None:
    gold = ["a", "b"] * 50
    pred = ["a", "b"] * 40 + ["b", "a"] * 10
    low, high = scoring.bootstrap_interval(lambda i: scoring.accuracy(gold, pred, i), 100)
    assert low <= scoring.accuracy(gold, pred) <= high
    assert 0.0 < low < high < 1.0


def test_all_correct_gives_the_degenerate_interval_one_one() -> None:
    gold = ["a", "b", "a"]
    assert scoring.bootstrap_interval(lambda i: scoring.accuracy(gold, gold, i), 3) == (1.0, 1.0)


def test_two_items_one_correct_has_the_full_range() -> None:
    # Each resample is 0, 1/2 or 1 correct with probability 1/4, 1/2, 1/4, so both 2.5% tails are
    # the extremes.
    interval = scoring.bootstrap_interval(lambda i: scoring.accuracy(["a", "b"], ["a", "x"], i), 2)
    assert interval == (0.0, 1.0)


def _rows(
    topics: list[str | None], sentiments: list[str | None], seconds: float
) -> dict[str, PredictionRow]:
    return {
        _id(n): PredictionRow(_id(n), t, s, seconds)
        for n, (t, s) in enumerate(zip(topics, sentiments, strict=True))
    }


def _labels(n: int) -> dict[str, tuple[str, str]]:
    return {_id(i): (TOPICS[i % 2], SENTIMENTS[i % 2]) for i in range(n)}


def _perfect(n: int, seconds: float) -> dict[str, PredictionRow]:
    return _rows([TOPICS[i % 2] for i in range(n)], [SENTIMENTS[i % 2] for i in range(n)], seconds)


def _worse(
    n: int, seconds: float, topic_wrong: int = 0, sentiment_wrong: int = 0, failed: int = 0
) -> dict[str, PredictionRow]:
    """Perfect, except the first `topic_wrong` topics, the first `sentiment_wrong` sentiments and
    the last `failed` messages (a failure: no labels at all) are wrong."""
    topics: list[str | None] = [TOPICS[i % 2] if i >= topic_wrong else "other" for i in range(n)]
    sentiments: list[str | None] = [
        SENTIMENTS[i % 2] if i >= sentiment_wrong else "negative" for i in range(n)
    ]
    for i in range(n - failed, n):
        topics[i] = sentiments[i] = None
    return _rows(topics, sentiments, seconds)


def test_identical_predictions_give_a_difference_interval_of_exactly_zero_zero() -> None:
    n = 40
    result = evaluation.compare(_labels(n), _perfect(n, 1.0), _perfect(n, 0.25), set(_labels(n)))
    assert result.topic.difference == 0.0 and result.topic.interval == (0.0, 0.0)
    assert result.sentiment.difference == 0.0 and result.sentiment.interval == (0.0, 0.0)
    noisy = _worse(n, 1.0, topic_wrong=8, sentiment_wrong=5)
    same = evaluation.compare(_labels(n), noisy, dict(noisy), set(_labels(n)))
    assert same.topic.interval == (0.0, 0.0) and same.sentiment.interval == (0.0, 0.0)


# --- the D93 rule at every threshold -----------------------------------------------------------


@pytest.mark.parametrize(
    ("b_mps", "ok"), [(2.99, False), (3.0, True), (3.01, True), (0.0, False), (1.0, False)]
)
def test_speed_threshold_is_one_and_a_half_times(b_mps: float, ok: bool) -> None:
    assert scoring.decide(2.0, b_mps, 0.0, 0.0).speed_ok is ok


def test_speed_needs_a_measured_a() -> None:
    assert scoring.decide(0.0, 5.0, 0.0, 0.0).speed_ok is False


@pytest.mark.parametrize(
    ("lower", "ok"),
    [(-0.0501, False), (-0.05000000000000004, True), (-0.05, True), (-0.049, True), (0.2, True)],
)
def test_topic_and_sentiment_thresholds_are_minus_five_points(lower: float, ok: bool) -> None:
    assert scoring.decide(1.0, 2.0, lower, 0.0).topic_ok is ok
    assert scoring.decide(1.0, 2.0, 0.0, lower).sentiment_ok is ok


def test_b_wins_only_if_all_three_checks_pass() -> None:
    assert scoring.decide(1.0, 1.5, -0.05, -0.05).b_wins
    assert not scoring.decide(1.0, 1.49, 0.0, 0.0).b_wins  # too slow
    assert not scoring.decide(1.0, 2.0, -0.06, 0.0).b_wins  # topic regressed
    assert not scoring.decide(1.0, 2.0, 0.0, -0.06).b_wins  # sentiment regressed


def _compare(
    b: dict[str, PredictionRow], n: int = 100, a: dict[str, PredictionRow] | None = None
) -> evaluation.CompareResult:
    return evaluation.compare(_labels(n), a or _perfect(n, 1.0), b, set(_labels(n)))


def test_constructed_predictions_a_faster_and_equally_good_b_wins() -> None:
    result = _compare(_perfect(100, 0.5))
    assert result.speedup == 2.0
    assert result.verdict.b_wins


def test_constructed_predictions_a_b_that_is_not_fast_enough_loses() -> None:
    result = _compare(_perfect(100, 0.9))  # 1.11 times faster
    assert result.verdict.topic_ok and result.verdict.sentiment_ok
    assert not result.verdict.speed_ok and not result.verdict.b_wins


def test_constructed_predictions_a_much_worse_topic_loses_even_if_fast() -> None:
    result = _compare(_worse(100, 0.5, topic_wrong=20))
    assert result.topic.difference == pytest.approx(-0.2)
    assert result.topic.interval[0] < scoring.MARGIN
    assert result.verdict.speed_ok and result.verdict.sentiment_ok
    assert not result.verdict.topic_ok and not result.verdict.b_wins


def test_constructed_predictions_a_much_worse_sentiment_loses_even_if_fast() -> None:
    result = _compare(_worse(100, 0.5, sentiment_wrong=20))
    assert result.sentiment.interval[0] < scoring.MARGIN
    assert result.verdict.topic_ok and not result.verdict.sentiment_ok
    assert not result.verdict.b_wins


def test_constructed_predictions_a_tiny_regression_is_inside_the_margin() -> None:
    result = _compare(_worse(100, 0.5, topic_wrong=1, sentiment_wrong=1))
    assert result.topic.difference == pytest.approx(-0.01)
    assert scoring.MARGIN <= result.topic.interval[0] < 0.0
    assert result.verdict.b_wins


def test_constructed_predictions_a_failure_counts_as_wrong_in_the_comparison() -> None:
    result = _compare(_worse(100, 0.5, failed=20))
    assert result.topic.b == pytest.approx(0.8) and result.sentiment.b == pytest.approx(0.8)
    assert not result.verdict.b_wins


def test_a_better_b_that_is_not_faster_still_loses() -> None:
    weak_a = _worse(100, 1.0, topic_wrong=20)
    result = _compare(_perfect(100, 1.0), a=weak_a)
    assert result.topic.difference == pytest.approx(0.2) and not result.verdict.b_wins


def test_compare_uses_only_messages_labeled_eligible_and_predicted_by_both() -> None:
    labels = _labels(100)
    rows_b = _perfect(100, 0.5)
    for n in range(10):
        del rows_b[_id(n)]  # B never predicted these
    eligible = {_id(n) for n in range(100)} - {_id(n) for n in range(95, 100)}  # consent revoked
    result = evaluation.compare(labels, _perfect(100, 1.0), rows_b, eligible)
    assert result.messages == 85


def test_compare_with_nothing_in_common_is_an_error() -> None:
    with pytest.raises(evaluation.NoScorableLabelsError):
        evaluation.compare(_labels(5), _perfect(5, 1.0), {}, set(_labels(5)))
    with pytest.raises(evaluation.NoScorableLabelsError):
        evaluation.compare(_labels(5), _perfect(5, 1.0), _perfect(5, 1.0), set())


def test_evaluate_drops_labels_that_are_no_longer_eligible_and_counts_failures() -> None:
    labels = _labels(10)
    rows = _worse(10, 1.0, failed=2)
    eligible = {_id(n) for n in range(2, 10)}  # the first two labels are dropped
    result = evaluation.evaluate(labels, rows, eligible)
    assert (result.labels, result.dropped, result.scored) == (10, 2, 8)
    assert result.failures == 2 and result.topic.accuracy == pytest.approx(6 / 8)
    with pytest.raises(evaluation.NoScorableLabelsError):
        evaluation.evaluate(labels, rows, set())


# --- predictions and verdict files -------------------------------------------------------------


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_predictions_round_trip_private_with_no_text(tmp_path: Path) -> None:
    rows = list(_worse(6, 0.25, topic_wrong=1, failed=1).values())
    evaluation.write_predictions(tmp_path, A_ID, PROMPT, rows)

    path = tmp_path / "predictions" / f"{A_ID}__{PROMPT}.csv"
    assert _mode(path) == 0o600 and _mode(path.parent) == 0o700
    assert path.read_text().splitlines()[0] == "message_id,topic,sentiment,seconds"
    assert evaluation.read_predictions(tmp_path, A_ID, PROMPT) == {r.message_id: r for r in rows}
    evaluation.write_predictions(tmp_path, A_ID, PROMPT, rows[:2])  # a rerun replaces the file
    assert len(evaluation.read_predictions(tmp_path, A_ID, PROMPT)) == 2
    assert [p.name for p in path.parent.iterdir()] == [path.name]  # no temp file left behind


@pytest.mark.parametrize(
    "body",
    [
        "message_id,topic,sentiment\n",
        "message_id,topic,sentiment,seconds\nnot-an-id,food,neutral,1.0\n",
        f"message_id,topic,sentiment,seconds\n{_id(1)},nope,neutral,1.0\n",
        f"message_id,topic,sentiment,seconds\n{_id(1)},food,,1.0\n",
        f"message_id,topic,sentiment,seconds\n{_id(1)},food,neutral,-1\n",
        f"message_id,topic,sentiment,seconds\n{_id(1)},food,neutral,nan\n",
        f"message_id,topic,sentiment,seconds\n{_id(1)},food,neutral,abc\n",
        f"message_id,topic,sentiment,seconds\n{_id(1)},food,neutral\n",
        f"message_id,topic,sentiment,seconds\n{_id(1)},food,neutral,1\n{_id(1)},food,neutral,1\n",
    ],
)
def test_malformed_predictions_are_rejected(tmp_path: Path, body: str) -> None:
    folder = tmp_path / "predictions"
    folder.mkdir()
    (folder / f"{A_ID}__{PROMPT}.csv").write_text(body)
    with pytest.raises(evaluation.PredictionsError):
        evaluation.read_predictions(tmp_path, A_ID, PROMPT)


def test_missing_predictions_and_a_hostile_model_id_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(evaluation.PredictionsError):
        evaluation.read_predictions(tmp_path, A_ID, PROMPT)
    with pytest.raises(evaluation.PredictionsError):
        evaluation.read_predictions(tmp_path, "../../etc/passwd", PROMPT)


def test_a_failure_row_has_empty_labels_and_reads_back_as_a_failure(tmp_path: Path) -> None:
    row = PredictionRow(_id(1), None, None, 0.5)
    evaluation.write_predictions(tmp_path, A_ID, PROMPT, [row])
    assert (tmp_path / "predictions" / f"{A_ID}__{PROMPT}.csv").read_text().splitlines()[1] == (
        f"{_id(1)},,,0.5"
    )
    assert evaluation.read_predictions(tmp_path, A_ID, PROMPT)[_id(1)].topic is None


def _write_verdict(root: Path, a: str, b: str, fast: bool) -> None:
    result = _compare(_perfect(100, 0.5 if fast else 1.0))
    evaluation.write_verdict(root, a, b, PROMPT, result)


def test_a_verdict_is_private_numbers_only_and_names_the_winner(tmp_path: Path) -> None:
    _write_verdict(tmp_path, A_ID, B_ID, fast=True)
    (path,) = (tmp_path / "experiments").iterdir()
    assert _mode(path) == 0o600 and re.fullmatch(r"compare-\d{8}T\d{12}Z\.json", path.name)
    body = json.loads(path.read_text())
    assert body["winner"] == B_ID and body["a"] == A_ID and body["messages"] == 100
    assert body["prompt_version"] == PROMPT
    assert evaluation.latest_winner(tmp_path) == B_ID


def test_the_latest_verdict_decides(tmp_path: Path) -> None:
    _write_verdict(tmp_path, A_ID, B_ID, fast=True)
    time.sleep(0.002)
    _write_verdict(tmp_path, A_ID, B_ID, fast=False)
    assert evaluation.latest_winner(tmp_path) == A_ID


def test_no_verdict_or_a_malformed_one_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(evaluation.VerdictError):
        evaluation.latest_winner(tmp_path)
    folder = tmp_path / "experiments"
    folder.mkdir()
    (folder / "compare-20261114T000000000000Z.json").write_text("not json")
    with pytest.raises(evaluation.VerdictError):
        evaluation.latest_winner(tmp_path)
    bad = {"a": A_ID, "b": B_ID, "winner": "someone-else"}
    (folder / "compare-20261114T000000000001Z.json").write_text(json.dumps(bad))
    with pytest.raises(evaluation.VerdictError):
        evaluation.latest_winner(tmp_path)
    (folder / "notes.json").write_text(json.dumps({"a": A_ID, "b": B_ID, "winner": B_ID}))
    with pytest.raises(evaluation.VerdictError):  # other file names are not verdicts
        evaluation.latest_winner(tmp_path)


# --- the outside-the-repo guard ----------------------------------------------------------------


def test_predictions_and_verdicts_are_refused_inside_the_repo(tmp_path: Path) -> None:
    inside = REPO_ROOT / "tests" / "gcw-eval-guard-test"
    link = tmp_path / "link"
    link.symlink_to(REPO_ROOT / "docs")
    for root in (inside, REPO_ROOT, link):
        with pytest.raises(evaluation.OutsideRepoError):
            evaluation.write_predictions(root, A_ID, PROMPT, list(_perfect(3, 1.0).values()))
        with pytest.raises(evaluation.OutsideRepoError):
            _write_verdict(root, A_ID, B_ID, fast=True)
    assert not inside.exists()
    assert not (REPO_ROOT / "docs" / "predictions").exists()
    assert not (REPO_ROOT / "predictions").exists() and not (REPO_ROOT / "experiments").exists()


def test_the_synthetic_directory_is_temporary_private_and_never_the_label_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    monkeypatch.setenv("GCW_LABEL_DIR", str(tmp_path / "labels"))
    path = commands.synthetic_dir()
    assert path.parent == tmp_path.resolve() and _mode(path) == 0o700
    assert commands.synthetic_dir() == path  # shared between tag-eval and tag-compare runs
    assert not (tmp_path / "labels").exists()


def test_the_synthetic_directory_refuses_a_temp_dir_inside_the_repo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inside = REPO_ROOT / "tests" / "fixtures"
    monkeypatch.setattr("tempfile.tempdir", str(inside))
    with pytest.raises(evaluation.OutsideRepoError):
        commands.synthetic_dir()
    assert not list(inside.glob("gcw-synthetic-eval-*"))  # refused before anything was created


# --- fixed output ------------------------------------------------------------------------------


def _eval_result() -> evaluation.EvalResult:
    return evaluation.evaluate(
        _labels(20), _worse(20, 0.5, topic_wrong=4, failed=1), set(_labels(20))
    )


def test_the_eval_lines_have_the_fixed_shape() -> None:
    lines = report.render_eval("gcw_dev", "synthetic", A_ID, PROMPT, _eval_result())
    report.check_lines(lines, report.EVAL_SHAPES)
    assert lines[:4] == [
        "target database: gcw_dev",
        "labels source: synthetic",
        f"model id: {A_ID}",
        f"prompt version: {PROMPT}",
    ]
    assert "labels: 20" in lines and "failures: 1" in lines and lines[-1] == "tag-eval: ok"
    assert any(
        re.fullmatch(r"topic accuracy 95% interval: \[\d\.\d{3}, \d\.\d{3}\]", ln) for ln in lines
    )
    # a class that appears in neither the labels nor the predictions has no F1
    assert "topic F1 (banter): n/a" in lines


def test_the_compare_lines_have_the_fixed_shape_and_name_the_winner() -> None:
    fast = report.render_compare(
        "gcw_dev", "synthetic", PROMPT, A_ID, B_ID, _compare(_perfect(100, 0.5))
    )
    slow = report.render_compare(
        "gcw_dev", "synthetic", PROMPT, A_ID, B_ID, _compare(_perfect(100, 1.0))
    )
    report.check_lines(fast, report.COMPARE_SHAPES)
    assert fast[-2] == f"winner: {B_ID}" and slow[-2] == f"winner: {A_ID}"
    assert "check speedup at least 1.5: pass" in fast and "check speedup at least 1.5: fail" in slow
    assert "speedup (B over A): 2.00" in fast


def test_the_promote_lines_have_the_fixed_shape() -> None:
    lines = report.render_promote("gcw_dev", B_ID, None)
    report.check_lines(lines, report.PROMOTE_SHAPES)
    assert lines[2] == "previous model id: none"
    assert report.render_promote("gcw_dev", B_ID, A_ID)[2] == f"previous model id: {A_ID}"


def test_a_line_that_could_carry_text_does_not_pass_the_shape_check() -> None:
    lines = report.render_eval("gcw_dev", "synthetic", A_ID, PROMPT, _eval_result())
    for bad in (
        "model id: /home/someone/labels",
        f"model id: {_id(1)} extra",
        "failures: SENTINELTEXT",
    ):
        tampered = [bad if ln.split(":")[0] == bad.split(":")[0] else ln for ln in lines]
        with pytest.raises(report.ShapeError):
            report.check_lines(tampered, report.EVAL_SHAPES)
    with pytest.raises(report.ShapeError):
        report.check_lines(lines[:-1], report.EVAL_SHAPES)


def test_failure_lines_name_a_step_and_a_class_only() -> None:
    for command in ("tag-eval", "tag-compare", "tag-promote"):
        for step in ("model", "label directory", "predictions", "scoring", "verdict", "promote"):
            line = report.failure_line(command, step, evaluation.NotWinnerError("SENTINELTEXT"))
            assert line == f"{command}: failed at {step} (NotWinnerError)"
    odd = type("Bad Name!", (Exception,), {})
    assert (
        report.failure_line("tag-eval", "scoring", odd())
        == "tag-eval: failed at scoring (Exception)"
    )


def test_commands_refuse_an_unregistered_model_before_anything_else(
    capsys: pytest.CaptureFixture[str],
) -> None:
    started: list[str] = []
    assert commands.run_tag_eval("nope", start_db=lambda: started.append("db")) == 1
    assert commands.run_tag_compare("nope", B_ID, start_db=lambda: started.append("db")) == 1
    assert commands.run_tag_compare(A_ID, A_ID, start_db=lambda: started.append("db")) == 1
    assert commands.run_tag_promote("nope", start_db=lambda: started.append("db")) == 1
    assert commands.run_llm_model("nope") == 1
    assert capsys.readouterr().out.splitlines() == [
        "tag-eval: failed at model (UnknownModelError)",
        "tag-compare: failed at model (UnknownModelError)",
        "tag-compare: failed at model (ValueError)",
        "tag-promote: failed at model (UnknownModelError)",
        "llm-model: failed at model (UnknownModelError)",
    ]
    assert started == []


# --- registry, compose, dbt var, Makefile ------------------------------------------------------


def test_the_registry_pins_two_distinct_models_with_checked_downloads() -> None:
    assert set(model.REGISTRY) == {A_ID, B_ID}
    assert model.REGISTRY[A_ID] == model.ModelSpec()  # the D90 default
    assert B_ID == "qwen2.5-1.5b-instruct-q4_k_m"
    files = {spec.file for spec in model.REGISTRY.values()}
    assert len(files) == 2
    for model_id, spec in model.REGISTRY.items():
        assert re.fullmatch(r"[A-Za-z0-9._-]+", model_id)
        assert re.fullmatch(r"[0-9a-f]{64}", spec.sha256)
        assert spec.url.startswith("https://") and spec.url.endswith(spec.file)
        assert re.search(r"/resolve/[0-9a-f]{40}/", spec.url)  # an exact commit, not "main"
    with pytest.raises(model.UnknownModelError):
        model.spec_for("nope")


def test_the_llm_service_takes_its_model_file_and_alias_from_the_environment() -> None:
    compose = (REPO / "docker-compose.yml").read_text()
    assert f"${{GCW_LLM_MODEL_ALIAS:-{model.MODEL_ID}}}" in compose
    assert f"${{GCW_LLM_MODEL_FILE:-{model.MODEL_FILE}}}" in compose


def test_start_llm_names_the_model_and_refuses_a_missing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GCW_MODEL_DIR", str(tmp_path))
    with pytest.raises(commands.ModelFileMissingError):
        commands.start_llm(B_ID)
    with pytest.raises(model.UnknownModelError):
        commands.start_llm("nope")


def test_the_dbt_fallback_var_is_the_default_model() -> None:
    text = (REPO / "dbt" / "dbt_project.yml").read_text()
    match = re.search(r"^  gcw_default_tag_model: (\S+)$", text, re.M)
    assert match and match.group(1) == model.MODEL_ID


def test_the_make_targets_pass_their_variables_and_tag_bench_is_gone() -> None:
    from test_makefile_landing import _dry_run

    assert _dry_run("tag-eval", MODEL="m")[-1] == "uv run gcw tag-eval --model m "
    assert _dry_run("tag-eval", MODEL="m", SYNTHETIC="1")[-1] == (
        "uv run gcw tag-eval --model m --synthetic"
    )
    assert _dry_run("tag-compare", A="a", B="b", SYNTHETIC="1")[-1] == (
        "uv run gcw tag-compare --a a --b b --synthetic"
    )
    assert _dry_run("tag-promote", MODEL="m")[-1] == "uv run gcw tag-promote --model m"
    for target in ("tag-eval", "tag-compare", "tag-promote"):
        assert target in (REPO / "Makefile").read_text().split(".PHONY:")[1].split("\n")[0]
    assert "tag-bench" not in (REPO / "Makefile").read_text()
    assert "tag-bench" not in (REPO / "src" / "gcwrapped" / "cli.py").read_text()


def test_make_refuses_a_missing_model_or_arm_without_running_anything() -> None:
    import subprocess

    for args in (["tag-eval"], ["tag-compare", "A=x"], ["tag-promote"]):
        done = subprocess.run(["make", *args], cwd=REPO, capture_output=True, text=True)
        assert done.returncode != 0 and "error: set" in done.stdout


def test_infinite_or_nan_seconds_cannot_enter_a_scored_file(tmp_path: Path) -> None:
    folder = tmp_path / "predictions"
    folder.mkdir()
    for bad in ("inf", "nan", "-0.5"):
        (folder / f"{A_ID}__{PROMPT}.csv").write_text(
            f"message_id,topic,sentiment,seconds\n{_id(1)},food,neutral,{bad}\n"
        )
        with pytest.raises(evaluation.PredictionsError):
            evaluation.read_predictions(tmp_path, A_ID, PROMPT)
    assert math.isfinite(scoring.messages_per_second([0.1]))
