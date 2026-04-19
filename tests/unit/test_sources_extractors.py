from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import httpx
import pytest
from anthropic import Anthropic

from memforge.core.llm_log import read_log
from memforge.core.models import Message, Transcript
from memforge.extractors.claude_cli import ClaudeCliExtractor
from memforge.extractors.claude_sdk import ClaudeExtractor
from memforge.sources.codex import CodexSource
from memforge.sources.cursor import CursorSource
from memforge.sources.project import ProjectScanner
from memforge.sources.stdin import FileSource, StdinSource

# ---------------------------------------------------------------------------
# StdinSource / FileSource
# ---------------------------------------------------------------------------


def test_stdin_source_wraps_inline_text():
    source = StdinSource(text="raw transcript body")

    transcript = source.latest_session()

    assert transcript is not None
    assert transcript.agent == "stdin"
    assert transcript.messages[1].content == "raw transcript body"


def test_file_source_reads_utf8_file(tmp_path: Path):
    path = tmp_path / "session.md"
    path.write_text("hello from file", encoding="utf-8")

    transcript = FileSource(path).latest_session()

    assert transcript is not None
    assert transcript.agent == "file"
    assert transcript.messages[1].content == "hello from file"


# ---------------------------------------------------------------------------
# CodexSource
# ---------------------------------------------------------------------------


def test_codex_source_parses_latest_conversation(tmp_path: Path):
    export_path = tmp_path / "conversations.json"
    export_path.write_text(
        """
        [
          {
            "id": "conv-1",
            "title": "Newest",
            "create_time": 1700000100,
            "mapping": {
              "root": {"id": "root", "parent": null, "children": ["u1"], "message": null},
              "u1": {
                "id": "u1",
                "parent": "root",
                "children": ["a1"],
                "message": {
                  "author": {"role": "user"},
                  "content": {"parts": ["How do retries work?"]}
                }
              },
              "a1": {
                "id": "a1",
                "parent": "u1",
                "children": [],
                "message": {"author": {"role": "assistant"}, "content": {"parts": ["Use jitter."]}}
              }
            }
          }
        ]
        """,
        encoding="utf-8",
    )

    transcript = CodexSource(export_path).latest_session()

    assert transcript is not None
    assert transcript.agent == "chatgpt"
    assert [m.role for m in transcript.messages] == ["user", "assistant"]
    assert transcript.messages[1].content == "Use jitter."


# ---------------------------------------------------------------------------
# CursorSource
# ---------------------------------------------------------------------------


def test_cursor_source_parses_workspace_db(tmp_path: Path):
    storage_root = tmp_path / "workspaceStorage"
    db_dir = storage_root / "hash-1"
    db_dir.mkdir(parents=True)
    db_path = db_dir / "state.vscdb"

    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE ItemTable (key TEXT, value TEXT)")
    conn.execute(
        "INSERT INTO ItemTable (key, value) VALUES (?, ?)",
        (
            "workbench.panel.aichat.view.aichat.chatdata.conversationHistory",
            '[{"role":"user","content":"Need auth help"},{"role":"assistant","content":"Use JWT"}]',
        ),
    )
    conn.commit()
    conn.close()

    transcript = CursorSource(storage_root=storage_root, cwd=tmp_path).latest_session()

    assert transcript is not None
    assert transcript.agent == "cursor"
    assert [m.content for m in transcript.messages] == ["Need auth help", "Use JWT"]


# ---------------------------------------------------------------------------
# ProjectScanner
# ---------------------------------------------------------------------------


def test_project_scanner_detect_returns_true_for_dir(tmp_path: Path):
    scanner = ProjectScanner(tmp_path)
    assert scanner.detect() is True


def test_project_scanner_detect_returns_false_for_missing_dir(tmp_path: Path):
    scanner = ProjectScanner(tmp_path / "nonexistent")
    assert scanner.detect() is False


