from __future__ import annotations

import subprocess
from pathlib import Path

from memforge.core.compiler import Compiler, _call_cli, _fingerprint, _resolve_backend
from memforge.core.llm_log import read_log, record_call, summarise
from memforge.core.models import Article, ArticleFrontMatter
from memforge.core.storage import Store
from memforge.exporters.obsidian import export_to_obsidian


def test_record_call_writes_llm_log(tmp_path: Path):
    with record_call(tmp_path, "claude-sonnet-4-6", "v1", "extract") as meta:
        meta["input_tokens"] = 100
        meta["output_tokens"] = 50

    entries = read_log(tmp_path)

    assert len(entries) == 1
    assert entries[0]["operation"] == "extract"
    assert entries[0]["cost_usd"] > 0


def test_summarise_aggregates_recent_entries():
    entries = [
        {
            "timestamp": "2026-04-19T00:00:00+00:00",
            "operation": "extract",
            "input_tokens": 10,
            "output_tokens": 5,
            "cost_usd": 0.01,
        },
        {
            "timestamp": "2026-04-19T00:00:00+00:00",
            "operation": "compile",
            "input_tokens": 20,
            "output_tokens": 10,
            "cost_usd": 0.02,
        },
    ]

    summary = summarise(entries, since_days=3650)

    assert summary["calls"] == 2
    assert summary["tokens"] == 45
    assert summary["by_operation"] == {"extract": 1, "compile": 1}


def test_call_cli_logs_success(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout="merged body", stderr=""),
    )

    result = _call_cli("system", "prompt", tmp_path)

    assert result == "merged body"
    entries = read_log(tmp_path)
    assert len(entries) == 1
    assert entries[0]["operation"] == "compile"


def test_call_cli_returns_empty_on_timeout(monkeypatch, tmp_path: Path):
    def raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="claude", timeout=120)

    monkeypatch.setattr("subprocess.run", raise_timeout)

    assert _call_cli("system", "prompt", tmp_path) == ""
    assert read_log(tmp_path) == []


def test_compiler_parse_daily_and_group(tmp_path: Path):
    store = Store(tmp_path)
    store.ensure_dirs()
    article = Article(
        front_matter=ArticleFrontMatter(
            id="pat-retry",
            type="pattern",
            title="Retry Pattern",
            slug="retry-pattern",
            tags=["retry"],
            confidence="high",
        ),
        body="body",
    )
    store.append_to_daily(article)

    compiler = Compiler.__new__(Compiler)
    entries = compiler._parse_daily_file(next(store.daily.glob("*.md")))
    groups = compiler._group_by_slug(entries)

    assert len(entries) == 1
    assert "retry-pattern" in groups
    assert groups["retry-pattern"][0]["title"] == "Retry Pattern"


def test_resolve_backend_prefers_env_and_cli(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr("shutil.which", lambda name: None)
    assert _resolve_backend("auto") == "api"

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr("shutil.which", lambda name: "claude")
    assert _resolve_backend("auto") == "cli"


def test_fingerprint_is_stable():
    assert _fingerprint("abc") == _fingerprint("abc")
    assert _fingerprint("abc") != _fingerprint("def")


def test_export_to_obsidian_writes_article_and_index(tmp_path: Path):
    store = Store(tmp_path / "store")
    store.ensure_dirs()
    store.write_article(
        Article(
            front_matter=ArticleFrontMatter(
                id="pat-circuit-breaker",
                type="pattern",
                title="Circuit Breaker",
                slug="circuit-breaker",
                tags=["resilience"],
                confidence="high",
            ),
            body="body",
        )
    )
    store.pin("circuit-breaker")

    out_dir = tmp_path / "vault"
    exported = export_to_obsidian(store, out_dir)

    assert exported == 1
    exported_file = out_dir / "Patterns" / "Circuit Breaker.md"
    assert exported_file.exists()
    assert "confidence: high" in exported_file.read_text("utf-8")
    assert (out_dir / "index.md").exists()
