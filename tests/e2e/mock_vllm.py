"""Mock vLLM server for E2E testing.

Runs as a background thread, responds to:
- GET  /health                  -> 200 OK
- POST /v1/responses            -> canned response
- POST /v1/chat/completions     -> canned chat response
- GET  /v1/models               -> model list
"""

from __future__ import annotations

import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler


class MockVLLMHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self._respond(200, {"status": "ok"})
        elif self.path == "/v1/models":
            self._respond(
                200,
                {
                    "object": "list",
                    "data": [
                        {
                            "id": "Qwen/Qwen3-32B",
                            "object": "model",
                            "owned_by": "zerollm",
                        }
                    ],
                },
            )
        else:
            self._respond(404, {"error": "not found"})

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length else b"{}"

        if self.path == "/v1/responses":
            self._respond(
                200,
                {
                    "id": "resp-mock",
                    "object": "response",
                    "output_text": "Hello! I'm a mock response.",
                },
            )
        elif self.path == "/v1/chat/completions":
            self._respond(
                200,
                {
                    "id": "chatcmpl-mock",
                    "object": "chat.completion",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": "Hello! I'm a mock vLLM response.",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 8,
                        "total_tokens": 18,
                    },
                },
            )
        else:
            self._respond(404, {"error": "not found"})

    def _respond(self, status: int, body: dict):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(body).encode())

    def log_message(self, format, *args):
        pass  # Suppress request logs during tests


class MockVLLMServer:
    def __init__(self, host: str = "127.0.0.1", port: int = 0):
        self.server = HTTPServer((host, port), MockVLLMHandler)
        self.host = host
        self.port = self.server.server_address[1]  # actual port (0 = auto-assign)
        self._thread: threading.Thread | None = None

    def start(self):
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self):
        self.server.shutdown()
        if self._thread:
            self._thread.join(timeout=5)
