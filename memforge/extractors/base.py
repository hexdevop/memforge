"""Extractor protocol."""

from __future__ import annotations

from typing import Protocol

from memforge.core.models import ExtractedUnit, Transcript


class Extractor(Protocol):
    async def extract(
        self,
        transcript: Transcript,
        note: str | None = None,
        hint_type: str | None = None,
    ) -> list[ExtractedUnit]: ...
