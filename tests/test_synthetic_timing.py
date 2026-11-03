"""The generator's timestamps must make sessions and reply times non-trivial in dbt."""

import itertools
import random

from gcwrapped.parsers import instagram_format as fmt
from synthetic.instagram import THREADS, build_conversation

SESSION_GAP_MS = 60 * 60_000  # the default session_gap_minutes
MULTI_HOUR_MS = 3 * 60 * 60_000


def history(seed: int = 1234) -> list[dict[str, object]]:
    thread, _title, participants, count = THREADS[0]
    return list(build_conversation(random.Random(seed), thread, participants, count))


def gaps(msgs: list[dict[str, object]]) -> list[int]:
    ts = [int(str(m[fmt.KEY_TIMESTAMP_MS])) for m in msgs]
    return [b - a for a, b in itertools.pairwise(ts)]


def test_bursts_are_separated_by_multi_hour_gaps() -> None:
    g = gaps(history())
    long_gaps = [x for x in g if x >= MULTI_HOUR_MS]
    assert len(long_gaps) >= 3  # several sessions, not one
    # Every gap is either a rapid in-burst gap (under the session gap) or a multi-hour break,
    # apart from the pinned twin pair, which is a zero gap.
    assert all(x < SESSION_GAP_MS or x >= MULTI_HOUR_MS for x in g)


def test_rapid_back_and_forth_dominates_within_bursts() -> None:
    g = gaps(history())
    rapid = [x for x in g if x <= 3 * 60_000]
    assert len(rapid) > len(g) // 2


def test_senders_form_multi_message_runs_and_alternate() -> None:
    senders = [m[fmt.KEY_SENDER] for m in history()]
    changes = sum(a != b for a, b in itertools.pairwise(senders))
    assert 0 < changes < len(senders) - 1  # some same-sender runs, some sender changes


def test_timing_is_deterministic_for_a_seed() -> None:
    assert history(7) == history(7)
    assert gaps(history(7)) != gaps(history(8))
