"""The two interactive flows, driven by scripted input over a synthetic index (D85, D86)."""

import logging
from pathlib import Path

import pytest

from gcwrapped.labeling.files import read_questions, read_skips, read_tags
from gcwrapped.labeling.flows import TOPIC_KEYS, run_questions, run_tags
from gcwrapped.labeling.schema import (
    QUESTION_CONTEXT_EACH_SIDE,
    SENTIMENTS,
    TAG_CONTEXT_MESSAGES,
    TOPICS,
)
from gcwrapped.labeling.terminal import safe_text
from labeling_support import Scripted, StepClock, make_index, message_id

TEXTS = [f"SENTINELTEXT message {n}" for n in range(12)]
INDEX = make_index({"t1": TEXTS})
IDS = [message_id(f"t1/{n}") for n in range(12)]


# --- Tags -------------------------------------------------------------------------------------


def _tags(tmp_path: Path, keys: str, sample: list[str] | None = None) -> tuple[Scripted, Path]:
    path = tmp_path / "tag_labels.csv"
    term = Scripted(keys)
    run_tags(term, INDEX, sample or IDS[:4], path, StepClock())
    return term, path


def test_a_topic_key_then_a_sentiment_key_labels_the_message(tmp_path: Path) -> None:
    _, path = _tags(tmp_path, "1gq")
    [label] = read_tags(path)
    assert (label.message_id, label.topic, label.sentiment) == (IDS[0], "plans", "positive")
    assert label.labeled_at.endswith("+00:00")


def test_every_topic_and_sentiment_key_maps_to_the_schema(tmp_path: Path) -> None:
    assert list(TOPIC_KEYS.values()) == list(TOPICS)  # 1..9 then 0 is the tenth
    assert TOPIC_KEYS["0"] == "other"
    keys = "".join(f"{key}{sent}" for key in TOPIC_KEYS for sent in "gnb")
    sample = [message_id(f"t1/{n}") for n in range(12)]
    path = tmp_path / "tag_labels.csv"
    run_tags(Scripted(keys + "q"), INDEX, sample, path, StepClock())
    labels = read_tags(path)
    assert len(labels) == 12  # the sample is 12 messages; the other scripted pairs go unread
    expected = [(topic, SENTIMENTS["gnb".index(sent)]) for topic in TOPICS for sent in "gnb"][:12]
    assert [(label.topic, label.sentiment) for label in labels] == expected


def test_undo_removes_the_last_label_and_shows_that_message_again(tmp_path: Path) -> None:
    term, path = _tags(tmp_path, "1g2nu3bq")
    labels = read_tags(path)
    assert [(label.message_id, label.topic) for label in labels] == [
        (IDS[0], "plans"),
        (IDS[1], "work"),
    ]
    assert "undid the last label" in term.text
    assert term.text.count(TEXTS[1]) >= 2  # shown, undone, shown again


def test_undo_with_nothing_labeled_changes_nothing(tmp_path: Path) -> None:
    term, path = _tags(tmp_path, "uq")
    assert "nothing to undo" in term.text
    assert read_tags(path) == []


def test_u_at_the_sentiment_step_goes_back_to_the_topic(tmp_path: Path) -> None:
    _, path = _tags(tmp_path, "1u2gq")
    [label] = read_tags(path)
    assert (label.topic, label.sentiment) == ("school", "positive")


def test_quitting_between_topic_and_sentiment_saves_no_partial_label(tmp_path: Path) -> None:
    _, path = _tags(tmp_path, "1gq")
    _, path = _tags(tmp_path, "21")  # a second run: topic chosen, then the input ends
    assert len(read_tags(path)) == 1


def test_quit_saves_and_a_second_run_resumes_at_the_first_unlabeled(tmp_path: Path) -> None:
    first, path = _tags(tmp_path, "1g2nq")
    assert "saved: 2 labeled" in first.text
    second = Scripted("3bq")
    run_tags(second, INDEX, IDS[:4], path, StepClock())
    assert f">>> {TEXTS[0]}" not in second.text and f">>> {TEXTS[1]}" not in second.text
    assert f">>> {TEXTS[2]}" in second.text  # resumed here; 0 and 1 only appear as its context
    assert [label.message_id for label in read_tags(path)] == IDS[:3]


def test_end_of_input_is_a_quit(tmp_path: Path) -> None:
    term, path = _tags(tmp_path, "")
    assert "saved: 0 labeled" in term.text
    assert read_tags(path) == []


