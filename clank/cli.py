"""Command line interface: argument parsing and the interactive REPL."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import __version__
from .command_line import split_command
from .config import PROVIDERS, Settings, load_env_file
from .db import Store
from .errors import ClankError, ConfigError, ProviderError, StreamInterrupted
from .providers import Provider, create_provider
from .session import ChatSession, open_session
from .ui import UI, ConsoleUI, configure_stdio

HELP_TEXT = """commands:
  /help            show this help
  /new             start a new conversation
  /sessions        list recent sessions in the local database
  /history         show the last messages of the current session
  /tools           list tools and allowed directories
  /model [name]    show available models or switch model
  /provider [name] show current provider or switch provider (ollama, deepseek)
  /exit            quit
anything else is sent to the model."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="clank",
        description="A minimal, local-first terminal AI assistant.",
    )
    parser.add_argument("--version", action="version", version=f"clank-reborn {__version__}")
    parser.add_argument(
        "-p", "--prompt", metavar="TEXT", help="send one prompt, print the answer, exit"
    )
    parser.add_argument("--provider", choices=PROVIDERS, help="model provider")
    parser.add_argument("--model", metavar="NAME", help="model to use")
    parser.add_argument(
        "--host",
        metavar="URL",
        help="Ollama host override (default: http://localhost:11434)",
    )
    parser.add_argument("--db", metavar="PATH", help="SQLite history database")
    parser.add_argument(
        "--continue",
        dest="continue_",
        action="store_true",
        help="continue the most recent session",
    )
    parser.add_argument("--session", metavar="ID", help="resume a session by id or prefix")
    parser.add_argument(
        "--allow-dir",
        action="append",
        default=None,
        metavar="PATH",
        help="directory the tools may read (repeatable; default: current directory)",
    )
    parser.add_argument("--no-tools", action="store_true", help="disable tool use")
    parser.add_argument("--max-tool-rounds", type=int, help="task tool-round ceiling; default 100, with progress review every 20 rounds")
    parser.add_argument("--offline", action="store_true", help="disable public search, page reading, and weather tools")
    parser.add_argument("--web", action="store_true", help="start the local browser workspace")
    parser.add_argument(
        "--autonomous",
        action="store_true",
        help="enable project edits/backups, development commands, and fixed verification",
    )
    parser.add_argument(
        "--test-command",
        metavar="COMMAND",
        help="fixed autonomous test command; default: this Python -m pytest -q",
    )
    parser.add_argument(
        "--port", type=int, default=8787, help="browser workspace port (default 8787)"
    )
    parser.add_argument(
        "--cloud-consent",
        action="store_true",
        help="allow sending this conversation and tool outputs to DeepSeek",
    )
    parser.add_argument("--list-models", action="store_true", help="list provider models and exit")
    parser.add_argument(
        "--list-sessions", action="store_true", help="list stored sessions and exit"
    )
    return parser


def apply_overrides(settings: Settings, args: argparse.Namespace) -> Settings:
    if args.provider:
        settings.provider = args.provider
    if args.model:
        settings.model = args.model
    if args.host:
        settings.ollama_host = args.host
    if args.db:
        settings.db_path = Path(args.db)
    if args.allow_dir:
        settings.allow_dirs = [Path(p) for p in args.allow_dir]
    if args.no_tools:
        settings.tools_enabled = False
    if args.max_tool_rounds is not None:
        if args.max_tool_rounds < 1:
            raise ConfigError("--max-tool-rounds must be at least 1")
        settings.max_tool_rounds = args.max_tool_rounds
    if args.offline:
        settings.web_enabled = False
    settings.cloud_consent = args.cloud_consent
    settings.autonomous = args.autonomous
    if args.test_command:
        try:
            settings.test_command = split_command(args.test_command)
        except ValueError as exc:
            raise ConfigError("Invalid quoting in --test-command") from exc
        if not settings.test_command:
            raise ConfigError("Test command must not be empty")
    else:
        settings.test_command = [sys.executable, "-m", "pytest", "-q"]
    settings.__post_init__()
    return settings


