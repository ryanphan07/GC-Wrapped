"""The served-model check (D126) against small local servers: only the expected model passes."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any

import pytest

from embedding_stub import StubEmbed
from gcwrapped.tagging import model
from gcwrapped.tagging.llamacpp import NonLocalUrlError, ServedModelMismatch, require_served_model
from gcwrapped.tagging.provider import ProviderUnavailable
from tagging_stub import StubLlm

REPO = Path(__file__).resolve().parents[1]


@contextmanager
def _server(status: int, body: str, location: str = "") -> Iterator[str]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def do_GET(self) -> None:
            self.send_response(status)
            if location:
                self.send_header("Location", location)
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def _listing(*ids: str) -> str:
    return json.dumps({"data": [{"id": i} for i in ids]})


def test_the_expected_model_passes_for_both_services() -> None:
    with StubLlm() as llm, StubEmbed() as embed:
        require_served_model(llm.url, model.MODEL_ID)
        require_served_model(f"{embed.url}/", model.EMBED_MODEL_ID)
        assert llm.requests == embed.requests == []  # nothing was sent to either


def test_another_served_model_is_refused() -> None:
    with StubLlm(served=model.SMALL_MODEL_ID) as llm, pytest.raises(ServedModelMismatch):
        require_served_model(llm.url, model.MODEL_ID)
    with StubEmbed() as embed, pytest.raises(ServedModelMismatch):
        require_served_model(embed.url, model.MODEL_ID)  # the embed server on the llm's URL


@pytest.mark.parametrize(
    ("status", "body"),
    [
        (200, _listing()),
        (200, _listing(model.MODEL_ID, "another")),
        (200, _listing(model.MODEL_FILE)),
        (200, "<html>"),
        (200, "[]"),
        (200, '{"data": "x"}'),
        (200, '{"models": []}'),
        (404, _listing(model.MODEL_ID)),
        (503, _listing(model.MODEL_ID)),
    ],
)
def test_any_other_answer_fails_closed(status: int, body: str) -> None:
    with _server(status, body) as url, pytest.raises(ServedModelMismatch):
        require_served_model(url, model.MODEL_ID)


def test_a_redirect_is_not_followed() -> None:
    with (
        StubLlm() as elsewhere,
        _server(302, "", location=f"{elsewhere.url}/v1/models") as url,
        pytest.raises(ServedModelMismatch),
    ):
        require_served_model(url, model.MODEL_ID)


def test_nothing_listening_and_a_non_local_url_are_refused_too() -> None:
    with StubLlm() as gone:
        url = gone.url
    with pytest.raises(ProviderUnavailable):
        require_served_model(url, model.MODEL_ID)
    with pytest.raises(NonLocalUrlError):
        require_served_model("http://example.com:8081", model.MODEL_ID)


def test_the_compose_aliases_are_the_ids_the_check_expects() -> None:
    compose = (REPO / "docker-compose.yml").read_text(encoding="utf-8")
    assert f"- {model.EMBED_MODEL_ID}\n" in compose
    assert f"${{GCW_LLM_MODEL_ALIAS:-{model.MODEL_ID}}}" in compose
