"""Regex-based secret scrubber."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

log = logging.getLogger(__name__)

# Each entry: (kind, pattern)
_DEFAULT_PATTERNS: list[tuple[str, str]] = [
    ("aws-access-key", r"AKIA[0-9A-Z]{16}"),
    ("aws-secret-key", r"(?i)aws.{0,20}secret.{0,20}['\"][0-9a-zA-Z/+]{40}['\"]"),
    ("github-pat", r"gh[pousr]_[A-Za-z0-9_]{36,255}"),
    ("gitlab-pat", r"glpat-[A-Za-z0-9\-_]{20,}"),
    ("gcp-api-key", r"AIza[0-9A-Za-z\-_]{35}"),
    ("azure-key", r"(?i)AccountKey=[A-Za-z0-9+/]{86}=="),
    ("rsa-private-key", r"-----BEGIN RSA PRIVATE KEY-----"),
    ("private-key", r"-----BEGIN (?:EC|DSA|OPENSSH) PRIVATE KEY-----"),
    ("jwt", r"eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),
    ("generic-secret", r"(?i)(?:secret|password|passwd|token|api[-_]?key)\s*[:=]\s*['\"]?[A-Za-z0-9_/+\-]{16,}['\"]?"),
]

_STRICT_EXTRA: list[tuple[str, str]] = [
    ("email", r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"),
    ("ipv4-private", r"(?:10|172\.(?:1[6-9]|2\d|3[01])|192\.168)\.\d{1,3}\.\d{1,3}"),
    ("phone", r"\+?[0-9][0-9\s\-\(\)]{7,}\d"),
]


@dataclass
class ScrubResult:
    text: str
    redacted: list[tuple[str, str]]  # (kind, original_snippet)
    was_modified: bool


def scrub(
    text: str,
    mode: str = "default",
    corporate_email_domains: list[str] | None = None,
) -> ScrubResult:
    patterns = list(_DEFAULT_PATTERNS)
    if mode == "strict":
        patterns.extend(_STRICT_EXTRA)
    if mode == "off":
        return ScrubResult(text=text, redacted=[], was_modified=False)

    redacted: list[tuple[str, str]] = []
    result = text

    for kind, pattern in patterns:
        compiled = re.compile(pattern)
        for match in compiled.finditer(result):
            snippet = match.group()[:40]
            redacted.append((kind, snippet))
            log.info("scrubber: redacting %s", kind)

        result = compiled.sub(f"[REDACTED:{kind}]", result)

    # Corporate email domains (exact domain check)
    if corporate_email_domains:
        for domain in corporate_email_domains:
            domain_pattern = re.compile(
                r"[a-zA-Z0-9._%+\-]+@" + re.escape(domain),
                re.IGNORECASE,
            )
            for match in domain_pattern.finditer(result):
                redacted.append(("corp-email", match.group()[:40]))
            result = domain_pattern.sub("[REDACTED:corp-email]", result)

    # Optional: detect-secrets baseline scan as second pass
    if mode != "off":
        result, ds_redacted = _detect_secrets_scan(result)
        redacted.extend(ds_redacted)

    return ScrubResult(
        text=result,
        redacted=redacted,
        was_modified=result != text,
    )


def _detect_secrets_scan(text: str) -> tuple[str, list[tuple[str, str]]]:
    """Run detect-secrets on text as a second pass. No-op if not installed."""
    try:
        from detect_secrets import SecretsCollection
        from detect_secrets.settings import transient_settings
    except ImportError:
        return text, []

    try:
        cfg = {"plugins_used": [{"name": p} for p in (
            "AWSKeyDetector",
            "ArtifactoryDetector",
            "AzureStorageKeyDetector",
            "BasicAuthDetector",
            "CloudantDetector",
            "GitHubTokenDetector",
            "HexHighEntropyString",
            "IbmCloudIamDetector",
            "IbmCosHmacDetector",
            "JwtTokenDetector",
            "MailchimpDetector",
            "NpmDetector",
            "PrivateKeyDetector",
            "SendGridDetector",
            "SlackDetector",
            "SoftlayerDetector",
            "SquareOAuthDetector",
            "StripeDetector",
            "TwilioKeyDetector",
        )]}
        with transient_settings(cfg):
            secrets = SecretsCollection()
            import tempfile, os
            with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as tmp:
                tmp.write(text)
                tmp_path = tmp.name
            try:
                secrets.scan_file(tmp_path)
            finally:
                os.unlink(tmp_path)

        redacted: list[tuple[str, str]] = []
        lines = text.splitlines(keepends=True)
        for _fname, found in secrets:
            for secret in found:
                lineno = secret.line_number - 1
                if 0 <= lineno < len(lines):
                    original = lines[lineno]
                    lines[lineno] = re.sub(
                        re.escape(secret.secret_value or ""),
                        "[REDACTED:detect-secrets]",
                        original,
                    ) if secret.secret_value else original
                    if lines[lineno] != original:
                        redacted.append(("detect-secrets", (secret.secret_value or "")[:40]))

        return "".join(lines), redacted
    except Exception:
        return text, []