def test_project_scanner_reads_readme(tmp_path: Path):
    (tmp_path / "README.md").write_text("# My Project\nDoes cool things.", encoding="utf-8")

    transcript = ProjectScanner(tmp_path).latest_session()

    contents = " ".join(m.content for m in transcript.messages)
    assert "My Project" in contents
    assert "Does cool things" in contents


def test_project_scanner_reads_pyproject(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "mylib"', encoding="utf-8")

    transcript = ProjectScanner(tmp_path).latest_session()

    contents = " ".join(m.content for m in transcript.messages)
    assert "mylib" in contents


def test_project_scanner_reads_claude_md(tmp_path: Path):
    (tmp_path / "CLAUDE.md").write_text("Always use async.", encoding="utf-8")

    transcript = ProjectScanner(tmp_path).latest_session()

    contents = " ".join(m.content for m in transcript.messages)
    assert "Always use async" in contents


def test_project_scanner_includes_tree(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("# main", encoding="utf-8")

    transcript = ProjectScanner(tmp_path).latest_session()

    first_msg = transcript.messages[0].content
    assert "src" in first_msg


def test_project_scanner_agent_name(tmp_path: Path):
    transcript = ProjectScanner(tmp_path).latest_session()
    assert transcript.agent == "project-scan"


def test_project_scanner_caps_large_files(tmp_path: Path):
    (tmp_path / "README.md").write_text("x" * 10_000, encoding="utf-8")

    transcript = ProjectScanner(tmp_path).latest_session()

    for msg in transcript.messages:
        assert len(msg.content) <= 5_000  # _FILE_CAP + header overhead


# ---------------------------------------------------------------------------
# ClaudeCliExtractor — parser
# ---------------------------------------------------------------------------


def test_claude_cli_extractor_parses_envelope_string_result():
    """Main fix: result field is a JSON string, not a parsed object."""
    extractor = ClaudeCliExtractor(max_units=5)

    raw = json.dumps(
        {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "result": json.dumps(
                {
                    "units": [
                        {
                            "type": "pattern",
                            "title": "Retry with jitter",
                            "body_md": "## Problem\nRetries storm.\n## Solution\nAdd jitter.",
                            "tags": ["resilience"],
                            "confidence": "high",
                        }
                    ]
                }
            ),
        }
    )

    units = extractor._parse_response(raw)

    assert len(units) == 1
    assert units[0].title == "Retry with jitter"
    assert units[0].type == "pattern"


def test_claude_cli_extractor_skips_is_error_envelope():
    extractor = ClaudeCliExtractor(max_units=5)

    raw = json.dumps(
        {
            "type": "result",
            "is_error": True,
            "result": "Not logged in · Please run /login",
        }
    )

    units = extractor._parse_response(raw)

    assert units == []


def test_claude_cli_extractor_parses_envelope_with_object_result():
    """Handles the case where result is already a parsed object (future-proofing)."""
    extractor = ClaudeCliExtractor(max_units=5)

    raw = json.dumps(
        {
            "type": "result",
            "is_error": False,
            "result": {
                "units": [
                    {
                        "type": "decision",
                        "title": "Use Postgres",
                        "body_md": "body",
                        "tags": ["db"],
                        "confidence": "medium",
                    }
                ]
            },
        }
    )

    units = extractor._parse_response(raw)

    assert len(units) == 1
    assert units[0].title == "Use Postgres"


def test_claude_cli_extractor_parses_bare_units_object():
    extractor = ClaudeCliExtractor(max_units=2)

    units = extractor._parse_response(
        '{"units":[{"type":"pattern","title":"Retry","body_md":"Body","tags":["ops"],"confidence":"high"}]}'
    )

    assert len(units) == 1
    assert units[0].title == "Retry"


def test_claude_cli_extractor_parses_bare_json_array():
    extractor = ClaudeCliExtractor(max_units=2)

    units = extractor._parse_response(
        '[{"type":"pattern","title":"Retry","body_md":"Body","tags":["ops"],"confidence":"high"}]'
    )

    assert len(units) == 1
    assert units[0].title == "Retry"


def test_claude_cli_extractor_parses_embedded_json_array():
    extractor = ClaudeCliExtractor(max_units=2)

    units = extractor._parse_response(
        "Prelude "
        '[{"type":"pattern","title":"Retry","body_md":"Body","tags":["ops"],"confidence":"high"}]'
        " epilogue"
    )

    assert len(units) == 1
    assert units[0].title == "Retry"


def test_claude_cli_extractor_returns_empty_on_invalid_json():
    extractor = ClaudeCliExtractor(max_units=5)

    units = extractor._parse_response("not json at all")

    assert units == []


def test_claude_cli_extractor_respects_max_units():
    extractor = ClaudeCliExtractor(max_units=2)
    items = [
        {"type": "pattern", "title": f"Item {i}", "body_md": "b", "tags": [], "confidence": "high"}
        for i in range(5)
    ]
    raw = json.dumps({"units": items})

    units = extractor._parse_response(raw)

    assert len(units) == 2


def test_claude_cli_extractor_coerces_unknown_type():
    extractor = ClaudeCliExtractor(max_units=5)

    units = extractor._parse_response(
        '[{"type":"unknown_type","title":"T","body_md":"b","tags":[],"confidence":"low"}]'
    )

    assert units[0].type == "pattern"


# ---------------------------------------------------------------------------
# ClaudeCliExtractor — compress_transcript
# ---------------------------------------------------------------------------


def test_compress_transcript_truncates_long_messages():
    transcript = Transcript(
        messages=[
            Message(role="user", content="short message"),
            Message(role="assistant", content="x" * 5_000),
        ]
    )

    compressed = ClaudeCliExtractor._compress_transcript(transcript)

    assert "short message" in compressed
    assert "…[truncated]" in compressed
    # The long block must be truncated
    assert len(compressed) < 3_000


def test_compress_transcript_keeps_short_messages_intact():
    transcript = Transcript(
        messages=[
            Message(role="user", content="What is the retry strategy?"),
            Message(role="assistant", content="Use exponential backoff with jitter."),
        ]
    )

    compressed = ClaudeCliExtractor._compress_transcript(transcript)

    assert "What is the retry strategy?" in compressed
    assert "exponential backoff" in compressed


def test_compress_transcript_skips_empty_messages():
    transcript = Transcript(
        messages=[
            Message(role="user", content="   "),
            Message(role="assistant", content="Answer"),
        ]
    )

    compressed = ClaudeCliExtractor._compress_transcript(transcript)

    assert "[USER]" not in compressed
    assert "Answer" in compressed


# ---------------------------------------------------------------------------
# ClaudeCliExtractor — async extract (mocked subprocess)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claude_cli_extract_unwraps_envelope_string_and_logs(monkeypatch, tmp_path: Path):
    extractor = ClaudeCliExtractor(max_units=2, timeout=5)
    transcript = Transcript(messages=[Message(role="user", content="hello")])

    envelope = json.dumps(
        {
            "type": "result",
            "is_error": False,
            "result": json.dumps(
                {
                    "units": [
                        {
                            "type": "pattern",
                            "title": "Retry",
                            "body_md": "Body",
                            "tags": [],
                            "confidence": "high",
                        }
                    ]
                }
            ),
        }
    )

    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout=envelope, stderr=""),
    )

    units = await extractor.extract(transcript, log_dir=tmp_path)

    assert len(units) == 1
    entries = read_log(tmp_path)
    assert entries[0]["units_extracted"] == 1


