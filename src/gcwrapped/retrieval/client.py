"""The llama.cpp embedding client (D102): text goes to a server on this machine and nowhere else.

The privacy properties are the tagger's, from the same code: `check_local_url` refuses any base URL
that is not localhost, 127.0.0.1 or a local compose name, and the opener follows no proxy and no
redirect. Errors carry no message text and no response body.
"""

import json
import math
import urllib.error
import urllib.request
from collections.abc import Sequence
from typing import Protocol

from gcwrapped.retrieval.window import DIMENSIONS, QUERY_INSTRUCTION
from gcwrapped.tagging.llamacpp import check_local_url, local_opener
from gcwrapped.tagging.provider import ProviderUnavailable

REQUEST_TIMEOUT_SECONDS = 600  # a batch of long windows on a busy laptop CPU
MAX_RESPONSE_BYTES = 1 << 25


class EmbedFailure(Exception):
    """The server answered, but not with one valid vector per input."""


class Embedder(Protocol):
    model_id: str

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


def embed_query(embedder: Embedder, query: str) -> list[float]:
    """A query's vector: the one place the instruction prefix is added (D102)."""
    return embedder.embed([QUERY_INSTRUCTION + query])[0]


def normalize(vector: Sequence[float]) -> list[float]:
    """The L2-normalized vector, or EmbedFailure if it is not DIMENSIONS finite numbers."""
    if len(vector) != DIMENSIONS or not all(
        isinstance(x, int | float) and math.isfinite(x) for x in vector
    ):
        raise EmbedFailure
    length = math.sqrt(sum(x * x for x in vector))
    if length == 0:
        raise EmbedFailure
    return [x / length for x in vector]


def parse_embeddings(body: object, expected: int) -> list[list[float]]:
    """The vectors of an OpenAI-style embeddings response, in input order."""
    try:
        assert isinstance(body, dict)
        data = sorted(body["data"], key=lambda item: item["index"])
        if [item["index"] for item in data] != list(range(expected)):
            raise EmbedFailure
        return [normalize(item["embedding"]) for item in data]
    except (AssertionError, KeyError, TypeError):
        raise EmbedFailure from None


class LlamaCppEmbedder:
    def __init__(
        self, base_url: str, *, model_id: str, timeout: float = REQUEST_TIMEOUT_SECONDS
    ) -> None:
        self._base = check_local_url(base_url)
        self.model_id = model_id
        self._timeout = timeout
        self._opener = local_opener()

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        request = urllib.request.Request(
            f"{self._base}/v1/embeddings",
            data=json.dumps({"model": self.model_id, "input": list(texts)}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                body = json.loads(response.read(MAX_RESPONSE_BYTES))
        except urllib.error.HTTPError:
            raise EmbedFailure from None  # an input too long for the batch, or a server error
        except TimeoutError:
            raise EmbedFailure from None
        except (urllib.error.URLError, ConnectionError, OSError):
            raise ProviderUnavailable from None  # nothing is listening: stop the run
        except ValueError:
            raise EmbedFailure from None  # the body was not JSON
        return parse_embeddings(body, len(texts))
