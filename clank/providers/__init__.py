"""Provider factory."""

from __future__ import annotations

from ..config import Settings
from ..errors import ConfigError
from .base import (
    ChatEvent,
    Done,
    ModelInfo,
    Provider,
    TextDelta,
    ToolCall,
    assistant_message,
    tool_message,
)
from .ollama import OllamaProvider

__all__ = [
    "ChatEvent",
    "Done",
    "ModelInfo",
    "Provider",
    "TextDelta",
    "ToolCall",
    "assistant_message",
    "tool_message",
    "create_provider",
]


def create_provider(settings: Settings) -> Provider:
    timeout = (settings.connect_timeout, settings.read_timeout)
    if not settings.model:
        raise ConfigError(
            "No Ollama model configured. Pass --model NAME or set CLANK_MODEL. "
            "Run 'clank --list-models' to see installed models."
        )
    return OllamaProvider(settings.model, settings.ollama_host, timeout=timeout)
