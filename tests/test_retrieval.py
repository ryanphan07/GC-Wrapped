"""Embeddings and retrieval without a database (D102-D104): the window builder, the embed version,
the local client, fusion, the eval and compare arithmetic, private files, fixed output, guards.
"""

import hashlib
import json
import math
import re
import stat
import subprocess
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

import pytest

from embedding_stub import StubEmbed
from gcwrapped.labeling.schema import QuestionLabel
from gcwrapped.labeling.source import EligibleIndex, Message
from gcwrapped.retrieval import commands, evaluation, misses, report, search, window
from gcwrapped.retrieval.client import (
    EmbedFailure,
    LlamaCppEmbedder,
    embed_query,
    normalize,
    parse_embeddings,
)
from gcwrapped.retrieval.embed import EmbedResult
from gcwrapped.retrieval.window import SourceMessage, build_window, build_windows
from gcwrapped.tagging import evaluation as tag_evaluation
from gcwrapped.tagging import model
from gcwrapped.tagging.commands import ModelFileMissingError
from gcwrapped.tagging.evaluation import OutsideRepoError
from gcwrapped.tagging.llamacpp import NonLocalUrlError
from gcwrapped.tagging.provider import ProviderUnavailable
from gcwrapped.tagging.report import ShapeError, check_lines

REPO = Path(__file__).resolve().parents[1]
EMBED_ID = model.EMBED_MODEL_ID
SENTINEL = "SENTINELTEXT"
VERSION = "0123456789ab"
CORPUS = "ba9876543210"


def _id(n: int) -> str:
    return hashlib.sha256(str(n).encode()).hexdigest()


def _message(n: int, text: str = "", thread: str = "t1", session: str = "t1:s1") -> SourceMessage:
    return SourceMessage(_id(n), thread, session, f"sender{n}", text or f"text {n}")


# --- the window (D103) -------------------------------------------------------------------------


def test_a_window_is_two_neighbours_each_side_in_time_order_one_line_each() -> None:
    session = [_message(n) for n in range(7)]
    built = build_window(session, 3)
    assert built.message_id == _id(3)
    assert built.member_ids == tuple(_id(n) for n in (1, 2, 3, 4, 5))
    assert built.text == "\n".join(f"sender{n}: text {n}" for n in (1, 2, 3, 4, 5))


def test_a_window_stops_at_the_ends_of_its_session() -> None:
    session = [_message(n) for n in range(3)]
    assert build_window(session, 0).member_ids == (_id(0), _id(1), _id(2))
    assert build_window(session, 2).member_ids == (_id(0), _id(1), _id(2))
    assert build_window(session[:1], 0).member_ids == (_id(0),)


def test_windows_never_cross_a_session_or_a_thread() -> None:
    messages = [
        _message(0),
        _message(1),
        _message(2, session="t1:s2"),
        _message(3, session="t1:s2"),
        _message(4, thread="t2", session="t2:s1"),
    ]
    members = {w.message_id: w.member_ids for w in build_windows(messages)}
    assert members == {
        _id(0): (_id(0), _id(1)),
        _id(1): (_id(0), _id(1)),
        _id(2): (_id(2), _id(3)),
        _id(3): (_id(2), _id(3)),
        _id(4): (_id(4),),
    }
    # even with a session label shared by mistake, the thread still separates
    same = [_message(0, thread="a", session="s"), _message(1, thread="b", session="s")]
    assert [w.member_ids for w in build_windows(same)] == [(_id(0),), (_id(1),)]


def test_line_breaks_inside_a_message_or_name_do_not_add_lines() -> None:
    messages = [SourceMessage(_id(0), "t", "s", "a\nname", "one\n\ntwo\tthree  ")]
    assert build_window(messages, 0).text == "a name: one two three"


def test_the_cap_drops_the_farthest_neighbours_first_the_later_one_on_a_tie() -> None:
    long = "x" * 450  # five lines are over the cap, four are under it
    session = [_message(n, long) for n in range(5)]
    assert build_window(session, 2).member_ids == tuple(_id(n) for n in (0, 1, 2, 3))
    longer = [_message(n, "y" * 600) for n in range(5)]  # only three fit: both at distance 2 go
    assert build_window(longer, 2).member_ids == tuple(_id(n) for n in (1, 2, 3))
    longest = [_message(n, "z" * 900) for n in range(5)]  # only two fit: the later neighbour goes
    built = build_window(longest, 2)
    assert built.member_ids == (_id(1), _id(2))
    assert len(built.text) <= window.MAX_WINDOW_CHARS


def test_a_centre_longer_than_the_cap_is_cut_and_stands_alone() -> None:
    session = [_message(0), _message(1, "w" * 5000), _message(2)]
    built = build_window(session, 1)
    assert built.member_ids == (_id(1),)
    assert len(built.text) == window.MAX_WINDOW_CHARS and built.text.startswith("sender1: w")


# --- the embed version -------------------------------------------------------------------------


