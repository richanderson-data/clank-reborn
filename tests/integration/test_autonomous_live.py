"""Opt-in live model fix, actual pytest execution, and backup restoration."""

import os
import sys
from dataclasses import replace

import pytest

from clank.providers.ollama import OllamaProvider
from clank.session import open_session
from clank.ui import NullUI

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("CLANK_TEST_AUTONOMY") != "1",
        reason="set CLANK_TEST_AUTONOMY=1 for live editing",
    ),
]


def test_real_model_fixes_and_verifies_add(store, settings, project_dir):
    original = "def add(a, b):\n    return a - b\n"
    (project_dir / "calc.py").write_text(original)
    (project_dir / "test_calc.py").write_text(
        "from calc import add\n"
        "def test_positive(): assert add(2,3)==5\n"
        "def test_negative(): assert add(-2,-3)==-5\n"
        "def test_zero(): assert add(0,7)==7\n"
    )
    model = os.environ.get("CLANK_TEST_MODEL", "qwen3.5:4b")
    host = os.environ.get("CLANK_TEST_HOST", "http://localhost:11434")
    settings = replace(
        settings,
        autonomous=True,
        model=model,
        ollama_host=host,
        test_command=[sys.executable, "-m", "pytest", "-q"],
    )
    session = open_session(store, settings, NullUI(), OllamaProvider(model, host))
    result = session.send(
        "Run the tests first, then read calc.py and test_calc.py. "
        "Fix the addition bug by editing calc.py, rerun tests until all three pass, "
        "and report actual results. Do not change the tests."
    )
    assert not result.stopped_early
    assert result.changed_files == ("calc.py",)
    assert result.test_status == "passed"
    assert any(
        "3 passed" in m["content"]
        for m in store.messages(session.session_id)
        if m["role"] == "tool"
    )
    actions = session.registry.autonomy
    actions.restore(actions.changes()[0]["id"])
    assert (project_dir / "calc.py").read_text() == original
