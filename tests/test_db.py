from pathlib import Path

import pytest

from clank.db import Store
from clank.errors import ClankError


def test_append_and_read_messages(store: Store) -> None:
    session_id = store.create_session()
    store.append(session_id, {"role": "user", "content": "hi"})
    store.append(session_id, {"role": "assistant", "content": "hello"})
    messages = store.messages(session_id)
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[0]["content"] == "hi"
    assert store.message_count(session_id) == 2


def test_tool_messages_round_trip(store: Store) -> None:
    session_id = store.create_session()
    calls = [{"id": "call_1", "name": "read_file", "arguments": {"path": "a.txt"}}]
    store.append(session_id, {"role": "assistant", "content": "", "tool_calls": calls})
    store.append(
        session_id,
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "name": "read_file",
            "content": "file body",
        },
    )
    messages = store.messages(session_id)
    assert messages[0]["tool_calls"] == calls
    assert messages[1]["tool_call_id"] == "call_1"
    assert messages[1]["name"] == "read_file"


def test_persistence_across_reopen(tmp_path: Path) -> None:
    path = tmp_path / "persist.db"
    with Store(path) as store:
        session_id = store.create_session("keep me")
        store.append(session_id, {"role": "user", "content": "still here"})
    with Store(path) as store:
        messages = store.messages(session_id)
        assert messages[0]["content"] == "still here"
        assert store.get_session(session_id)["title"] == "keep me"


def test_latest_and_list_sessions(store: Store) -> None:
    first = store.create_session("first")
    second = store.create_session("second")
    store.append(second, {"role": "user", "content": "newest"})
    latest = store.latest_session()
    assert latest is not None and latest["id"] == second
    listed = store.list_sessions()
    assert listed[0]["id"] == second
    assert listed[0]["message_count"] == 1
    assert {row["id"] for row in listed} == {first, second}


def test_latest_session_empty(store: Store) -> None:
    assert store.latest_session() is None


def test_set_title_truncates(store: Store) -> None:
    session_id = store.create_session()
    store.set_title(session_id, "x" * 200)
    assert len(store.get_session(session_id)["title"]) == 80


def test_delete_session_cascades(store: Store) -> None:
    session_id = store.create_session()
    store.append(session_id, {"role": "user", "content": "gone soon"})
    store.delete_session(session_id)
    assert store.get_session(session_id) is None
    assert store.messages(session_id) == []


def test_find_session_prefix(store: Store) -> None:
    session_id = store.create_session()
    assert store.find_session(session_id[:8]) == session_id
    assert store.find_session(session_id) == session_id
    assert store.find_session("") is None
    with pytest.raises(ClankError, match="no stored session"):
        store.find_session("zzzzzzzz")


def test_find_session_ambiguous(store: Store, monkeypatch: pytest.MonkeyPatch) -> None:
    class FixedUUID:
        def __init__(self, value: str):
            self.hex = value

    monkeypatch.setattr("clank.db.uuid.uuid4", lambda: FixedUUID("aaaa1111"))
    store.create_session()
    monkeypatch.setattr("clank.db.uuid.uuid4", lambda: FixedUUID("aaaa2222"))
    store.create_session()
    with pytest.raises(ClankError, match="ambiguous"):
        store.find_session("aaaa")


def test_missing_parent_directory_is_created(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "deeper" / "history.db"
    with Store(path) as store:
        store.create_session()
    assert path.exists()
