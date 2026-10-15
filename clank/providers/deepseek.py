"""DeepSeek provider.

DeepSeek exposes an OpenAI-compatible chat completions API, so requests
use SSE `data:` lines. Tool call deltas are accumulated by index before a
complete ToolCall event is emitted. Only requests is required; the API key
comes from DEEPSEEK_API_KEY (or CLANK_DEEPSEEK_API_KEY).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator

import requests

from ..config import DEFAULT_DEEPSEEK_MODEL
from ..errors import (
    ConfigError,
    ProviderConnectionError,
    ProviderHTTPError,
    ProviderProtocolError,
    StreamInterrupted,
)
from .base import ChatEvent, Done, ModelInfo, Provider, TextDelta, ToolCall, is_timeout_error


class DeepSeekProvider(Provider):
    name = "deepseek"

    def __init__(
        self,
        model: str = DEFAULT_DEEPSEEK_MODEL,
        api_key: str = "",
        base_url: str = "https://api.deepseek.com",
        timeout: tuple[float, float] = (10.0, 300.0),
    ):
        super().__init__(model or DEFAULT_DEEPSEEK_MODEL, timeout)
        if not api_key or not api_key.strip():
            raise ConfigError(
                "DeepSeek requires an API key. Set DEEPSEEK_API_KEY "
                "(or CLANK_DEEPSEEK_API_KEY) in your environment or .env file."
            )
        self.api_key = api_key.strip()
        self.base_url = base_url.rstrip("/")

    @property
    def headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }

    def _connection_error(self, exc: Exception) -> ProviderConnectionError:
        return ProviderConnectionError(
            f"cannot reach the DeepSeek API at {self.base_url} "
            f"({exc.__class__.__name__}). Check your network connection."
        )

    def chat_stream(
        self, messages: list[dict], tools: list[dict] | None = None
    ) -> Iterator[ChatEvent]:
        payload: dict = {
            "model": self.model,
            "messages": [self._to_openai(m) for m in messages],
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
        url = f"{self.base_url}/chat/completions"
        try:
            response = requests.post(
                url, json=payload, headers=self.headers, stream=True, timeout=self.timeout
            )
        except requests.exceptions.ConnectionError as exc:
            raise self._connection_error(exc) from exc
        except requests.exceptions.Timeout as exc:
            raise ProviderConnectionError(
                f"DeepSeek at {self.base_url} timed out while connecting"
            ) from exc
        except requests.exceptions.RequestException as exc:
            raise ProviderConnectionError(f"DeepSeek request failed: {exc}") from exc

        try:
            if response.status_code >= 400:
                raise self._http_error(response)
            yield from self._iter_sse(response)
        finally:
            response.close()

    def _http_error(self, response: requests.Response) -> ProviderHTTPError:
        body = (response.text or "").strip()
        message = body
        try:
            parsed = json.loads(body)
            if isinstance(parsed, dict):
                error = parsed.get("error")
                if isinstance(error, dict):
                    message = error.get("message") or body
                elif error:
                    message = str(error)
        except ValueError:
            pass
        hint = ""
        message = str(message)
        if response.status_code == 401:
            hint = " Check that DEEPSEEK_API_KEY is valid and not expired."
        elif response.status_code == 402:
            hint = " The DeepSeek account has insufficient balance."
        return ProviderHTTPError(
            f"DeepSeek returned HTTP {response.status_code}: {message[:400]}{hint}",
            status_code=response.status_code,
        )

    def _iter_sse(self, response: requests.Response) -> Iterator[ChatEvent]:
        pending: dict[int, dict] = {}
        finish_reason = "stop"
        completed = False
        try:
            for raw_line in response.iter_lines(decode_unicode=True):
                if not raw_line:
                    continue
                line = raw_line.strip()
                if not line or line.startswith(":"):
                    continue
                if not line.startswith("data:"):
                    continue
                data_text = line[len("data:") :].strip()
                if data_text == "[DONE]":
                    completed = True
                    break
                try:
                    data = json.loads(data_text)
                except ValueError as exc:
                    raise ProviderProtocolError(
                        f"DeepSeek sent malformed SSE data: {data_text[:120]!r}"
                    ) from exc
                if isinstance(data, dict) and data.get("error"):
                    error = data["error"]
                    message = error.get("message") if isinstance(error, dict) else str(error)
                    raise ProviderProtocolError(
                        f"DeepSeek reported an error mid-stream: {message}"
                    )
                choices = data.get("choices") or []
                if not choices:
                    continue
                choice = choices[0]
                if choice.get("finish_reason"):
                    finish_reason = choice["finish_reason"]
                delta = choice.get("delta") or {}
                content = delta.get("content")
                if content is not None and not isinstance(content, str):
                    raise ProviderProtocolError("DeepSeek content must be text")
                if content:
                    yield TextDelta(content)
                for raw_call in delta.get("tool_calls") or []:
                    index = raw_call.get("index", 0)
                    slot = pending.setdefault(index, {"id": "", "name": "", "args": ""})
                    if raw_call.get("id"):
                        slot["id"] = raw_call["id"]
                    function = raw_call.get("function") or {}
                    if function.get("name"):
                        slot["name"] += function["name"]
                    if function.get("arguments"):
                        slot["args"] += function["arguments"]
        except (requests.exceptions.ChunkedEncodingError, requests.exceptions.ConnectionError) as exc:
            if is_timeout_error(exc):
                raise StreamInterrupted(
                    "the DeepSeek stream stalled and was closed after "
                    f"{self.timeout[1]:g}s without new data; "
                    "raise CLANK_REQUEST_TIMEOUT for slow responses"
                ) from exc
            raise StreamInterrupted(
                f"the DeepSeek stream was interrupted before it finished "
                f"({exc.__class__.__name__})"
            ) from exc
        except requests.exceptions.Timeout as exc:
            raise StreamInterrupted(
                "the DeepSeek stream stalled and was closed after "
                f"{self.timeout[1]:g}s without new data; "
                "raise CLANK_REQUEST_TIMEOUT for slow responses"
            ) from exc

        except (AttributeError, TypeError, KeyError) as exc:
            raise ProviderProtocolError("DeepSeek sent an invalid message or tool-call shape") from exc

        if not completed:
            raise StreamInterrupted("DeepSeek closed the stream before its completion marker; "
                                    "the partial response was not saved")

        for index in sorted(pending):
            slot = pending[index]
            parse_error: str | None = None
            arguments: dict = {}
            if slot["args"].strip():
                try:
                    parsed = json.loads(slot["args"])
                except ValueError as exc:
                    parse_error = f"tool arguments were not valid JSON: {exc}"
                else:
                    if isinstance(parsed, dict):
                        arguments = parsed
                    else:
                        parse_error = (
                            "tool arguments were not a JSON object "
                            f"(got {type(parsed).__name__})"
                        )
            yield ToolCall(
                id=slot["id"] or f"call_{uuid.uuid4().hex[:12]}",
                name=slot["name"],
                arguments=arguments,
                parse_error=parse_error,
            )
        yield Done(finish_reason or "stop")

    @staticmethod
    def _to_openai(message: dict) -> dict:
        role = message.get("role", "user")
        if role == "tool":
            return {
                "role": "tool",
                "tool_call_id": message.get("tool_call_id") or "",
                "content": message.get("content") or "",
            }
        payload: dict = {"role": role, "content": message.get("content") or ""}
        calls = message.get("tool_calls")
        if calls:
            payload["tool_calls"] = [
                {
                    "id": call.get("id") or f"call_{uuid.uuid4().hex[:12]}",
                    "type": "function",
                    "function": {
                        "name": call.get("name", ""),
                        "arguments": json.dumps(call.get("arguments", {})),
                    },
                }
                for call in calls
            ]
        return payload

    def list_models(self) -> list[ModelInfo]:
        url = f"{self.base_url}/models"
        try:
            response = requests.get(url, headers=self.headers, timeout=(self.timeout[0], 15.0))
        except requests.exceptions.ConnectionError as exc:
            raise self._connection_error(exc) from exc
        except requests.exceptions.RequestException as exc:
            raise ProviderConnectionError(f"DeepSeek request failed: {exc}") from exc
        if response.status_code >= 400:
            raise self._http_error(response)
        try:
            data = response.json()
        except ValueError as exc:
            raise ProviderProtocolError("DeepSeek /models did not return JSON") from exc
        if not isinstance(data, dict) or not isinstance(data.get("data", []), list):
            raise ProviderProtocolError("DeepSeek /models returned an invalid model list")
        if any(not isinstance(item, dict) or not isinstance(item.get("id", ""), str)
               for item in data.get("data") or []):
            raise ProviderProtocolError("DeepSeek /models returned an invalid model entry")
        return sorted(
            (ModelInfo(name=item.get("id", "")) for item in data.get("data") or []),
            key=lambda m: m.name.lower(),
        )
