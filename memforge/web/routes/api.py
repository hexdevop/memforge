"""API endpoints for HTMX actions."""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import HTMLResponse

from memforge.config import GLOBAL_ROOT, Config, load_config
from memforge.core.models import Article, ArticleFrontMatter, Draft
from memforge.core.storage import Store, slugify

router = APIRouter()


def _allowed_roots() -> list[Path]:
    return [(Path.cwd() / ".memforge").resolve(), GLOBAL_ROOT.resolve()]


def _resolve_if_allowed(path_str: str) -> Path | None:
    path = Path(path_str).resolve()
    for root in _allowed_roots():
        if path.is_relative_to(root):
            return path
    return None


def _config_for_store(store: Store) -> Config:
    if store.root.resolve() == GLOBAL_ROOT.resolve():
        return load_config()
    return load_config(store.root.parent)


# ---------------------------------------------------------------------------
# GET /api/daily-content  — lazy-load daily log content
# ---------------------------------------------------------------------------


@router.get("/daily-content", response_class=HTMLResponse)
async def daily_content(path: str) -> HTMLResponse:
    from markdown_it import MarkdownIt

    p = _resolve_if_allowed(path)
    if p is None:
        raise HTTPException(status_code=403, detail="Path outside store")
    if not p.exists():
        return HTMLResponse("<em>File not found</em>")
    text = p.read_text("utf-8")
    # Strip front-matter blocks and render markdown
    blocks = text.split("\n\n---\n\n")
    html_parts = []
    for block in blocks:
        block = block.strip()
        if block.startswith("---"):
            end = block.find("---", 3)
            if end != -1:
                block = block[end + 3 :].strip()
        if block:
            html_parts.append(MarkdownIt().render(block))
    return HTMLResponse("<hr class='my-4'>".join(html_parts) or "<em>Empty</em>")


def _find_store_for_draft(draft_id: str) -> tuple[Store | None, Draft | None]:
    for root in [Path.cwd() / ".memforge", GLOBAL_ROOT]:
        store = Store(root)
        if store.memory.exists():
            d = store.get_draft(draft_id)
            if d:
                return store, d
    return None, None


def _find_store_for_slug(slug: str) -> tuple[Store | None, object]:
    for root in [Path.cwd() / ".memforge", GLOBAL_ROOT]:
        store = Store(root)
        if store.memory.exists():
            a = store.find_article_by_slug(slug)
            if a:
                return store, a
    return None, None


def _find_store_for_pinned_slug(slug: str) -> Store | None:
    for root in [Path.cwd() / ".memforge", GLOBAL_ROOT]:
        store = Store(root)
        if not store.memory.exists():
            continue
        if slug in store.pinned_slugs():
            return store
    for root in [Path.cwd() / ".memforge", GLOBAL_ROOT]:
        store = Store(root)
        if store.memory.exists() and store.find_article_by_slug(slug):
            return store
    return None


def _store_for_path(path: Path) -> Store | None:
    for root in _allowed_roots():
        if path.is_relative_to(root):
            return Store(root)
    return None


# ---------------------------------------------------------------------------
# POST /api/commit/<draft_id>  — Accept draft → daily
# ---------------------------------------------------------------------------


@router.post("/commit/{draft_id}", response_class=HTMLResponse)
async def commit_draft(draft_id: str) -> HTMLResponse:
    store, draft = _find_store_for_draft(draft_id)
    if not store or not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    fm = ArticleFrontMatter(
        id=f"{draft.unit.type[:3]}-{draft.draft_id[:8]}",
        type=draft.unit.type,
        title=draft.unit.title,
        slug=slugify(draft.unit.title),
        tags=draft.unit.tags,
        confidence=draft.unit.confidence,
        source_agent=draft.source_agent,
        source_session_id=draft.source_session_id,
        transcript_hash=draft.transcript_hash,
    )
    article = Article(front_matter=fm, body=draft.unit.body_md)
    path = store.append_to_daily(article)
    store.delete_draft(draft_id)

    cfg = _config_for_store(store)
    if cfg.git.auto_commit:
        store.git_commit(
            f"commit(daily): {draft.unit.title[:50]}",
            [path],
        )

    return HTMLResponse(
        f'<div class="text-green-600 text-sm py-1">✓ Committed: {draft.unit.title}</div>'
    )


