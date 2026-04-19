"""OpenAI ChatGPT / Codex source adapter.

Reads conversation history from a ChatGPT export JSON file.
Export: chatgpt.com → Settings → Data Controls → Export Data → conversations.json

Format (simplified):
  [
    {
      "title": "...",
      "create_time": 1700000000.0,
      "mapping": {
        "<node_id>": {
          "message": {
            "author": {"role": "user"|"assistant"|"system"|"tool"},
            "content": {"parts": ["text..."] | [{"type": "text", "text": "..."}]}
          }
        }
      }
    }
  ]
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from memforge.core.models import Message, Transcript


class CodexSource:
    name = "chatgpt"

    def __init__(self, conversations_path: Path | None = None) -> None:
        self._path = conversations_path or self._default_path()

    @staticmethod
    def _default_path() -> Path:
        return Path.home() / "Downloads" / "conversations.json"

    def detect(self) -> bool:
        return self._path.exists()

    def latest_session(self) -> Transcript | None:
        conversations = self._load()
        if not conversations:
            return None
        # Most recent by create_time
        conv = max(conversations, key=lambda c: c.get("create_time") or 0)
        return self._parse_conversation(conv)

    def by_id(self, session_id: str) -> Transcript:
        for conv in self._load():
            if conv.get("id") == session_id:
                t = self._parse_conversation(conv)
                if t:
                    return t
        raise KeyError(f"ChatGPT conversation not found: {session_id}")

    def all_sessions(self) -> list[Transcript]:
        result = []
        for conv in self._load():
            t = self._parse_conversation(conv)
            if t and t.messages:
                result.append(t)
        return result

    def _load(self) -> list[dict]:  # type: ignore[type-arg]
        try:
            data = json.loads(self._path.read_text("utf-8"))
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def _parse_conversation(self, conv: dict) -> Transcript | None:  # type: ignore[type-arg]
        mapping = conv.get("mapping") or {}
        messages = self._extract_messages(mapping)
        if not messages:
            return None

        create_time = conv.get("create_time")
        started_at = datetime.fromtimestamp(create_time, tz=UTC) if create_time else None

        return Transcript(
            session_id=conv.get("id", conv.get("title", "unknown")),
            agent=self.name,
            started_at=started_at,
            messages=messages,
            cwd=str(Path.cwd()),
        )

    def _extract_messages(self, mapping: dict) -> list[Message]:  # type: ignore[type-arg]
        # Reconstruct ordered message list by following parent→child links
        nodes = {k: v for k, v in mapping.items() if v and v.get("message")}

        # Build child→parent map and find roots
        children: dict[str, list[str]] = {}
        parents: dict[str, str | None] = {}
        for node_id, node in mapping.items():
            parent_id = node.get("parent")
            parents[node_id] = parent_id
            if parent_id:
                children.setdefault(parent_id, []).append(node_id)

        roots = [n for n in mapping if not parents.get(n)]

        ordered: list[str] = []
        stack = list(roots)
        while stack:
            node_id = stack.pop(0)
            ordered.append(node_id)
            for child in children.get(node_id, []):
                stack.insert(0, child)

        messages: list[Message] = []
        for node_id in ordered:
            node = nodes.get(node_id)
            if not node:
                continue
            msg = node.get("message") or {}
            author = (msg.get("author") or {}).get("role", "")
            if author not in ("user", "assistant"):
                continue
            content = self._extract_content(msg.get("content") or {})
            if content:
                messages.append(Message(role=author, content=content))

        return messages

    def _extract_content(self, content: dict | str) -> str:  # type: ignore[type-arg]
        if isinstance(content, str):
            return content.strip()

        parts = content.get("parts") or []
        text_parts: list[str] = []
        for part in parts:
            if isinstance(part, str):
                text_parts.append(part)
            elif isinstance(part, dict):
                if part.get("type") == "text":
                    text_parts.append(part.get("text", ""))
        return " ".join(p for p in text_parts if p).strip()
