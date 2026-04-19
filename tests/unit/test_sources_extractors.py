from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from memforge.core.llm_log import read_log
from memforge.core.models import Message, Transcript
from memforge.extractors.claude_cli import ClaudeCliExtractor
from memforge.extractors.claude_sdk import ClaudeExtractor
from memforge.sources.codex import CodexSource
from memforge.sources.cursor import CursorSource
from memforge.sources.stdin import FileSource, StdinSource


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
                "message": {"author": {"role": "user"}, "content": {"parts": ["How do retries work?"]}}
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


def test_claude_cli_extractor_parses_enveloped_json():
    extractor = ClaudeCliExtractor(max_units=2)

    units = extractor._parse_response(
        '{"type":"result","result":{"units":[{"type":"pattern","title":"Retry","body_md":"Body","tags":["ops"],"confidence":"high"}]}}'
    )

    assert len(units) == 1
    assert units[0].title == "Retry"
    assert units[0].type == "pattern"


def test_claude_cli_extractor_parses_embedded_json_array():
    extractor = ClaudeCliExtractor(max_units=2)

    units = extractor._parse_response(
        'Prelude [{"type":"pattern","title":"Retry","body_md":"Body","tags":["ops"],"confidence":"high"}] epilogue'
    )

    assert len(units) == 1
    assert units[0].title == "Retry"


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


def test_claude_sdk_extractor_parses_json_array_without_client_init():
    extractor = ClaudeExtractor.__new__(ClaudeExtractor)
    extractor._max_units = 5

    units = extractor._parse_response(
        'Prelude [{"type":"decision","title":"Use Postgres","body_md":"Body","tags":["db"],"confidence":"medium","links":["https://example.com"]}] epilogue'
    )

    assert len(units) == 1
    assert units[0].title == "Use Postgres"
    assert units[0].links == ["https://example.com"]


def test_claude_sdk_extractor_parses_units_object_without_client_init():
    extractor = ClaudeExtractor.__new__(ClaudeExtractor)
    extractor._max_units = 5

    units = extractor._parse_response(
        '{"units":[{"type":"pattern","title":"Circuit Breaker","body_md":"Body","tags":["resilience"],"confidence":"high"}]}'
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
    extractor._client = SimpleNamespace(
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

    extractor._client = SimpleNamespace(messages=SimpleNamespace(create=raise_api_error))
    transcript = Transcript(messages=[Message(role="user", content="hello")])

    with pytest.raises(anthropic.APIError):
        await extractor.extract(transcript, log_dir=tmp_path)

    entries = read_log(tmp_path)
    assert len(entries) == 1
    assert entries[0]["error"] == "boom"


def test_package_exports_include_all_sources_and_extractors():
    from memforge.extractors import ClaudeCliExtractor as ExportedCliExtractor
    from memforge.extractors import ClaudeExtractor as ExportedClaudeExtractor
    from memforge.sources import ClaudeCodeSource as ExportedClaudeCodeSource
    from memforge.sources import CodexSource as ExportedCodexSource
    from memforge.sources import CursorSource as ExportedCursorSource

    assert ExportedCliExtractor is ClaudeCliExtractor
    assert ExportedClaudeExtractor is ClaudeExtractor
    assert ExportedClaudeCodeSource.__name__ == "ClaudeCodeSource"
    assert ExportedCodexSource is CodexSource
    assert ExportedCursorSource is CursorSource