def make_provider(settings: Settings, allow_missing_model: bool = False) -> Provider:
    if allow_missing_model and settings.provider == "ollama" and not settings.model:
        from .providers.ollama import OllamaProvider

        timeout = (settings.connect_timeout, settings.read_timeout)
        return OllamaProvider("", settings.ollama_host, timeout=timeout)
    return create_provider(settings)


def resolved_roots(settings: Settings) -> list[str]:
    roots = []
    for path in settings.allow_dirs:
        try:
            roots.append(str(Path(path).resolve()))
        except OSError:
            roots.append(str(path))
    return roots


def list_models(provider: Provider, ui: UI) -> int:
    try:
        models = provider.list_models()
    except ProviderError as exc:
        ui.error(str(exc))
        return 1
    if not models:
        ui.info("no models reported by the provider")
        return 0
    ui.info(f"{provider.name}: {len(models)} model(s) available")
    for model in models:
        if model.size:
            ui.info(f"  {model.name}  ({model.size / 1_000_000_000:.1f} GB)")
        else:
            ui.info(f"  {model.name}")
    return 0


def list_sessions(store: Store, ui: UI) -> int:
    sessions = store.list_sessions(limit=20)
    if not sessions:
        ui.info(f"no sessions in {store.path}")
        return 0
    ui.info(f"sessions in {store.path}")
    for session in sessions:
        title = session["title"] or "(untitled)"
        ui.info(
            f"  {session['id'][:8]}  {session['updated_at']}  "
            f"{session['message_count']:>3} msg  {title}"
        )
    return 0


def show_history(session: ChatSession, ui: UI) -> None:
    messages = session.store.messages(session.session_id)
    if not messages:
        ui.info("this session has no messages yet")
        return
    shown = messages[-20:]
    if len(messages) > len(shown):
        ui.info(f"showing the last {len(shown)} of {len(messages)} messages")
    for message in shown:
        role = message["role"]
        content = " ".join((message.get("content") or "").split())
        if len(content) > 120:
            content = content[:120] + "..."
        suffix = ""
        if message.get("tool_calls"):
            names = ", ".join(call.get("name", "?") for call in message["tool_calls"])
            suffix = f" [tool calls: {names}]"
        ui.info(f"  {role:>9}: {content}{suffix}")


def show_tools(session: ChatSession, settings: Settings, ui: UI) -> None:
    if session.registry is None or not settings.tools_enabled:
        ui.info("tools are disabled for this session")
        return
    ui.info("available tools:")
    ui.info(session.registry.describe())
    ui.info(f"allowed directories: {', '.join(resolved_roots(settings))}")


def handle_command(
    line: str,
    session: ChatSession,
    settings: Settings,
    store: Store,
    ui: UI,
) -> tuple[ChatSession, bool]:
    command, _, rest = line[1:].partition(" ")
    command = command.strip().lower()
    rest = rest.strip()

    if command in ("exit", "quit"):
        ui.info("bye")
        return session, False
    if command == "help":
        ui.info(HELP_TEXT)
        return session, True
    if command == "new":
        session = open_session(store, settings, ui, session.provider)
        ui.info(f"started new session {session.session_id[:8]}")
        return session, True
    if command == "sessions":
        list_sessions(store, ui)
        return session, True
    if command == "history":
        show_history(session, ui)
        return session, True
    if command == "tools":
        show_tools(session, settings, ui)
        return session, True
    if command == "model":
        if rest:
            session.provider.model = rest
            settings.model = rest
            settings.provider_models[session.provider.name] = rest
            ui.info(f"model set to {rest}")
            return session, True
        list_models(session.provider, ui)
        return session, True
    if command == "provider":
        if not rest:
            ui.info(f"current provider: {session.provider.name} (model {session.provider.model})")
            return session, True
        target, _, selected_model = rest.partition(" ")
        if target not in PROVIDERS:
            ui.warning(f"unknown provider {rest!r}; choose from: {', '.join(PROVIDERS)}")
            return session, True
        previous_provider = settings.provider
        previous_model = settings.model
        settings.provider_models[session.provider.name] = session.provider.model
        if target == "deepseek" and not settings.cloud_consent:
            ui.warning(
                "DeepSeek sends history and file contents to the cloud. "
                "Restart with --cloud-consent to enable it."
            )
            return session, True
        settings.provider = target
        settings.model = selected_model.strip() or settings.provider_models.get(target, "")
        try:
            new_provider = make_provider(settings)
        except ClankError as exc:
            settings.provider = previous_provider
            settings.model = previous_model
            ui.error(str(exc))
            return session, True
        session.provider = new_provider
        settings.model = new_provider.model
        ui.info(f"provider set to {new_provider.name} (model {new_provider.model})")
        return session, True

    ui.warning(f"unknown command /{command}; type /help for commands")
    return session, True


