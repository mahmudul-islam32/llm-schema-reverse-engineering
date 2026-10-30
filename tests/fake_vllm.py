"""A tiny OpenAI-compatible server for tests (stands in for a vLLM endpoint)."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class FakeVLLM:
    def __init__(self, content: str | dict[str, Any], model: str = "fake/model-7b") -> None:
        self.content = content if isinstance(content, str) else json.dumps(content)
        self.model = model
        self.requests: list[dict[str, Any]] = []
        self.fail_next = 0
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def _send(self, code: int, body: dict[str, Any]) -> None:
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:
                self._send(200, {"data": [{"id": outer.model}]})

            def do_POST(self) -> None:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append(body)
                if outer.fail_next:
                    outer.fail_next -= 1
                    self._send(503, {"error": "busy"})
                    return
                if "ranking" in body["messages"][0]["content"]:
                    content = json.dumps({"ranking": [{"id": 0, "confidence": 0.9}]})
                else:
                    content = outer.content
                self._send(
                    200,
                    {
                        "model": outer.model,
                        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 100, "completion_tokens": 20},
                    },
                )

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}/v1"

    def __enter__(self) -> FakeVLLM:
        self.thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.server.shutdown()
        self.server.server_close()
