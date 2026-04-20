"""Project scanner — builds a Transcript from project files for mem scan."""

from __future__ import annotations

import subprocess
from pathlib import Path

from memforge.core.models import Message, Transcript

# Files read in full (capped at _FILE_CAP chars each)
_KEY_FILES = [
    "README.md",
    "README.rst",
    "CLAUDE.md",
    "AGENTS.md",
    "pyproject.toml",
    "setup.cfg",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "Makefile",
    "docker-compose.yml",
    "docker-compose.yaml",
    "Dockerfile",
    "ARCHITECTURE.md",
    "CONTRIBUTING.md",
    "docs/architecture.md",
    "requirements.txt",
    ".env.example",
]

_FILE_CAP = 4_000
_TREE_DEPTH = 3
_IGNORE_DIRS = {
    ".git",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    "dist",
    "build",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    "*.egg-info",
    ".memforge",
}


class ProjectScanner:
    """Reads project files and produces a Transcript for knowledge extraction."""

    name = "project-scan"

    def __init__(self, root: Path | None = None, note: str | None = None) -> None:
        self._root = root or Path.cwd()
        self._note = note

    def detect(self) -> bool:
        return self._root.is_dir()

    def latest_session(self) -> Transcript:
        messages: list[Message] = []

        # 1. User-provided description / context note
        if self._note:
            messages.append(
                Message(
                    role="user",
                    content=f"Project context provided by user:\n{self._note}",
                )
            )

        # 2. Project metadata summary (name, description, tech stack)
        meta = _extract_project_meta(self._root)
        if meta:
            messages.append(Message(role="user", content=f"Project metadata:\n{meta}"))

        # 3. Directory tree overview
        tree = _build_tree(self._root, max_depth=_TREE_DEPTH)
        messages.append(
            Message(
                role="user",
                content=f"Project root: {self._root}\n\nDirectory structure:\n{tree}",
            )
        )

        # 4. Key project files
        for rel in _KEY_FILES:
            path = self._root / rel
            if path.exists():
                text = path.read_text("utf-8", errors="replace")[:_FILE_CAP]
                messages.append(
                    Message(
                        role="user",
                        content=f"=== {rel} ===\n{text}",
                    )
                )

        # 5. First GitHub Actions workflow (if any)
        workflows_dir = self._root / ".github" / "workflows"
        if workflows_dir.is_dir():
            for wf in sorted(workflows_dir.glob("*.yml"))[:1]:
                text = wf.read_text("utf-8", errors="replace")[:_FILE_CAP]
                messages.append(
                    Message(role="user", content=f"=== .github/workflows/{wf.name} ===\n{text}")
                )

        # 6. Python package entry-point (first __init__.py with content)
        for init in sorted(self._root.glob("*/__init__.py"))[:3]:
            text = init.read_text("utf-8", errors="replace").strip()
            if text and "__init_placeholder__" not in text:
                rel_path = init.relative_to(self._root)
                messages.append(
                    Message(
                        role="user",
                        content=f"=== {rel_path} ===\n{text[:_FILE_CAP]}",
                    )
                )
                break

        return Transcript(
            agent=self.name,
            messages=messages,
            cwd=str(self._root),
        )


def _extract_project_meta(root: Path) -> str:
    """Extract name, description, version, and tech stack from project files."""
    lines: list[str] = []

    # pyproject.toml
    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        try:
            import tomllib  # Python 3.11+
        except ImportError:
            try:
                import tomli as tomllib  # type: ignore[no-redef]
            except ImportError:
                tomllib = None  # type: ignore[assignment]
        if tomllib:
            try:
                data = tomllib.loads(pyproject.read_text("utf-8"))
                proj = data.get("project", {})
                if proj.get("name"):
                    lines.append(f"Name: {proj['name']}")
                if proj.get("version"):
                    lines.append(f"Version: {proj['version']}")
                if proj.get("description"):
                    lines.append(f"Description: {proj['description']}")
                if proj.get("keywords"):
                    lines.append(f"Keywords: {', '.join(proj['keywords'])}")
                if proj.get("requires-python"):
                    lines.append(f"Python: {proj['requires-python']}")
            except Exception:
                pass

    # package.json
    package_json = root / "package.json"
    if package_json.exists():
        try:
            import json

            data = json.loads(package_json.read_text("utf-8"))
            if data.get("name"):
                lines.append(f"Name: {data['name']}")
            if data.get("version"):
                lines.append(f"Version: {data['version']}")
            if data.get("description"):
                lines.append(f"Description: {data['description']}")
            if data.get("keywords"):
                lines.append(f"Keywords: {', '.join(data['keywords'])}")
        except Exception:
            pass

    # Tech stack detection
    tech: list[str] = []
    indicators = {
        "pyproject.toml": "Python",
        "package.json": "Node.js",
        "Cargo.toml": "Rust",
        "go.mod": "Go",
        "pom.xml": "Java/Maven",
        "build.gradle": "Java/Gradle",
        "Gemfile": "Ruby",
        "Dockerfile": "Docker",
        "docker-compose.yml": "Docker Compose",
        "docker-compose.yaml": "Docker Compose",
        ".github/workflows": "GitHub Actions",
    }
    for indicator, label in indicators.items():
        if (root / indicator).exists() and label not in tech:
            tech.append(label)
    if tech:
        lines.append(f"Tech stack: {', '.join(tech)}")

    # Git remote URL
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            lines.append(f"Git remote: {result.stdout.strip()}")
    except Exception:
        pass

    # Git branch
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            lines.append(f"Git branch: {result.stdout.strip()}")
    except Exception:
        pass

    return "\n".join(lines)


def _build_tree(root: Path, max_depth: int) -> str:
    lines: list[str] = []

    def _walk(path: Path, prefix: str, depth: int) -> None:
        if depth > max_depth:
            return
        try:
            entries = sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name))
        except PermissionError:
            return
        for entry in entries:
            name = entry.name
            if name.startswith(".") and name not in (".env.example", ".github"):
                continue
            if any(entry.match(pat) for pat in _IGNORE_DIRS):
                continue
            connector = "├── " if entry != entries[-1] else "└── "
            lines.append(f"{prefix}{connector}{name}{'/' if entry.is_dir() else ''}")
            if entry.is_dir():
                extension = "│   " if entry != entries[-1] else "    "
                _walk(entry, prefix + extension, depth + 1)

    _walk(root, "", 1)
    return "\n".join(lines[:200])  # cap at 200 lines
