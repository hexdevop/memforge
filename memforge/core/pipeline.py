"""save → extract → scrub → draft pipeline."""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass

from memforge.config import Config
from memforge.core.models import Draft, Transcript
from memforge.core.storage import Store
from memforge.extractors import ClaudeCliExtractor, ClaudeExtractor
from memforge.scrubber.regex import scrub

log = logging.getLogger(__name__)


@dataclass
class SaveResult:
    drafts: list[Draft]
    transcript_hash: str
    quarantined: int
    backend_used: str = "unknown"


def _select_extractor(cfg: Config) -> tuple[ClaudeCliExtractor | ClaudeExtractor, str]:
    """Return the right extractor based on config and environment."""
    backend = getattr(cfg.extractor, "backend", "auto")

    if backend == "auto":
        has_api_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
        has_cli = shutil.which("claude") is not None
        # Prefer subscription CLI over API key — avoids per-token cost by default
        if has_cli:
            backend = "cli"
        elif has_api_key:
            backend = "api"
        else:
            raise RuntimeError(
                "No LLM backend available.\n"
                "  Option 1 (API key): export ANTHROPIC_API_KEY=sk-ant-...\n"
                "  Option 2 (subscription): install Claude Code — https://claude.ai/code"
            )

    if backend == "cli":
        from memforge.extractors.claude_cli import ClaudeCliExtractor

        return (
            ClaudeCliExtractor(
                prompt_version=cfg.extractor.prompt_version,
                max_units=cfg.extractor.max_units_per_save,
                language=cfg.extractor.language,
            ),
            "cli",
        )
    else:
        from memforge.extractors.claude_sdk import ClaudeExtractor

        return (
            ClaudeExtractor(
                model=cfg.extractor.model,
                prompt_version=cfg.extractor.prompt_version,
                max_units=cfg.extractor.max_units_per_save,
                language=cfg.extractor.language,
            ),
            "api",
        )


async def run_save(
    transcript: Transcript,
    store: Store,
    config: Config,
    note: str | None = None,
    hint_type: str | None = None,
    dry_run: bool = False,
) -> SaveResult:
    transcript_hash = transcript.hash

    extractor, backend = _select_extractor(config)
    units = await extractor.extract(
        transcript,
        note=note,
        hint_type=hint_type,
        log_dir=store.logs,
    )

    if not units:
        log.info("Extractor returned no units.")
        return SaveResult(
            drafts=[],
            transcript_hash=transcript_hash,
            quarantined=0,
            backend_used=backend,
        )

    drafts: list[Draft] = []
    quarantined = 0

    for unit in units:
        scrub_result = scrub(
            unit.body_md,
            mode=config.scrubber.regex,
            corporate_email_domains=config.scrubber.corporate_email_domains,
        )
        unit = unit.model_copy(update={"body_md": scrub_result.text})

        quarantine = scrub_result.was_modified
        if quarantine:
            quarantined += 1
            log.warning(
                "Scrubber redacted %d item(s) in unit '%s'",
                len(scrub_result.redacted),
                unit.title,
            )

        if dry_run:
            continue

        draft = store.write_draft(
            unit=unit,
            transcript_hash=transcript_hash,
            source_agent=transcript.agent,
            source_session_id=transcript.session_id,
            quarantine=quarantine,
        )
        drafts.append(draft)

    return SaveResult(
        drafts=drafts,
        transcript_hash=transcript_hash,
        quarantined=quarantined,
        backend_used=backend,
    )
