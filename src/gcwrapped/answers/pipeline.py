"""One question to one answer (D111): retrieve with the default method, build the numbered
excerpts, ask the local model under the schema, check the output and map citations to messages.

An output is one of four things. Answered: answerable, at least one citation, and every [n] in
the text is one of the citations. Abstained: not answerable and no citations. Timed out: the
server did not respond in time (D116). Invalid: anything else, including a request the server
refused; its text is dropped. No request is retried. Nothing here prints, logs or stores.
"""

import json
import re
from dataclasses import dataclass
from typing import Any, Protocol

import psycopg

from gcwrapped.answers.excerpts import TOP_K, Source, load_excerpts
from gcwrapped.answers.prompt import (
    MAX_ANSWER_CHARS,
    MAX_CITATIONS,
    MAX_TOKENS,
    build_user_content,
    response_schema,
    system_prompt,
)
from gcwrapped.retrieval import evaluation as retrieval
from gcwrapped.retrieval.search import Retriever
from gcwrapped.tagging.llamacpp import ChatFailure, ChatTimeout

ANSWERED = "answered"
ABSTAINED = "abstained"
INVALID = "invalid"
TIMED_OUT = "timed_out"
OUTCOMES = (ANSWERED, ABSTAINED, INVALID, TIMED_OUT)
SCHEMA_NAME = "answer"
REQUEST_TIMEOUT_SECONDS = 600  # a long prompt on a busy laptop CPU
_MARKER = re.compile(r"\[(\s*\d+(?:\s*,\s*\d+)*\s*)\]")  # [3], and the grouped form [1, 2]


class ChatModel(Protocol):
    def complete(
        self,
        system: str,
        user: str,
        *,
        schema_name: str,
        schema: dict[str, Any],
        max_tokens: int,
    ) -> object: ...


@dataclass(frozen=True)
class Answer:
    outcome: str
    text: str  # with its [n] markers; empty unless answered
    sources: tuple[Source, ...]  # every message the model was shown, numbered 1..N
    citations: tuple[int, ...]  # numbers into `sources`, each once; empty unless answered
    prompt_tokens: int | None = None  # the server's count (D116); None if it reported none
    completion_tokens: int | None = None

    @property
    def cited(self) -> tuple[Source, ...]:
        return tuple(self.sources[n - 1] for n in self.citations)


def markers(text: str) -> set[int]:
    """Every number written as a [n] marker in the text."""
    return {int(n) for group in _MARKER.findall(text) for n in group.split(",")}


def _output(body: object) -> dict[str, object] | None:
    """The three fields of a response whose content is exactly that JSON object, else None."""
    try:
        output = json.loads(body["choices"][0]["message"]["content"])  # type: ignore[index]
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    if not isinstance(output, dict) or set(output) != {"answerable", "citations", "answer"}:
        return None
    return output


def token_usage(body: object) -> tuple[int | None, int | None]:
    """(prompt tokens, completion tokens) from the response's `usage`, each None when it is
    absent or not a count."""
    usage = body.get("usage") if isinstance(body, dict) else None
    if not isinstance(usage, dict):
        return None, None
    prompt, completion = (
        value if type(value) is int and value >= 0 else None
        for value in (usage.get("prompt_tokens"), usage.get("completion_tokens"))
    )
    return prompt, completion


def parse_answer(body: object, source_count: int) -> tuple[str, str, tuple[int, ...]]:
    """(outcome, text, citations) of a chat response. Checks everything the grammar should
    already guarantee, and what it cannot: that the markers match the citations."""
    invalid: tuple[str, str, tuple[int, ...]] = (INVALID, "", ())
    output = _output(body)
    if output is None:
        return invalid
    answerable, citations, text = output["answerable"], output["citations"], output["answer"]
    if not isinstance(answerable, bool) or not isinstance(text, str):
        return invalid
    if not isinstance(citations, list) or len(citations) > MAX_CITATIONS:
        return invalid
    # `type(n) is int`: a JSON true is an int to isinstance
    if not all(type(n) is int and 1 <= n <= source_count for n in citations):
        return invalid
    if not answerable:
        return invalid if citations else (ABSTAINED, "", ())
    unique = tuple(dict.fromkeys(citations))  # repeats are dropped, first order kept
    if not unique or not text.strip() or len(text) > MAX_ANSWER_CHARS:
        return invalid
    if not markers(text) <= set(unique):
        return invalid
    return ANSWERED, text, unique


class AnswerPipeline:
    """Answering bound to one connection, one retriever, one model, one retrieval method and one
    prompt variant (D121). The variant has no default, so no caller can ignore the promoted one."""

    def __init__(
        self,
        conn: psycopg.Connection,
        retriever: Retriever,
        model: ChatModel,
        method: str,
        *,
        variant: str,
    ) -> None:
        if method not in retrieval.METHODS:
            raise retrieval.UnknownMethodError
        self._system = system_prompt(variant)
        self._conn = conn
        self._version = retriever.version
        self._search = retriever.vector if method == retrieval.VECTOR else retriever.hybrid
        self._model = model

    def answer(self, question: str) -> Answer:
        """A ProviderUnavailable (no server) or EmbedFailure (the question) propagates."""
        hits = self._search(question, TOP_K)
        excerpts = load_excerpts(self._conn, self._version, [h.message_id for h in hits])
        sources = tuple(source for excerpt in excerpts for source in excerpt)
        if not sources:  # nothing to show, so there is nothing to ask the model
            return Answer(ABSTAINED, "", (), ())
        try:
            body = self._model.complete(
                self._system,
                build_user_content(excerpts, question),
                schema_name=SCHEMA_NAME,
                schema=response_schema(len(sources)),
                max_tokens=MAX_TOKENS,
            )
        except ChatTimeout:
            return Answer(TIMED_OUT, "", sources, ())
        except ChatFailure:
            return Answer(INVALID, "", sources, ())
        outcome, text, citations = parse_answer(body, len(sources))
        return Answer(outcome, text, sources, citations, *token_usage(body))
