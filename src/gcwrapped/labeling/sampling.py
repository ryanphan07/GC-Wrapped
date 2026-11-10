"""Seeded, stratified sampling and the frozen sample file (D86).

Strata are threads. Each thread gets a floor (THREAD_FLOOR, or all of its messages if it has fewer),
and the rest of the target is shared out in proportion to how many eligible messages each thread
has, by largest remainder. The draw is deterministic: same eligible IDs and same seed, same sample,
and it does not depend on the order the IDs arrive in. The result is frozen to a JSON file of
message IDs so labeling can stop and resume on exactly the same items even if more messages are
ingested later.
"""

import json
import os
import random
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

from gcwrapped.labeling.schema import THREAD_FLOOR

FROZEN_VERSION = 1


class FrozenSampleError(Exception):
    """The frozen sample file is not in the expected shape. Only the class name is ever printed."""


def allocate(sizes: Mapping[str, int], target: int, floor: int = THREAD_FLOOR) -> dict[str, int]:
    """How many messages to draw from each thread, given each thread's eligible count.

    Takes everything if the total is at most `target`. Otherwise gives each thread
    min(floor, size), then splits the remaining target in proportion to the room each thread has
    left, by largest remainder (ties by thread ID). If the floors alone exceed the target (many tiny
    threads), the floors are dropped and the split is proportional to size.
    """
    total = sum(sizes.values())
    if total <= target:
        return dict(sizes)
    base = {t: min(floor, n) for t, n in sizes.items()}
    if sum(base.values()) > target:
        base = dict.fromkeys(sizes, 0)
    room = {t: sizes[t] - base[t] for t in sizes}
    left = target - sum(base.values())
    pool = sum(room.values())
    # Exact integer arithmetic: whole share and remainder (over `pool`) per thread.
    whole = {t: left * room[t] // pool for t in sizes}
    remainder = {t: left * room[t] % pool for t in sizes}
    drawn = {t: base[t] + whole[t] for t in sizes}
    # Largest remainder; a thread can never be given more than it has.
    order = sorted(sizes, key=lambda t: (-remainder[t], t))
    short = target - sum(drawn.values())
    for t in order:
        if short == 0:
            break
        if drawn[t] < sizes[t]:
            drawn[t] += 1
            short -= 1
    return drawn


def draw(
    ids_by_thread: Mapping[str, Sequence[str]], target: int, seed: int, salt: str
) -> list[str]:
    """The sample: message IDs in a seeded shuffled order (so a session mixes threads).

    `salt` separates draws that share a seed (tags and question candidates are independent).
    """
    rng = random.Random(f"{seed}:{salt}")
    threads = sorted(ids_by_thread)
    counts = allocate({t: len(ids_by_thread[t]) for t in threads}, target)
    chosen: list[str] = []
    for thread in threads:
        pool = sorted(ids_by_thread[thread])  # sorted, so input order cannot change the draw
        chosen += rng.sample(pool, counts[thread])
    rng.shuffle(chosen)
    return chosen


def write_frozen(path: Path, ids: Sequence[str], seed: int, target: int) -> None:
    """Write the sample file atomically, mode 0600. IDs only; no text."""
    payload = {"version": FROZEN_VERSION, "seed": seed, "target": target, "message_ids": list(ids)}
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".sample-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def read_frozen(path: Path) -> list[str]:
    """The frozen IDs. Raises FrozenSampleError if the file is not what write_frozen wrote."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        raise FrozenSampleError from None
    if not isinstance(data, dict):
        raise FrozenSampleError
    ids = data.get("message_ids")
    if (
        data.get("version") != FROZEN_VERSION
        or not isinstance(ids, list)
        or not all(isinstance(i, str) for i in ids)
        or len(set(ids)) != len(ids)
    ):
        raise FrozenSampleError
    return [str(i) for i in ids]


def frozen_or_draw(
    path: Path,
    ids_by_thread: Mapping[str, Sequence[str]],
    target: int,
    seed: int,
    salt: str,
) -> list[str]:
    """Load the frozen sample if it exists, else draw once and freeze it."""
    if path.exists():
        return read_frozen(path)
    ids = draw(ids_by_thread, target, seed, salt)
    write_frozen(path, ids, seed, target)
    return ids
