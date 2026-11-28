"""The identity record (D136): one hash over what search returns and what the model would be sent,
so a change meant to alter speed only can be shown to alter nothing.

For each question and both retrieval methods it records the ranked IDs of the search the retrieval
eval runs, then, for each prompt variant, the numbered source IDs and the whole request the answer
pipeline would send. The pipeline runs as it does for an answer, against a model that records the
request instead of being asked. Only the hash and counts leave this module; nothing is stored.
"""

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import psycopg

from gcwrapped.answers.pipeline import AnswerPipeline
from gcwrapped.answers.prompt import VARIANTS
from gcwrapped.labeling.schema import QuestionLabel
from gcwrapped.retrieval import evaluation as retrieval
from gcwrapped.retrieval.search import Retriever


@dataclass(frozen=True)
class Record:
    questions: int
    prompts: int  # requests the model would have been sent
    sha256: str  # the first 12 hex characters
    searchable: int
    corpus_hash: str
    embed_version: str


class _Capture:
    """Stands where the chat model does: keeps each request, answers with nothing."""

    def __init__(self) -> None:
        self.requests: list[list[object]] = []

    def complete(
        self,
        system: str,
        user: str,
        *,
        schema_name: str,
        schema: dict[str, Any],
        max_tokens: int,
    ) -> object:
        self.requests.append([system, user, schema_name, schema, max_tokens])
        return {}


def build_record(
    conn: psycopg.Connection, retriever: Retriever, questions: Sequence[QuestionLabel]
) -> Record:
    capture = _Capture()
    entries: list[object] = []
    for question in questions:
        for method in retrieval.METHODS:
            search = retriever.vector if method == retrieval.VECTOR else retriever.hybrid
            hits = [h.message_id for h in search(question.question, retrieval.DEPTH)]
            entries.append([question.question_id, method, hits])
            for variant in VARIANTS:
                pipeline = AnswerPipeline(conn, retriever, capture, method, variant=variant)
                sources = pipeline.answer(question.question).sources
                ids = [s.message.message_id for s in sources]
                entries.append([question.question_id, method, variant, ids])
    searchable = retriever.searchable_ids()
    # Keys stay in the order built: the schema's field order is part of the request (D111).
    material = json.dumps([entries, capture.requests]).encode("utf-8")
    return Record(
        questions=len(questions),
        prompts=len(capture.requests),
        sha256=hashlib.sha256(material).hexdigest()[:12],
        searchable=len(searchable),
        corpus_hash=retrieval.corpus_hash(searchable),
        embed_version=retriever.version,
    )
