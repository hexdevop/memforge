"""BM25 + substring retriever."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from rank_bm25 import BM25Okapi


@dataclass
class SearchResult:
    slug: str
    title: str
    record_type: str
    score: float
    snippet: str
    path: str
    tags: list[str] = field(default_factory=list)


def retrieve(
    query: str,
    index_path: Path,
    articles_root: Path,
    top_k: int = 5,
    max_snippet_chars: int = 400,
    record_type: str | None = None,
) -> list[SearchResult]:
    if not index_path.exists():
        return []

    index_text = index_path.read_text("utf-8")
    entries = _parse_index(index_text)

    if record_type:
        entries = [e for e in entries if e.get("type") == record_type]

    if not entries:
        return []

    # BM25 over titles + tags
    tokenized = [_tokenize(e.get("title", "") + " " + " ".join(e.get("tags", []))) for e in entries]
    bm25 = BM25Okapi(tokenized)
    scores = bm25.get_scores(_tokenize(query))

    ranked = sorted(zip(scores, entries), key=lambda x: x[0], reverse=True)

    results: list[SearchResult] = []
    for score, entry in ranked[:top_k]:
        slug = entry.get("slug", "")
        path_str = entry.get("path", "")
        article_path = articles_root / path_str if path_str else None
        snippet = _get_snippet(article_path, query, max_snippet_chars)
        results.append(
            SearchResult(
                slug=slug,
                title=entry.get("title", slug),
                record_type=entry.get("type", ""),
                score=score,
                snippet=snippet,
                path=path_str,
                tags=entry.get("tags", []),
            )
        )

    # Fallback: if all BM25 scores are 0, do substring search
    if all(r.score == 0 for r in results):
        results = _substring_fallback(query, entries, articles_root, top_k, max_snippet_chars)

    return [r for r in results if r.slug]


_TYPE_DIR: dict[str, str] = {
    "decision": "decisions",
    "pattern": "concepts",
    "gotcha": "gotchas",
    "contract": "contracts",
    "glossary": "glossary",
    "todo": "todo-knowledge",
}


def _type_to_dir(record_type: str) -> str:
    return _TYPE_DIR.get(record_type, f"{record_type}s")


def _parse_index(text: str) -> list[dict]:  # type: ignore[type-arg]
    """Parse entries like: - slug (type, confidence) — Title [tags: a, b]"""
    entries: list[dict] = []  # type: ignore[type-arg]
    pattern = re.compile(
        r"- (?P<slug>\S+) \((?P<type>\w+), (?P<confidence>\w+)\) — "
        r"(?P<title>[^\[]+?)(?:\s*\[tags: (?P<tags>[^\]]+)\])?$"
    )
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("### "):
            section = line[4:].strip()
            # detect "type (N)" pattern
            m = re.match(r"(\w+)(?:\s+\(\d+\))?", section)
            if m:
                m.group(1)
        m = pattern.match(line)
        if m:
            tags_str = m.group("tags") or ""
            tags = [t.strip() for t in tags_str.split(",") if t.strip()]
            record_type = m.group("type")
            entry = {
                "slug": m.group("slug"),
                "type": record_type,
                "confidence": m.group("confidence"),
                "title": m.group("title").strip(),
                "tags": tags,
                "path": f"knowledge/{_type_to_dir(record_type)}/{m.group('slug')}.md",
            }
            entries.append(entry)
    return entries


def _tokenize(text: str) -> list[str]:
    return re.sub(r"[^\w\s]", " ", text.lower()).split()


def _get_snippet(path: Path | None, query: str, max_chars: int) -> str:
    if not path or not path.exists():
        return ""
    text = path.read_text("utf-8", errors="replace")
    # Strip YAML front-matter before searching
    if text.startswith("---"):
        end = text.find("---", 3)
        if end != -1:
            text = text[end + 3 :].lstrip()
    query_lower = query.lower()
    text_lower = text.lower()
    pos = text_lower.find(query_lower.split()[0] if query_lower.split() else "")
    if pos == -1:
        return text[:max_chars].strip()
    start = max(0, pos - 100)
    return text[start : start + max_chars].strip()


def _substring_fallback(
    query: str,
    entries: list[dict],  # type: ignore[type-arg]
    articles_root: Path,
    top_k: int,
    max_snippet_chars: int,
) -> list[SearchResult]:
    query_lower = query.lower()
    results: list[SearchResult] = []
    for entry in entries:
        title_match = query_lower in entry.get("title", "").lower()
        tag_match = any(query_lower in t for t in entry.get("tags", []))
        if title_match or tag_match:
            path_str = entry.get("path", "")
            article_path = articles_root / path_str if path_str else None
            results.append(
                SearchResult(
                    slug=entry.get("slug", ""),
                    title=entry.get("title", ""),
                    record_type=entry.get("type", ""),
                    score=1.0,
                    snippet=_get_snippet(article_path, query, max_snippet_chars),
                    path=path_str,
                    tags=entry.get("tags", []),
                )
            )
    return results[:top_k]
