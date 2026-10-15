"""SQLite persistence for sessions and messages.

Messages are stored in the canonical wire shape used throughout Clank:

    {"role": "user" | "assistant" | "tool" | "system", "content": str,
     "tool_calls": [{"id": str, "name": str, "arguments": dict}]?,  # assistant only
     "tool_call_id": str?, "name": str?}                            # tool only

The schema is intentionally boring: two tables, JSON only where lists are
needed. Conversation history lives in one local file that the user owns.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path

from .errors import ClankError

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id         TEXT PRIMARY KEY,
    title      TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role         TEXT NOT NULL,
    content      TEXT NOT NULL DEFAULT '',
    tool_calls   TEXT,
    tool_call_id TEXT,
    name         TEXT,
    created_at   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, id);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);
"""

SCHEMA_VERSION = 1


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Store:
    """A tiny wrapper around a single SQLite file."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        if self.path.parent and str(self.path.parent) not in ("", "."):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SCHEMA)
        row = self._conn.execute("SELECT version FROM schema_version").fetchone()
        if row is None:
            self._conn.execute("INSERT INTO schema_version(version) VALUES (?)", (SCHEMA_VERSION,))
        elif row["version"] != SCHEMA_VERSION:
            self._conn.close()
            raise ClankError(
                "Unsupported conversation database version. "
                "Use the matching Clank version; do not delete your history."
            )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def create_session(self, title: str = "") -> str:
        session_id = uuid.uuid4().hex
        now = utcnow()
        with self._conn:
            self._conn.execute(
                "INSERT INTO sessions(id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (session_id, title, now, now),
            )
        return session_id

    def get_session(self, session_id: str) -> dict | None:
        row = self._conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return dict(row) if row else None

    def latest_session(self) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM sessions ORDER BY updated_at DESC, rowid DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    def list_sessions(self, limit: int = 20) -> list[dict]:
        rows = self._conn.execute(
            """
            SELECT s.id, s.title, s.created_at, s.updated_at,
                   COUNT(m.id) AS message_count
            FROM sessions s
            LEFT JOIN messages m ON m.session_id = s.id
            GROUP BY s.id
            ORDER BY s.updated_at DESC, s.rowid DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def set_title(self, session_id: str, title: str) -> None:
        with self._conn:
            self._conn.execute(
                "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
                (title[:80], utcnow(), session_id),
            )

    def find_session(self, prefix: str) -> str | None:
        """Resolve a full session id or an unambiguous prefix of one."""
        prefix = (prefix or "").strip()
        if not prefix:
            return None
        if self.get_session(prefix):
            return prefix
        matches = [
            row["id"] for row in self.list_sessions(limit=500) if row["id"].startswith(prefix)
        ]
        if len(matches) == 1:
            return matches[0]
        if not matches:
            raise ClankError(
                f"no stored session matches {prefix!r}. "
                "Run 'clank --list-sessions' to see saved sessions."
            )
        raise ClankError(
            f"session id {prefix!r} is ambiguous ({len(matches)} matches); use more characters."
        )

    def delete_session(self, session_id: str) -> None:
        with self._conn:
            self._conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))

    def append(self, session_id: str, message: dict) -> None:
        self.append_many(session_id, [message])

    def append_many(self, session_id: str, messages: list[dict]) -> None:
        """Persist a related message batch atomically."""
        with self._conn:
            for message in messages:
                self._insert_message(session_id, message)
            self._conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (utcnow(), session_id),
            )

    def _insert_message(self, session_id: str, message: dict) -> None:
        tool_calls = message.get("tool_calls")
        self._conn.execute(
            """
                INSERT INTO messages(session_id, role, content, tool_calls,
                                     tool_call_id, name, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
            (
                session_id,
                message.get("role", ""),
                message.get("content") or "",
                json.dumps(tool_calls) if tool_calls else None,
                message.get("tool_call_id"),
                message.get("name"),
                utcnow(),
            ),
        )

    def messages(self, session_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM messages WHERE session_id = ? ORDER BY id", (session_id,)
        ).fetchall()
        messages = []
        damaged_turn = False
        for row in rows:
            message = self._row_to_message(row)
            if message["role"] == "user":
                damaged_turn = False
            if message.pop("recovery_warning", False):
                damaged_turn = True
            if damaged_turn and message["role"] == "tool":
                continue  # Never send orphaned tool responses to a provider.
            messages.append(message)
        return messages

    def message_count(self, session_id: str) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM messages WHERE session_id = ?", (session_id,)
        ).fetchone()
        return int(row["n"])

    @staticmethod
    def _row_to_message(row: sqlite3.Row) -> dict:
        message: dict = {"role": row["role"], "content": row["content"] or ""}
        if row["tool_calls"]:
            try:
                calls = json.loads(row["tool_calls"])
                if not isinstance(calls, list) or any(
                    not isinstance(call, dict)
                    or not isinstance(call.get("id"), str)
                    or not isinstance(call.get("name"), str)
                    or not isinstance(call.get("arguments"), dict)
                    for call in calls
                ):
                    raise ValueError("invalid tool-call shape")
                message["tool_calls"] = calls
            except ValueError:
                message["recovery_warning"] = True
                message["content"] = (
                    "[History recovery: damaged tool-call data and its tool responses "
                    "were omitted from this view. The original database was not modified.]\n"
                    + message["content"]
                )
        if row["tool_call_id"]:
            message["tool_call_id"] = row["tool_call_id"]
        if row["name"]:
            message["name"] = row["name"]
        return message
