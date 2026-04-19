"""Unit tests for indexer."""

from datetime import UTC, datetime

from memforge.core.indexer import build_index
from memforge.core.models import Article, ArticleFrontMatter


def _make_article(slug: str, record_type="decision", tags=None) -> Article:
    fm = ArticleFrontMatter(
        id=f"test-{slug}",
        type=record_type,
        title=f"Title for {slug}",
        slug=slug,
        tags=tags or ["test"],
        confidence="high",
        updated=datetime.now(UTC),
    )
    return Article(front_matter=fm, body="body")


def test_build_index_empty():
    result = build_index([], pinned_slugs=[])
    assert "# MemForge Index" in result
    assert "Articles: 0" in result


def test_build_index_with_articles():
    articles = [
        _make_article("auth-flow", "pattern", ["auth"]),
        _make_article("chose-jwt", "decision", ["auth", "architecture"]),
    ]
    result = build_index(articles, pinned_slugs=["auth-flow"])
    assert "auth-flow" in result
    assert "chose-jwt" in result
    assert "## pinned" in result
    assert "## by_type" in result
    assert "## by_topic" in result


def test_build_index_pinned_section():
    articles = [_make_article("x")]
    result = build_index(articles, pinned_slugs=["x"])
    assert "- x" in result


def test_build_index_token_limit():
    # Generate many articles to test trimming
    articles = [_make_article(f"slug-{i}", tags=[f"tag{i}"]) for i in range(100)]
    result = build_index(articles, pinned_slugs=[], max_tokens=100)
    assert len(result) < 100 * 20  # should be trimmed
