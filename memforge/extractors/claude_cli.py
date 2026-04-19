"""Extractor backed by the local claude CLI (subscription auth, no API key)."""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time
from pathlib import Path

from memforge.core.models import ExtractedUnit, Transcript

log = logging.getLogger(__name__)

_EXTRACT_SCHEMA = json.dumps({
    "type": "object",
    "properties": {
        "units": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": ["decision", "pattern", "gotcha", "contract", "glossary", "todo"],
                    },
                    "title": {"type": "string"},
                    "body_md": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "confidence": {
                        "type": "string",
                        "enum": ["high", "medium", "low"],
                    },
                },
                "required": ["type", "title", "body_md", "tags", "confidence"],
            },
        }
    },
    "required": ["units"],
})


def is_available() -> bool:
    return shutil.which("claude") is not None


class ClaudeCliExtractor:
    """Runs extraction via `claude -p` — uses Claude subscription, no API key."""

    def __init__(
        self,
        prompt_version: str = "v1",
        max_units: int = 10,
        language: str = "en",
        timeout: int = 120,
    ) -> None:
        self._prompt_version = prompt_version
        self._max_units = max_units
        self._language = language
        self._timeout = timeout
        self._system_prompt = self._load_prompt(prompt_version)

    @staticmethod
    def _load_prompt(version: str) -> str:
        p = Path(__file__).parent.parent / "prompts" / f"extractor.{version}.md"
        return p.read_text("utf-8") if p.exists() else ""

    async def extract(
        self,
        transcript: Transcript,
        note: str | None = None,
        hint_type: str | None = None,
        log_dir: Path | None = None,
    ) -> list[ExtractedUnit]:
        user_msg = self._build_user_message(transcript, note, hint_type)

        cmd = [
            "claude",
            "--print",
            "--output-format", "json",
            "--json-schema", _EXTRACT_SCHEMA,
            "--system-prompt", self._system_prompt,
            user_msg,
        ]

        t0 = time.monotonic()
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self._timeout,
                encoding="utf-8",
            )
        except subprocess.TimeoutExpired:
            log.error("claude CLI timed out after %ds", self._timeout)
            return []
        except FileNotFoundError:
            log.error("claude CLI not found — install Claude Code or set ANTHROPIC_API_KEY")
            return []

        latency_ms = int((time.monotonic() - t0) * 1000)

        if result.returncode != 0:
            log.error("claude CLI error (rc=%d): %s", result.returncode, result.stderr[:200])
            return []

        units = self._parse_response(result.stdout)
        self._log_call(log_dir, latency_ms, len(units))
        return units

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
        parts.append(transcript.text[:40_000])
        return "\n".join(parts)

    def _parse_response(self, raw: str) -> list[ExtractedUnit]:
        # --output-format json wraps in {"type":"result","result":..., "subtype":"..."}
        try:
            outer = json.loads(raw)
            # Unwrap claude CLI json envelope
            if isinstance(outer, dict):
                if "result" in outer:
                    data = outer["result"]
                elif "units" in outer:
                    data = outer
                else:
                    # Try finding units anywhere
                    data = outer
            else:
                data = outer
        except json.JSONDecodeError:
            # Fallback: find JSON array or object in raw text
            start = raw.find("{")
            if start == -1:
                start = raw.find("[")
            end = raw.rfind("}") + 1 if "{" in raw else raw.rfind("]") + 1
            if start == -1 or end <= 0:
                log.warning("ClaudeCliExtractor: no JSON in response")
                return []
            try:
                data = json.loads(raw[start:end])
            except json.JSONDecodeError:
                log.warning("ClaudeCliExtractor: JSON parse failed")
                return []

        # data may be {"units": [...]} or directly a list
        raw_list = []
        if isinstance(data, dict):
            raw_list = data.get("units", [])
            if not raw_list:
                # Maybe the model returned a bare list inside result
                for v in data.values():
                    if isinstance(v, list):
                        raw_list = v
                        break
        elif isinstance(data, list):
            raw_list = data

        units: list[ExtractedUnit] = []
        valid_types = {"decision", "pattern", "gotcha", "contract", "glossary", "todo"}
        for item in raw_list:
            if not isinstance(item, dict):
                continue
            rtype = item.get("type", "pattern")
            if rtype not in valid_types:
                rtype = "pattern"
            try:
                units.append(ExtractedUnit(
                    type=rtype,  # type: ignore[arg-type]
                    title=str(item.get("title", "Untitled")),
                    body_md=str(item.get("body_md", "")),
                    tags=[str(t) for t in item.get("tags", [])],
                    confidence=item.get("confidence", "medium"),  # type: ignore[arg-type]
                ))
            except Exception as exc:
                log.warning("Skipping malformed unit: %s", exc)

        return units[: self._max_units]

    def _log_call(self, log_dir: Path | None, latency_ms: int, n_units: int) -> None:
        if not log_dir:
            return
        try:
            from memforge.core.llm_log import _append
            _append(log_dir, {
                "timestamp": __import__("datetime").datetime.now(
                    __import__("datetime").timezone.utc
                ).isoformat(),
                "operation": "extract",
                "model": "claude-subscription",
                "prompt_version": self._prompt_version,
                "input_tokens": 0,
                "output_tokens": 0,
                "cost_usd": 0.0,
                "latency_ms": latency_ms,
                "error": None,
                "units_extracted": n_units,
            })
        except Exception:
            pass
