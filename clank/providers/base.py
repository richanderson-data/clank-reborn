"""Provider interface and shared message/event types.

Messages use the canonical wire shape described in clank.db. Providers are
responsible for translating that shape to their own API and for streaming
back ChatEvents: TextDelta for content tokens, ToolCall for complete tool
invocations, and Done when the response is finished.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass

import requests


def is_timeout_error(exc: BaseException) -> bool:
    """Detect a read timeout hidden inside a wrapped connection error.

    requests converts urllib3 ReadTimeoutError into ConnectionError while
    streaming, so the class alone cannot tell a stall from a dropped socket.
    """
    if isinstance(exc, requests.exceptions.Timeout):
        return True
    texts = [str(exc).lower()]
    if exc.args and exc.args[0] is not None:
        texts.append(str(exc.args[0]).lower())
    return any("timed out" in text for text in texts)


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict
    parse_error: str | None = None


@dataclass(frozen=True)
class Done:
    reason: str = "stop"


ChatEvent = TextDelta | ToolCall | Done


@dataclass(frozen=True)
class ModelInfo:
    name: str
    size: int | None = None
    modified: str | None = None


class Provider(ABC):
    name: str = "provider"

    def __init__(self, model: str, timeout: tuple[float, float]):
        self.model = model
        self.timeout = timeout

    @abstractmethod
    def chat_stream(
        self, messages: list[dict], tools: list[dict] | None = None
    ) -> Iterator[ChatEvent]:
        """Stream a completion for *messages*, optionally exposing *tools*."""

    @abstractmethod
    def list_models(self) -> list[ModelInfo]:
        """Return models available on this provider."""


def assistant_message(text: str, calls: list[ToolCall]) -> dict:
    message: dict = {"role": "assistant", "content": text}
    if calls:
        message["tool_calls"] = [
            {"id": call.id, "name": call.name, "arguments": call.arguments}
            for call in calls
        ]
    return message


def tool_message(call: ToolCall, content: str) -> dict:
    return {
        "role": "tool",
        "tool_call_id": call.id,
        "name": call.name,
        "content": content,
    }
