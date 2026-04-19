"""Daily logs → knowledge compiler."""

from __future__ import annotations

import hashlib
import logging
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from memforge.core.models import Article, ArticleFrontMatter, RecordType
from memforge.core.storage import Store, slugify

log = logging.getLogger(__name__)


def _load_prompt(version: str) -> str:
    p = Path(__file__).parent.parent / "prompts" / f"compiler.{version}.md"
    return p.read_text("utf-8") if p.exists() else ""


def _call_api(client, model: str, system: str, prompt: str, log_dir: Path | None) -> str:
    """Call Anthropic API directly."""
    import anthropic
    from memforge.core.llm_log import record_call

    _log_dir = log_dir or Path.cwd() / ".memforge" / "logs"
    with record_call(_log_dir, model, "v1", "compile") as meta:
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        meta["input_tokens"] = response.usage.input_tokens
        meta["output_tokens"] = response.usage.output_tokens
    return next((b.text for b in response.content if b.type == "text"), "").strip()


def _call_cli(system: str, prompt: str, log_dir: Path | None, timeout: int = 120) -> str:
    """Call claude CLI (subscription auth, no API key)."""
    t0 = time.monotonic()
    try:
        result = subprocess.run(
            ["claude", "--print", "--system-prompt", system, prompt],
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
        )
    except subprocess.TimeoutExpired:
        log.error("claude CLI timed out during compile")
        return ""
    except FileNotFoundError:
        log.error("claude CLI not found")
        return ""

    latency_ms = int((time.monotonic() - t0) * 1000)

    if result.returncode != 0:
        log.error("claude CLI error (rc=%d): %s", result.returncode, result.stderr[:200])
        return ""

    if log_dir:
        try:
            from memforge.core.llm_log import _append
            _append(log_dir, {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "operation": "compile",
                "model": "claude-subscription",
                "prompt_version": "v1",
                "input_tokens": 0,
                "output_tokens": 0,
                "cost_usd": 0.0,
                "latency_ms": latency_ms,
                "error": None,
            })
        except Exception:
            pass

    return result.stdout.strip()


class Compiler:
    def __init__(
        self,
        model: str = "claude-sonnet-4-6",
        prompt_version: str = "v1",
        backend: str = "auto",
    ) -> None:
        self._model = model
        self._system = _load_prompt(prompt_version)
        self._backend = _resolve_backend(backend)
        self._client = None
        if self._backend == "api":
            import anthropic
            self._client = anthropic.Anthropic()

    def compile(
        self,
        store: Store,
        since: datetime | None = None,
        dry_run: bool = False,
    ) -> tuple[list[Article], list[Article]]:
        """Compile daily logs into knowledge articles.

        Returns (created, updated) lists of Articles.
        """
        daily_entries = self._collect_daily_entries(store, since)
        if not daily_entries:
            log.info("No new daily entries to compile.")
            return [], []

        groups = self._group_by_slug(daily_entries)

        created: list[Article] = []
        updated: list[Article] = []

        for slug, entries in groups.items():
            existing = store.find_article_by_slug(slug)
            if existing:
                merged = self._merge(existing, entries, log_dir=store.logs)
                if merged and not dry_run:
                    store.write_article(merged)
                    updated.append(merged)
            else:
                new_article = self._create_from_entries(entries, slug)
                if new_article and not dry_run:
                    store.write_article(new_article)
                    created.append(new_article)

        return created, updated

    def _collect_daily_entries(
        self, store: Store, since: datetime | None
    ) -> list[dict]:  # type: ignore[type-arg]
        entries: list[dict] = []  # type: ignore[type-arg]
        for daily_file in sorted(store.daily.glob("*.md")):
            if since:
                try:
                    file_date = datetime.strptime(daily_file.stem, "%Y-%m-%d").replace(
                        tzinfo=timezone.utc
                    )
                    if file_date < since:
                        continue
                except ValueError:
                    pass
            entries.extend(self._parse_daily_file(daily_file))
        return entries

    def _parse_daily_file(self, path: Path) -> list[dict]:  # type: ignore[type-arg]
        import frontmatter

        text = path.read_text("utf-8")
        blocks = text.split("\n\n---\n\n")
        entries = []
        for block in blocks:
            block = block.strip()
            if not block:
                continue
            try:
                post = frontmatter.loads(block)
                entries.append(
                    {
                        "type": post.get("type", "pattern"),
                        "title": post.get("title", ""),
                        "slug": post.get("slug", slugify(post.get("title", ""))),
                        "body": post.content,
                        "tags": post.get("tags", []),
                        "confidence": post.get("confidence", "medium"),
                        "source_agent": post.get("source_agent"),
                        "source_session_id": post.get("source_session_id"),
                    }
                )
            except Exception:
                pass
        return entries

    def _group_by_slug(
        self, entries: list[dict]  # type: ignore[type-arg]
    ) -> dict[str, list[dict]]:  # type: ignore[type-arg]
        groups: dict[str, list[dict]] = {}  # type: ignore[type-arg]
        for entry in entries:
            slug = entry.get("slug") or slugify(entry.get("title", ""))
            groups.setdefault(slug, []).append(entry)
        return groups

    def _merge(
        self,
        existing: Article,
        new_entries: list[dict],  # type: ignore[type-arg]
        log_dir: Path | None = None,
    ) -> Article | None:
        new_bodies = "\n\n".join(e.get("body", "") for e in new_entries)
        prompt = (
            f"EXISTING ARTICLE:\n{existing.body}\n\n"
            f"NEW ENTRIES:\n{new_bodies}"
        )

        try:
            if self._backend == "api":
                merged_body = _call_api(self._client, self._model, self._system, prompt, log_dir)
            else:
                merged_body = _call_cli(self._system, prompt, log_dir)
        except Exception as e:
            log.error("Merge LLM call failed: %s", e)
            return None

        if not merged_body:
            return None

        fm = existing.front_matter.model_copy(
            update={
                "updated": datetime.now(timezone.utc),
                "fingerprint": _fingerprint(merged_body),
            }
        )
        return Article(front_matter=fm, body=merged_body)

    def _create_from_entries(
        self, entries: list[dict], slug: str  # type: ignore[type-arg]
    ) -> Article | None:
        first = entries[0]
        combined_body = "\n\n".join(e.get("body", "") for e in entries)
        record_type: RecordType = first.get("type", "pattern")  # type: ignore[assignment]
        tags = first.get("tags", [])
        for e in entries[1:]:
            for t in e.get("tags", []):
                if t not in tags:
                    tags.append(t)

        fm = ArticleFrontMatter(
            id=f"{record_type[:3]}-{slug}",
            type=record_type,
            title=first.get("title", slug),
            slug=slug,
            tags=tags,
            confidence=first.get("confidence", "medium"),  # type: ignore[arg-type]
            source_agent=first.get("source_agent"),
            source_session_id=first.get("source_session_id"),
            fingerprint=_fingerprint(combined_body),
        )
        return Article(front_matter=fm, body=combined_body)


def _resolve_backend(backend: str) -> str:
    """Resolve 'auto' to 'api' or 'cli' based on available credentials."""
    import os
    import shutil

    if backend != "auto":
        return backend
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "api"
    if shutil.which("claude"):
        return "cli"
    return "api"  # will fail with a clear error on first call


def _fingerprint(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()[:16]