def test_the_embed_version_is_pinned() -> None:
    """A change re-embeds everything and voids stored retrieval results: change it on purpose,
    with a decisions entry."""
    assert window.embed_version() == "e3ac6a56e412"


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("BEFORE", 3),
        ("AFTER", 1),
        ("MAX_WINDOW_CHARS", 1999),
        ("LINE_FORMAT", "{sender} - {text}"),
        ("SESSION_RULE", "gap"),
        ("QUERY_INSTRUCTION", "Query:"),
    ],
)
def test_the_embed_version_changes_with_every_window_constant_and_the_query_instruction(
    name: str, value: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = window.embed_version()
    monkeypatch.setattr(window, name, value)
    assert window.embed_version() != before


def test_the_embed_version_changes_with_the_model_file(monkeypatch: pytest.MonkeyPatch) -> None:
    before = window.embed_version()
    changed = replace(model.EMBED_MODEL_SPEC, sha256="0" * 64)
    monkeypatch.setitem(model.EMBED_REGISTRY, EMBED_ID, changed)
    assert window.embed_version() != before


# --- the client --------------------------------------------------------------------------------


def test_a_query_gets_the_instruction_prefix_and_a_stored_window_gets_none() -> None:
    with StubEmbed() as stub:
        client = LlamaCppEmbedder(stub.url, model_id=EMBED_ID)
        client.embed(["a: stored window"])
        embed_query(client, "when is it")
    first, second = (r["input"] for r in stub.requests)
    assert first == ["a: stored window"]
    assert second == [
        "Instruct: Given a web search query, retrieve relevant passages that answer the query\n"
        "Query:when is it"
    ]
    assert {r["model"] for r in stub.requests} == {EMBED_ID}


def test_vectors_come_back_normalized_and_in_input_order() -> None:
    with StubEmbed() as stub:
        client = LlamaCppEmbedder(stub.url, model_id=EMBED_ID)
        both = client.embed(["alpha alpha beta", "gamma"])
        alone = client.embed(["gamma"])
    assert both[1] == alone[0]
    assert all(math.isclose(sum(x * x for x in v), 1.0) for v in both)


@pytest.mark.parametrize(
    "url",
    ["http://example.com:8082", "https://127.0.0.1:8082", "http://127.0.0.1.example.com"],
)
def test_a_non_local_embedding_url_is_refused(url: str) -> None:
    with pytest.raises(NonLocalUrlError):
        LlamaCppEmbedder(url, model_id=EMBED_ID)


def test_failures_carry_no_text_and_a_redirect_is_not_followed() -> None:
    with StubEmbed() as other, StubEmbed(mode="redirect", redirect_to=other.url) as stub:
        with pytest.raises(EmbedFailure) as caught:
            LlamaCppEmbedder(stub.url, model_id=EMBED_ID).embed([SENTINEL])
        assert other.requests == []
    assert SENTINEL not in repr(caught.value) and caught.value.__cause__ is None
    for mode in ("error500", "short"):
        with StubEmbed(mode=mode) as stub, pytest.raises(EmbedFailure):
            LlamaCppEmbedder(stub.url, model_id=EMBED_ID).embed([SENTINEL])


def test_nothing_listening_stops_the_run() -> None:
    with StubEmbed() as stub:
        url = stub.url
    with pytest.raises(ProviderUnavailable):
        LlamaCppEmbedder(url, model_id=EMBED_ID, timeout=2).embed(["x"])


@pytest.mark.parametrize(
    "body",
    [
        None,
        {},
        {"data": []},
        {"data": [{"index": 1, "embedding": [1.0] * 1024}]},
        {"data": [{"index": 0, "embedding": [0.0] * 1024}]},
        {"data": [{"index": 0, "embedding": [float("nan")] * 1024}]},
        {"data": [{"index": 0, "embedding": ["1"] * 1024}]},
    ],
)
def test_a_malformed_response_is_a_failure(body: object) -> None:
    with pytest.raises(EmbedFailure):
        parse_embeddings(body, 1)


def test_normalize_is_l2() -> None:
    vector = normalize([3.0, 4.0, *([0.0] * 1022)])
    assert vector[:2] == [0.6, 0.8]


# --- fusion (D104) -----------------------------------------------------------------------------


def test_reciprocal_rank_fusion_by_hand() -> None:
    hits = search.fuse(["a", "b", "c"], ["c", "d"])
    expected = {"a": 1 / 61, "b": 1 / 62, "c": 1 / 63 + 1 / 61, "d": 1 / 62}
    assert {h.message_id: h.score for h in hits} == pytest.approx(expected)
    # c is in both lists; b and d tie, and the one in the vector list goes first
    assert [h.message_id for h in hits] == ["c", "a", "b", "d"]


def test_fusion_ties_are_deterministic_and_the_constants_are_the_decided_ones() -> None:
    assert (search.CANDIDATES, search.RRF_K) == (50, 60)
    assert [h.message_id for h in search.fuse(["b"], ["a"])] == ["b", "a"]  # vector rank first
    assert [h.message_id for h in search.fuse([], ["b", "a"])] == ["b", "a"]
    assert search.fuse([], []) == []


# --- eval arithmetic ---------------------------------------------------------------------------


def _row(
    n: int, rank: int | None, seconds: float = 0.1, gold: tuple[int, ...] = ()
) -> evaluation.Row:
    """Question n with its gold message at `rank` of ten results (None: not retrieved)."""
    target = _id(1000 + n)
    results = [_id(2000 + 100 * n + i) for i in range(10)]
    if rank is not None:
        results[rank - 1] = target
    return evaluation.Row(
        f"q{n:03d}",
        _id(3000 + n),
        target,
        (target, *(_id(g) for g in gold)),
        tuple(results),
        seconds,
    )


def test_recall_and_mrr_by_hand() -> None:
    rows = [_row(1, 1, 0.1), _row(2, 3, 0.2), _row(3, 6, 0.3), _row(4, None, 0.4)]
    result = evaluation.score(rows, questions=6)
    assert (result.questions, result.dropped, result.scored) == (6, 2, 4)
    assert [result.recall[k].value for k in (1, 5, 10)] == [0.25, 0.5, 0.75]
    assert result.mrr.value == pytest.approx((1 + 1 / 3 + 1 / 6 + 0) / 4)
    assert (result.p50, result.p95) == (0.2, 0.4)  # nearest rank
    for estimate in (*result.recall.values(), result.mrr):
        low, high = estimate.interval
        assert low <= estimate.value <= high


def test_any_gold_message_makes_a_hit_and_the_first_one_sets_the_rank() -> None:
    row = _row(1, 7, gold=(5,))
    results = list(row.retrieved_message_ids)
    results[1] = _id(5)
    row = replace(row, retrieved_message_ids=tuple(results))
    assert evaluation.first_hit_rank(row) == 2
    assert (evaluation.hit_at(row, 1), evaluation.hit_at(row, 5)) == (0.0, 1.0)
    assert evaluation.reciprocal_rank(row) == 0.5


@pytest.mark.parametrize(
    ("successes", "n", "interval"),
    [
        (0, 31, (0.0, 0.1103)),  # D117
        (31, 31, (0.8897, 1.0)),
        # published score intervals without continuity correction (Newcombe 1998, table I)
        (81, 263, (0.2553, 0.3662)),
        (15, 148, (0.0624, 0.1605)),
        (0, 20, (0.0, 0.1611)),
        (1, 29, (0.0061, 0.1718)),
    ],
)
def test_the_wilson_interval(successes: int, n: int, interval: tuple[float, float]) -> None:
    assert evaluation.wilson_interval(successes, n) == pytest.approx(interval, abs=5e-5)


def test_the_wilson_bounds_at_the_ends_are_exact_and_the_other_one_leaves_a_width() -> None:
    for n in range(1, 201):  # float noise must never print a bound as -0.000
        low, high = evaluation.wilson_interval(0, n)
        assert low == 0.0 and 0.0 < high < 1.0
        low, high = evaluation.wilson_interval(n, n)
        assert high == 1.0 and 0.0 < low < 1.0


@pytest.mark.parametrize(("successes", "n"), [(0, 0), (2, 1), (-1, 1)])
def test_the_wilson_interval_needs_a_count_within_a_total(successes: int, n: int) -> None:
    with pytest.raises(ValueError):
        evaluation.wilson_interval(successes, n)


def test_recall_has_a_wilson_interval_and_mrr_keeps_the_bootstrap() -> None:
    result = evaluation.score([_row(n, 1, 0.1) for n in range(1, 6)], questions=5)
    for k in evaluation.RECALL_AT:
        assert result.recall[k].interval == evaluation.wilson_interval(5, 5)
    assert result.mrr.interval == (1.0, 1.0)


def test_a_constant_outcome_gives_a_degenerate_bootstrap_interval() -> None:
    assert evaluation.estimate([1.0] * 5).interval == (1.0, 1.0)
    with pytest.raises(evaluation.NoScorableQuestionsError):
        evaluation.score([], questions=3)


def test_questions_without_an_eligible_source_or_a_searchable_gold_are_dropped() -> None:
    def question(n: int, source: int, *gold: int) -> QuestionLabel:
        return QuestionLabel(
            f"q{n:03d}", "q?", tuple(_id(g) for g in gold), _id(source), "2026-01-01T00:00:00+00:00"
        )

    questions = [
        question(1, 1, 1, 2),  # gold cut to the searchable one
        question(2, 3, 3),  # gold not embedded
        question(3, 4, 4, 1),  # source not eligible
    ]
    kept, dropped = evaluation.split_questions(
        questions, eligible={_id(1), _id(2), _id(3)}, searchable={_id(1), _id(4)}
    )
    assert dropped == 2
    assert [(q.question_id, q.gold_message_ids) for q in kept] == [("q001", (_id(1),))]


# --- the comparison (D104) ---------------------------------------------------------------------


def _predictions(
    method: str,
    ranks: list[int | None],
    seconds: float = 0.1,
    version: str = VERSION,
    corpus: str = CORPUS,
) -> evaluation.Predictions:
    rows = tuple(_row(n, rank, seconds) for n, rank in enumerate(ranks, start=1))
    return evaluation.Predictions(method, version, "synthetic", corpus, rows)


def _everything(*predictions: evaluation.Predictions) -> set[str]:
    return {m for p in predictions for r in p.rows for m in r.referenced_ids}


def test_hybrid_wins_only_when_the_whole_paired_interval_is_above_zero_and_it_is_fast() -> None:
    a = _predictions("vector", [None] * 20)
    b = _predictions("hybrid", [1] * 20, seconds=0.5)
    result = evaluation.compare(a, b, _everything(a, b))
    assert (result.scored, result.a_recall, result.b_recall) == (20, 0.0, 1.0)
    assert result.difference.value == 1.0 and result.difference.interval == (1.0, 1.0)
    assert result.latency_ok and result.b_wins

    slow = _predictions("hybrid", [1] * 20, seconds=1.0)  # the limit is strictly under 1.0 s
    result = evaluation.compare(a, slow, _everything(a, slow))
    assert not result.latency_ok and not result.b_wins


def test_an_interval_that_reaches_zero_keeps_vector() -> None:
    a = _predictions("vector", [1] * 10 + [None] * 10)
    b = _predictions("hybrid", [1] * 11 + [None] * 9)  # one question better: not enough
    result = evaluation.compare(a, b, _everything(a, b))
    assert result.difference.value == pytest.approx(0.05)
    assert result.difference.interval[0] == 0.0 and not result.b_wins


def test_the_recall_compared_is_at_five() -> None:
    a = _predictions("vector", [6] * 8)
    b = _predictions("hybrid", [5] * 8)
    result = evaluation.compare(a, b, _everything(a, b))
    assert (result.a_recall, result.b_recall) == (0.0, 1.0)


def test_the_difference_is_paired() -> None:
    a = _predictions("vector", [1, None] * 10)
    b = _predictions("hybrid", [1, None] * 10)  # identical per question: no spread at all
    assert evaluation.compare(a, b, _everything(a, b)).difference.interval == (0.0, 0.0)


def test_a_question_that_refers_to_a_message_no_longer_eligible_is_not_scored() -> None:
    a = _predictions("vector", [1, 1, 1])
    b = _predictions("hybrid", [1, 1, 1])
    eligible = _everything(a, b) - {b.rows[1].retrieved_message_ids[4]}
    assert evaluation.compare(a, b, eligible).scored == 2
    with pytest.raises(evaluation.NoScorableQuestionsError):
        evaluation.compare(a, b, set())


def test_different_embed_versions_or_question_sets_cannot_be_compared() -> None:
    a = _predictions("vector", [1, 2])
    with pytest.raises(evaluation.EmbedVersionMismatch):
        evaluation.compare(a, _predictions("hybrid", [1, 2], version="f" * 12), set())
    b = _predictions("hybrid", [1, 2])
    first = b.rows[0]
    for changed in (
        replace(b, rows=b.rows[:1]),
        replace(b, rows=(replace(first, question_sha256=_id(9)), b.rows[1])),
        replace(b, rows=(replace(first, gold_message_ids=(_id(9),)), b.rows[1])),
        replace(b, source="real"),
    ):
        with pytest.raises(evaluation.QuestionSetMismatch):
            evaluation.compare(a, changed, set())


# --- the corpus (D109) -------------------------------------------------------------------------


def test_the_corpus_hash_names_the_set_of_searchable_ids() -> None:
    expected = hashlib.sha256(f"{min(_id(1), _id(2))}\n{max(_id(1), _id(2))}".encode()).hexdigest()
    assert evaluation.corpus_hash([_id(1), _id(2)]) == expected[:12]
    assert evaluation.corpus_hash([_id(2), _id(1)]) == expected[:12]  # order does not matter
    assert evaluation.corpus_hash([_id(1)]) != expected[:12]


def test_unembedded_counts_the_eligible_messages_search_cannot_return() -> None:
    assert evaluation.unembedded([_id(1), _id(2), _id(3)], {_id(2), _id(4)}) == 2
    assert evaluation.unembedded([_id(1)], {_id(1), _id(4)}) == 0


def test_files_made_over_different_corpora_cannot_be_compared() -> None:
    a = _predictions("vector", [1, 2])
    b = _predictions("hybrid", [1, 2], corpus="f" * 12)
    with pytest.raises(evaluation.CorpusMismatch):
        evaluation.compare(a, b, _everything(a, b))


# --- verdicts (D110) ---------------------------------------------------------------------------


def _write_verdict(root: Path, hybrid_wins: bool) -> None:
    a = _predictions("vector", [None] * 20)
    b = _predictions("hybrid", [1 if hybrid_wins else None] * 20)
    evaluation.write_verdict(root, a, evaluation.compare(a, b, _everything(a, b)))


def test_a_verdict_holds_numbers_method_names_version_and_corpus_only(tmp_path: Path) -> None:
    _write_verdict(tmp_path, hybrid_wins=True)
    (path,) = (tmp_path / "experiments").iterdir()
    assert re.fullmatch(r"retrieval-compare-\d{8}T\d{12}Z\.json", path.name)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    body = json.loads(path.read_text())
    assert {k for k, v in body.items() if isinstance(v, str)} == {
        "a",
        "b",
        "winner",
        "embed_version",
        "corpus_hash",
    }
    assert (body["winner"], body["embed_version"], body["corpus_hash"]) == (
        "hybrid",
        VERSION,
        CORPUS,
    )
    assert evaluation.latest_verdict(tmp_path) == ("hybrid", VERSION)


def test_the_latest_retrieval_verdict_decides_and_tag_verdicts_are_ignored(tmp_path: Path) -> None:
    with pytest.raises(evaluation.VerdictError):
        evaluation.latest_verdict(tmp_path)
    _write_verdict(tmp_path, hybrid_wins=True)
    _write_verdict(tmp_path, hybrid_wins=False)
    (tmp_path / "experiments" / "compare-99991231T000000000000Z.json").write_text("{}")
    assert evaluation.latest_verdict(tmp_path) == ("vector", VERSION)
    with pytest.raises(tag_evaluation.VerdictError):  # and the tagger never reads retrieval ones
        (tmp_path / "experiments" / "compare-99991231T000000000000Z.json").unlink()
        tag_evaluation.latest_winner(tmp_path)


def test_a_new_retrieval_verdict_wins_even_after_the_clock_stepped_back(tmp_path: Path) -> None:
    _write_verdict(tmp_path, hybrid_wins=True)
    (path,) = (tmp_path / "experiments").iterdir()
    path.rename(path.with_name("retrieval-compare-29990101T000000000000Z.json"))
    # a newer name of another kind does not move this one
    (tmp_path / "experiments" / "answer-compare-39990101T000000000000Z.json").write_text("{}")
    _write_verdict(tmp_path, hybrid_wins=False)
    assert evaluation.latest_verdict(tmp_path) == ("vector", VERSION)
    assert (tmp_path / "experiments" / "retrieval-compare-29990101T000000000001Z.json").is_file()


def test_a_malformed_latest_verdict_fails_closed(tmp_path: Path) -> None:
    _write_verdict(tmp_path, hybrid_wins=True)
    (path,) = (tmp_path / "experiments").iterdir()
    good = path.read_text()
    for bad in (
        "not json",
        "[]",
        good.replace('"winner": "hybrid"', '"winner": "other"'),
        good.replace('"b": "hybrid"', '"b": "vector"'),
        good.replace(VERSION, "new"),
    ):
        path.write_text(bad)
        with pytest.raises(evaluation.VerdictError):
            evaluation.latest_verdict(tmp_path)


# --- private files -----------------------------------------------------------------------------


def test_predictions_round_trip_privately_and_hold_no_text(tmp_path: Path) -> None:
    questions = [
        QuestionLabel("q001", f"{SENTINEL}?", (_id(1),), _id(1), "2026-01-01T00:00:00+00:00")
    ]
    rows = evaluation.run_method(
        lambda query, k: [search.Hit(_id(1), 1.0)], questions, clock=iter([1.0, 1.25]).__next__
    )
    assert rows[0].seconds == 0.25
    stored = evaluation.Predictions("vector", VERSION, "real", CORPUS, tuple(rows))
    evaluation.write_predictions(tmp_path, stored)
    path = tmp_path / "retrieval" / "vector.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert SENTINEL not in path.read_text()
    assert evaluation.read_predictions(tmp_path, "vector") == stored
    assert [p.name for p in path.parent.iterdir()] == ["vector.json"]  # no temporary file left


def test_a_missing_or_malformed_predictions_file_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(evaluation.RetrievalPredictionsError):
        evaluation.read_predictions(tmp_path, "vector")
    stored = _predictions("vector", [1])
    evaluation.write_predictions(tmp_path, stored)
    path = tmp_path / "retrieval" / "vector.json"
    good = path.read_text()
    for bad in (
        "not json",
        "[]",
        good.replace('"vector"', '"hybrid"'),
        good.replace('"q001"', f'"{SENTINEL}"'),
        good.replace(_id(1001), SENTINEL),
        good.replace('"seconds": 0.1', '"seconds": -1'),
        good.replace('"seconds": 0.1', '"seconds": NaN'),
        good.replace('"corpus_hash"', '"corpus"'),  # required (D109): a pre-D109 file fails
        good.replace(CORPUS, SENTINEL),
    ):
        path.write_text(bad)
        with pytest.raises(evaluation.RetrievalPredictionsError):
            evaluation.read_predictions(tmp_path, "vector")
    with pytest.raises(evaluation.UnknownMethodError):
        evaluation.predictions_path(tmp_path, "../vector")


def test_predictions_are_never_written_inside_the_repo() -> None:
    with pytest.raises(OutsideRepoError):
        evaluation.write_predictions(REPO / "tests", _predictions("vector", [1]))
    assert not (REPO / "tests" / "retrieval").exists()


# --- fixed output ------------------------------------------------------------------------------


def _eval_lines() -> list[str]:
    result = evaluation.score([_row(1, 1), _row(2, None)], questions=3)
    return report.render_eval("gcw_dev", "synthetic", "vector", VERSION, result)


def test_the_embed_lines_are_the_fixed_ones() -> None:
    result = EmbedResult(EMBED_ID, VERSION, 10, 6, 3, 1, 2.0)
    assert report.render_embed("gcw_dev", result) == [
        "target database: gcw_dev",
        f"model id: {EMBED_ID}",
        f"embed version: {VERSION}",
        "messages eligible: 10",
        "embedded this run: 6",
        "already current: 3",
        "failures: 1",
        "seconds: 2.00",
        "messages per second: 3.50",
        "embed: ok",
    ]


def test_the_eval_lines_are_the_fixed_ones() -> None:
    lines = _eval_lines()
    assert [line.split(":")[0] for line in lines] == [
        "target database",
        "questions source",
        "method",
        "embed version",
        "messages not embedded",
        "questions",
        "questions dropped (gold not embedded or ineligible)",
        "questions scored",
        "recall@1",
        "recall@1 95% interval",
        "recall@5",
        "recall@5 95% interval",
        "recall@10",
        "recall@10 95% interval",
        "MRR@10",
        "MRR@10 95% interval",
        "p50 retrieval seconds",
        "p95 retrieval seconds",
        "retrieval-eval",
    ]
    assert "questions dropped (gold not embedded or ineligible): 1" in lines
    assert "recall@1: 0.500" in lines and lines[-1] == "retrieval-eval: ok"


def test_the_compare_lines_are_the_fixed_ones() -> None:
    a = _predictions("vector", [None] * 4)
    b = _predictions("hybrid", [1] * 4, seconds=0.25)
    result = evaluation.compare(a, b, _everything(a, b))
    assert report.render_compare("vector", "hybrid", result) == [
        "questions scored: 4",
        "recall@5 A: 0.000",
        "recall@5 B: 1.000",
        "recall@5 difference (B minus A): 1.000",
        "recall@5 difference 95% interval: [1.000, 1.000]",
        "p95 seconds B: 0.250",
        "latency limit met: yes",
        "winner: hybrid",
        "retrieval-compare: ok",
    ]


@pytest.mark.parametrize(
    "bad",
    [
        f"target database: {SENTINEL}",
        f"questions source: {SENTINEL}",
        f"method: {SENTINEL}",
        f"embed version: {_id(1)}",
        f"questions: {SENTINEL}",
        f"recall@1: {SENTINEL}",
    ],
)
def test_a_line_that_could_carry_text_or_an_id_does_not_pass(bad: str) -> None:
    lines = _eval_lines()
    tampered = [bad if line.split(":")[0] == bad.split(":")[0] else line for line in lines]
    assert tampered != lines
    with pytest.raises(ShapeError):
        check_lines(tampered, report.EVAL_SHAPES)


def test_a_smuggled_value_stops_the_render() -> None:
    with pytest.raises(ShapeError):
        report.render_embed("gcw_dev", EmbedResult(SENTINEL, VERSION))
    with pytest.raises(ShapeError):
        report.render_eval(
            "gcw_dev", "synthetic", "vector", SENTINEL, evaluation.score([_row(1, 1)], 1)
        )


def test_failure_lines_name_a_step_and_a_class_only() -> None:
    line = report.failure_line("retrieval-compare", "predictions", RuntimeError(SENTINEL))
    assert line == "retrieval-compare: failed at predictions (RuntimeError)"
    odd = type("Bad Name!", (Exception,), {})
    assert report.failure_line("embed", "docker", odd()) == "embed: failed at docker (Exception)"


# --- guards ------------------------------------------------------------------------------------


def test_commands_refuse_a_bad_method_before_anything_starts(
    capsys: pytest.CaptureFixture[str],
) -> None:
    started: list[str] = []

    def start() -> None:
        started.append("started")

    assert commands.run_retrieval_eval("keyword", start_db=start, start_service=start) == 1
    for pair in (("hybrid", "vector"), ("vector", "vector"), ("vector", "keyword")):
        assert commands.run_retrieval_compare(*pair, start_db=start) == 1
    assert capsys.readouterr().out.splitlines() == [
        "retrieval-eval: failed at method (UnknownMethodError)",
        *["retrieval-compare: failed at method (UnknownMethodError)"] * 3,
    ]
    assert started == []


def test_start_embed_refuses_a_missing_model_file_before_docker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GCW_MODEL_DIR", str(tmp_path))
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("docker was called"))
    with pytest.raises(ModelFileMissingError):
        commands.start_embed()


