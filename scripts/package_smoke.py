"""Smoke-check a wheel's installed contents without importing the checkout.

Run with Python -I after installing the wheel into a temporary --target folder.
No provider calls, credentials, or existing conversations are used.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import threading
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("installed", type=Path)
    args = parser.parse_args()
    target = args.installed.resolve()
    sys.path.insert(0, str(target))
    import requests

    import clank
    import clank.web as web
    from clank.config import Settings
    from clank.providers.base import Done, TextDelta, ToolCall
    from clank.tools import build_default_registry
    from clank.tools.autonomous import AutonomousTools
    from clank.tools.permissions import Sandbox
    from clank.web import WorkspaceServer

    assert Path(clank.__file__).resolve().is_relative_to(target), "Imported checkout, not wheel"

    class SmokeProvider:
        def chat_stream(self, messages, tools=None):
            if messages[-1]["role"] == "user" and messages[-1]["content"] == "repair":
                yield ToolCall(
                    "edit",
                    "edit_file",
                    {"path": "calc.py", "old_text": "return a-b", "new_text": "return a+b"},
                )
                yield Done()
                return
            yield TextDelta("Installed wheel works")
            yield Done()

    web.create_provider = lambda settings: SmokeProvider()
    with tempfile.TemporaryDirectory(prefix="clank-package-") as temporary:
        project = Path(temporary) / "project"
        project.mkdir()
        original = "def add(a,b): return a-b\n"
        (project / "calc.py").write_text(original)
        sandbox = Sandbox([project])
        names = set(build_default_registry(sandbox, web_enabled=True).names())
        assert {"git_info", "web_search", "read_webpage", "get_weather"} <= names
        actions = AutonomousTools(sandbox, Path(temporary) / "command-state", [])
        checked = actions.run_command([sys.executable, "-c", "print('real build check')"])
        assert "real build check" in checked and "exit code 0" in checked
        settings = Settings(
            model="smoke",
            db_path=Path(temporary) / "smoke.db",
            allow_dirs=[project],
            autonomous=True,
            test_command=[
                sys.executable,
                "-c",
                "import calc; assert calc.add(2,3)==5; print('actual assertion passed')",
            ],
        )
        assert settings.max_tool_rounds == 100
        server = WorkspaceServer(settings, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}"
        headers = {"Authorization": "Bearer " + server.token}
        try:
            for asset in ("/", "/app.js", "/style.css", "/export.css"):
                response = requests.get(url + asset, timeout=5)
                assert response.status_code == 200 and response.content, asset
            response = requests.post(
                url + "/api/chat",
                headers=headers,
                json={"prompt": "hello", "model": "smoke"},
                timeout=5,
            )
            assert "Installed wheel works" in response.text and "event: done" in response.text
            sessions = requests.get(url + "/api/sessions", headers=headers, timeout=5).json()
            sid = sessions["sessions"][0]["id"]
            exported = requests.get(
                url + "/api/export?id=" + sid, headers=headers, timeout=5
            ).json()["markdown"]
            assert "Installed wheel works" in exported
            repaired = requests.post(
                url + "/api/chat",
                headers=headers,
                json={"prompt": "repair", "model": "smoke", "autonomous": True},
                timeout=10,
            )
            assert '"test_status": "passed"' in repaired.text
            changes = requests.get(url + "/api/changes", headers=headers, timeout=5).json()
            restored = requests.post(
                url + "/api/restore",
                headers=headers,
                json={"id": changes["changes"][0]["id"]},
                timeout=5,
            )
            assert restored.status_code == 200
            assert (project / "calc.py").read_text() == original
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
    print("Installed-wheel smoke passed: assets, streaming, SQLite, export, edit, real tests, undo, commands, internet/Git registration, 100-round default")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
