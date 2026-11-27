"""Where retrieval loses questions (D139), as counts only. Nothing here prints or stores.

The latest retrieval-eval of a method keeps the top DEPTH results of each question, so each
question is searched again, with the same retriever and method, SEARCH_DEPTH deep. That is refused
unless the warehouse still is the one the eval searched (embed version, corpus hash) and the
question set is the one it scored, so the ranks are that run's.

The right message of a question is its best-ranked gold message, or its first gold message when
none is in the top SEARCH_DEPTH. A near miss is a miss at HIT_AT whose gold message is inside the
window of one of the first HIT_AT results.
"""

from collections import Counter
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass

from gcwrapped.labeling.schema import QuestionLabel
from gcwrapped.labeling.source import EligibleIndex
from gcwrapped.retrieval import evaluation
from gcwrapped.retrieval.search import Hit

SEARCH_DEPTH = 50
HIT_AT = evaluation.COMPARE_AT  # 5, the rank the D104 rule counts
NOT_FOUND = "not in the top 50"
# (name, highest rank or word count in the bucket); the last length bucket has no upper bound.
RANK_BUCKETS = (("1", 1), ("2-5", 5), ("6-10", 10), ("11-50", SEARCH_DEPTH))
LENGTH_BUCKETS: tuple[tuple[str, int | None], ...] = (("1-3", 3), ("4-10", 10), ("11+", None))


def rank_bucket(rank: int | None) -> str:
    """The bucket of a rank counted from 1; None means the search did not return the message."""
    if rank is not None:
        for name, highest in RANK_BUCKETS:
            if rank <= highest:
                return name
    return NOT_FOUND


def length_bucket(words: int) -> str:
    for name, highest in LENGTH_BUCKETS:
        if highest is None or words <= highest:
            return name
    raise AssertionError("the last bucket has no upper bound")


@dataclass(frozen=True)
class Breakdown:
    method: str
    questions: int  # in the question file
    gold_not_searchable: int  # dropped: no gold message can be returned by search
    source_not_eligible: int  # dropped: the message the question was written from
    scored: int
    ranks: Counter[str]  # by rank_bucket
    hit_lengths: Counter[str]  # hits at HIT_AT, by the right message's length_bucket
    miss_lengths: Counter[str]
    near_misses: int
    changed: int  # questions whose first DEPTH results differ from the stored run's


def check_same_run(
    stored: evaluation.Predictions,
    kept: Sequence[QuestionLabel],
    source: str,
    version: str,
    searchable: Collection[str],
) -> None:
    """Refuse unless searching again repeats the stored run: the same embed version, corpus and
    questions with the same text, source and gold."""
    if stored.embed_version != version:
        raise evaluation.EmbedVersionMismatch
    if stored.corpus_hash != evaluation.corpus_hash(searchable):
        raise evaluation.CorpusMismatch
    now = {
        q.question_id: (
            evaluation.question_hash(q.question),
            q.source_message_id,
            tuple(sorted(q.gold_message_ids)),
        )
        for q in kept
    }
    if stored.source != source or evaluation.question_set(stored) != now:
        raise evaluation.QuestionSetMismatch


def breakdown(
    stored: evaluation.Predictions,
    questions: Sequence[QuestionLabel],
    eligible: EligibleIndex,
    searchable: Collection[str],
    search: Callable[[str, int], list[Hit]],
    window_members: Callable[[Sequence[str]], Collection[str]],
) -> Breakdown:
    """Search each scorable question again and count where its right message lands.
    `window_members` gives every message in the windows of the results it is handed."""
    kept, _ = evaluation.split_questions(questions, eligible, searchable)
    if not kept:
        raise evaluation.NoScorableQuestionsError
    unsearchable = sum(1 for q in questions if not any(g in searchable for g in q.gold_message_ids))
    rows = {r.question_id: r for r in stored.rows}
    ranks: Counter[str] = Counter()
    hit_lengths: Counter[str] = Counter()
    miss_lengths: Counter[str] = Counter()
    near_misses = changed = 0
    for question in kept:
        found = [h.message_id for h in search(question.question, SEARCH_DEPTH)]
        gold = question.gold_message_ids
        rank = next((n for n, m in enumerate(found, start=1) if m in gold), None)
        ranks[rank_bucket(rank)] += 1
        right = gold[0] if rank is None else found[rank - 1]
        bucket = length_bucket(len(eligible.get(right).text.split()))
        if rank is not None and rank <= HIT_AT:
            hit_lengths[bucket] += 1
        else:
            miss_lengths[bucket] += 1
            members = window_members(found[:HIT_AT])
            near_misses += any(g in members for g in gold)
        stored_row = rows[question.question_id]
        changed += tuple(found[: evaluation.DEPTH]) != stored_row.retrieved_message_ids
    return Breakdown(
        method=stored.method,
        questions=len(questions),
        gold_not_searchable=unsearchable,
        source_not_eligible=len(questions) - len(kept) - unsearchable,
        scored=len(kept),
        ranks=ranks,
        hit_lengths=hit_lengths,
        miss_lengths=miss_lengths,
        near_misses=near_misses,
        changed=changed,
    )