def test_start_embed_starts_only_the_embed_service_of_the_env_files_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / model.EMBED_MODEL_SPEC.file).write_bytes(b"stand-in")
    monkeypatch.setenv("GCW_MODEL_DIR", str(tmp_path))
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", lambda argv, **kwargs: calls.append(argv))
    commands.start_embed()
    assert calls[-1][:3] == ["docker", "compose", "--env-file"]
    assert calls[-1][4:] == ["--profile", "embed", "up", "-d", "--wait", "embed"]


def test_the_embed_url_defaults_to_this_machine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GCW_EMBED_URL", "")
    monkeypatch.setenv("GCW_EMBED_PORT", "9999")
    assert commands.embed_url() == "http://127.0.0.1:9999"


# --- registry, compose, Makefile ---------------------------------------------------------------


def test_the_embedding_model_is_pinned_in_a_sibling_registry() -> None:
    spec = model.EMBED_REGISTRY[EMBED_ID]
    assert spec.file == "Qwen3-Embedding-0.6B-Q8_0.gguf"
    assert spec.url.startswith("https://huggingface.co/Qwen/") and spec.url.endswith(spec.file)
    assert re.search(r"/resolve/[0-9a-f]{40}/", spec.url)
    assert re.fullmatch(r"[0-9a-f]{64}", spec.sha256)
    assert EMBED_ID not in model.REGISTRY  # no tagger command can serve or promote it
    assert model.downloadable_spec(EMBED_ID) == spec
    assert model.downloadable_spec(model.MODEL_ID) == model.REGISTRY[model.MODEL_ID]
    with pytest.raises(model.UnknownModelError):
        model.downloadable_spec("nope")
    with pytest.raises(model.UnknownModelError):
        model.spec_for(EMBED_ID)


