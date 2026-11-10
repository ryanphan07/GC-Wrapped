"""The committed synthetic labeled sets (tests/fixtures/eval/) and the label file loader (D86)."""

import hashlib
import json
from collections import Counter
from pathlib import Path

import pytest

from gcwrapped.labeling.files import (
    LabelFileError,
    read_questions,
    read_tags,
    write_questions,
    write_tags,
)
from gcwrapped.labeling.schema import (
    QUESTION_HEADER,
    QUESTIONS_FILE,
    SENTIMENTS,
    TAG_HEADER,
    TAG_LABELS_FILE,
    TOPICS,
    QuestionLabel,
)
from synthetic import write_eval_fixtures
from synthetic.eval_set import EVAL_MESSAGES, EVAL_QUESTIONS
from synthetic.instagram import (
    CONSENT_BY_SENDER,
    EVAL_FOLDER,
    SYNTHETIC_THREAD_IDS,
    THREADS,
    _histories,
    consent_ledger_rows,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "eval"
# sha256 of the first four threads' histories (default seed) as they were before the eval thread
# was added: one shared rng, so appending a thread must not change any earlier one.
EARLIER_THREADS_DIGEST = "8ccc33181582b7429041542700fa661a38b2761eaa7a2e0b9c437f91bee6691f"
A_ID = "a" * 64
B_ID = "b" * 64
STAMP = "2026-11-10T00:00:00+00:00"


# --- The committed sets -----------------------------------------------------------------------


def test_tag_set_has_about_sixty_labels_covering_every_class() -> None:
    tags = read_tags(FIXTURES / TAG_LABELS_FILE)
    assert len(tags) == 60
    assert {t.topic for t in tags} == set(TOPICS)
    assert {t.sentiment for t in tags} == set(SENTIMENTS)
    per_topic = Counter(t.topic for t in tags)
    assert min(per_topic.values()) >= 5
    for topic in TOPICS:  # every topic shows every sentiment, so neither axis can be guessed
        assert {t.sentiment for t in tags if t.topic == topic} == set(SENTIMENTS), topic


def test_question_set_has_about_thirty_questions_with_gold() -> None:
    questions = read_questions(FIXTURES / QUESTIONS_FILE)
    assert len(questions) == 31
    tagged = {t.message_id for t in read_tags(FIXTURES / TAG_LABELS_FILE)}
    for question in questions:
        assert question.source_message_id in question.gold_message_ids
        assert set(question.gold_message_ids) <= tagged
    assert any(len(q.gold_message_ids) > 1 for q in questions)  # multi-message gold exists
    assert len({q.question_id for q in questions}) == len(questions)


def test_questions_are_paraphrases_not_the_message_text() -> None:
    texts = {text for _, _, text in EVAL_MESSAGES}
    assert not {q for q, _, _ in EVAL_QUESTIONS} & texts


def test_committed_files_equal_what_the_generator_produces(tmp_path: Path) -> None:
    """The CSVs cannot drift from the generator or the parser's message IDs."""
    tags, questions = write_eval_fixtures.build()
    write_tags(tmp_path / TAG_LABELS_FILE, tags)
    write_questions(tmp_path / QUESTIONS_FILE, questions)
    for name in (TAG_LABELS_FILE, QUESTIONS_FILE):
        assert (tmp_path / name).read_bytes() == (FIXTURES / name).read_bytes(), name


def test_every_labeled_message_is_in_a_thread_of_yes_senders() -> None:
    """The eval thread's senders all consented, so every labeled message is eligible (D85)."""
    participants = next(p for folder, _, p, _ in THREADS if folder == EVAL_FOLDER)
    assert {CONSENT_BY_SENDER[name] for name in participants} == {"yes"}
    rows = [r for r in consent_ledger_rows() if r.thread_id == SYNTHETIC_THREAD_IDS[-1]]
    assert rows
    assert {r.consent for r in rows} == {"yes"}


def test_eval_thread_is_appended_without_changing_earlier_threads() -> None:
    assert THREADS[-1][0] == EVAL_FOLDER
    histories = _histories(1234)
    assert len(histories) == len(THREADS)
    digest = hashlib.sha256(
        json.dumps(histories[:-1], sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    assert digest == EARLIER_THREADS_DIGEST


# --- The loader -------------------------------------------------------------------------------


def _tag_csv(tmp_path: Path, *rows: str, header: str | None = None) -> Path:
    path = tmp_path / TAG_LABELS_FILE
    path.write_text("\n".join([header or ",".join(TAG_HEADER), *rows]) + "\n", encoding="utf-8")
    return path


def _question_csv(tmp_path: Path, *rows: str) -> Path:
    path = tmp_path / QUESTIONS_FILE
    path.write_text("\n".join([",".join(QUESTION_HEADER), *rows]) + "\n", encoding="utf-8")
    return path


def test_missing_files_load_as_empty(tmp_path: Path) -> None:
    assert read_tags(tmp_path / "nope.csv") == []
    assert read_questions(tmp_path / "nope.csv") == []


@pytest.mark.parametrize(
    ("row", "kind", "line"),
    [
        (f"{A_ID},sports,positive,{STAMP}", "bad_topic", 2),
        (f"{A_ID},plans,happy,{STAMP}", "bad_sentiment", 2),
        (f"short,plans,positive,{STAMP}", "bad_message_id", 2),
        (f"{A_ID},plans,positive,yesterday", "bad_timestamp", 2),
        (f"{A_ID},plans,positive,2026-11-10T00:00:00", "bad_timestamp", 2),  # no UTC offset
        (f"{A_ID},plans,positive", "bad_row", 2),
    ],
)
def test_tag_loader_rejects_bad_rows(tmp_path: Path, row: str, kind: str, line: int) -> None:
    with pytest.raises(LabelFileError) as raised:
        read_tags(_tag_csv(tmp_path, row))
    assert (raised.value.kind, raised.value.line) == (kind, line)


def test_tag_loader_rejects_a_bad_header_and_duplicates(tmp_path: Path) -> None:
    with pytest.raises(LabelFileError, match="bad_header at line 1"):
        read_tags(_tag_csv(tmp_path, header="id,topic,sentiment,labeled_at"))
    row = f"{A_ID},plans,positive,{STAMP}"
    with pytest.raises(LabelFileError, match="duplicate_message_id at line 3"):
        read_tags(_tag_csv(tmp_path, row, row))


@pytest.mark.parametrize(
    ("row", "kind"),
    [
        (f"q001,,{A_ID},{A_ID},{STAMP}", "empty_question"),
        (f"q001,why,,{A_ID},{STAMP}", "bad_gold"),
        (f"q001,why,{A_ID};{A_ID},{A_ID},{STAMP}", "bad_gold"),
        (f"q001,why,{A_ID},{B_ID},{STAMP}", "source_not_in_gold"),
        (f"1,why,{A_ID},{A_ID},{STAMP}", "bad_question_id"),
        (f"q001,why,{A_ID};nope,{A_ID},{STAMP}", "bad_gold"),
    ],
)
def test_question_loader_rejects_bad_rows(tmp_path: Path, row: str, kind: str) -> None:
    with pytest.raises(LabelFileError) as raised:
        read_questions(_question_csv(tmp_path, row))
    assert raised.value.kind == kind


def test_loader_errors_never_echo_values(tmp_path: Path) -> None:
    secret = "SENTINELQUESTION in my own words"
    path = _question_csv(tmp_path, f"q001,{secret},{A_ID},{B_ID},{STAMP}")
    with pytest.raises(LabelFileError) as raised:
        read_questions(path)
    assert "SENTINEL" not in str(raised.value)
    assert "SENTINEL" not in repr(raised.value)


def test_writers_round_trip_and_set_a_private_mode(tmp_path: Path) -> None:
    tags = read_tags(FIXTURES / TAG_LABELS_FILE)
    questions = read_questions(FIXTURES / QUESTIONS_FILE)
    write_tags(tmp_path / "t.csv", tags)
    write_questions(tmp_path / "q.csv", questions)
    assert read_tags(tmp_path / "t.csv") == tags
    assert read_questions(tmp_path / "q.csv") == questions
    assert (tmp_path / "t.csv").stat().st_mode & 0o777 == 0o600
    assert list(tmp_path.glob(".labels-*")) == []  # no temp file left behind


def test_a_question_with_a_comma_and_quotes_round_trips(tmp_path: Path) -> None:
    label = QuestionLabel("q001", 'who said "yes", then left?', (A_ID, B_ID), A_ID, STAMP)
    write_questions(tmp_path / "q.csv", [label])
    assert read_questions(tmp_path / "q.csv") == [label]
