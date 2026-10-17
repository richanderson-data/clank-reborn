"""Shared fixtures.

Provider tests run against a real local HTTP server that streams NDJSON bytes
over a socket, so Ollama streaming, timeouts, and connection failures are
exercised without needing a running Ollama service.
"""

from __future__ import annotations

import copy
import json
import socket
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from clank.config import Settings
from clank.db import Store
from clank.providers.base import ModelInfo, Provider
from clank.ui import NullUI


class ConnectionAborted(Exception):
    pass


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"

    def log_message(self, *args: object) -> None:
        pass

    def handle(self) -> None:
        try:
            super().handle()
        except (ConnectionAborted, ConnectionResetError, BrokenPipeError, OSError):
            pass

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        return json.loads(raw or b"{}")

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _abort(self) -> None:
        try:
            self.connection.setsockopt(
                socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0)
            )
        except OSError:
            pass
        try:
            self.connection.close()
        except OSError:
            pass
        raise ConnectionAborted()


class OllamaHandler(_Handler):
    def do_GET(self) -> None:
        state = self.server.state  # type: ignore[attr-defined]
        if self.path == "/api/tags":
            self._send_json(200, state.get("tags", {"models": []}))
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        state = self.server.state  # type: ignore[attr-defined]
        if self.path != "/api/chat":
            self._send_json(404, {"error": "not found"})
            return
        body = self._read_json()
        state["requests"].append(body)
        if not state["responses"]:
            self._send_json(500, {"error": "fake server ran out of scripted responses"})
            return
        script = state["responses"].pop(0)
        if isinstance(script, tuple):
            status, payload = script
            self._send_json(status, payload)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()
        for item in script:
            if item == "__abort__":
                self._abort()
            if isinstance(item, tuple) and item[0] == "sleep":
                time.sleep(item[1])
                continue
            if isinstance(item, tuple) and item[0] == "raw":
                self.wfile.write((item[1] + "\n").encode("utf-8"))
            else:
                self.wfile.write((json.dumps(item) + "\n").encode("utf-8"))
            self.wfile.flush()
            time.sleep(0.005)



class FakeServer:
    def __init__(self, httpd: ThreadingHTTPServer, state: dict):
        self.httpd = httpd
        self.state = state
        self.url = f"http://127.0.0.1:{httpd.server_address[1]}"

    def enqueue(self, *scripts: object) -> None:
        self.state["responses"].extend(scripts)

    def requests(self) -> list:
        return self.state["requests"]

    def shutdown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def _start(handler: type[_Handler], state: dict) -> FakeServer:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    httpd.state = state  # type: ignore[attr-defined]
    thread = threading.Thread(
        target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    )
    thread.start()
    return FakeServer(httpd, state)


@pytest.fixture
def ollama_server() -> FakeServer:
    state: dict = {
        "responses": [],
        "requests": [],
        "tags": {"models": [{"name": "qwen3.5:4b", "size": 2_500_000_000}]},
    }
    server = _start(OllamaHandler, state)
    yield server
    server.shutdown()


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    (root / "src").mkdir(parents=True)
    (root / "notes.txt").write_text("hello clank\nsecond line\n", encoding="utf-8")
    (root / "src" / "calc.py").write_text(
        "def add(a, b):\n    return a - b  # bug\n", encoding="utf-8"
    )
    outside = tmp_path / "outside.txt"
    outside.write_text("top secret\n", encoding="utf-8")
    return root


@pytest.fixture
def store(tmp_path: Path) -> Store:
    with Store(tmp_path / "history.db") as s:
        yield s


@pytest.fixture
def settings(project_dir: Path, tmp_path: Path) -> Settings:
    return Settings(
        model="fake-model",
        db_path=tmp_path / "history.db",
        allow_dirs=[project_dir],
    )


@pytest.fixture
def unused_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class FakeProvider(Provider):
    """Scripted provider: each chat_stream call consumes one script."""

    name = "fake"

    def __init__(self, scripts: list):
        super().__init__("fake-model", (1.0, 1.0))
        self.scripts = list(scripts)
        self.calls: list[dict] = []

    def chat_stream(self, messages, tools=None):
        self.calls.append({"messages": copy.deepcopy(messages), "tools": tools})
        script = self.scripts.pop(0)
        if isinstance(script, BaseException):
            raise script
        yield from script

    def list_models(self):
        return [ModelInfo(name="fake-model", size=1_000_000)]


class RecordingUI(NullUI):
    def __init__(self):
        self.events: list[tuple] = []

    def assistant_delta(self, text: str) -> None:
        self.events.append(("delta", text))

    def error(self, text: str) -> None:
        self.events.append(("error", text))

    def warning(self, text: str) -> None:
        self.events.append(("warning", text))

    def info(self, text: str) -> None:
        self.events.append(("info", text))

    def tool_result(self, name, arguments, ok, denied, output) -> None:
        self.events.append(("tool", name, ok, denied, output))

    def kinds(self) -> list[str]:
        return [event[0] for event in self.events]
