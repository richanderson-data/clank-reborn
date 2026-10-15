"""Live integration tests against the real DeepSeek API.

Skipped unless CLANK_TEST_DEEPSEEK=1 and DEEPSEEK_API_KEY are set. These
use real API credits, which is why they never run in CI.
"""

from __future__ import annotations

import os

import pytest

from clank.config import DEFAULT_DEEPSEEK_MODEL
from clank.providers.base import TextDelta
from clank.providers.deepseek import DeepSeekProvider

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("CLANK_TEST_DEEPSEEK") != "1"
        or not (os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("CLANK_DEEPSEEK_API_KEY")),
        reason="set CLANK_TEST_DEEPSEEK=1 and DEEPSEEK_API_KEY to run live DeepSeek tests",
    ),
]


@pytest.fixture(scope="module")
def provider() -> DeepSeekProvider:
    key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("CLANK_DEEPSEEK_API_KEY") or ""
    return DeepSeekProvider(
        os.environ.get("CLANK_TEST_MODEL", DEFAULT_DEEPSEEK_MODEL),
        api_key=key,
        base_url=os.environ.get("CLANK_DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
    )


def test_lists_models(provider: DeepSeekProvider) -> None:
    models = provider.list_models()
    assert any(m.name for m in models)


def test_streams_a_real_response(provider: DeepSeekProvider) -> None:
    events = list(
        provider.chat_stream(
            [{"role": "user", "content": "Reply with exactly one word: pong"}],
            tools=None,
        )
    )
    text = "".join(event.text for event in events if isinstance(event, TextDelta))
    assert text.strip()