def test_unknown_keys_are_ignored(tmp_path: Path) -> None:
    _, path = _tags(tmp_path, "x\n 1z9gq")
    [label] = read_tags(path)
    assert (label.topic, label.sentiment) == ("plans", "positive")


def test_a_finished_sample_reports_done_and_stops(tmp_path: Path) -> None:
    path = tmp_path / "tag_labels.csv"
    run_tags(Scripted("1g2n"), INDEX, IDS[:2], path, StepClock())
    term = Scripted("1g")  # would label something if anything were pending
    end = run_tags(term, INDEX, IDS[:2], path, StepClock())
    assert end.finished and end.total == 2
    assert "all done: 2 labeled" in term.text
    assert len(read_tags(path)) == 2


def test_the_message_is_shown_with_at_most_two_previous_eligible_messages(tmp_path: Path) -> None:
    term, _ = _tags(tmp_path, "q", sample=[IDS[5]])
    assert TAG_CONTEXT_MESSAGES == 2
    shown = term.text
    assert TEXTS[3] in shown and TEXTS[4] in shown and TEXTS[5] in shown
    assert TEXTS[2] not in shown and TEXTS[6] not in shown  # not three back, nothing after


def test_context_skips_ineligible_neighbours_because_they_are_not_in_the_index(
    tmp_path: Path,
) -> None:
    """A message from a "no" sender never reaches the index, so it can never be context."""
    kept = [t for n, t in enumerate(TEXTS) if n != 4]
    index = make_index({"t1": kept})
    term = Scripted("q")
    target = next(m for m in index.ids_by_thread()["t1"] if index.get(m).text == TEXTS[5])
    run_tags(term, index, [target], tmp_path / "tag_labels.csv", StepClock())
    assert TEXTS[4] not in term.text
    assert TEXTS[3] in term.text and TEXTS[2] in term.text  # the two before it, skipping the gap


def test_context_never_crosses_threads(tmp_path: Path) -> None:
    index = make_index({"a": ["A one", "A two"], "b": ["B one", "B two"]})
    term = Scripted("q")
    run_tags(term, index, [message_id("b/0")], tmp_path / "t.csv", StepClock())
    assert "A two" not in term.text and "A one" not in term.text


def test_sample_items_no_longer_eligible_are_skipped_not_shown(tmp_path: Path) -> None:
    gone = message_id("revoked/0")  # in the frozen sample, but its sender's consent was revoked
    term = Scripted("1gq")
    run_tags(term, INDEX, [gone, IDS[0]], tmp_path / "t.csv", StepClock())
    assert [label.message_id for label in read_tags(tmp_path / "t.csv")] == [IDS[0]]


def test_control_characters_in_a_message_are_not_sent_to_the_terminal(tmp_path: Path) -> None:
    index = make_index({"t": ["\x1b[2Jboom\x1b]0;title\x07 and\nmore ‮evil"]})
    term = Scripted("q")
    run_tags(term, index, [message_id("t/0")], tmp_path / "t.csv", StepClock())
    assert "\x1b" not in term.text and "\x07" not in term.text and "‮" not in term.text
    assert safe_text("a\nb\tc\x00d") == "a b c�d"


def test_labeling_logs_no_message_text(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG):
        _tags(tmp_path, "1g2nu3bq")
    assert "SENTINELTEXT" not in caplog.text


# --- Questions --------------------------------------------------------------------------------


def _questions(
    tmp_path: Path,
    keys: str,
    lines: list[str],
    candidates: list[str] | None = None,
    target: int = 100,
) -> Scripted:
    term = Scripted(keys, lines)
    run_questions(
        term,
        INDEX,
        candidates or [IDS[5], IDS[0]],
        tmp_path / "questions.csv",
        tmp_path / "skips.csv",
        StepClock(),
        target,
    )
    return term


def test_a_question_has_the_shown_message_as_gold_plus_the_marked_context(tmp_path: Path) -> None:
    # Context is numbered 1-6 in sent order: messages 2,3,4 before IDS[5] and 6,7,8 after.
    _questions(tmp_path, "wq", ["when is the meeting", "1 5"])
    [question] = read_questions(tmp_path / "questions.csv")
    assert question.question_id == "q001"
    assert question.question == "when is the meeting"
    assert question.source_message_id == IDS[5]
    assert question.gold_message_ids == (IDS[2], IDS[5], IDS[7])  # chronological, source included


def test_no_extra_numbers_means_the_gold_is_just_the_shown_message(tmp_path: Path) -> None:
    _questions(tmp_path, "wq", ["a question", ""])
    [question] = read_questions(tmp_path / "questions.csv")
    assert question.gold_message_ids == (IDS[5],)