def _service(name: str) -> str:
    text = (REPO / "docker-compose.yml").read_text()
    match = re.search(rf"^  {name}:\n(.*?)(?=^\S|^  \w+:\n|\Z)", text, re.S | re.M)
    assert match
    return match.group(1)


def test_the_embed_service_is_the_pinned_image_local_only_and_leaks_nothing() -> None:
    svc = _service("embed")
    image = re.search(r"image: (ghcr\.io/ggml-org/llama\.cpp:server@sha256:[0-9a-f]{64})", svc)
    assert image and image.group(1) in _service("llm")
    assert 'profiles: ["embed"]' in svc and ":/models:ro" in svc
    assert re.search(r'- "127\.0\.0\.1:\$\{GCW_EMBED_PORT:-8082\}:8080"', svc)
    assert svc.count("ports:") == 1
    assert f"/models/{model.EMBED_MODEL_SPEC.file}" in svc and f"- {EMBED_ID}\n" in svc
    assert "- --embedding" in svc and re.search(r"- --pooling\n\s+- last\n", svc)
    assert "- --no-slots" in svc and "- --no-webui" in svc
    for forbidden in ("--props", "--metrics", "--verbose", "--log-verbose", "-lv", "--api-key"):
        assert forbidden not in svc
    assert "healthcheck:" in svc