@pytest.mark.asyncio
async def test_claude_cli_extract_returns_empty_on_is_error_envelope(monkeypatch, tmp_path: Path):
    extractor = ClaudeCliExtractor(max_units=2, timeout=5)
    transcript = Transcript(messages=[Message(role="user", content="hello")])

    envelope = json.dumps(
        {
            "type": "result",
            "is_error": True,
            "result": "Not logged in",
        }
    )

    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 0, stdout=envelope, stderr=""),
    )

    units = await extractor.extract(transcript, log_dir=tmp_path)

    assert units == []


@pytest.mark.asyncio
async def test_claude_cli_extract_logs_success(monkeypatch, tmp_path: Path):
    extractor = ClaudeCliExtractor(max_units=2, timeout=5)
    transcript = Transcript(messages=[Message(role="user", content="hello")])

    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **k: subprocess.CompletedProcess(
            a[0],
            0,
            stdout='{"units":[{"type":"pattern","title":"Retry","body_md":"Body","tags":[],"confidence":"high"}]}',
            stderr="",
        ),
    )

    units = await extractor.extract(transcript, log_dir=tmp_path)

    assert len(units) == 1
    entries = read_log(tmp_path)
    assert len(entries) == 1
    assert entries[0]["operation"] == "extract"
    assert entries[0]["units_extracted"] == 1


