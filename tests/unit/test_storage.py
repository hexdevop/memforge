"""Unit tests for storage layer."""

import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
from memforge.core.models import Article, ArticleFrontMatter, ExtractedUnit
from memforge.core.storage import Store, slugify


@pytest.fixture
def tmp_store(tmp_path: Path) -> Store:
    store = Store(tmp_path)
    store.ensure_dirs()
    return store


def _make_article(slug: str = "test-article") -> Article:
    fm = ArticleFrontMatter(
        id=f"dec-{slug}",
        type="decision",
        title="Test Article",
        slug=slug,
        tags=["test"],
        confidence="high",
    )
    return Article(front_matter=fm, body="## Context\nTest body.")


def test_write_and_read_draft(tmp_store: Store):
    unit = ExtractedUnit(
        type="pattern",
        title="My Pattern",
        body_md="## Problem\nFoo\n## Solution\nBar",
        tags=["foo"],
        confidence="medium",
    )
    draft = tmp_store.write_draft(
        unit=unit,
        transcript_hash="sha256:abc",
        source_agent="claude-code",
        source_session_id="sess-123",
    )
    assert draft.draft_id

    loaded = tmp_store.get_draft(draft.draft_id)
    assert loaded is not None
    assert loaded.unit.title == "My Pattern"
    assert loaded.unit.type == "pattern"


def test_list_drafts_empty(tmp_store: Store):
    assert tmp_store.list_drafts() == []


def test_delete_draft(tmp_store: Store):
    unit = ExtractedUnit(type="gotcha", title="G", body_md="x", confidence="low")
    draft = tmp_store.write_draft(unit, "h", "agent", "sess")
    assert tmp_store.delete_draft(draft.draft_id)
    assert tmp_store.get_draft(draft.draft_id) is None


def test_write_and_find_article(tmp_store: Store):
    article = _make_article("chose-jwt")
    tmp_store.write_article(article)
    found = tmp_store.find_article_by_slug("chose-jwt")
    assert found is not None
    assert found.front_matter.title == "Test Article"


def test_pin_unpin(tmp_store: Store):
    tmp_store.pin("auth-flow")
    tmp_store.pin("rate-limiting")
    assert "auth-flow" in tmp_store.pinned_slugs()
    tmp_store.unpin("auth-flow")
    assert "auth-flow" not in tmp_store.pinned_slugs()
    assert "rate-limiting" in tmp_store.pinned_slugs()


def test_append_to_daily(tmp_store: Store):
    article = _make_article()
    path = tmp_store.append_to_daily(article)
    assert path.exists()
    content = path.read_text("utf-8")
    assert "Test Article" in content


def test_slugify():
    assert slugify("Chose JWT over Session") == "chose-jwt-over-session"
    assert slugify("  hello  world  ") == "hello-world"
    assert slugify("") != ""
