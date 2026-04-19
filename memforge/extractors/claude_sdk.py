"""Extractor backed by Anthropic API."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import anthropic

from memforge.core.models import ExtractedUnit, RecordType, Transcript

log = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


def _load_prompt(version: str) -> str:
    path = _PROMPTS_DIR / f"extractor.{version}.md"
    if path.exists():
        return path.read_text("utf-8")
    raise FileNotFoundError(f"Extractor prompt not found: {path}")


class ClaudeExtractor:
    def __init__(
        self,
        model: str = "claude-sonnet-4-6",
        prompt_version: str = "v1",
        max_units: int = 10,
        language: str = "en",
    ) -> None:
        self._client = anthropic.Anthropic()
        self._model = model
        self._prompt_version = prompt_version
        self._max_units = max_units
        self._language = language
        self._system_prompt = _load_prompt(prompt_version)

    async def extract(
        self,
        transcript: Transcript,
        note: str | None = None,
        hint_type: str | None = None,
        log_dir: Path | None = None,
    ) -> list[ExtractedUnit]:
        from memforge.core.llm_log import record_call

        user_content = self._build_user_message(transcript, note, hint_type)

        _log_dir = log_dir or Path.cwd() / ".memforge" / "logs"
        with record_call(_log_dir, self._model, self._prompt_version, "extract") as meta:
            try:
                response = self._client.messages.create(
                    model=self._model,
                    max_tokens=4096,
                    system=self._system_prompt,
                    messages=[{"role": "user", "content": user_content}],
                )
                meta["input_tokens"] = response.usage.input_tokens
                meta["output_tokens"] = response.usage.output_tokens
            except anthropic.APIError as e:
                log.error("Anthropic API error during extraction: %s", e)
                raise

        text = next(
            (block.text for block in response.content if block.type == "text"), ""
        )
        return self._parse_response(text)

    def _build_user_message(
        self,
        transcript: Transcript,
        note: str | None,
        hint_type: str | None,
    ) -> str:
        parts: list[str] = []
        if note:
            parts.append(f"User note: {note}")
        if hint_type:
            parts.append(f"Preferred type: {hint_type}")
        parts.append(f"Max units to extract: {self._max_units}")
        parts.append(f"Language for output: {self._language}")
        parts.append("")
        parts.append("=== TRANSCRIPT ===")
        parts.append(transcript.text[:40_000])  # hard cap to avoid token abuse
        return "\n".join(parts)

    def _parse_response(self, text: str) -> list[ExtractedUnit]:
        # Extract JSON array from the response
        start = text.find("[")
        end = text.rfind("]") + 1
        if start == -1 or end == 0:
            log.warning("Extractor returned no JSON array; returning empty list")
            return []

        try:
            raw_list = json.loads(text[start:end])
        except json.JSONDecodeError as e:
            log.error("Failed to parse extractor JSON: %s", e)
            return []

        units: list[ExtractedUnit] = []
        valid_types = {"decision", "pattern", "gotcha", "contract", "glossary", "todo"}

        for item in raw_list:
            if not isinstance(item, dict):
                continue
            record_type = item.get("type", "pattern")
            if record_type not in valid_types:
                record_type = "pattern"
            try:
                unit = ExtractedUnit(
                    type=record_type,  # type: ignore[arg-type]
                    title=str(item.get("title", "Untitled")),
                    body_md=str(item.get("body_md", "")),
                    tags=[str(t) for t in item.get("tags", [])],
                    confidence=item.get("confidence", "medium"),  # type: ignore[arg-type]
                    links=[str(l) for l in item.get("links", [])],
                )
                units.append(unit)
            except Exception as e:
                log.warning("Skipping malformed unit: %s", e)

        return units[: self._max_units]
