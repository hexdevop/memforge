"""Cursor IDE source adapter.

Reads chat history from Cursor's SQLite workspace storage.
  Windows: %APPDATA%/Cursor/User/workspaceStorage/<hash>/state.vscdb
  macOS:   ~/Library/Application Support/Cursor/User/workspaceStorage/<hash>/state.vscdb
  Linux:   ~/.config/Cursor/User/workspaceStorage/<hash>/state.vscdb
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

from memforge.core.models import Message, Transcript


def _cursor_storage_root() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", "~")).expanduser()
    elif hasattr(os, "uname") and os.uname().sysname == "Darwin":
        base = Path("~/Library/Application Support").expanduser()
    else:
        base = Path("~/.config").expanduser()
    return base / "Cursor" / "User" / "workspaceStorage"


class CursorSource:
    name = "cursor"

    def __init__(self, storage_root: Path | None = None, cwd: Path | None = None) -> None:
        self._root = storage_root or _cursor_storage_root()
        self._cwd = cwd or Path.cwd()

    def detect(self) -> bool:
        return self._root.exists() and any(self._root.glob("*/state.vscdb"))

    def latest_session(self) -> Transcript | None:
        dbs = sorted(
            self._root.glob("*/state.vscdb"), key=lambda p: p.stat().st_mtime, reverse=True
        )
        for db in dbs:
            t = self._parse_db(db)
            if t and t.messages:
                return t
        return None

    def by_id(self, session_id: str) -> Transcript:
        for db in self._root.glob("*/state.vscdb"):
            if db.parent.name == session_id:
                t = self._parse_db(db)
                if t:
                    return t
        raise KeyError(f"Cursor session not found: {session_id}")

    def _parse_db(self, db_path: Path) -> Transcript | None:
        try:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                "SELECT value FROM ItemTable WHERE key LIKE '%conversationHistory%' "
                "OR key LIKE '%chatHistory%' LIMIT 10"
            )
            rows = cur.fetchall()
            conn.close()
        except Exception:
            return None

        messages: list[Message] = []
        for row in rows:
            try:
                data = json.loads(row["value"])
                messages.extend(_extract_messages(data))
            except (json.JSONDecodeError, KeyError):
                continue

        if not messages:
            return None

        return Transcript(
            session_id=db_path.parent.name,
            agent=self.name,
            started_at=messages[0].ts if messages else None,
            messages=messages,
            cwd=str(self._cwd),
        )


def _extract_messages(data: object) -> list[Message]:
    messages: list[Message] = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                role = item.get("role", "")
                content = item.get("content") or item.get("text") or ""
                if role in ("user", "assistant") and content:
                    messages.append(Message(role=role, content=str(content)))
    elif isinstance(data, dict):
        for key in ("history", "conversations", "messages", "turns"):
            if key in data and isinstance(data[key], list):
                messages.extend(_extract_messages(data[key]))
                if messages:
                    break
    return messages
