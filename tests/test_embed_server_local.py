"""The real embedding server leaks no text (D102). Runs only where the embed service is up.

Skipped when nothing answers at the configured URL, so CI's integration job skips it. Locally:
`docker compose --env-file .env --profile embed up -d --wait embed`, then `make test-integration`.
"""

import math
import subprocess
import urllib.error
import urllib.request

import pytest

from gcwrapped.retrieval import commands
from gcwrapped.retrieval.client import LlamaCppEmbedder, embed_query
from gcwrapped.tagging import model

pytestmark = pytest.mark.integration

SENTINEL = "SENTINELLEAKCHECK"


def _status(url: str) -> int:
    try:
        with urllib.request.urlopen(url, timeout=5) as response:  # the local embed service only
            return int(response.status)
    except urllib.error.HTTPError as exc:
        return exc.code


@pytest.fixture
def server() -> str:
    url = commands.embed_url()
    try:
        if _status(f"{url}/health") != 200:
            pytest.skip("embed service is not healthy")
    except OSError:
        pytest.skip("embed service is not running")
    return url


def test_real_vectors_are_unit_length_related_texts_are_closer_and_nothing_is_logged(
    server: str,
) -> None:
    client = LlamaCppEmbedder(server, model_id=model.EMBED_MODEL_ID)
    related, unrelated = client.embed(
        [f"sam: dinner is at seven {SENTINEL}", f"sam: my laptop battery died {SENTINEL}"]
    )
    query = embed_query(client, f"what time is dinner {SENTINEL}")

    def cosine(a: list[float], b: list[float]) -> float:
        return sum(x * y for x, y in zip(a, b, strict=True))

    assert all(math.isclose(cosine(v, v), 1.0) for v in (related, unrelated, query))
    assert cosine(query, related) > cosine(query, unrelated)
    logs = subprocess.run(
        [
            "docker",
            "compose",
            "--env-file",
            ".env",
            "--profile",
            "embed",
            "logs",
            "--no-color",
            "embed",
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    combined = logs.stdout + logs.stderr
    assert "request" in combined.lower() or "slot" in combined.lower()  # we did read real logs
    assert SENTINEL not in combined and "dinner is at seven" not in combined
    assert _status(f"{server}/slots") == 501
    assert _status(f"{server}/metrics") == 501
    assert _status(f"{server}/") == 404
