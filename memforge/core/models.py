"""Core domain models."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

RecordType = Literal["decision", "pattern", "gotcha", "contract", "glossary", "todo"]
RecordStatus = Literal["active", "deprecated", "archived"]
Confidence = Literal["high", "medium", "low"]
Scope = Literal["project", "global"]


# ---------------------------------------------------------------------------
# Transcript (normalised input)
# ---------------------------------------------------------------------------


class Message(BaseModel):
    role: Literal["user", "assistant", "system", "tool"]
    content: str
    ts: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Transcript(BaseModel):
    session_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    agent: str = "unknown"
    started_at: datetime | None = None
    messages: list[Message] = Field(default_factory=list)
    cwd: str | None = None

    @property
    def hash(self) -> str:
        payload = "".join(m.content for m in self.messages).encode()
        return "sha256:" + hashlib.sha256(payload).hexdigest()

    @property
    def text(self) -> str:
        parts = []
        for m in self.messages:
            parts.append(f"[{m.role.upper()}]\n{m.content}")
        return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Extractor output
# ---------------------------------------------------------------------------


class CodeRef(BaseModel):
    path: str
    revision: str | None = None


class ExtractedUnit(BaseModel):
    type: RecordType
    title: str
    body_md: str
    tags: list[str] = Field(default_factory=list)
    refs: list[CodeRef] = Field(default_factory=list)
    confidence: Confidence = "medium"
    links: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Article (knowledge record)
# ---------------------------------------------------------------------------


class ArticleFrontMatter(BaseModel):
    id: str
    type: RecordType
    title: str
    slug: str
    scope: Scope = "project"
    tags: list[str] = Field(default_factory=list)
    created: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated: datetime = Field(default_factory=lambda: datetime.now(UTC))
    source_agent: str | None = None
    source_session_id: str | None = None
    transcript_hash: str | None = None
    refs: list[CodeRef] = Field(default_factory=list)
    links: list[str] = Field(default_factory=list)
    supersedes: str | None = None
    status: RecordStatus = "active"
    confidence: Confidence = "medium"
    fingerprint: str | None = None
    quarantine: bool = False

    model_config = {"populate_by_name": True}


class Article(BaseModel):
    front_matter: ArticleFrontMatter
    body: str

    @property
    def slug(self) -> str:
        return self.front_matter.slug

    @property
    def id(self) -> str:
        return self.front_matter.id


# ---------------------------------------------------------------------------
# Draft (inbox item)
# ---------------------------------------------------------------------------


class Draft(BaseModel):
    draft_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    unit: ExtractedUnit
    transcript_hash: str
    source_agent: str
    source_session_id: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    quarantine: bool = False


# ---------------------------------------------------------------------------
# Index entry
# ---------------------------------------------------------------------------


class IndexEntry(BaseModel):
    slug: str
    type: RecordType
    title: str
    tags: list[str] = Field(default_factory=list)
    confidence: Confidence = "medium"
    scope: Scope = "project"
    pinned: bool = False
    updated: datetime | None = None
    cite_count: int = 0
    path: str = ""
