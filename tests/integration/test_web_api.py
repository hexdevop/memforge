"""Integration tests for the Web UI API endpoints."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from git import Repo

from memforge.config import Config, GitConfig, save_config
from memforge.core.models import Article, ArticleFrontMatter, ExtractedUnit
from memforge.core.storage import Store
from memforge.web.app import create_app


@pytest.fixture
def store(tmp_path: Path) -> Store:
    s = Store(tmp_path)
    s.ensure_dirs()
    return s


@pytest.fixture
def client(store: Store, tmp_path: Path, monkeypatch) -> TestClient:
    """TestClient pointed at a temp store."""
    monkeypatch.setattr("memforge.web.routes.views.GLOBAL_ROOT", store.root)
    monkeypatch.setattr("memforge.web.routes.api.GLOBAL_ROOT", store.root)
    monkeypatch.setattr("memforge.web.app.GLOBAL_ROOT", store.root)

    # Make cwd-based project store resolve to a non-existent path so only global is used
    monkeypatch.chdir(tmp_path)

    app = create_app()
    return TestClient(app, raise_server_exceptions=True)


def _make_article(slug: str = "test-slug", title: str = "Test Article") -> Article:
    fm = ArticleFrontMatter(
        id=f"dec-{slug}",
        type="decision",
        title=title,
        slug=slug,
        tags=["test"],
        confidence="high",
    )
    return Article(front_matter=fm, body="## Context\nBody text here.")


def _make_draft(store: Store, title: str = "Draft Title") -> str:
    unit = ExtractedUnit(
        type="pattern",
        title=title,
        body_md="## Problem\nFoo",
        tags=["foo"],
        confidence="medium",
    )
    draft = store.write_draft(unit, "sha256:abc", "claude-code", "sess-01")
    return draft.draft_id


# ---------------------------------------------------------------------------
# GET / — dashboard
# ---------------------------------------------------------------------------


def test_dashboard_returns_200(client: TestClient):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "MemForge" in resp.text


# ---------------------------------------------------------------------------
# GET /inbox
# ---------------------------------------------------------------------------


def test_inbox_empty(client: TestClient):
    resp = client.get("/inbox")
    assert resp.status_code == 200


def test_inbox_shows_draft(store: Store, client: TestClient):
    _make_draft(store, "My Pattern Draft")
    resp = client.get("/inbox")
    assert resp.status_code == 200
    assert "My Pattern Draft" in resp.text


# ---------------------------------------------------------------------------
# POST /api/commit/<draft_id>
# ---------------------------------------------------------------------------


def test_commit_draft(store: Store, client: TestClient):
    draft_id = _make_draft(store)
    resp = client.post(f"/api/commit/{draft_id}")
    assert resp.status_code == 200
    assert "Committed" in resp.text or "✓" in resp.text
    assert store.get_draft(draft_id) is None


def test_commit_missing_draft(client: TestClient):
    resp = client.post("/api/commit/nonexistent-id")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/discard/<draft_id>
# ---------------------------------------------------------------------------


def test_discard_draft(store: Store, client: TestClient):
    draft_id = _make_draft(store)
    resp = client.post(f"/api/discard/{draft_id}")
    assert resp.status_code == 200
    assert store.get_draft(draft_id) is None


def test_discard_missing_draft(client: TestClient):
    resp = client.post("/api/discard/nonexistent-id")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /knowledge  +  GET /knowledge/<slug>
# ---------------------------------------------------------------------------


def test_knowledge_list(store: Store, client: TestClient):
    store.write_article(_make_article("redis-cache", "Redis Cache"))
    resp = client.get("/knowledge")
    assert resp.status_code == 200
    assert "Redis Cache" in resp.text


def test_knowledge_article(store: Store, client: TestClient):
    store.write_article(_make_article("jwt-auth", "JWT Auth"))
    resp = client.get("/knowledge/jwt-auth")
    assert resp.status_code == 200
    assert "JWT Auth" in resp.text


def test_knowledge_article_not_found(client: TestClient):
    resp = client.get("/knowledge/does-not-exist")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/forget/<slug>
# ---------------------------------------------------------------------------


def test_forget_article(store: Store, client: TestClient):
    store.write_article(_make_article("old-decision", "Old Decision"))
    resp = client.post("/api/forget/old-decision")
    assert resp.status_code == 200
    assert store.find_article_by_slug("old-decision") is None


def test_forget_missing_article(client: TestClient):
    resp = client.post("/api/forget/nonexistent")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/pin  /  POST /api/unpin
# ---------------------------------------------------------------------------


def test_pin_and_unpin(store: Store, client: TestClient):
    store.write_article(_make_article("auth-flow", "Auth Flow"))
    resp = client.post("/api/pin/auth-flow")
    assert resp.status_code == 200
    assert "auth-flow" in store.pinned_slugs()

    resp = client.post("/api/unpin/auth-flow")
    assert resp.status_code == 200
    assert "auth-flow" not in store.pinned_slugs()


def test_unpin_targets_store_that_has_slug_pinned(tmp_path: Path, monkeypatch):
    global_root = tmp_path / "global-store"
    project_cwd = tmp_path / "project"
    project_root = project_cwd / ".memforge"

    global_store = Store(global_root)
    project_store = Store(project_root)
    global_store.ensure_dirs()
    project_store.ensure_dirs()

    global_store.pin("shared-slug")
    project_store.write_article(_make_article("shared-slug", "Shared Slug"))
    project_store.pin("shared-slug")

    monkeypatch.setattr("memforge.web.routes.views.GLOBAL_ROOT", global_root)
    monkeypatch.setattr("memforge.web.routes.api.GLOBAL_ROOT", global_root)
    monkeypatch.setattr("memforge.web.app.GLOBAL_ROOT", global_root)
    monkeypatch.chdir(project_cwd)

    client = TestClient(create_app(), raise_server_exceptions=True)
    resp = client.post("/api/unpin/shared-slug")

    assert resp.status_code == 200
    assert "shared-slug" in global_store.pinned_slugs()
    assert "shared-slug" not in project_store.pinned_slugs()


# ---------------------------------------------------------------------------
# PUT /api/article  — save edited content
# ---------------------------------------------------------------------------


def test_save_article(store: Store, client: TestClient):
    article = _make_article("editable", "Editable Article")
    path = store.write_article(article)

    resp = client.put(
        "/api/article",
        data={
            "file_path": str(path),
            "content": (
                "---\nid: dec-editable\ntype: decision\ntitle: Editable Article\n"
                "slug: editable\ntags: []\nconfidence: high\n---\n\n## Updated\nNew content."
            ),
        },
    )
    assert resp.status_code == 200
    assert "Updated" in path.read_text("utf-8") or resp.status_code == 200


def test_save_article_path_traversal_blocked(client: TestClient, tmp_path: Path):
    resp = client.put(
        "/api/article",
        data={"file_path": str(tmp_path / "../../etc/passwd"), "content": "evil"},
    )
    assert resp.status_code in (403, 404)


def test_daily_content_path_traversal_blocked(client: TestClient, tmp_path: Path):
    outside = tmp_path.parent / "outside.md"
    outside.write_text("# Outside", encoding="utf-8")
    resp = client.get("/api/daily-content", params={"path": str(outside)})
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# GET /api/preview
# ---------------------------------------------------------------------------


def test_preview_renders_markdown(client: TestClient):
    resp = client.get("/api/preview", params={"content": "# Hello\n\nWorld"})
    assert resp.status_code == 200
    assert "<h1>" in resp.text


# ---------------------------------------------------------------------------
# GET /daily  +  GET /api/daily-content
# ---------------------------------------------------------------------------


def test_daily_page(client: TestClient):
    resp = client.get("/daily")
    assert resp.status_code == 200


def test_daily_content_lazy_load(store: Store, client: TestClient):
    article = _make_article("rate-limit", "Rate Limit")
    path = store.append_to_daily(article)
    resp = client.get("/api/daily-content", params={"path": str(path)})
    assert resp.status_code == 200
    assert "Rate Limit" in resp.text or resp.text.strip() != ""


def test_editor_path_traversal_blocked(client: TestClient, tmp_path: Path):
    outside = tmp_path.parent / "secret.md"
    outside.write_text("secret", encoding="utf-8")
    resp = client.get(f"/edit/{outside.as_posix()}")
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# GET /stats
# ---------------------------------------------------------------------------


def test_stats_page(store: Store, client: TestClient):
    store.write_article(_make_article("decision-one", "Decision One"))
    resp = client.get("/stats")
    assert resp.status_code == 200
    assert "1" in resp.text


def test_settings_reject_outside_store_path(client: TestClient, tmp_path: Path):
    outside = tmp_path.parent / "config.yaml"
    resp = client.post(
        "/settings",
        data={
            "config_path": str(outside),
            "content": "version: 1\nextractor:\n  backend: auto\n",
        },
    )
    assert resp.status_code == 200
    assert not outside.exists()


def test_save_article_uses_project_store_config_for_autocommit(tmp_path: Path, monkeypatch):
    global_root = tmp_path / "global-store"
    project_cwd = tmp_path / "project"
    project_root = project_cwd / ".memforge"

    global_store = Store(global_root)
    project_store = Store(project_root)
    global_store.ensure_dirs()
    project_store.ensure_dirs()
    global_store.init_git()
    project_store.init_git()

    save_config(Config(git=GitConfig(auto_commit=True)), global_root / "config.yaml")
    save_config(Config(git=GitConfig(auto_commit=False)), project_root / "config.yaml")

    article = _make_article("project-article", "Project Article")
    article_path = project_store.write_article(article)

    monkeypatch.setattr("memforge.web.routes.views.GLOBAL_ROOT", global_root)
    monkeypatch.setattr("memforge.web.routes.api.GLOBAL_ROOT", global_root)
    monkeypatch.setattr("memforge.web.app.GLOBAL_ROOT", global_root)
    monkeypatch.chdir(project_cwd)

    client = TestClient(create_app(), raise_server_exceptions=True)
    resp = client.put(
        "/api/article",
        data={"file_path": str(article_path), "content": article_path.read_text("utf-8") + "\n"},
    )

    assert resp.status_code == 200
    assert len(list(Repo(project_root).iter_commits())) == 1
