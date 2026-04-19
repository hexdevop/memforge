from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from typer.testing import CliRunner

from memforge.cli.main import app
from memforge.core.indexer import build_index, write_index
from memforge.core.models import (
    Article,
    ArticleFrontMatter,
    Draft,
    ExtractedUnit,
    Message,
    Transcript,
)
from memforge.core.pipeline import SaveResult
from memforge.core.storage import Store

runner = CliRunner()


def test_init_creates_project_store(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["init", "--no-git"])

    assert result.exit_code == 0
    assert (tmp_path / ".memforge" / "memory" / "inbox").exists()
    assert (tmp_path / ".memforge" / "config.yaml").exists()


def test_save_dry_run_reports_created_drafts(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    init_result = runner.invoke(app, ["init", "--no-git"])
    assert init_result.exit_code == 0

    transcript = Transcript(
        session_id="cli-save-01",
        agent="stdin",
        started_at=datetime.now(UTC),
        messages=[
            Message(role="user", content="Summarize the retry strategy."),
            Message(role="assistant", content="Use exponential backoff with jitter."),
        ],
    )

    draft = Draft(
        unit=ExtractedUnit(
            type="pattern",
            title="Retry with Exponential Backoff",
            body_md="## Pattern\nUse backoff with jitter.",
            tags=["retry"],
            confidence="high",
        ),
        transcript_hash=transcript.hash,
        source_agent=transcript.agent,
        source_session_id=transcript.session_id,
    )

    async def fake_run_save(*args, **kwargs):
        return SaveResult(
            drafts=[draft],
            transcript_hash=transcript.hash,
            quarantined=0,
            backend_used="api",
        )

    monkeypatch.setattr("memforge.cli.main._load_transcript", lambda *a, **k: transcript)
    monkeypatch.setattr("memforge.cli.main.run_save", fake_run_save)

    result = runner.invoke(app, ["save", "--source", "stdin", "--dry-run"])

    assert result.exit_code == 0
    assert "would create 1 draft" in result.stdout
    assert list((tmp_path / ".memforge" / "memory" / "inbox").glob("draft-*.md")) == []


def test_commit_all_promotes_drafts_to_daily(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    init_result = runner.invoke(app, ["init", "--no-git"])
    assert init_result.exit_code == 0

    store = Store(tmp_path / ".memforge")
    draft = store.write_draft(
        unit=ExtractedUnit(
            type="decision",
            title="Use Redis for Rate Limiting",
            body_md="## Decision\nUse Redis buckets.",
            tags=["redis"],
            confidence="high",
        ),
        transcript_hash="sha256:test",
        source_agent="claude-code",
        source_session_id="sess-01",
    )

    result = runner.invoke(app, ["commit", "--all"])

    assert result.exit_code == 0
    assert store.get_draft(draft.draft_id) is None
    daily_files = list(store.daily.glob("*.md"))
    assert len(daily_files) == 1
    assert "Use Redis for Rate Limiting" in daily_files[0].read_text("utf-8")


def test_recall_prints_ranked_results(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    init_result = runner.invoke(app, ["init", "--no-git"])
    assert init_result.exit_code == 0

    store = Store(tmp_path / ".memforge")
    article = Article(
        front_matter=ArticleFrontMatter(
            id="pat-rate-limiting",
            type="pattern",
            title="Rate Limiting Pattern",
            slug="rate-limiting-pattern",
            tags=["redis", "traffic"],
            confidence="high",
        ),
        body="## Solution\nUse token bucket in Redis.",
    )
    store.write_article(article)
    index_content = build_index(
        list(store.iter_articles()), pinned_slugs=[], max_tokens=4000, order=["type"]
    )
    write_index(store.knowledge, index_content)

    result = runner.invoke(app, ["recall", "rate limiting", "--top", "1"])

    assert result.exit_code == 0
    assert "Rate Limiting Pattern" in result.stdout
    assert "rate-limiting-pattern" in result.stdout


def test_review_opens_all_draft_paths(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init", "--no-git"])
    store = Store(tmp_path / ".memforge")
    draft = store.write_draft(
        unit=ExtractedUnit(
            type="pattern",
            title="Review Me",
            body_md="body",
            confidence="medium",
        ),
        transcript_hash="sha256:test",
        source_agent="stdin",
        source_session_id="sess-01",
    )

    opened: list[list[str]] = []
    monkeypatch.setenv("EDITOR", "fake-editor")
    monkeypatch.setattr("subprocess.run", lambda args: opened.append(args))

    result = runner.invoke(app, ["review"])

    assert result.exit_code == 0
    assert opened == [["fake-editor", str(store.inbox / f"draft-{draft.draft_id}.md")]]


def test_lint_reports_success_for_valid_store(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init", "--no-git"])
    store = Store(tmp_path / ".memforge")
    store.write_article(
        Article(
            front_matter=ArticleFrontMatter(
                id="dec-valid",
                type="decision",
                title="Valid Article",
                slug="valid-article",
                confidence="high",
            ),
            body="body",
        )
    )

    result = runner.invoke(app, ["lint"])

    assert result.exit_code == 0
    assert "No issues found" in result.stdout


def test_forget_and_restore_roundtrip(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init", "--no-git"])
    store = Store(tmp_path / ".memforge")
    store.write_article(
        Article(
            front_matter=ArticleFrontMatter(
                id="dec-cache",
                type="decision",
                title="Cache Decision",
                slug="cache-decision",
                confidence="high",
            ),
            body="body",
        )
    )

    forget_result = runner.invoke(app, ["forget", "cache-decision"])
    restore_result = runner.invoke(app, ["restore", "cache-decision"])

    assert forget_result.exit_code == 0
    assert restore_result.exit_code == 0
    assert store.find_article_by_slug("cache-decision") is not None


def test_forget_and_restore_missing_return_nonzero(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init", "--no-git"])

    forget_result = runner.invoke(app, ["forget", "missing-slug"])
    restore_result = runner.invoke(app, ["restore", "missing-slug"])

    assert forget_result.exit_code == 1
    assert restore_result.exit_code == 1


def test_doctor_fails_without_backend(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("shutil.which", lambda name: None)

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 1
    assert "No LLM backend available" in result.stdout


def test_export_obsidian_writes_files(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init", "--no-git"])
    store = Store(tmp_path / ".memforge")
    store.write_article(
        Article(
            front_matter=ArticleFrontMatter(
                id="pat-exported",
                type="pattern",
                title="Exported Pattern",
                slug="exported-pattern",
                tags=["docs"],
                confidence="medium",
            ),
            body="body",
        )
    )

    out_dir = tmp_path / "vault"
    result = runner.invoke(app, ["export", "--to", "obsidian", "--out", str(out_dir)])

    assert result.exit_code == 0
    assert (out_dir / "Patterns" / "Exported Pattern.md").exists()
    assert (out_dir / "index.md").exists()


def test_export_rejects_unsupported_format(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runner.invoke(app, ["init", "--no-git"])

    result = runner.invoke(app, ["export", "--to", "markdown"])

    assert result.exit_code == 1
    assert "Unsupported format" in result.stdout