@pytest.mark.asyncio
async def test_claude_cli_extract_returns_empty_on_timeout(monkeypatch, tmp_path: Path):
    extractor = ClaudeCliExtractor(timeout=1)
    transcript = Transcript(messages=[Message(role="user", content="hello")])

    def raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="claude", timeout=1)

    monkeypatch.setattr("subprocess.run", raise_timeout)

    units = await extractor.extract(transcript, log_dir=tmp_path)

    assert units == []
    assert read_log(tmp_path) == []


@pytest.mark.asyncio
async def test_claude_cli_extract_returns_empty_on_nonzero_rc(monkeypatch, tmp_path: Path):
    extractor = ClaudeCliExtractor(max_units=2, timeout=5)
    transcript = Transcript(messages=[Message(role="user", content="hello")])

    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **k: subprocess.CompletedProcess(a[0], 1, stdout="", stderr="auth error"),
    )

    units = await extractor.extract(transcript, log_dir=tmp_path)

    assert units == []


@pytest.mark.asyncio
async def test_claude_cli_cmd_does_not_contain_bare_or_tools(monkeypatch, tmp_path: Path):
    """Regression: --bare breaks OAuth/keychain auth; --tools '' causes rc=1."""
    extractor = ClaudeCliExtractor(max_units=2, timeout=5)
    transcript = Transcript(messages=[Message(role="user", content="hello")])

    captured: list[list[str]] = []

    def capture_cmd(*args, **kwargs):
        captured.append(list(args[0]))
        return subprocess.CompletedProcess(args[0], 0, stdout='{"units":[]}', stderr="")

    monkeypatch.setattr("subprocess.run", capture_cmd)

    await extractor.extract(transcript, log_dir=tmp_path)

    cmd = captured[0]
    assert "--bare" not in cmd
    assert "" not in cmd  # no empty-string tool arg


# ---------------------------------------------------------------------------
# ClaudeExtractor (SDK)
# ---------------------------------------------------------------------------


def test_claude_sdk_extractor_parses_json_array_without_client_init():
    extractor = ClaudeExtractor.__new__(ClaudeExtractor)
    extractor._max_units = 5

    units = extractor._parse_response(
        "Prelude "
        '[{"type":"decision","title":"Use Postgres","body_md":"Body",'
        '"tags":["db"],"confidence":"medium","links":["https://example.com"]}]'
        " epilogue"
    )

    assert len(units) == 1
    assert units[0].title == "Use Postgres"
    assert units[0].links == ["https://example.com"]


def test_claude_sdk_extractor_parses_units_object_without_client_init():
    extractor = ClaudeExtractor.__new__(ClaudeExtractor)
    extractor._max_units = 5

    units = extractor._parse_response(
        '{"units":[{"type":"pattern","title":"Circuit Breaker","body_md":"Body",'
        '"tags":["resilience"],"confidence":"high"}]}'
    )

    assert len(units) == 1
    assert units[0].title == "Circuit Breaker"
    assert units[0].type == "pattern"