def test_the_make_targets_pass_their_variables() -> None:
    from test_makefile_landing import _dry_run

    assert _dry_run("embed") == ["uv run gcw embed "]
    assert _dry_run("embed", SYNTHETIC="1") == ["uv run gcw embed --synthetic"]
    assert _dry_run("retrieval-eval", METHOD="hybrid")[-1] == (
        "uv run gcw retrieval-eval --method hybrid "
    )
    assert _dry_run("retrieval-compare", A="vector", B="hybrid", SYNTHETIC="1")[-1] == (
        "uv run gcw retrieval-compare --a vector --b hybrid --synthetic"
    )
    assert _dry_run("retrieval-misses") == ["uv run gcw retrieval-misses "]
    assert _dry_run("retrieval-misses", SYNTHETIC="1") == [
        "uv run gcw retrieval-misses --synthetic"
    ]
    phony = (REPO / "Makefile").read_text().split(".PHONY:")[1].split("\n")[0]
    for target in ("embed", "retrieval-eval", "retrieval-compare", "retrieval-misses"):
        assert f" {target} " in phony


def test_make_refuses_a_missing_method_or_arm_without_running_anything() -> None:
    for args in (["retrieval-eval"], ["retrieval-compare", "A=vector"]):
        done = subprocess.run(["make", *args], cwd=REPO, capture_output=True, text=True)
        assert done.returncode != 0 and "error: set" in done.stdout


