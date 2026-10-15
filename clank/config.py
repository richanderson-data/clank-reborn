"""Configuration loading.

Settings come from, in order of precedence:

1. Command line flags (applied by the CLI after from_env()).
2. Environment variables (CLANK_* plus DEEPSEEK_API_KEY).
3. A .env file in the working directory (does not override the environment).
4. Built-in defaults.

No third-party configuration library is used on purpose: the whole loader
is small enough to read in one sitting.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from pathlib import Path

from .errors import ConfigError

PROVIDERS = ("ollama", "deepseek")
DEFAULT_OLLAMA_HOST = "http://localhost:11434"
DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_DEEPSEEK_MODEL = "deepseek-chat"
DEFAULT_MAX_HISTORY = 40
DEFAULT_MAX_TOOL_ROUNDS = 100
DEFAULT_CONNECT_TIMEOUT = 10.0
DEFAULT_READ_TIMEOUT = 300.0


def default_db_path() -> Path:
    return Path.home() / ".clank-reborn" / "clank.db"


def load_env_file(path: Path, environ: dict[str, str] | None = None) -> int:
    """Load KEY=VALUE lines from *path* without overriding existing values.

    Returns the number of variables that were set. Supports ``#`` comments,
    blank lines, an optional ``export`` prefix, and single or double quotes.
    """
    env = environ if environ is not None else os.environ
    if not path.is_file():
        return 0
    loaded = 0
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, sep, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if not sep or not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if key not in env:
            env[key] = value
            loaded += 1
    return loaded


def _env_int(env: dict[str, str], key: str, default: int) -> int:
    raw = env.get(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} must be an integer, got {raw!r}") from exc
    if value < 1:
        raise ConfigError(f"{key} must be at least 1, got {value}")
    return value


def _env_float(env: dict[str, str], key: str, default: float) -> float:
    raw = env.get(key)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} must be a number, got {raw!r}") from exc
    if not math.isfinite(value) or value <= 0:
        raise ConfigError(f"{key} must be positive, got {value}")
    return value


@dataclass
class Settings:
    provider: str = "ollama"
    model: str = ""
    ollama_host: str = DEFAULT_OLLAMA_HOST
    deepseek_api_key: str = ""
    deepseek_base_url: str = DEFAULT_DEEPSEEK_BASE_URL
    db_path: Path = field(default_factory=default_db_path)
    allow_dirs: list[Path] = field(default_factory=lambda: [Path.cwd()])
    max_history: int = DEFAULT_MAX_HISTORY
    max_tool_rounds: int = DEFAULT_MAX_TOOL_ROUNDS
    connect_timeout: float = DEFAULT_CONNECT_TIMEOUT
    read_timeout: float = DEFAULT_READ_TIMEOUT
    tools_enabled: bool = True
    file_tools_enabled: bool = True
    web_enabled: bool = True
    provider_models: dict[str, str] = field(default_factory=dict)
    cloud_consent: bool = False
    autonomous: bool = False
    test_command: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.provider = self.provider.strip().lower()
        if self.provider not in PROVIDERS:
            raise ConfigError(
                f"Unknown provider {self.provider!r}. Choose one of: {', '.join(PROVIDERS)}"
            )
        self.db_path = Path(self.db_path).expanduser()
        self.allow_dirs = [Path(p).expanduser() for p in self.allow_dirs]
        if self.autonomous and (len(self.allow_dirs) != 1 or not self.tools_enabled or not self.file_tools_enabled):
            raise ConfigError("Autonomous mode needs one project directory and enabled tools")

    @classmethod
    def from_env(cls, environ: dict[str, str] | None = None) -> Settings:
        env = environ if environ is not None else os.environ
        api_key = env.get("CLANK_DEEPSEEK_API_KEY") or env.get("DEEPSEEK_API_KEY") or ""
        db_raw = env.get("CLANK_DB_PATH", "").strip()
        return cls(
            provider=env.get("CLANK_PROVIDER", "ollama"),
            model=env.get("CLANK_MODEL", "").strip(),
            ollama_host=env.get("CLANK_OLLAMA_HOST", DEFAULT_OLLAMA_HOST).strip()
            or DEFAULT_OLLAMA_HOST,
            deepseek_api_key=api_key.strip(),
            deepseek_base_url=env.get("CLANK_DEEPSEEK_BASE_URL", DEFAULT_DEEPSEEK_BASE_URL).strip()
            or DEFAULT_DEEPSEEK_BASE_URL,
            db_path=Path(db_raw) if db_raw else default_db_path(),
            max_history=_env_int(env, "CLANK_MAX_HISTORY", DEFAULT_MAX_HISTORY),
            max_tool_rounds=_env_int(env, "CLANK_MAX_TOOL_ROUNDS", DEFAULT_MAX_TOOL_ROUNDS),
            read_timeout=_env_float(env, "CLANK_REQUEST_TIMEOUT", DEFAULT_READ_TIMEOUT),
            web_enabled=env.get("CLANK_WEB_ENABLED", "true").lower() not in ("0", "false", "no"),
        )
