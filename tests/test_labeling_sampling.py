"""Seeded stratified sampling and the frozen sample file (D86). Pure: no database."""

import json
import random
from pathlib import Path

import pytest

from gcwrapped.labeling.sampling import (
    FrozenSampleError,
    allocate,
    draw,
    frozen_or_draw,
    read_frozen,
    write_frozen,
)


def _threads(sizes: dict[str, int]) -> dict[str, list[str]]:
    return {t: [f"{t}-{i:04d}" for i in range(n)] for t, n in sizes.items()}


def test_allocation_is_proportional_with_a_floor_and_sums_to_the_target() -> None:
    sizes = {"big": 900, "mid": 90, "small": 8, "tiny": 2}
    drawn = allocate(sizes, 200)
    assert sum(drawn.values()) == 200
    assert drawn["tiny"] == 2  # fewer than the floor: all of it
    assert drawn["small"] >= 5  # the floor, even though proportion would give ~1
    assert drawn["big"] > drawn["mid"] > drawn["small"]
    assert all(drawn[t] <= sizes[t] for t in sizes)


def test_a_thread_below_the_floor_is_still_floored_not_starved() -> None:
    drawn = allocate({"a": 10_000, "b": 50}, 200)
    assert drawn["b"] >= 5
    assert sum(drawn.values()) == 200


def test_everything_is_taken_when_the_target_exceeds_the_pool() -> None:
    assert allocate({"a": 30, "b": 12}, 200) == {"a": 30, "b": 12}


def test_floors_are_dropped_when_they_alone_exceed_the_target() -> None:
    sizes = {f"t{i}": 20 for i in range(10)}
    drawn = allocate(sizes, 20)  # floors would be 50
    assert sum(drawn.values()) == 20
    assert max(drawn.values()) - min(drawn.values()) <= 1


@pytest.mark.parametrize("seed", range(5))
def test_allocation_never_exceeds_a_thread_and_hits_the_target(seed: int) -> None:
    rng = random.Random(seed)
    sizes = {f"t{i}": rng.randint(1, 400) for i in range(rng.randint(1, 12))}
    target = rng.randint(1, 300)
    drawn = allocate(sizes, target)
    assert sum(drawn.values()) == min(target, sum(sizes.values()))
    assert all(0 <= drawn[t] <= sizes[t] for t in sizes)


def test_the_draw_is_stable_across_runs_and_independent_of_input_order() -> None:
    ids = _threads({"x": 300, "y": 120, "z": 40})
    first = draw(ids, 100, 7, "tags")
    assert draw(ids, 100, 7, "tags") == first
    shuffled = {t: random.Random(1).sample(v, len(v)) for t, v in reversed(list(ids.items()))}
    assert draw(shuffled, 100, 7, "tags") == first
    assert len(first) == len(set(first)) == 100


def test_different_seeds_and_salts_draw_differently() -> None:
    ids = _threads({"x": 300, "y": 120})
    base = draw(ids, 50, 7, "tags")
    assert draw(ids, 50, 8, "tags") != base
    assert draw(ids, 50, 7, "questions") != base


def test_the_order_mixes_threads_rather_than_listing_them_in_blocks() -> None:
    ids = _threads({"x": 200, "y": 200})
    sample = draw(ids, 100, 3, "tags")
    threads = [m.split("-")[0] for m in sample]
    assert threads != sorted(threads)


def test_the_frozen_sample_is_not_redrawn(tmp_path: Path) -> None:
    path = tmp_path / "sample.json"
    ids = _threads({"x": 300, "y": 120})
    first = frozen_or_draw(path, ids, 50, 7, "tags")
    # More messages arrive and the target changes: the frozen file still wins.
    more = _threads({"x": 600, "y": 240, "z": 90})
    assert frozen_or_draw(path, more, 80, 9, "tags") == first
    assert read_frozen(path) == first
    assert path.stat().st_mode & 0o777 == 0o600
    assert list(tmp_path.glob(".sample-*")) == []


def test_the_frozen_file_holds_ids_and_parameters_only(tmp_path: Path) -> None:
    path = tmp_path / "sample.json"
    write_frozen(path, ["a", "b"], 7, 2)
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "version": 1,
        "seed": 7,
        "target": 2,
        "message_ids": ["a", "b"],
    }


@pytest.mark.parametrize(
    "content",
    ["not json", "[]", '{"version": 2, "message_ids": []}', '{"version": 1, "message_ids": [1]}'],
)
def test_a_malformed_frozen_file_is_refused_not_redrawn(tmp_path: Path, content: str) -> None:
    path = tmp_path / "sample.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(FrozenSampleError):
        frozen_or_draw(path, _threads({"x": 10}), 5, 1, "tags")
    assert path.read_text(encoding="utf-8") == content  # left alone