# ---------------------------------------------------------------------------
# PUT /api/draft/<draft_id>  — Update draft body in-place
# ---------------------------------------------------------------------------


@router.put("/draft/{draft_id}", response_class=HTMLResponse)
async def update_draft(draft_id: str, content: str = Form(...)) -> HTMLResponse:
    store, draft = _find_store_for_draft(draft_id)
    if not store or not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    import frontmatter as fm_mod

    path = store.inbox / f"draft-{draft_id}.md"
    post = fm_mod.load(str(path))
    post.content = content
    path.write_text(fm_mod.dumps(post), encoding="utf-8")

    return HTMLResponse('<span class="text-green-600 text-xs">✓ Saved</span>')


# ---------------------------------------------------------------------------
# POST /api/discard/<draft_id>
# ---------------------------------------------------------------------------


@router.post("/discard/{draft_id}", response_class=HTMLResponse)
async def discard_draft(draft_id: str) -> HTMLResponse:
    for root in [Path.cwd() / ".memforge", GLOBAL_ROOT]:
        store = Store(root)
        if store.memory.exists() and store.delete_draft(draft_id):
            return HTMLResponse("")  # htmx will remove the element
    raise HTTPException(status_code=404, detail="Draft not found")


# ---------------------------------------------------------------------------
# POST /api/forget/<slug>
# ---------------------------------------------------------------------------


@router.post("/forget/{slug}", response_class=HTMLResponse)
async def forget_article(slug: str) -> HTMLResponse:
    store, article = _find_store_for_slug(slug)
    if not store or not article:
        raise HTTPException(status_code=404, detail="Article not found")

    for article_path in store.knowledge.rglob("*.md"):
        a = store.read_article(article_path)
        if a and a.slug == slug:
            dest = store.archive / article_path.name
            shutil.move(str(article_path), str(dest))
            cfg = _config_for_store(store)
            if cfg.git.auto_commit:
                store.git_commit(f"forget(archive): {slug}")
            return HTMLResponse('<div class="text-amber-600 text-sm">Article archived.</div>')
    raise HTTPException(status_code=404, detail="Article file not found")


# ---------------------------------------------------------------------------
# PUT /api/article  — Save edited article
# ---------------------------------------------------------------------------


@router.put("/article", response_class=HTMLResponse)
async def save_article(file_path: str = Form(...), content: str = Form(...)) -> HTMLResponse:
    p = _resolve_if_allowed(file_path)
    if p is None:
        raise HTTPException(status_code=403, detail="Path outside store")
    if not p.exists():
        raise HTTPException(status_code=404, detail="File not found")

    p.write_text(content, encoding="utf-8")

    store = _store_for_path(p)
    if store is not None:
        cfg = _config_for_store(store)
        if cfg.git.auto_commit:
            store.git_commit(f"edit: {p.name}", [p])

    return HTMLResponse('<div class="text-green-600 text-sm">✓ Saved</div>')


# ---------------------------------------------------------------------------
# POST /api/pin/<slug>  /  POST /api/unpin/<slug>
# ---------------------------------------------------------------------------


@router.post("/pin/{slug}", response_class=HTMLResponse)
async def pin_article(slug: str) -> HTMLResponse:
    store = _find_store_for_pinned_slug(slug)
    if store and store.find_article_by_slug(slug):
        store.pin(slug)
        return HTMLResponse(
            f'<button hx-post="/api/unpin/{slug}" hx-swap="outerHTML" '
            f'class="btn-sm bg-amber-100 text-amber-800">Unpin</button>'
        )
    raise HTTPException(status_code=404, detail="Article not found")


@router.post("/unpin/{slug}", response_class=HTMLResponse)
async def unpin_article(slug: str) -> HTMLResponse:
    store = _find_store_for_pinned_slug(slug)
    if store:
        store.unpin(slug)
        return HTMLResponse(
            f'<button hx-post="/api/pin/{slug}" hx-swap="outerHTML" '
            f'class="btn-sm bg-gray-100 text-gray-600">Pin</button>'
        )
    raise HTTPException(status_code=404)


# ---------------------------------------------------------------------------
# GET /api/preview  — Markdown preview for editor
# ---------------------------------------------------------------------------


@router.get("/preview", response_class=HTMLResponse)
async def preview(content: str = "") -> HTMLResponse:
    from markdown_it import MarkdownIt

    md = MarkdownIt()
    html = md.render(content)
    return HTMLResponse(html)