def test_the_synthetic_questions_are_the_committed_ones() -> None:
    assert commands.SYNTHETIC_QUESTIONS == REPO / "tests" / "fixtures" / "eval" / "questions.csv"
    assert commands.SYNTHETIC_QUESTIONS.is_file()


# --- retrieval-misses (D139) -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rank", "bucket"),
    [
        (1, "1"),
        (2, "2-5"),
        (5, "2-5"),
        (6, "6-10"),
        (10, "6-10"),
        (11, "11-50"),
        (50, "11-50"),
        (51, "not in the top 50"),
        (None, "not in the top 50"),
    ],
)
def test_rank_bucket_edges(rank: int | None, bucket: str) -> None:
    assert misses.rank_bucket(rank) == bucket


@pytest.mark.parametrize(
    ("words", "bucket"),
    [(1, "1-3"), (3, "1-3"), (4, "4-10"), (10, "4-10"), (11, "11+"), (500, "11+")],
)
def test_length_bucket_edges(words: int, bucket: str) -> None:
    assert misses.length_bucket(words) == bucket


# message number -> words in its text. 5 has no searchable vector and 6 is not eligible.
_WORDS = {1: 2, 2: 4, 3: 11, 4: 3, 5: 1, 7: 1, 8: 10}
_ELIGIBLE = EligibleIndex(
    Message(_id(n), "t1", " ".join([SENTINEL] * words)) for n, words in _WORDS.items()
)
_SEARCHABLE = frozenset(_id(n) for n in _WORDS if n != 5)
# question number -> (gold, source, {rank: message returned there})
_ASKED: dict[int, tuple[tuple[int, ...], int, dict[int, int]]] = {
    1: ((1,), 1, {1: 1}),  # a hit at rank 1, 2 words
    2: ((2,), 2, {5: 2}),  # a hit at rank 5, 4 words
    3: ((3,), 3, {6: 3}),  # a miss at rank 6, 11 words, inside a top-5 window
    4: ((4,), 4, {}),  # not returned at all, 3 words
    5: ((5,), 1, {}),  # dropped: its gold cannot be searched
    6: ((1,), 6, {}),  # dropped: its source is not eligible
    7: ((7, 8), 7, {11: 8}),  # the best-ranked gold is the right message: 8, 10 words, rank 11
}
_QUESTIONS = [
    QuestionLabel(f"q{n:03d}", f"{SENTINEL} question {n}", tuple(map(_id, gold)), _id(source), "x")
    for n, (gold, source, _) in _ASKED.items()
]


