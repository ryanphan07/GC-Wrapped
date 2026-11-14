"""A stub llama.cpp server: schema-valid JSON for /v1/chat/completions, no model (D89).

Used by the tests and by CI, which never downloads a model. It answers with a topic and sentiment
chosen deterministically from the user content, and records every request body so a test can assert
what was sent. Modes let a test provoke the failures the client must handle:
  ok            valid answer
  invalid_once  an invalid answer for the first request only, then valid
  invalid       always an invalid answer
  error500      HTTP 500
  redirect      HTTP 302 to `redirect_to` (the client must not follow it)
Run as a script (`python tests/tagging_stub.py --port 8099`) to serve until killed.
"""

import argparse
import json
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from types import TracebackType
from typing import Any, Self

from gcwrapped.labeling.schema import SENTIMENTS, TOPICS


def answer_for(user: str) -> dict[str, str]:
    h = zlib.crc32(user.encode("utf-8"))
    return {"topic": TOPICS[h % len(TOPICS)], "sentiment": SENTIMENTS[(h // 7) % len(SENTIMENTS)]}


class _Handler(BaseHTTPRequestHandler):
    server: "StubLlm"

    def log_message(self, format: str, *args: Any) -> None:
        return  # never log request lines

    def do_GET(self) -> None:
        self.send_response(200 if self.path == "/health" else 404)
        self.end_headers()

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        srv = self.server
        srv.requests.append(body)
        n = len(srv.requests)
        if srv.mode == "error500":
            self.send_response(500)
            self.end_headers()
            return
        if srv.mode == "redirect":
            self.send_response(302)
            self.send_header("Location", srv.redirect_to)
            self.end_headers()
            return
        user = body["messages"][-1]["content"]
        content = json.dumps(answer_for(user))
        if srv.mode == "invalid" or (srv.mode == "invalid_once" and n == 1):
            content = json.dumps({"topic": "not-a-topic", "sentiment": "positive"})
        payload = json.dumps({"choices": [{"message": {"role": "assistant", "content": content}}]})
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(payload.encode("utf-8"))


class StubLlm(ThreadingHTTPServer):
    def __init__(self, port: int = 0, mode: str = "ok", redirect_to: str = "") -> None:
        super().__init__(("127.0.0.1", port), _Handler)
        self.mode = mode
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8099)
    StubLlm(parser.parse_args().port).serve_forever()
