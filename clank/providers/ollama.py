"""Ollama provider.

Talks to the native Ollama HTTP API (/api/chat, /api/tags) using NDJSON
streaming. No official client library is used so the request/response
shapes stay visible and testable.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator

import requests

from ..errors import (
    ProviderConnectionError,
    ProviderHTTPError,
    ProviderProtocolError,
    StreamInterrupted,
)
from .base import ChatEvent, Done, ModelInfo, Provider, TextDelta, ToolCall, is_timeout_error


class OllamaProvider(Provider):
    name = "ollama"

    def __init__(
        self,
        model: str,
        host: str = "http://localhost:11434",
        timeout: tuple[float, float] = (10.0, 300.0),
    ):
        super().__init__(model, timeout)
        self.host = host.rstrip("/")

    def _connection_error(self, exc: Exception) -> ProviderConnectionError:
        return ProviderConnectionError(
            f"cannot reach Ollama at {self.host} ({exc.__class__.__name__}). "
            "Is it running? Start it with 'ollama serve' or set CLANK_OLLAMA_HOST."
        )

    def chat_stream(
        self, messages: list[dict], tools: list[dict] | None = None
    ) -> Iterator[ChatEvent]:
        payload: dict = {
            "model": self.model,
            "messages": [self._to_ollama(m) for m in messages],
            "stream": True,
            "options": {"temperature": 0},
        }
        if tools:
            payload["tools"] = tools
        url = f"{self.host}/api/chat"
        try:
            response = requests.post(url, json=payload, stream=True, timeout=self.timeout)
        except requests.exceptions.ConnectionError as exc:
            raise self._connection_error(exc) from exc
        except requests.exceptions.Timeout as exc:
            raise ProviderConnectionError(
                f"Ollama at {self.host} timed out while connecting "
                f"({self.timeout[0]:g}s). The server may be overloaded or loading a model."
            ) from exc
        except requests.exceptions.RequestException as exc:
            raise ProviderConnectionError(f"Ollama request failed: {exc}") from exc

        try:
            if response.status_code >= 400:
                raise self._http_error(response)
            yield from self._iter_stream(response)
        finally:
            response.close()

    def _http_error(self, response: requests.Response) -> ProviderHTTPError:
        body = (response.text or "").strip()
        try:
            message = json.loads(body).get("error", body)
        except (ValueError, AttributeError):
            message = body
        message = str(message)
        hint = ""
        if response.status_code == 404 and "not found" in message.lower():
            hint = f" Pull it first with 'ollama pull {self.model}' or choose another with --model."
        elif "does not support tools" in message.lower():
            hint = " Choose a tool-capable model or run with --no-tools."
        return ProviderHTTPError(
            f"Ollama returned HTTP {response.status_code}: {message[:400]}{hint}",
            status_code=response.status_code,
        )

    def _iter_stream(self, response: requests.Response) -> Iterator[ChatEvent]:
        tool_index = 0
        try:
            for raw_line in response.iter_lines(decode_unicode=True):
                if not raw_line or not raw_line.strip():
                    continue
                try:
                    data = json.loads(raw_line)
                except ValueError as exc:
                    raise ProviderProtocolError(
                        f"Ollama sent a malformed stream line: {raw_line[:120]!r}"
                    ) from exc
                if not isinstance(data, dict):
                    raise ProviderProtocolError(
                        f"Ollama sent an unexpected stream value: {raw_line[:120]!r}"
                    )
                if data.get("error"):
                    raise ProviderProtocolError(
                        f"Ollama reported an error mid-stream: {data['error']}"
                    )
                message = data.get("message") or {}
                content = message.get("content")
                if content is not None and not isinstance(content, str):
                    raise ProviderProtocolError("Ollama content must be text")
                if content:
                    yield TextDelta(content)
                for raw_call in message.get("tool_calls") or []:
                    function = raw_call.get("function") or {}
                    name = function.get("name") or ""
                    arguments = function.get("arguments")
                    parse_error: str | None = None
                    if not isinstance(arguments, dict):
                        parse_error = (
                            "tool arguments were not a JSON object "
                            f"(got {type(arguments).__name__})"
                        )
                        arguments = {}
                    yield ToolCall(
                        id=raw_call.get("id") or f"call_{uuid.uuid4().hex[:12]}",
                        name=name,
                        arguments=arguments,
                        parse_error=parse_error,
                    )
                    tool_index += 1
                if data.get("done"):
                    yield Done(data.get("done_reason") or "stop")
                    return
        except (requests.exceptions.ChunkedEncodingError, requests.exceptions.ConnectionError) as exc:
            if is_timeout_error(exc):
                raise StreamInterrupted(
                    "the Ollama stream stalled and was closed after "
                    f"{self.timeout[1]:g}s without new data; "
                    "raise CLANK_REQUEST_TIMEOUT for slow models"
                ) from exc
            raise StreamInterrupted(
                f"the Ollama stream was interrupted before it finished ({exc.__class__.__name__})"
            ) from exc
        except requests.exceptions.Timeout as exc:
            raise StreamInterrupted(
                "the Ollama stream stalled and was closed after "
                f"{self.timeout[1]:g}s without new data; "
                "raise CLANK_REQUEST_TIMEOUT for slow models"
            ) from exc
        except (AttributeError, TypeError, KeyError) as exc:
            raise ProviderProtocolError("Ollama sent an invalid message or tool-call shape") from exc
        raise StreamInterrupted("Ollama closed the stream before its completion marker; "
                                "the partial response was not saved")

    @staticmethod
    def _to_ollama(message: dict) -> dict:
        role = message.get("role", "user")
        if role == "tool":
            return {
                "role": "tool",
                "content": message.get("content") or "",
                "tool_name": message.get("name") or "",
            }
        payload: dict = {"role": role, "content": message.get("content") or ""}
        calls = message.get("tool_calls")
        if calls:
            payload["tool_calls"] = [
                {
                    "function": {
                        "name": call.get("name", ""),
                        "arguments": call.get("arguments", {}),
                    }
                }
                for call in calls
            ]
        return payload

    def list_models(self) -> list[ModelInfo]:
        url = f"{self.host}/api/tags"
        try:
            response = requests.get(url, timeout=(self.timeout[0], 15.0))
        except requests.exceptions.ConnectionError as exc:
            raise self._connection_error(exc) from exc
        except requests.exceptions.RequestException as exc:
            raise ProviderConnectionError(f"Ollama request failed: {exc}") from exc
        if response.status_code >= 400:
            raise self._http_error(response)
        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderProtocolError("Ollama /api/tags did not return JSON") from exc
        if not isinstance(data, dict) or not isinstance(data.get("models", []), list):
            raise ProviderProtocolError("Ollama /api/tags returned an invalid model list")
        models = []
        for item in data.get("models") or []:
            if not isinstance(item, dict) or not isinstance(item.get("name", item.get("model", "")), str):
                raise ProviderProtocolError("Ollama /api/tags returned an invalid model entry")
            models.append(
                ModelInfo(
                    name=item.get("name") or item.get("model") or "",
                    size=item.get("size"),
                    modified=item.get("modified_at"),
                )
            )
        return sorted(models, key=lambda m: m.name.lower())