def test_the_context_is_three_numbered_messages_on_each_side(tmp_path: Path) -> None:
    term = _questions(tmp_path, "q", [])
    assert QUESTION_CONTEXT_EACH_SIDE == 3
    shown = term.text
    for n in (2, 3, 4, 6, 7, 8):
        assert TEXTS[n] in shown
    assert TEXTS[1] not in shown and TEXTS[9] not in shown
    assert f"  1. {TEXTS[2]}" in shown and f"  6. {TEXTS[8]}" in shown
    assert f">>> {TEXTS[5]}" in shown


def test_a_candidate_at_the_start_of_a_thread_has_no_numbers_for_missing_context(
    tmp_path: Path,
) -> None:
    term = _questions(tmp_path, "wq", ["q about the first one", "9"], candidates=[IDS[0]])
    # IDS[0] has three after it and none before, so the numbers are 1-3 and "9" is refused.
    assert "use numbers from 1 to 3" in term.text
    assert read_questions(tmp_path / "questions.csv") == []  # then the input ended


@pytest.mark.parametrize("reply", ["0", "7", "a", "1 x", "-1"])
def test_bad_number_replies_are_asked_again(tmp_path: Path, reply: str) -> None:
    term = _questions(tmp_path, "wq", ["a question", reply, "2"])
    assert "use numbers from 1 to 6" in term.text
    [question] = read_questions(tmp_path / "questions.csv")
    assert question.gold_message_ids == (IDS[3], IDS[5])


def test_a_blank_question_cancels_back_to_the_same_message(tmp_path: Path) -> None:
    term = _questions(tmp_path, "wq", [""])
    assert read_questions(tmp_path / "questions.csv") == []
    assert term.text.count(f">>> {TEXTS[5]}") == 2


def test_skip_is_remembered_and_the_next_candidate_is_shown(tmp_path: Path) -> None:
    term = _questions(tmp_path, "sq", [])
    assert [s.message_id for s in read_skips(tmp_path / "skips.csv")] == [IDS[5]]
    assert f">>> {TEXTS[0]}" in term.text


def test_resume_does_not_show_answered_or_skipped_candidates_again(tmp_path: Path) -> None:
    _questions(tmp_path, "swq", ["answered one", ""], candidates=[IDS[5], IDS[0], IDS[9]])
    again = _questions(tmp_path, "q", [], candidates=[IDS[5], IDS[0], IDS[9]])
    assert f">>> {TEXTS[9]}" in again.text
    assert f">>> {TEXTS[5]}" not in again.text and f">>> {TEXTS[0]}" not in again.text


def test_undo_reverts_whichever_of_answer_or_skip_came_last(tmp_path: Path) -> None:
    cands = [IDS[5], IDS[0], IDS[9]]
    _questions(tmp_path, "swuq", ["answered", ""], candidates=cands)
    # skip(5) -> answer(0) -> undo removes the answer; the skip stays.
    assert read_questions(tmp_path / "questions.csv") == []
    assert [s.message_id for s in read_skips(tmp_path / "skips.csv")] == [IDS[5]]
    _questions(tmp_path, "uq", [], candidates=cands)  # a later session: undo removes the skip
    assert read_skips(tmp_path / "skips.csv") == []


def test_question_ids_continue_after_an_undo(tmp_path: Path) -> None:
    _questions(tmp_path, "wwuwq", ["one", "", "two", "", "three", ""], candidates=[IDS[5], IDS[0]])
    assert [q.question_id for q in read_questions(tmp_path / "questions.csv")] == ["q001", "q002"]


def test_the_flow_stops_at_the_target(tmp_path: Path) -> None:
    term = Scripted("wws", ["first", "", "second", ""])
    end = run_questions(
        term,
        INDEX,
        [IDS[5], IDS[0], IDS[9], IDS[2]],
        tmp_path / "q.csv",
        tmp_path / "s.csv",
        StepClock(),
        target=2,
    )
    assert not read_skips(tmp_path / "s.csv")  # the 's' keys were never read
    assert end.finished and end.total == 2
    assert "target reached: 2 questions" in term.text


def test_input_ending_at_the_question_prompt_saves_nothing_half_done(tmp_path: Path) -> None:
    _questions(tmp_path, "w", [])
    assert read_questions(tmp_path / "questions.csv") == []
    _questions(tmp_path, "w", ["only the question"])  # ends at the gold prompt
    assert read_questions(tmp_path / "questions.csv") == []
