"""A stub llama.cpp embedding server: /v1/embeddings with no model (D102).

Each text becomes a bag-of-words vector: one dimension per word (by crc32), so texts that share
words are close and a test can say which result should rank first. The query instruction prefix is
removed first, so a query is compared on its own words. The vectors are not normalized; the client
does that. Every request body is recorded. Modes:
  ok         a vector per input
  error500   HTTP 500 for every request
  reject     HTTP 500 for any request with an input containing `reject_text`
  short      vectors of the wrong dimension
  redirect   HTTP 302 to `redirect_to` (the client must not follow it)
"""

import json
import re
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from types import TracebackType
from typing import Any, Self

from gcwrapped.retrieval.window import DIMENSIONS, QUERY_INSTRUCTION


def vector_for(text: str) -> list[float]:
    vector = [0.0] * DIMENSIONS
    for word in re.findall(r"\w+", text.removeprefix(QUERY_INSTRUCTION).lower()) or [""]:
        vector[zlib.crc32(word.encode("utf-8")) % DIMENSIONS] += 1.0
    return vector


class _Handler(BaseHTTPRequestHandler):
    server: "StubEmbed"

    def log_message(self, format: str, *args: Any) -> None:
        return  # never log request lines

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        srv = self.server
        srv.requests.append(body)
        inputs: list[str] = body["input"]
        if srv.mode == "redirect":
            self.send_response(302)
            self.send_header("Location", srv.redirect_to)
            self.end_headers()
            return
        rejected = srv.mode == "reject" and any(srv.reject_text in t for t in inputs)
        if srv.mode == "error500" or rejected:
            self.send_response(500)
            self.end_headers()
            return
        size = 8 if srv.mode == "short" else DIMENSIONS
        data = [{"index": i, "embedding": vector_for(t)[:size]} for i, t in enumerate(inputs)]
        payload = json.dumps({"data": data[::-1]})  # out of order: the index decides
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(payload.encode("utf-8"))


class StubEmbed(ThreadingHTTPServer):
    def __init__(self, mode: str = "ok", reject_text: str = "", redirect_to: str = "") -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.mode = mode
        self.reject_text = reject_text
        self.redirect_to = redirect_to
        self.requests: list[dict[str, Any]] = []

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"

    def __enter__(self) -> Self:
        Thread(target=self.serve_forever, daemon=True).start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.shutdown()
        self.server_close()
