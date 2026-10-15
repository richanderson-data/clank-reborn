"""Loopback browser workspace; no extra dependencies or external assets."""

from __future__ import annotations

import json
import secrets
import sqlite3
import threading
import webbrowser
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .config import Settings
from .db import Store
from .errors import ClankError
from .providers import create_provider
from .providers.ollama import OllamaProvider
from .session import open_session
from .tools.autonomous import AutonomousTools
from .tools.permissions import Sandbox
from .ui import UI

MAX_BODY = 64_000


class StreamUI(UI):
    def __init__(self, emit):
        self.emit = emit

    def assistant_delta(self, text):
        self.emit("delta", {"text": text})

    def tool_result(self, name, arguments, ok, denied, output):
        self.emit(
            "tool",
            {
                "name": name,
                "arguments": arguments,
                "ok": ok,
                "denied": denied,
                "output": output[:20_000],
            },
        )

    def error(self, text):
        self.emit("notice", {"text": text})

    def info(self, text):
        self.emit("progress", {"text": text})


class WorkspaceServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, settings: Settings, port: int = 8787):
        self.settings = settings
        self.token = secrets.token_urlsafe(32)
        self.turn_lock = threading.Lock()
        self.cancelled = threading.Event()
        super().__init__(("127.0.0.1", port), WorkspaceHandler)


class WorkspaceHandler(BaseHTTPRequestHandler):
    server: WorkspaceServer

    def log_message(self, *args):
        # Never log prompts, local paths, authorization, or conversation IDs.
        pass

    def headers_for(self, status, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; "
            "script-src 'self'; style-src 'self'; connect-src 'self'; "
            "img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'",
        )
        self.end_headers()

    def respond(self, status, data):
        self.headers_for(status, "application/json; charset=utf-8")
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def allowed_host(self):
        return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

    def authenticated(self):
        value = self.headers.get("Authorization", "")
        return secrets.compare_digest(
            value.encode("utf-8"), ("Bearer " + self.server.token).encode("utf-8")
        )

    def setup(self):
        super().setup()
        self.connection.settimeout(15)

    def do_GET(self):
        if not self.allowed_host():
            self.respond(403, {"error": "Invalid local workspace address"})
            return
        route = urlparse(self.path)
        static = {
            "/": "index.html",
            "/app.js": "app.js",
            "/style.css": "style.css",
            "/export.css": "export.css",
        }
        if route.path in static:
            asset = Path(__file__).parent / "web_assets" / static[route.path]
            kind = {
                "/": "text/html",
                "/app.js": "text/javascript",
                "/style.css": "text/css",
                "/export.css": "text/css",
            }
            self.headers_for(200, kind[route.path] + "; charset=utf-8")
            self.wfile.write(asset.read_bytes())
            return
        if not self.authenticated():
            self.respond(401, {"error": "Open the private link printed when Clank starts"})
            return
        try:
            settings = self.server.settings
            if route.path == "/api/status":
                provider = OllamaProvider("", settings.ollama_host, timeout=(3, 10))
                error = ""
                try:
                    models = [m.name for m in provider.list_models()]
                except ClankError as exc:
                    models = []
                    error = str(exc)
                self.respond(
                    200,
                    {
                        "models": models,
                        "model": settings.model,
                        "provider": settings.provider,
                        "roots": [str(p.resolve()) for p in settings.allow_dirs],
                        "tools": settings.tools_enabled,
                        "web_available": settings.tools_enabled and settings.web_enabled,
                        "autonomous_available": settings.autonomous,
                        "max_tool_rounds": settings.max_tool_rounds,
                        "test_command": settings.test_command if settings.autonomous else [],
                        "deepseek_available": bool(settings.deepseek_api_key),
                        "error": error,
                    },
                )
            elif route.path == "/api/changes":
                actions = self.actions() if settings.autonomous else None
                self.respond(200, {"changes": actions.changes() if actions else []})
            elif route.path == "/api/sessions":
                with Store(settings.db_path) as store:
                    self.respond(200, {"sessions": store.list_sessions(limit=100)})
            elif route.path in ("/api/history", "/api/export"):
                sid = parse_qs(route.query).get("id", [""])[0]
                with Store(settings.db_path) as store:
                    if not store.get_session(sid):
                        self.respond(404, {"error": "Conversation not found"})
                    else:
                        messages = store.messages(sid)
                        if route.path == "/api/export":
                            self.respond(200, {"markdown": export_markdown(messages)})
                        else:
                            self.respond(200, {"messages": messages})
            else:
                self.respond(404, {"error": "Not found"})
        except (ClankError, OSError) as exc:
            self.respond(500, {"error": str(exc)})
        except sqlite3.Error:
            self.respond(
                500,
                {
                    "error": "Cannot read the conversation database. "
                    "Check its location and permissions; do not delete your history."
                },
            )

    def do_POST(self):
        origin = self.headers.get("Origin")
        expected = f"http://127.0.0.1:{self.server.server_port}"
        if not self.allowed_host() or (origin and origin != expected):
            self.respond(403, {"error": "Cross-origin requests are not allowed"})
            return
        if not self.authenticated():
            self.respond(401, {"error": "Workspace authorization required"})
            return
        if self.path == "/api/cancel":
            if not self.server.turn_lock.locked():
                self.respond(409, {"error": "No task is running"})
                return
            self.server.cancelled.set()
            self.respond(200, {"message": "Stop requested; waiting for the current step to end"})
            return
        if self.path not in ("/api/chat", "/api/restore"):
            self.respond(404, {"error": "Not found"})
            return
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= MAX_BODY:
                raise ValueError("Message is too large or empty")
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise ValueError("Expected a message object")
            if self.path == "/api/restore":
                if not self.server.settings.autonomous:
                    raise ValueError("Launch with --autonomous to enable undo")
                if not self.server.turn_lock.acquire(blocking=False):
                    self.respond(
                        409, {"error": "Wait for the running task before undoing a change"}
                    )
                    return
                try:
                    self.respond(200, {"message": self.actions().restore(data.get("id"))})
                finally:
                    self.server.turn_lock.release()
                return
            prompt = data.get("prompt", "")
            model = data.get("model", "")
            sid = data.get("session_id")
            if not isinstance(prompt, str) or not prompt.strip():
                raise ValueError("Write a message first")
            if not isinstance(model, str) or not model.strip():
                raise ValueError("Choose an installed model first")
            if sid is not None and not isinstance(sid, str):
                raise ValueError("Invalid conversation ID")
            provider_name = data.get("provider", "ollama")
            if provider_name not in ("ollama", "deepseek"):
                raise ValueError("Unknown provider")
            if provider_name == "deepseek" and data.get("cloud_consent") is not True:
                raise ValueError("Confirm that history and file results may be sent to DeepSeek")
            autonomous = data.get("autonomous") is True
            if autonomous and not self.server.settings.autonomous:
                raise ValueError("Autonomous mode is not enabled for this server")
            if autonomous and provider_name == "deepseek" and data.get("cloud_tools") is not True:
                raise ValueError("Cloud autonomy needs explicit file-access consent")
            settings = replace(
                self.server.settings,
                provider=provider_name,
                model=model,
                cloud_consent=provider_name == "deepseek",
                autonomous=autonomous,
                web_enabled=self.server.settings.web_enabled and data.get("web_enabled", True) is True,
                file_tools_enabled=self.server.settings.file_tools_enabled
                and (provider_name == "ollama" or data.get("cloud_tools") is True),
            )
            provider = create_provider(settings)
        except (ValueError, ClankError) as exc:
            self.respond(400, {"error": str(exc)})
            return
        if not self.server.turn_lock.acquire(blocking=False):
            self.respond(409, {"error": "Clank is answering another message. Try again shortly."})
            return
        try:
            self.server.cancelled.clear()
            with Store(settings.db_path) as store:
                try:
                    session = open_session(
                        store,
                        settings,
                        StreamUI(self.emit),
                        provider,
                        session_id=sid,
                        cancelled=self.server.cancelled,
                    )
                except ClankError as exc:
                    self.respond(404, {"error": str(exc)})
                    return
                self.headers_for(200, "text/event-stream; charset=utf-8")
                self.emit("session", {"id": session.session_id})
                try:
                    result = session.send(prompt)
                    self.emit(
                        "done",
                        {
                            "tools": result.tool_calls,
                            "stopped_early": result.stopped_early,
                            "changed_files": result.changed_files,
                            "test_status": result.test_status,
                        },
                    )
                except ClankError as exc:
                    self.emit("error", {"text": str(exc)})
                except sqlite3.Error:
                    self.emit(
                        "error",
                        {
                            "text": "Cannot save this conversation. "
                            "Check database permissions and available disk space."
                        },
                    )
                except Exception:
                    self.emit("error", {"text": "Unexpected task failure. Completed edits remain in Changes; reload saved history before retrying. No unfinished answer was saved."})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except (sqlite3.Error, OSError, ClankError):
            # This branch occurs before streaming, or when the socket cannot write.
            try:
                self.respond(500, {"error": "Cannot open the conversation database or connection."})
            except OSError:
                pass
        finally:
            self.server.turn_lock.release()

    def emit(self, event, data):
        self.wfile.write(
            (f"event: {event}\ndata: " + json.dumps(data, ensure_ascii=False) + "\n\n").encode(
                "utf-8"
            )
        )
        self.wfile.flush()

    def actions(self):
        settings = self.server.settings
        return AutonomousTools(
            Sandbox(settings.allow_dirs), settings.db_path.parent, settings.test_command
        )


