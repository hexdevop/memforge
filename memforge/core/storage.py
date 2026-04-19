"""File I/O and git integration."""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import frontmatter
import git

from memforge.core.models import Article, ArticleFrontMatter, Draft, ExtractedUnit, RecordType

# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


class Store:
    """Represents a single .memforge storage tree (global or project)."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.memory = root / "memory"
        self.inbox = self.memory / "inbox"
        self.daily = self.memory / "daily"
        self.knowledge = self.memory / "knowledge"
        self.archive = self.memory / "archive"
        self.sessions = self.memory / "sessions"
        self.pinned_file = self.memory / "pinned.md"
        self.index_file = self.knowledge / "index.md"
        self.logs = root / "logs"

    def ensure_dirs(self) -> None:
        for d in [
            self.inbox,
            self.daily,
            self.knowledge / "concepts",
            self.knowledge / "decisions",
            self.knowledge / "gotchas",
            self.knowledge / "contracts",
            self.knowledge / "glossary",
            self.knowledge / "todo-knowledge",
            self.archive,
            self.sessions,
            self.logs,
        ]:
            d.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Git
    # ------------------------------------------------------------------

    def init_git(self) -> git.Repo:
        if (self.root / ".git").exists():
            return git.Repo(self.root)
        repo = git.Repo.init(self.root)
        self._write_gitignore()
        repo.index.add([".gitignore"])
        repo.index.commit("chore: init memforge repository")
        return repo

    def _write_gitignore(self) -> None:
        gi = self.root / ".gitignore"
        gi.write_text(
            "# MemForge\nlogs/\nmemory/sessions/\n*.quarantine\n",
            encoding="utf-8",
        )

    def git_commit(self, message: str, paths: list[Path] | None = None) -> str | None:
        """Stage and commit. Returns short SHA or None if nothing to commit."""
        try:
            repo = git.Repo(self.root)
        except git.InvalidGitRepositoryError:
            return None

        if paths:
            rel = [str(p.relative_to(self.root)) for p in paths]
            repo.index.add(rel)
        else:
            repo.git.add(A=True)

        if not repo.index.diff("HEAD") and not repo.untracked_files:
            return None

        commit = repo.index.commit(message)
        return commit.hexsha[:8]

    def git_revert(self, sha: str) -> None:
        repo = git.Repo(self.root)
        repo.git.revert(sha, no_edit=True)

    # ------------------------------------------------------------------
    # Draft (inbox)
    # ------------------------------------------------------------------

    def write_draft(
        self,
        unit: ExtractedUnit,
        transcript_hash: str,
        source_agent: str,
        source_session_id: str,
        quarantine: bool = False,
    ) -> Draft:
        draft = Draft(
            unit=unit,
            transcript_hash=transcript_hash,
            source_agent=source_agent,
            source_session_id=source_session_id,
            quarantine=quarantine,
        )
        path = self.inbox / f"draft-{draft.draft_id}.md"
        meta = {
            "draft_id": draft.draft_id,
            "type": unit.type,
            "title": unit.title,
            "tags": unit.tags,
            "confidence": unit.confidence,
            "transcript_hash": transcript_hash,
            "source_agent": source_agent,
            "source_session_id": source_session_id,
            "created_at": draft.created_at.isoformat(),
            "quarantine": quarantine,
        }
        body = unit.body_md
        if unit.refs:
            body += "\n\n## Code refs\n" + "\n".join(
                f"- `{r.path}`" + (f" @ {r.revision}" if r.revision else "") for r in unit.refs
            )
        path.write_text(frontmatter.dumps(frontmatter.Post(body, **meta)), encoding="utf-8")
        return draft

    def list_drafts(self) -> list[Draft]:
        drafts: list[Draft] = []
        for p in sorted(self.inbox.glob("draft-*.md")):
            d = self._parse_draft(p)
            if d:
                drafts.append(d)
        return drafts

    def get_draft(self, draft_id: str) -> Draft | None:
        p = self.inbox / f"draft-{draft_id}.md"
        if not p.exists():
            return None
        return self._parse_draft(p)

    def _parse_draft(self, path: Path) -> Draft | None:
        try:
            post = frontmatter.load(str(path))
            unit = ExtractedUnit(
                type=post["type"],
                title=post["title"],
                tags=post.get("tags", []),
                confidence=post.get("confidence", "medium"),
                body_md=post.content,
            )
            return Draft(
                draft_id=post["draft_id"],
                unit=unit,
                transcript_hash=post.get("transcript_hash", ""),
                source_agent=post.get("source_agent", "unknown"),
                source_session_id=post.get("source_session_id", ""),
                created_at=datetime.fromisoformat(
                    post.get("created_at", datetime.now(UTC).isoformat())
                ),
                quarantine=post.get("quarantine", False),
            )
        except Exception:
            return None

    def delete_draft(self, draft_id: str) -> bool:
        p = self.inbox / f"draft-{draft_id}.md"
        if p.exists():
            p.unlink()
            return True
        return False

    # ------------------------------------------------------------------
    # Daily log
    # ------------------------------------------------------------------

    def append_to_daily(self, article: Article, date: datetime | None = None) -> Path:
        day = (date or datetime.now(UTC)).strftime("%Y-%m-%d")
        path = self.daily / f"{day}.md"
        entry = _render_article(article)
        if path.exists():
            path.write_text(path.read_text("utf-8") + "\n\n---\n\n" + entry, encoding="utf-8")
        else:
            path.write_text(entry, encoding="utf-8")
        return path

    # ------------------------------------------------------------------
    # Knowledge articles
    # ------------------------------------------------------------------

    def article_dir(self, record_type: RecordType) -> Path:
        mapping: dict[RecordType, str] = {
            "decision": "decisions",
            "pattern": "concepts",
            "gotcha": "gotchas",
            "contract": "contracts",
            "glossary": "glossary",
            "todo": "todo-knowledge",
        }
        return self.knowledge / mapping[record_type]

    def write_article(self, article: Article) -> Path:
        d = self.article_dir(article.front_matter.type)
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{article.slug}.md"
        path.write_text(_render_article(article), encoding="utf-8")
        return path

    def read_article(self, path: Path) -> Article | None:
        if not path.exists():
            return None
        return _parse_article(path)

    def iter_articles(self) -> Iterator[Article]:
        for p in self.knowledge.rglob("*.md"):
            if p.name == "index.md":
                continue
            a = _parse_article(p)
            if a:
                yield a

    def find_article_by_slug(self, slug: str) -> Article | None:
        for article in self.iter_articles():
            if article.slug == slug:
                return article
        return None

    # ------------------------------------------------------------------
    # Pinned
    # ------------------------------------------------------------------

    def pinned_slugs(self) -> list[str]:
        if not self.pinned_file.exists():
            return []
        lines = self.pinned_file.read_text("utf-8").splitlines()
        return [ln.strip("- ").strip() for ln in lines if ln.strip().startswith("-")]

    def pin(self, slug: str) -> None:
        slugs = self.pinned_slugs()
        if slug not in slugs:
            slugs.append(slug)
        self.pinned_file.write_text(
            "# Pinned entries\n\n" + "\n".join(f"- {s}" for s in slugs) + "\n",
            encoding="utf-8",
        )

    def unpin(self, slug: str) -> None:
        slugs = [s for s in self.pinned_slugs() if s != slug]
        self.pinned_file.write_text(
            "# Pinned entries\n\n" + "\n".join(f"- {s}" for s in slugs) + "\n",
            encoding="utf-8",
        )


# ---------------------------------------------------------------------------
# Article serialisation helpers
# ---------------------------------------------------------------------------


def _render_article(article: Article) -> str:
    fm = article.front_matter
    meta: dict = {  # type: ignore[type-arg]
        "id": fm.id,
        "type": fm.type,
        "title": fm.title,
        "slug": fm.slug,
        "scope": fm.scope,
        "tags": fm.tags,
        "created": fm.created.isoformat(),
        "updated": fm.updated.isoformat(),
        "status": fm.status,
        "confidence": fm.confidence,
    }
    if fm.source_agent:
        meta["source"] = {
            "agent": fm.source_agent,
            "session_id": fm.source_session_id,
            "transcript_hash": fm.transcript_hash,
        }
    if fm.refs:
        meta["refs"] = [{"path": r.path, "revision": r.revision} for r in fm.refs]
    if fm.links:
        meta["links"] = fm.links
    if fm.supersedes:
        meta["supersedes"] = fm.supersedes
    if fm.fingerprint:
        meta["fingerprint"] = fm.fingerprint
    if fm.quarantine:
        meta["quarantine"] = True
    post = frontmatter.Post(article.body, **meta)
    return str(frontmatter.dumps(post))


def _parse_article(path: Path) -> Article | None:
    try:
        post = frontmatter.load(str(path))
        src = post.get("source", {}) or {}
        fm = ArticleFrontMatter(
            id=post["id"],
            type=post["type"],
            title=post["title"],
            slug=post["slug"],
            scope=post.get("scope", "project"),
            tags=post.get("tags", []),
            created=datetime.fromisoformat(str(post.get("created", datetime.now(UTC).isoformat()))),
            updated=datetime.fromisoformat(str(post.get("updated", datetime.now(UTC).isoformat()))),
            source_agent=src.get("agent") if src else None,
            source_session_id=src.get("session_id") if src else None,
            transcript_hash=src.get("transcript_hash") if src else None,
            refs=post.get("refs", []),
            links=post.get("links", []),
            supersedes=post.get("supersedes"),
            status=post.get("status", "active"),
            confidence=post.get("confidence", "medium"),
            fingerprint=post.get("fingerprint"),
            quarantine=post.get("quarantine", False),
        )
        return Article(front_matter=fm, body=post.content)
    except Exception:
        return None


def slugify(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text or str(uuid.uuid4())[:8]
