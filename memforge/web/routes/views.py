"""Page view routes."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from memforge.config import GLOBAL_ROOT
from memforge.core.retriever import retrieve
from memforge.core.storage import Store

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
_CONFIG_DEFAULT = """\
version: 1

user:
  name: user
  email: ""
  timezone: UTC

extractor:
  backend: auto
  prompt_version: v1
  max_units_per_save: 10
  language: en
  model: claude-sonnet-4-6

compiler:
  prompt_version: v1
  conflict_strategy: llm-merge

retriever:
  top_k: 5
  bm25: true
  max_snippet_chars: 400

index:
  max_tokens: 4000
  order: [pinned, recent, cited]

scrubber:
  regex: default
  detect_secrets: false
  corporate_email_domains: []

sources:
  claude_code:
    enabled: true
    root: "~/.claude/projects"
  cursor:
    enabled: true
  codex:
    enabled: false
  stdin:
    enabled: true
    save_sessions: false

ui:
  host: 127.0.0.1
  port: 7777
  theme: auto

git:
  auto_commit: true
  sign_commits: false

logging:
  level: info
  file: logs/memforge.log
"""


_TYPE_DIR = {
    "decision": "decisions",
    "pattern": "concepts",
    "gotcha": "gotchas",
    "contract": "contracts",
    "glossary": "glossary",
    "todo": "todo-knowledge",
}


def _article_file_path(store_root: Path, article_type: str, slug: str) -> str:
    """Return the absolute file path for an article, URL-safe (forward slashes)."""
    dir_name = _TYPE_DIR.get(article_type, f"{article_type}s")
    p = store_root / "memory" / "knowledge" / dir_name / f"{slug}.md"
    return str(p).replace("\\", "/")


def _markdown_filter(text: str) -> str:
    from markdown_it import MarkdownIt

    res = MarkdownIt().render(text)
    return str(res)


templates.env.filters["markdown"] = _markdown_filter

router = APIRouter()


def _allowed_roots() -> list[Path]:
    return [GLOBAL_ROOT.resolve(), (Path.cwd() / ".memforge").resolve()]


def _resolve_if_allowed(path_str: str) -> Path | None:
    path = Path(path_str).resolve()
    for root in _allowed_roots():
        if path.is_relative_to(root):
            return path
    return None


def _stores() -> list[Store]:
    result = []
    for root in [GLOBAL_ROOT, Path.cwd() / ".memforge"]:
        s = Store(root)
        if s.memory.exists():
            result.append(s)
    return result


def _tr(request: Request, name: str, ctx: dict[str, Any]) -> HTMLResponse:
    """Compat wrapper for Starlette 1.0 TemplateResponse (request-first API)."""
    return templates.TemplateResponse(request, name, ctx)


# ---------------------------------------------------------------------------
# Dashboard /
# ---------------------------------------------------------------------------


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request) -> HTMLResponse:
    stores = _stores()
    total_articles = 0
    total_drafts = 0
    pinned = []
    recent = []

    for store in stores:
        articles = list(store.iter_articles())
        total_articles += len(articles)
        total_drafts += len(store.list_drafts())
        pinned_slugs = store.pinned_slugs()
        for slug in pinned_slugs:
            a = store.find_article_by_slug(slug)
            if a:
                pinned.append(a)
        recent_sorted = sorted(articles, key=lambda a: a.front_matter.updated, reverse=True)[:5]
        recent.extend(recent_sorted)

    recent = sorted(recent, key=lambda a: a.front_matter.updated, reverse=True)[:5]

    return _tr(
        request,
        "dashboard.html",
        {
            "total_articles": total_articles,
            "total_drafts": total_drafts,
            "pinned": pinned,
            "recent": recent,
            "page": "dashboard",
        },
    )


# ---------------------------------------------------------------------------
# Inbox /inbox
# ---------------------------------------------------------------------------


@router.get("/inbox", response_class=HTMLResponse)
async def inbox(request: Request) -> HTMLResponse:
    stores = _stores()
    drafts = []
    for store in stores:
        for d in store.list_drafts():
            drafts.append({"draft": d, "store_root": str(store.root)})

    return _tr(request, "inbox.html", {"drafts": drafts, "page": "inbox"})


# ---------------------------------------------------------------------------
# Knowledge /knowledge
# ---------------------------------------------------------------------------


@router.get("/knowledge", response_class=HTMLResponse)
async def knowledge(
    request: Request,
    q: str = "",
    type_filter: str = "",
    tag: str = "",
) -> HTMLResponse:
    stores = _stores()
    articles = []
    all_tags: set[str] = set()
    all_types: set[str] = set()

    for store in stores:
        for a in store.iter_articles():
            all_tags.update(a.front_matter.tags)
            all_types.add(a.front_matter.type)

            if type_filter and a.front_matter.type != type_filter:
                continue
            if tag and tag not in a.front_matter.tags:
                continue
            if (
                q
                and q.lower() not in (a.front_matter.title + " ".join(a.front_matter.tags)).lower()
            ):
                continue
            articles.append(a)

    articles.sort(key=lambda a: a.front_matter.updated, reverse=True)

    # BM25 search if query given
    if q:
        results = []
        for store in stores:
            results.extend(
                retrieve(
                    query=q,
                    index_path=store.index_file,
                    articles_root=store.memory,
                    top_k=20,
                )
            )
        slugs_ordered = [r.slug for r in sorted(results, key=lambda x: x.score, reverse=True)]
        slug_to_article = {a.slug: a for a in articles}
        if slugs_ordered:
            articles = [slug_to_article[s] for s in slugs_ordered if s in slug_to_article]

    return _tr(
        request,
        "knowledge.html",
        {
            "articles": articles,
            "all_tags": sorted(all_tags),
            "all_types": sorted(all_types),
            "q": q,
            "type_filter": type_filter,
            "tag": tag,
            "page": "knowledge",
        },
    )


# ---------------------------------------------------------------------------
# Article /knowledge/<slug>
# ---------------------------------------------------------------------------


@router.get("/knowledge/{slug}", response_class=HTMLResponse)
async def article_view(request: Request, slug: str) -> HTMLResponse:
    for store in _stores():
        article = store.find_article_by_slug(slug)
        if article:
            pinned = slug in store.pinned_slugs()
            file_path = _article_file_path(store.root, article.front_matter.type, slug)
            return _tr(
                request,
                "article.html",
                {
                    "article": article,
                    "pinned": pinned,
                    "file_path": file_path,
                    "page": "knowledge",
                },
            )

    return HTMLResponse("<h1>404 — Article not found</h1>", status_code=404)


# ---------------------------------------------------------------------------
# Daily /daily
# ---------------------------------------------------------------------------


@router.get("/daily", response_class=HTMLResponse)
async def daily(request: Request) -> HTMLResponse:
    stores = _stores()
    days = []
    for store in stores:
        for f in sorted(store.daily.glob("*.md"), reverse=True)[:30]:
            days.append({"date": f.stem, "path": str(f), "store_root": str(store.root)})

    return _tr(request, "daily.html", {"days": days, "page": "daily"})


# ---------------------------------------------------------------------------
# Stats /stats
# ---------------------------------------------------------------------------


@router.get("/stats", response_class=HTMLResponse)
async def stats(request: Request) -> HTMLResponse:
    stores = _stores()
    type_counts: Counter[str] = Counter()
    tag_counts: Counter[str] = Counter()
    total = 0
    total_drafts = 0

    for store in stores:
        articles = list(store.iter_articles())
        total += len(articles)
        total_drafts += len(store.list_drafts())
        for a in articles:
            type_counts[a.front_matter.type] += 1
            for t in a.front_matter.tags:
                tag_counts[t] += 1

    top_tags = tag_counts.most_common(10)

    return _tr(
        request,
        "stats.html",
        {
            "total": total,
            "total_drafts": total_drafts,
            "type_counts": dict(type_counts),
            "top_tags": top_tags,
            "page": "stats",
        },
    )


# ---------------------------------------------------------------------------
# Settings /settings
# ---------------------------------------------------------------------------


@router.get("/settings", response_class=HTMLResponse)
async def settings_get(request: Request) -> HTMLResponse:
    configs = _collect_configs()
    return _tr(request, "settings.html", {"configs": configs, "saved": False, "errors": []})


@router.post("/settings", response_class=HTMLResponse)
async def settings_post(request: Request) -> HTMLResponse:
    import yaml

    form = await request.form()
    config_path_str = form.get("config_path", "")
    content = form.get("content", "")

    errors: list[str] = []
    saved = False

    if config_path_str and content:
        p = _resolve_if_allowed(str(config_path_str))
        if p is None:
            errors.append("Path not in a known store.")
        else:
            try:
                parsed = yaml.safe_load(content)
                from memforge.config import Config

                Config.model_validate(parsed or {})
                if isinstance(content, str):
                    p.write_text(content, encoding="utf-8")
                else:
                    p.write_text((await content.read()).decode(), encoding="utf-8")
                saved = True
            except Exception as exc:
                errors.append(str(exc))

    configs = _collect_configs()
    return _tr(request, "settings.html", {"configs": configs, "saved": saved, "errors": errors})


def _collect_configs() -> list[tuple[str, str, str]]:
    result = []
    for label, root in [
        ("Global (~/.memforge)", GLOBAL_ROOT),
        ("Project (.memforge)", Path.cwd() / ".memforge"),
    ]:
        cfg_path = root / "config.yaml"
        if cfg_path.exists():
            content = cfg_path.read_text("utf-8")
        elif root.exists():
            content = _CONFIG_DEFAULT
        else:
            continue
        result.append((label, str(cfg_path), content))
    return result


# ---------------------------------------------------------------------------
# Editor /edit/<path:path>
# ---------------------------------------------------------------------------


@router.get("/edit/{file_path:path}", response_class=HTMLResponse)
async def editor(request: Request, file_path: str) -> HTMLResponse:
    p = _resolve_if_allowed(file_path)
    if p is None:
        return HTMLResponse("<h1>403 - Path outside store</h1>", status_code=403)
    if not p.exists():
        return HTMLResponse("<h1>404 — File not found</h1>", status_code=404)

    content = p.read_text("utf-8")
    return _tr(
        request,
        "editor.html",
        {
            "file_path": file_path,
            "content": content,
            "page": "editor",
        },
    )
