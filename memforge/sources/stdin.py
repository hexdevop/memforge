"""stdin / file / clipboard source adapter."""

from __future__ import annotations

import sys
from pathlib import Path

from memforge.core.models import Message, Transcript


class StdinSource:
    name = "stdin"

    def __init__(self, text: str | None = None, file: Path | None = None) -> None:
        self._text = text
        self._file = file

    def detect(self) -> bool:
        if self._file and self._file.exists():
            return True
        if self._text:
            return True
        return not sys.stdin.isatty()

    def latest_session(self) -> Transcript | None:
        text = self._read()
        if not text:
            return None
        return _wrap(text, source=self.name)

    def by_id(self, session_id: str) -> Transcript:
        raise KeyError("stdin source does not support session lookup by id")

    def _read(self) -> str:
        if self._text:
            return self._text
        if self._file:
            return self._file.read_text("utf-8")
        if not sys.stdin.isatty():
            return sys.stdin.read()
        return ""


def _wrap(text: str, source: str = "stdin") -> Transcript:
    return Transcript(
        agent=source,
        messages=[
            Message(role="user", content="[raw input]"),
            Message(role="assistant", content=text),
        ],
    )


class FileSource(StdinSource):
    name = "file"

    def __init__(self, path: Path) -> None:
        super().__init__(file=path)
