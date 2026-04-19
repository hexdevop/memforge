"""Unit tests for core models."""

from memforge.core.models import Message, Transcript


def test_transcript_hash_is_deterministic():
    t = Transcript(messages=[Message(role="user", content="hello")])
    assert t.hash == t.hash
    assert t.hash.startswith("sha256:")


def test_transcript_hash_differs_by_content():
    t1 = Transcript(messages=[Message(role="user", content="hello")])
    t2 = Transcript(messages=[Message(role="user", content="world")])
    assert t1.hash != t2.hash


def test_transcript_text():
    t = Transcript(
        messages=[
            Message(role="user", content="hi"),
            Message(role="assistant", content="hello"),
        ]
    )
    text = t.text
    assert "[USER]" in text
    assert "[ASSISTANT]" in text
    assert "hi" in text
