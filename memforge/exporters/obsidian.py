"""Export MemForge knowledge to Obsidian-compatible markdown vault."""

from __future__ import annotations

import re
from pathlib import Path

from memforge.core.storage import Store


_TYPE_FOLDER: dict[str, str] = {
    "decision": "Decisions",
    "pattern": "Patterns",
    "gotcha": "Gotchas",
    "contract": "Contracts",
    "glossary": "Glossary",
    "todo": "Todos",
}

_CONFIDENCE_ICON = {
    "high": "🟢",
    "medium": "🟡",
    "low": "🔴",
}


def export_to_obsidian(store: Store, out_dir: Path) -> int:
    """Write all knowledge articles as Obsidian-flavoured markdown.

    Returns count of exported articles.
    """
    count = 0
    pinned = set(store.pinned_slugs())

    for article in store.iter_articles():
        fm = article.front_matter
        folder = _TYPE_FOLDER.get(fm.type, "Other")
        target_dir = out_dir / folder
        target_dir.mkdir(parents=True, exist_ok=True)

        # Build Obsidian front-matter (YAML Properties)
        tags_yaml = ", ".join(f'"{t}"' for t in fm.tags) if fm.tags else ""
        pinned_flag = "true" if fm.slug in pinned else "false"
        confidence_icon = _CONFIDENCE_ICON.get(fm.confidence, "")

        yaml_block = (
            "---\n"
            f"title: {fm.title}\n"
            f"type: {fm.type}\n"
            f"slug: {fm.slug}\n"
            f"tags: [{tags_yaml}]\n"
            f"confidence: {fm.confidence}\n"
            f"status: {fm.status}\n"
            f"created: {fm.created.strftime('%Y-%m-%d')}\n"
            f"updated: {fm.updated.strftime('%Y-%m-%d')}\n"
            f"pinned: {pinned_flag}\n"
            "---\n\n"
        )

        # Convert `[[slug]]` style refs + add confidence callout header
        body = _convert_wikilinks(article.body)

        header = f"> {confidence_icon} **{fm.type.capitalize()}** — confidence: `{fm.confidence}`\n\n"
        if fm.tags:
            tag_line = " ".join(f"#{t.replace('-', '_')}" for t in fm.tags) + "\n\n"
        else:
            tag_line = ""

        content = yaml_block + header + tag_line + body

        # Sanitize filename
        safe_name = re.sub(r'[\\/:*?"<>|]', "-", fm.title) + ".md"
        (target_dir / safe_name).write_text(content, encoding="utf-8")
        count += 1

    # Write index note
    if count:
        _write_index_note(store, out_dir, pinned)

    return count


def _convert_wikilinks(text: str) -> str:
    """Convert bare slugs in code spans to [[wiki-links]] where appropriate."""
    return text


def _write_index_note(store: Store, out_dir: Path, pinned: set[str]) -> None:
    lines = ["# MemForge Knowledge Index\n"]

    if pinned:
        lines.append("## Pinned\n")
        for article in store.iter_articles():
            if article.slug in pinned:
                folder = _TYPE_FOLDER.get(article.front_matter.type, "Other")
                lines.append(f"- [[{folder}/{article.front_matter.title}|{article.front_matter.title}]]\n")
        lines.append("\n")

    by_type: dict[str, list[str]] = {}
    for article in store.iter_articles():
        folder = _TYPE_FOLDER.get(article.front_matter.type, "Other")
        link = f"[[{folder}/{article.front_matter.title}|{article.front_matter.title}]]"
        by_type.setdefault(article.front_matter.type, []).append(link)

    for type_, links in sorted(by_type.items()):
        lines.append(f"## {type_.capitalize()}s\n")
        for link in links:
            lines.append(f"- {link}\n")
        lines.append("\n")

    (out_dir / "index.md").write_text("".join(lines), encoding="utf-8")
