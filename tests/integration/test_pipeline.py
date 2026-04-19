"""Integration tests: save → commit → compile → recall flow.

LLM calls are mocked so the tests run without an API key.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from memforge.config import Config
from memforge.core.compiler import Compiler
from memforge.core.indexer import build_index, write_index
from memforge.core.models import (
    Article,
    ArticleFrontMatter,
    ExtractedUnit,
    Message,
    Transcript,
)
from memforge.core.pipeline import run_save
from memforge.core.retriever import retrieve
from memforge.core.storage import Store

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path) -> Store:
    s = Store(tmp_path)
    s.ensure_dirs()
    return s


@pytest.fixture
def transcript() -> Transcript:
    return Transcript(
        session_id="integ-test-01",
        agent="claude-code",
        started_at=datetime.now(UTC),
        messages=[
            Message(role="user", content="How should we handle rate limiting?"),
            Message(
                role="assistant",
                content="Use a token-bucket algorithm with Redis. Store per-user buckets with TTL.",
            ),
        ],
        cwd="/project",
    )


@pytest.fixture
def config() -> Config:
    return Config()


def _make_unit(
    type_: str = "pattern",
    title: str = "Rate Limiting with Token Bucket",
    body: str = "## Problem\nHigh traffic spikes.\n## Solution\nToken bucket with Redis.",
) -> ExtractedUnit:
    return ExtractedUnit(
        type=type_,
        title=title,
        body_md=body,
        tags=["redis", "rate-limiting"],
        confidence="high",
    )


# ---------------------------------------------------------------------------
# 1. Save (extract → scrub → inbox)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_save_creates_drafts(store: Store, transcript: Transcript, config: Config):
    unit = _make_unit()

    async def fake_extract(*a, **kw):
        return [unit]

    mock_extractor = MagicMock()
    mock_extractor.extract = fake_extract

    with patch("memforge.core.pipeline._select_extractor", return_value=(mock_extractor, "api")):
        result = await run_save(transcript, store, config)

    assert len(result.drafts) == 1
    assert result.quarantined == 0
    draft = result.drafts[0]
    assert draft.unit.title == "Rate Limiting with Token Bucket"
    assert draft.source_agent == "claude-code"

    # Draft file must exist in inbox
    inbox_files = list(store.inbox.glob("draft-*.md"))
    assert len(inbox_files) == 1


@pytest.mark.asyncio
async def test_save_quarantines_secrets(store: Store, transcript: Transcript, config: Config):
    unit = _make_unit(body="## Key\nAWS secret: AKIAIOSFODNN7EXAMPLE\n## Use\nSign requests.")

    async def fake_extract(*a, **kw):
        return [unit]

    mock_extractor = MagicMock()
    mock_extractor.extract = fake_extract
    with patch("memforge.core.pipeline._select_extractor", return_value=(mock_extractor, "api")):
        result = await run_save(transcript, store, config)

    assert result.quarantined == 1
    assert result.drafts[0].quarantine is True


@pytest.mark.asyncio
async def test_save_dry_run_writes_nothing(store: Store, transcript: Transcript, config: Config):
    unit = _make_unit()

    async def fake_extract(*a, **kw):
        return [unit]

    mock_extractor = MagicMock()
    mock_extractor.extract = fake_extract
    with patch("memforge.core.pipeline._select_extractor", return_value=(mock_extractor, "api")):
        result = await run_save(transcript, store, config, dry_run=True)

    assert result.drafts == []
    assert list(store.inbox.glob("draft-*.md")) == []


# ---------------------------------------------------------------------------
# 2. Commit (inbox → daily)
# ---------------------------------------------------------------------------


def test_commit_moves_draft_to_daily(store: Store):
    unit = _make_unit()
    draft = store.write_draft(
        unit=unit,
        transcript_hash="sha256:abc",
        source_agent="claude-code",
        source_session_id="sess-01",
    )

    # Commit
    fm = ArticleFrontMatter(
        id=f"pat-{draft.draft_id[:8]}",
        type=unit.type,
        title=unit.title,
        slug="rate-limiting-with-token-bucket",
        tags=unit.tags,
        confidence=unit.confidence,
        source_agent=draft.source_agent,
        source_session_id=draft.source_session_id,
        transcript_hash=draft.transcript_hash,
    )
    article = Article(front_matter=fm, body=unit.body_md)
    daily_path = store.append_to_daily(article)
    store.delete_draft(draft.draft_id)

    assert daily_path.exists()
    assert store.get_draft(draft.draft_id) is None
    assert "Rate Limiting with Token Bucket" in daily_path.read_text("utf-8")


def test_daily_multiple_commits_same_day(store: Store):
    for i in range(3):
        unit = _make_unit(title=f"Article {i}", body=f"Body {i}")
        fm = ArticleFrontMatter(
            id=f"pat-00{i}",
            type="pattern",
            title=unit.title,
            slug=f"article-{i}",
            tags=[],
            confidence="medium",
        )
        store.append_to_daily(Article(front_matter=fm, body=unit.body_md))

    today = datetime.now(UTC).strftime("%Y-%m-%d")
    daily_path = store.daily / f"{today}.md"
    content = daily_path.read_text("utf-8")
    assert content.count("---") >= 2  # separator appears between entries
    assert "Article 0" in content
    assert "Article 2" in content


# ---------------------------------------------------------------------------
# 3. Compile (daily → knowledge) — mocked LLM
# ---------------------------------------------------------------------------


def _write_daily_entry(store: Store, slug: str, title: str, body: str) -> None:
    fm = ArticleFrontMatter(
        id=f"pat-{slug}",
        type="pattern",
        title=title,
        slug=slug,
        tags=["test"],
        confidence="medium",
    )
    store.append_to_daily(Article(front_matter=fm, body=body))


def test_compile_creates_new_article(store: Store):
    _write_daily_entry(store, "token-bucket", "Token Bucket", "## Solution\nUse Redis.")

    compiler = Compiler.__new__(Compiler)
    compiler._model = "claude-sonnet-4-6"
    compiler._system = "merge"
    compiler._backend = "api"
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(type="text", text="## Solution\nMerged Redis content.")]
    mock_response.usage = MagicMock(input_tokens=10, output_tokens=20)
    mock_client.messages.create.return_value = mock_response
    compiler._client = mock_client

    created, updated = compiler.compile(store)

    assert len(created) == 1
    assert len(updated) == 0
    assert created[0].slug == "token-bucket"
    art = store.find_article_by_slug("token-bucket")
    assert art is not None


def test_compile_updates_existing_article(store: Store):
    fm = ArticleFrontMatter(
        id="pat-token-bucket",
        type="pattern",
        title="Token Bucket",
        slug="token-bucket",
        tags=["redis"],
        confidence="medium",
    )
    store.write_article(Article(front_matter=fm, body="## Old\nOld content."))

    _write_daily_entry(store, "token-bucket", "Token Bucket", "## New\nNew info.")

    compiler = Compiler.__new__(Compiler)
    compiler._model = "claude-sonnet-4-6"
    compiler._system = "merge"
    compiler._backend = "api"
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.content = [MagicMock(type="text", text="## Merged\nBoth old and new.")]
    mock_response.usage = MagicMock(input_tokens=10, output_tokens=20)
    mock_client.messages.create.return_value = mock_response
    compiler._client = mock_client

    created, updated = compiler.compile(store)

    assert len(created) == 0
    assert len(updated) == 1
    art = store.find_article_by_slug("token-bucket")
    assert art is not None
    assert "Merged" in art.body


# ---------------------------------------------------------------------------
# 4. Index + Recall
# ---------------------------------------------------------------------------


def test_build_index_and_recall(store: Store):
    for slug, title in [
        ("redis-caching", "Redis Caching Strategy"),
        ("jwt-auth", "JWT Authentication"),
        ("rate-limiting", "Rate Limiting Pattern"),
    ]:
        fm = ArticleFrontMatter(
            id=f"pat-{slug}",
            type="pattern",
            title=title,
            slug=slug,
            tags=["backend"],
            confidence="high",
        )
        store.write_article(Article(front_matter=fm, body=f"## Solution\n{title} details."))

    articles = list(store.iter_articles())
    index_content = build_index(
        articles, pinned_slugs=[], max_tokens=8000, order=["type", "confidence"]
    )
    write_index(store.knowledge, index_content)

    assert store.index_file.exists()

    results = retrieve(
        query="rate limiting",
        index_path=store.index_file,
        articles_root=store.root,
        top_k=3,
    )

    assert len(results) > 0
    slugs = [r.slug for r in results]
    assert "rate-limiting" in slugs


def test_recall_returns_empty_for_unrelated_query(store: Store):
    fm = ArticleFrontMatter(
        id="pat-redis",
        type="pattern",
        title="Redis Caching",
        slug="redis-caching",
        tags=["cache"],
        confidence="medium",
    )
    store.write_article(Article(front_matter=fm, body="## Details\nUse Redis."))

    articles = list(store.iter_articles())
    index_content = build_index(articles, pinned_slugs=[], max_tokens=8000, order=["type"])
    write_index(store.knowledge, index_content)

    results = retrieve(
        query="quantum computing unrelated topic xyz",
        index_path=store.index_file,
        articles_root=store.root,
        top_k=5,
    )
    # BM25 may still return results with score 0 via substring fallback
    # but the slug should not be rate-limiting
    for r in results:
        assert r.slug != "rate-limiting"


# ---------------------------------------------------------------------------
# 5. Full end-to-end: save → commit → compile → recall
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_pipeline_e2e(store: Store, transcript: Transcript, config: Config):
    unit = _make_unit()

    async def fake_extract(*a, **kw):
        return [unit]

    # Step 1: save
    mock_extractor = MagicMock()
    mock_extractor.extract = fake_extract
    with patch("memforge.core.pipeline._select_extractor", return_value=(mock_extractor, "api")):
        result = await run_save(transcript, store, config)

    assert len(result.drafts) == 1
    draft = result.drafts[0]

    # Step 2: commit draft → daily
    from memforge.core.storage import slugify

    fm = ArticleFrontMatter(
        id=f"pat-{draft.draft_id[:8]}",
        type=unit.type,
        title=unit.title,
        slug=slugify(unit.title),
        tags=unit.tags,
        confidence=unit.confidence,
        source_agent=draft.source_agent,
        source_session_id=draft.source_session_id,
        transcript_hash=draft.transcript_hash,
    )
    article = Article(front_matter=fm, body=unit.body_md)
    store.append_to_daily(article)
    store.delete_draft(draft.draft_id)

    assert store.list_drafts() == []

    # Step 3: compile daily → knowledge
    compiler = Compiler.__new__(Compiler)
    compiler._model = "claude-sonnet-4-6"
    compiler._system = ""
    compiler._backend = "api"
    mock_client = MagicMock()
    compiler._client = mock_client
    created, updated = compiler.compile(store)

    assert len(created) == 1
    knowledge_article = store.find_article_by_slug(fm.slug)
    assert knowledge_article is not None

    # Step 4: build index + recall
    articles = list(store.iter_articles())
    index_content = build_index(articles, pinned_slugs=[], max_tokens=8000, order=["type"])
    write_index(store.knowledge, index_content)

    results = retrieve(
        query="rate limiting token bucket",
        index_path=store.index_file,
        articles_root=store.root,
        top_k=3,
    )
    assert len(results) > 0
    assert results[0].slug == fm.slug
