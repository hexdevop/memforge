"""Unit tests for the scrubber."""

from memforge.scrubber.regex import scrub


def test_aws_access_key_redacted():
    text = "key=AKIAIOSFODNN7EXAMPLE here"
    result = scrub(text)
    assert "[REDACTED:aws-access-key]" in result.text
    assert result.was_modified


def test_github_pat_redacted():
    text = "token: ghp_aBcDeFgHiJkLmNoPqRsTuVwXyZ1234567890ab"
    result = scrub(text)
    assert "[REDACTED:github-pat]" in result.text
    assert result.was_modified


def test_jwt_redacted():
    token = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
    result = scrub(token)
    assert "[REDACTED:jwt]" in result.text


def test_clean_text_unchanged():
    text = "This is a normal development note about using FastAPI."
    result = scrub(text)
    assert not result.was_modified
    assert result.text == text


def test_mode_off_skips_scrubbing():
    text = "key=AKIAIOSFODNN7EXAMPLE"
    result = scrub(text, mode="off")
    assert result.text == text
    assert not result.was_modified


def test_corporate_email_redacted():
    text = "Contact admin@corp.example.com for access"
    result = scrub(text, corporate_email_domains=["corp.example.com"])
    assert "[REDACTED:corp-email]" in result.text


def test_multiple_secrets_all_redacted():
    text = "key=AKIAIOSFODNN7EXAMPLE and token=ghp_aBcDeFgHiJkLmNoPqRsTuVwXyZ1234567890ab"
    result = scrub(text)
    assert result.was_modified
    assert len(result.redacted) >= 2