@pytest.mark.asyncio
async def test_claude_sdk_extract_success_reads_text_block_and_logs(tmp_path: Path):
    extractor = ClaudeExtractor.__new__(ClaudeExtractor)
    extractor._model = "claude-sonnet-4-6"
    extractor._prompt_version = "v1"
    extractor._max_units = 5
    extractor._language = "en"
    extractor._system_prompt = "system"
    extractor._client = cast(
        Anthropic,
        cast(
            object,
            SimpleNamespace(
                messages=SimpleNamespace(
                    create=lambda **kwargs: SimpleNamespace(
                        content=[
                            SimpleNamespace(
                                type="text",
                                text='[{"type":"pattern","title":"Retry","body_md":"Body","tags":[],"confidence":"high"}]',
                            )
                        ],
                        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
                    )
                )
            ),
        ),
    )
    transcript = Transcript(messages=[Message(role="user", content="hello")])

    units = await extractor.extract(transcript, log_dir=tmp_path)

    assert len(units) == 1
    entries = read_log(tmp_path)
    assert len(entries) == 1
    assert entries[0]["input_tokens"] == 10
    assert entries[0]["output_tokens"] == 5


@pytest.mark.asyncio
async def test_claude_sdk_extract_raises_api_error_and_logs(tmp_path: Path):
    import anthropic

    extractor = ClaudeExtractor.__new__(ClaudeExtractor)
    extractor._model = "claude-sonnet-4-6"
    extractor._prompt_version = "v1"
    extractor._max_units = 5
    extractor._language = "en"
    extractor._system_prompt = "system"

    def raise_api_error(**kwargs):
        raise anthropic.APIError("boom", httpx.Request("POST", "https://example.com"), body=None)

    extractor._client = cast(
        Anthropic, cast(object, SimpleNamespace(messages=SimpleNamespace(create=raise_api_error)))
    )
    transcript = Transcript(messages=[Message(role="user", content="hello")])

    with pytest.raises(anthropic.APIError):
        await extractor.extract(transcript, log_dir=tmp_path)

    entries = read_log(tmp_path)
    assert len(entries) == 1
    assert entries[0]["error"] == "boom"


# ---------------------------------------------------------------------------
# Backend preference
# ---------------------------------------------------------------------------


def test_select_extractor_prefers_cli_over_api_key(monkeypatch):
    """CLI (subscription) must be preferred when both CLI and API key are available."""
    import shutil

    from memforge.config import Config
    from memforge.core.pipeline import _select_extractor

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
    monkeypatch.setattr(
        shutil, "which", lambda name: "/usr/bin/claude" if name == "claude" else None
    )

    _, backend = _select_extractor(Config())

    assert backend == "cli"


def test_select_extractor_falls_back_to_api_when_no_cli(monkeypatch):
    import shutil

    from memforge.config import Config
    from memforge.core.pipeline import _select_extractor

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
    monkeypatch.setattr(shutil, "which", lambda name: None)

    _, backend = _select_extractor(Config())

    assert backend == "api"


def test_select_extractor_raises_when_neither_available(monkeypatch):
    import shutil

    from memforge.config import Config
    from memforge.core.pipeline import _select_extractor

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(shutil, "which", lambda name: None)

    with pytest.raises(RuntimeError, match="No LLM backend"):
        _select_extractor(Config())


# ---------------------------------------------------------------------------
# Package exports
# ---------------------------------------------------------------------------


def test_package_exports_include_all_sources_and_extractors():
    from memforge.extractors import ClaudeCliExtractor as ExportedCliExtractor
    from memforge.extractors import ClaudeExtractor as ExportedClaudeExtractor
    from memforge.sources import ClaudeCodeSource as ExportedClaudeCodeSource
    from memforge.sources import CodexSource as ExportedCodexSource
    from memforge.sources import CursorSource as ExportedCursorSource
    from memforge.sources import ProjectScanner as ExportedProjectScanner

    assert ExportedCliExtractor is ClaudeCliExtractor
    assert ExportedClaudeExtractor is ClaudeExtractor
    assert ExportedClaudeCodeSource.__name__ == "ClaudeCodeSource"
    assert ExportedCodexSource is CodexSource
    assert ExportedCursorSource is CursorSource
    assert ExportedProjectScanner is ProjectScanner