def _results(number: int, depth: int) -> list[search.Hit]:
    """`depth` results for a question: filler messages except where _ASKED places one."""
    placed = _ASKED[number][2]
    return [
        search.Hit(_id(placed.get(rank, 1000 * number + rank)), 1 / rank)
        for rank in range(1, depth + 1)
    ]


def _stored(kept: list[QuestionLabel], source: str = "real") -> evaluation.Predictions:
    rows = evaluation.run_method(lambda q, k: _results(int(q.split()[-1]), k), kept)
    return evaluation.Predictions(
        "vector", VERSION, source, evaluation.corpus_hash(_SEARCHABLE), tuple(rows)
    )


def test_a_breakdown_counts_every_question_where_its_right_message_landed() -> None:
    kept, _ = evaluation.split_questions(_QUESTIONS, _ELIGIBLE, _SEARCHABLE)
    stored = _stored(kept)
    stored = replace(  # one question whose stored top 10 differs from what search returns now
        stored,
        rows=(replace(stored.rows[0], retrieved_message_ids=(_id(2),)), *stored.rows[1:]),
    )
    depths: list[int] = []
    asked: list[int] = []

    def find(question: str, k: int) -> list[search.Hit]:
        depths.append(k)
        asked.append(int(question.split()[-1]))
        return _results(asked[-1], k)

    def window_members(centres: Sequence[str]) -> set[str]:
        assert len(centres) == 5  # only the top-5 results' windows
        near = {_id(3)} if _id(3001) in centres else set()
        return {*centres, *near}

    found = misses.breakdown(stored, _QUESTIONS, _ELIGIBLE, _SEARCHABLE, find, window_members)
    assert asked == [1, 2, 3, 4, 7] and set(depths) == {50}
    assert (found.questions, found.gold_not_searchable, found.source_not_eligible) == (7, 1, 1)
    assert found.scored == 5
    assert dict(found.ranks) == {
        "1": 1,
        "2-5": 1,
        "6-10": 1,
        "11-50": 1,
        "not in the top 50": 1,
    }
    assert dict(found.hit_lengths) == {"1-3": 1, "4-10": 1}
    assert dict(found.miss_lengths) == {"11+": 1, "1-3": 1, "4-10": 1}
    assert (found.near_misses, found.changed) == (1, 1)

    lines = report.render_misses("gcw_dev", "synthetic", VERSION, [found])
    assert lines == [
        "target database: gcw_dev",
        "questions source: synthetic",
        f"embed version: {VERSION}",
        "method: vector",
        "questions: 7",
        "dropped, right message not searchable: 1",
        "dropped, source message not eligible: 1",
        "questions scored: 5",
        "right message at rank 1: 1",
        "right message at rank 2-5: 1",
        "right message at rank 6-10: 1",
        "right message at rank 11-50: 1",
        "right message not in the top 50: 1",
        "hit@5, right message of 1-3 words: 1",
        "hit@5, right message of 4-10 words: 1",
        "hit@5, right message of 11+ words: 0",
        "miss@5, right message of 1-3 words: 1",
        "miss@5, right message of 4-10 words: 1",
        "miss@5, right message of 11+ words: 1",
        "misses@5 with the right message inside a top-5 window: 1",
        "questions whose top 10 differs from the stored run: 1",
        "retrieval-misses: ok",
    ]


def test_misses_are_refused_unless_searching_again_repeats_the_stored_run() -> None:
    kept, _ = evaluation.split_questions(_QUESTIONS, _ELIGIBLE, _SEARCHABLE)
    stored = _stored(kept)
    misses.check_same_run(stored, kept, "real", VERSION, _SEARCHABLE)
    with pytest.raises(evaluation.EmbedVersionMismatch):
        misses.check_same_run(stored, kept, "real", CORPUS, _SEARCHABLE)
    with pytest.raises(evaluation.CorpusMismatch):
        misses.check_same_run(stored, kept, "real", VERSION, _SEARCHABLE - {_id(8)})
    reworded = replace(kept[0], question="another question 1")
    other_gold = replace(kept[0], gold_message_ids=(_id(2),))
    for questions, source in (
        ([reworded, *kept[1:]], "real"),
        ([other_gold, *kept[1:]], "real"),
        (kept[1:], "real"),  # a question removed, or added
        (kept, "synthetic"),
    ):
        with pytest.raises(evaluation.QuestionSetMismatch):
            misses.check_same_run(stored, questions, source, VERSION, _SEARCHABLE)
