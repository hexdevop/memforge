"""Deterministic index.md builder."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from memforge.core.models import Article, IndexEntry, RecordType


def build_index(
    articles: list[Article],
    pinned_slugs: list[str],
    max_tokens: int = 4000,
    order: list[str] | None = None,
) -> str:
    if order is None:
        order = ["pinned", "recent", "cited"]

    entries = [_article_to_entry(a, pinned_slugs) for a in articles]
    entries = [e for e in entries if e is not None]

    # Sort: pinned first, then by updated desc
    pinned = [e for e in entries if e.pinned]
    rest = [e for e in entries if not e.pinned]
    rest.sort(key=lambda e: e.updated or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    ordered = pinned + rest

    # Group by type and topic
    by_type: dict[RecordType, list[IndexEntry]] = defaultdict(list)
    by_topic: dict[str, list[IndexEntry]] = defaultdict(list)

    for entry in ordered:
        by_type[entry.type].append(entry)
        for tag in entry.tags:
            by_topic[tag].append(entry)

    lines: list[str] = [
        "# MemForge Index",
        f"# Generated: {datetime.now(timezone.utc).isoformat()}",
        f"# Articles: {len(entries)}",
        "",
    ]

    # Pinned section
    if pinned_slugs:
        lines.append("## pinned")
        for slug in pinned_slugs:
            lines.append(f"- {slug}")
        lines.append("")

    # By topic
    lines.append("## by_topic")
    for topic in sorted(by_topic):
        lines.append(f"\n### {topic}")
        for e in by_topic[topic]:
            lines.append(_format_entry(e))

    lines.append("")

    # By type
    lines.append("## by_type")
    for rtype in sorted(by_type):
        group = by_type[rtype]
        lines.append(f"\n### {rtype} ({len(group)})")
        for e in group:
            lines.append(_format_entry(e))

    return _trim_to_tokens("\n".join(lines), max_tokens)


def _article_to_entry(article: Article, pinned_slugs: list[str]) -> IndexEntry | None:
    fm = article.front_matter
    if fm.status == "archived":
        return None
    return IndexEntry(
        slug=fm.slug,
        type=fm.type,
        title=fm.title,
        tags=fm.tags,
        confidence=fm.confidence,
        scope=fm.scope,
        pinned=fm.slug in pinned_slugs,
        updated=fm.updated,
        path=f"knowledge/{fm.type}s/{fm.slug}.md",
    )


def _format_entry(e: IndexEntry) -> str:
    tags_str = f" [tags: {', '.join(e.tags)}]" if e.tags else ""
    return f"- {e.slug} ({e.type}, {e.confidence}) — {e.title}{tags_str}"


def _trim_to_tokens(text: str, max_tokens: int) -> str:
    # Rough approximation: 1 token ≈ 4 chars
    max_chars = max_tokens * 4
    if len(text) <= max_chars:
        return text
    # Keep head (pinned + recent) and truncate
    truncated = text[:max_chars]
    last_newline = truncated.rfind("\n")
    return truncated[:last_newline] + "\n\n# [index truncated — increase index.max_tokens]\n"


def write_index(store_knowledge_dir: Path, content: str) -> Path:
    path = store_knowledge_dir / "index.md"
    path.write_text(content, encoding="utf-8")
    return path
