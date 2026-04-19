"""Source adapter protocol."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from memforge.core.models import Transcript


@runtime_checkable
class Source(Protocol):
    name: str

    def detect(self) -> bool:
        """Return True if this source has something readable in the current environment."""
        ...

    def latest_session(self) -> Transcript | None:
        """Return the most recent session transcript, or None."""
        ...

    def by_id(self, session_id: str) -> Transcript:
        """Return a specific session by ID. Raise KeyError if not found."""
        ...