def repl(session: ChatSession, settings: Settings, store: Store, ui: UI) -> int:
    while True:
        try:
            line = ui.prompt().strip()
        except (EOFError, KeyboardInterrupt):
            ui.info("")
            ui.info("bye")
            return 0
        if not line:
            continue
        if line.startswith("/"):
            session, keep_going = handle_command(line, session, settings, store, ui)
            if not keep_going:
                return 0
            continue
        try:
            session.send(line)
        except StreamInterrupted as exc:
            ui.warning(str(exc))
        except ProviderError as exc:
            ui.error(str(exc))
        except ClankError as exc:
            ui.error(str(exc))


def run_once(session: ChatSession, ui: UI, prompt: str) -> int:
    try:
        session.send(prompt)
        return 0
    except StreamInterrupted as exc:
        ui.warning(str(exc))
        return 1
    except ProviderError as exc:
        ui.error(str(exc))
        return 1
    except ClankError as exc:
        ui.error(str(exc))
        return 1


def main(argv: list[str] | None = None) -> int:
    configure_stdio()
    parser = build_parser()
    args = parser.parse_args(argv)
    load_env_file(Path.cwd() / ".env")

    try:
        settings = apply_overrides(Settings.from_env(), args)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.web:
        from .web import serve

        return serve(settings, args.port)

    if (
        settings.provider == "deepseek"
        and not settings.cloud_consent
        and not args.list_models
        and not args.list_sessions
    ):
        print(
            "error: DeepSeek sends conversation history and tool outputs to the cloud. "
            "Use --cloud-consent to explicitly enable it.",
            file=sys.stderr,
        )
        return 2

    with Store(settings.db_path) as store:
        return _main_with_store(args, settings, store)


def _main_with_store(args: argparse.Namespace, settings: Settings, store: Store) -> int:
    ui = ConsoleUI()

    if args.list_sessions:
        return list_sessions(store, ui)

    try:
        provider = make_provider(settings, allow_missing_model=args.list_models)
    except ConfigError as exc:
        ui.error(str(exc))
        return 2

    if args.list_models:
        return list_models(provider, ui)

    try:
        session_id = store.find_session(args.session) if args.session else None
        session = open_session(
            store,
            settings,
            ui,
            provider,
            session_id=session_id,
            continue_latest=args.continue_ and not session_id,
        )
    except ClankError as exc:
        ui.error(str(exc))
        return 2

    roots = resolved_roots(settings) if settings.tools_enabled else []
    if settings.autonomous:
        ui.warning(
            "Autonomous mode: project edits are enabled with backups. "
            "Development commands and fixed tests run with your OS permissions, "
            "not in an OS sandbox. Command-made changes do not get undo backups."
        )
    ui.banner(
        version=__version__,
        provider=session.provider.name,
        model=session.provider.model,
        session=session.session_id[:8],
        db_path=store.path,
        roots=roots,
        tools=settings.tools_enabled,
    )

    if args.prompt:
        return run_once(session, ui, args.prompt)
    return repl(session, settings, store, ui)
