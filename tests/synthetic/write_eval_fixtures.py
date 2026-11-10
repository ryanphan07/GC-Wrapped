"""Write the committed synthetic labeled sets: tests/fixtures/eval/{tag_labels,questions}.csv.

Usage: uv run python tests/synthetic/write_eval_fixtures.py [--out DIR]

Generates the default-seed synthetic exports into a temporary directory, parses both with the real
parser, and looks each message in `eval_set.EVAL_MESSAGES` up by its text inside the eval thread to
get its message_key (the ID the warehouse uses). Files are written with the same writers the
labeling tool uses, so the schemas are the real ones. A test regenerates them and requires the
committed files to be identical, so the CSVs cannot drift from the generator.

The labeled_at stamp is a constant: the files must be byte-for-byte reproducible.
"""

import argparse
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gcwrapped.labeling.files import write_questions, write_tags
from gcwrapped.labeling.schema import (
    QUESTIONS_FILE,
    TAG_LABELS_FILE,
    QuestionLabel,
    TagLabel,
)
from gcwrapped.parsers.instagram import parse_export_consented
from synthetic.eval_set import EVAL_MESSAGES, EVAL_QUESTIONS
from synthetic.instagram import EVAL_FOLDER, SYNTHETIC_THREAD_IDS, generate_pair

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "eval"
LABELED_AT = "2026-11-10T00:00:00+00:00"
DEFAULT_SEED = 1234
# The tags and questions files in the fixture dir use the real names from the labeling schema.


def eval_message_ids(seed: int = DEFAULT_SEED) -> list[str]:
    """message_key of each EVAL_MESSAGES entry, in order. Raises if any text is missing or twice."""
    thread_id = SYNTHETIC_THREAD_IDS[-1]
    assert EVAL_FOLDER.endswith(thread_id)
    with tempfile.TemporaryDirectory() as tmp:
        root_a, root_b = generate_pair(Path(tmp), seed)
        by_text: dict[str, set[str]] = {}
        for message in parse_export_consented([root_a, root_b], frozenset({thread_id})):
            by_text.setdefault(message.content or "", set()).add(message.message_key)
    ids: list[str] = []
    for _, _, text in EVAL_MESSAGES:
        keys = by_text.get(text, set())
        if len(keys) != 1:
            raise SystemExit(f"eval message not found exactly once in the generated thread: {text}")
        ids.append(next(iter(keys)))
    return ids


def build(seed: int = DEFAULT_SEED) -> tuple[list[TagLabel], list[QuestionLabel]]:
    ids = eval_message_ids(seed)
    tags = [
        TagLabel(ids[i], topic, sentiment, LABELED_AT)
        for i, (topic, sentiment, _) in enumerate(EVAL_MESSAGES)
    ]
    questions = [
        QuestionLabel(
            f"q{n:03d}",
            question,
            tuple(ids[i] for i in sorted({source, *extra})),
            ids[source],
            LABELED_AT,
        )
        for n, (question, source, extra) in enumerate(EVAL_QUESTIONS, start=1)
    ]
    return tags, questions


def main() -> None:
    ap = argparse.ArgumentParser(description="Write the synthetic labeled sets")
    ap.add_argument("--out", type=Path, default=FIXTURE_DIR)
    args = ap.parse_args()
    tags, questions = build()
    args.out.mkdir(parents=True, exist_ok=True)
    write_tags(args.out / TAG_LABELS_FILE, tags)
    write_questions(args.out / QUESTIONS_FILE, questions)
    print(f"wrote {len(tags)} tag labels and {len(questions)} questions to {args.out}")


if __name__ == "__main__":
    main()
