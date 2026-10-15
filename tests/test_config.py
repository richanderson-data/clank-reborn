from pathlib import Path

import pytest

from clank.config import (
    DEFAULT_MAX_HISTORY,
    Settings,
    default_db_path,
    load_env_file,
)
from clank.errors import ConfigError


def test_load_env_file_parses_values(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\n"
        "\n"
        "CLANK_MODEL=llama3.2\n"
        'export CLANK_PROVIDER="deepseek"\n'
        "DEEPSEEK_API_KEY='sk-test'\n"
        "BROKEN LINE\n",
        encoding="utf-8",
    )
    env: dict[str, str] = {}
    assert load_env_file(env_file, env) == 3
    assert env["CLANK_MODEL"] == "llama3.2"
    assert env["CLANK_PROVIDER"] == "deepseek"
    assert env["DEEPSEEK_API_KEY"] == "sk-test"


def test_load_env_file_does_not_override(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("CLANK_MODEL=from-file\n", encoding="utf-8")
    env = {"CLANK_MODEL": "from-env"}
    assert load_env_file(env_file, env) == 0
    assert env["CLANK_MODEL"] == "from-env"


def test_load_env_file_missing_is_noop(tmp_path: Path) -> None:
    assert load_env_file(tmp_path / "nope.env", {}) == 0


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings.from_env({})
    assert settings.provider == "ollama"
    assert settings.model == ""
    assert settings.db_path == default_db_path()
    assert settings.max_history == DEFAULT_MAX_HISTORY
    assert settings.tools_enabled is True


def test_settings_reads_environment() -> None:
    settings = Settings.from_env(
        {
            "CLANK_PROVIDER": "DeepSeek",
            "CLANK_MODEL": "deepseek-chat",
            "CLANK_DEEPSEEK_API_KEY": "sk-abc",
            "CLANK_MAX_HISTORY": "5",
            "CLANK_DB_PATH": r"C:\tmp\clank.db",
        }
    )
    assert settings.provider == "deepseek"
    assert settings.model == "deepseek-chat"
    assert settings.deepseek_api_key == "sk-abc"
    assert settings.max_history == 5
    assert settings.db_path == Path(r"C:\tmp\clank.db")


def test_settings_falls_back_to_deepseek_api_key() -> None:
    settings = Settings.from_env({"DEEPSEEK_API_KEY": "sk-fallback"})
    assert settings.deepseek_api_key == "sk-fallback"


def test_settings_rejects_unknown_provider() -> None:
    with pytest.raises(ConfigError, match="Unknown provider"):
        Settings.from_env({"CLANK_PROVIDER": "openai"})


def test_settings_rejects_bad_int() -> None:
    with pytest.raises(ConfigError, match="CLANK_MAX_HISTORY"):
        Settings.from_env({"CLANK_MAX_HISTORY": "many"})


def test_settings_rejects_bad_timeout() -> None:
    with pytest.raises(ConfigError, match="CLANK_REQUEST_TIMEOUT"):
        Settings.from_env({"CLANK_REQUEST_TIMEOUT": "-1"})
