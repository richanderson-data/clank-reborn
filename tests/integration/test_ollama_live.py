"""Live integration tests against a real local Ollama server.

These are skipped unless CLANK_TEST_OLLAMA=1 is set, so CI and new
contributors never need a local model to run the main test suite.

Run locally with:

    $env:CLANK_TEST_OLLAMA=1
    $env:CLANK_TEST_MODEL="qwen2.5:7b"
    python -m pytest tests/integration -v
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from clank.config import Settings
from clank.db import Store
from clank.providers.base import TextDelta, ToolCall
from clank.providers.ollama import OllamaProvider
from clank.session import ChatSession
from clank.tools import build_default_registry
from clank.tools.permissions import Sandbox
from clank.ui import NullUI

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("CLANK_TEST_OLLAMA") != "1",
        reason="set CLANK_TEST_OLLAMA=1 to run live Ollama tests",
    ),
]

MODEL = os.environ.get("CLANK_TEST_MODEL", "")
HOST = os.environ.get("CLANK_OLLAMA_HOST", "http://localhost:11434")


@pytest.fixture(scope="module")
def provider() -> OllamaProvider:
    if not MODEL:
        pytest.skip("set CLANK_TEST_MODEL to an installed Ollama model")
    return OllamaProvider(MODEL, HOST, timeout=(10.0, 300.0))


def test_lists_models(provider: OllamaProvider) -> None:
    models = provider.list_models()
    assert models, "Ollama reported no models"
    assert any(m.name == provider.model for m in models), (
        f"{provider.model} is not installed; installed: {[m.name for m in models]}"
    )


def test_streams_a_real_response(provider: OllamaProvider) -> None:
    events = list(
        provider.chat_stream(
            [{"role": "user", "content": "Reply with exactly one word: pong"}],
            tools=None,
        )
    )
    text = "".join(event.text for event in events if isinstance(event, TextDelta))
    assert text.strip(), "the model returned no content"


def test_real_tool_round_trip(provider: OllamaProvider, tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "notes.txt").write_text("the secret word is banana\n", encoding="utf-8")
    settings = Settings(
        provider="ollama",
        model=MODEL,
        db_path=tmp_path / "live.db",
        allow_dirs=[project],
    )
    with Store(settings.db_path) as store:
        registry = build_default_registry(Sandbox([project]))
        session = ChatSession(
            store,
            provider,
            registry,
            settings,
            NullUI(),
        )
        result = session.send(
            "Use the list_directory tool on the project root, then read notes.txt "
            "with read_file if it exists, and tell me the secret word."
        )
        messages = store.messages(session.session_id)
    tool_messages = [m for m in messages if m["role"] == "tool"]
    assert result.text.strip(), "the model returned no content"
    if not tool_messages:
        pytest.skip("the model chose not to call tools for this prompt")
    outputs = " ".join(m["content"] for m in tool_messages)
    assert "notes.txt" in outputs or "banana" in outputs
    prompt_dump = " ".join(
        str(call)
        for m in messages
        if m.get("tool_calls")
        for call in m["tool_calls"]
    )
    assert "list_directory" in prompt_dump or "read_file" in prompt_dump


def test_real_streaming_tool_call_events(provider: OllamaProvider) -> None:
    tools = [
        {
            "type": "function",
            "function": {
                "name": "list_directory",
                "description": "List files in the project.",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                },
            },
        }
    ]
    events = list(
        provider.chat_stream(
            [
                {
                    "role": "user",
                    "content": "Call the list_directory tool for the current directory now.",
                }
            ],
            tools=tools,
        )
    )
    calls = [event for event in events if isinstance(event, ToolCall)]
    if not calls:
        pytest.skip("the model chose not to call tools for this prompt")
    assert calls[0].name == "list_directory"
    assert calls[0].parse_error is None
