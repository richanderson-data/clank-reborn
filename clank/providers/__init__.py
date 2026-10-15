"""Provider factory."""

from __future__ import annotations

from ..config import DEFAULT_DEEPSEEK_MODEL, Settings
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
from .deepseek import DeepSeekProvider
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
    if settings.provider == "ollama":
        if not settings.model:
            raise ConfigError(
                "No Ollama model configured. Pass --model NAME or set CLANK_MODEL. "
                "Run 'clank --list-models' to see installed models."
            )
        return OllamaProvider(settings.model, settings.ollama_host, timeout=timeout)
    if settings.provider == "deepseek":
        return DeepSeekProvider(
            settings.model or DEFAULT_DEEPSEEK_MODEL,
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            timeout=timeout,
        )
    raise ConfigError(f"Unknown provider {settings.provider!r}")