def export_markdown(messages: list[dict]) -> str:
    parts = ["# Clank conversation\n"]
    for message in messages:
        parts.append(f"## {message['role'].upper()}\n")
        if message.get("name"):
            parts.append(f"Tool: {message['name']}\n")
        parts.append(message.get("content", "") + "\n")
        for call in message.get("tool_calls", []):
            parts.append(
                "Tool request: "
                + call["name"]
                + "\n\n```json\n"
                + json.dumps(call["arguments"], ensure_ascii=False, indent=2)
                + "\n```\n"
            )
    return "\n".join(parts)


def serve(settings: Settings, port: int):
    if not 0 <= port <= 65535:
        print("error: port must be between 0 and 65535")
        return 2
    try:
        if settings.tools_enabled:
            for root in settings.allow_dirs:
                if not root.is_dir():
                    print(f"error: approved project directory does not exist: {root}")
                    return 2
        with Store(settings.db_path):
            pass
        server = WorkspaceServer(settings, port)
    except (OSError, sqlite3.Error, ClankError) as exc:
        print(f"error: could not start the workspace: {exc}")
        return 1
    url = f"http://127.0.0.1:{server.server_port}/#token={server.token}"
    print(
        "Clank workspace is ready. Open this private link on this computer:\n"
        f"{url}\n"
        "Press Ctrl+C to stop.",
        flush=True,
    )
    try:
        webbrowser.open(url)
    except OSError:
        pass  # The printed link also works when no default browser is configured.
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
