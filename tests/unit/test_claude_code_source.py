"""Unit tests for Claude Code source adapter."""

from pathlib import Path

from memforge.sources.claude_code import ClaudeCodeSource


def test_parse_simple_session(tmp_path: Path):
    fixture = Path(__file__).parent.parent / "fixtures" / "transcripts" / "simple_session.jsonl"
    session_dir = tmp_path / "abc123"
    session_dir.mkdir()
    import shutil

    shutil.copy(fixture, session_dir / "test-session-id.jsonl")

    source = ClaudeCodeSource(root=tmp_path, cwd=tmp_path)
    transcript = source.latest_session()

    assert transcript is not None
    assert transcript.agent == "claude-code"
    assert len(transcript.messages) == 4
    assert "JWT" in transcript.messages[1].content


def test_detect_returns_false_when_empty(tmp_path: Path):
    source = ClaudeCodeSource(root=tmp_path)
    assert not source.detect()


def test_detect_returns_true_with_jsonl(tmp_path: Path):
    d = tmp_path / "proj"
    d.mkdir()
    (d / "session.jsonl").write_text("{}\n")
    source = ClaudeCodeSource(root=tmp_path)
    assert source.detect()
