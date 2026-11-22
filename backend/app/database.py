from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any

from .config import get_settings, sqlite_path

_database_lock = threading.RLock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


def _connect() -> sqlite3.Connection:
    connection = sqlite3.connect(
        sqlite_path(get_settings().database_url), timeout=10, isolation_level=None
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 10000")
    return connection


def initialize_database() -> None:
    with _database_lock, _connect() as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                system_prompt TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
                content TEXT NOT NULL,
                metadata TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(session_id)
                    ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_messages_session_id_id
                ON messages(session_id, id);
            """
        )


def database_is_ready() -> bool:
    try:
        with _database_lock, _connect() as connection:
            connection.execute("SELECT 1").fetchone()
        return True
    except (OSError, sqlite3.Error, ValueError):
        return False


def load_session_context(
    session_id: str, limit: int | None = None
) -> tuple[bool, str | None, list[dict[str, str]]]:
    with _database_lock, _connect() as connection:
        session = connection.execute(
            "SELECT system_prompt FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if session is None:
            return False, None, []
        if limit is None:
            rows = connection.execute(
                "SELECT role, content FROM messages "
                "WHERE session_id = ? ORDER BY id ASC",
                (session_id,),
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT role, content FROM ("
                "SELECT id, role, content FROM messages "
                "WHERE session_id = ? ORDER BY id DESC LIMIT ?) "
                "ORDER BY id ASC",
                (session_id, limit),
            ).fetchall()
    return True, session["system_prompt"], [
        {"role": row["role"], "content": row["content"]} for row in rows
    ]


def save_successful_turn(
    *,
    session_id: str,
    user_message: str,
    assistant_reply: str,
    metadata: dict[str, Any],
    system_prompt: str,
    prompt_was_overridden: bool,
) -> None:
    """Commit a complete turn atomically, only after an LLM reply exists."""
    timestamp = _now()
    with _database_lock, _connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "INSERT OR IGNORE INTO sessions(session_id, system_prompt, created_at) "
            "VALUES (?, ?, ?)",
            (session_id, system_prompt, timestamp),
        )
        if prompt_was_overridden:
            connection.execute(
                "UPDATE sessions SET system_prompt = ? WHERE session_id = ?",
                (system_prompt, session_id),
            )
        connection.execute(
            "INSERT INTO messages(session_id, role, content, metadata, created_at) "
            "VALUES (?, 'user', ?, NULL, ?)",
            (session_id, user_message, timestamp),
        )
        connection.execute(
            "INSERT INTO messages(session_id, role, content, metadata, created_at) "
            "VALUES (?, 'assistant', ?, ?, ?)",
            (session_id, assistant_reply, json.dumps(metadata), timestamp),
        )
        connection.commit()


def list_session_messages(session_id: str) -> list[dict[str, Any]] | None:
    with _database_lock, _connect() as connection:
        exists = connection.execute(
            "SELECT 1 FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if exists is None:
            return None
        rows = connection.execute(
            "SELECT role, content, metadata FROM messages "
            "WHERE session_id = ? ORDER BY id ASC",
            (session_id,),
        ).fetchall()

    messages: list[dict[str, Any]] = []
    for row in rows:
        item: dict[str, Any] = {"role": row["role"], "content": row["content"]}
        if row["metadata"]:
            item["metadata"] = json.loads(row["metadata"])
        messages.append(item)
    return messages

