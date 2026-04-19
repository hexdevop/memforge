"""Claude Code source adapter.

Reads ~/.claude/projects/<hash>/<session-id>.jsonl files produced by Claude Code.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from memforge.core.models import Message, Transcript


class ClaudeCodeSource:
    name = "claude-code"

    def __init__(self, root: Path | None = None, cwd: Path | None = None) -> None:
        self._root = (
            root or Path(os.environ.get("CLAUDE_PROJECTS_ROOT", "~/.claude/projects")).expanduser()
        )
        self._cwd = cwd or Path.cwd()

    # ------------------------------------------------------------------
    # Protocol
    # ------------------------------------------------------------------

    def detect(self) -> bool:
        return self._root.exists() and any(self._root.glob("*/*.jsonl"))

    def latest_session(self) -> Transcript | None:
        sessions = self._all_jsonl_files()
        if not sessions:
            return None
        latest = max(sessions, key=lambda p: p.stat().st_mtime)
        return self._parse(latest)

    def by_id(self, session_id: str) -> Transcript:
        for p in self._all_jsonl_files():
            if p.stem == session_id:
                return self._parse(p)
        raise KeyError(f"Session not found: {session_id}")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _project_hash(self) -> str:
        cwd_str = str(self._cwd)
        return hashlib.md5(cwd_str.encode()).hexdigest()

    def _all_jsonl_files(self) -> list[Path]:
        files: list[Path] = []

        # Prefer files in the project-specific directory
        project_dir = self._root / self._project_hash()
        if project_dir.exists():
            files.extend(project_dir.glob("*.jsonl"))

        # Fall back to scanning all project dirs if none found
        if not files:
            files.extend(self._root.glob("*/*.jsonl"))

        return files

    def _parse(self, path: Path) -> Transcript:
        messages: list[Message] = []
        raw_lines = path.read_text("utf-8", errors="replace").splitlines()

        for line in raw_lines:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue

            msg_type = obj.get("type", "")
            ts_raw = obj.get("timestamp")
            ts = _parse_ts(ts_raw)

            if msg_type == "user":
                content = _extract_content(obj.get("message", {}))
                if content:
                    messages.append(Message(role="user", content=content, ts=ts))

            elif msg_type == "assistant":
                content = _extract_content(obj.get("message", {}))
                if content:
                    messages.append(Message(role="assistant", content=content, ts=ts))

        started_at = messages[0].ts if messages else None

        return Transcript(
            session_id=path.stem,
            agent=self.name,
            started_at=started_at,
            messages=messages,
            cwd=str(self._cwd),
        )


def _extract_content(msg: dict) -> str:  # type: ignore[type-arg]
    """Extract text from Claude Code message structure."""
    if isinstance(msg.get("content"), str):
        return msg["content"]
    if isinstance(msg.get("content"), list):
        parts: list[str] = []
        for block in msg["content"]:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif block.get("type") == "tool_result":
                    for inner in block.get("content", []):
                        if isinstance(inner, dict) and inner.get("type") == "text":
                            parts.append(inner.get("text", ""))
        return "\n".join(p for p in parts if p)
    return ""


def _parse_ts(raw: object) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw)).astimezone(UTC)
    except (ValueError, TypeError):
        return None
