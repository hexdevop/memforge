"""Project scanner — builds a Transcript from project files for mem scan."""

from __future__ import annotations

from pathlib import Path

from memforge.core.models import Message, Transcript

# Files read in full (capped at _FILE_CAP chars each)
_KEY_FILES = [
    "README.md",
    "README.rst",
    "CLAUDE.md",
    "pyproject.toml",
    "setup.cfg",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "Makefile",
    "ARCHITECTURE.md",
    "CONTRIBUTING.md",
    "docs/architecture.md",
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

    def __init__(self, root: Path | None = None) -> None:
        self._root = root or Path.cwd()

    def detect(self) -> bool:
        return self._root.is_dir()

    def latest_session(self) -> Transcript:
        messages: list[Message] = []

        # 1. Directory tree overview
        tree = _build_tree(self._root, max_depth=_TREE_DEPTH)
        messages.append(
            Message(
                role="user",
                content=f"Project root: {self._root}\n\nDirectory structure:\n{tree}",
            )
        )

        # 2. Key project files
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

        # 3. Python package entry-point (first __init__.py with content)
        for init in sorted(self._root.glob("*/__init__.py"))[:3]:
            text = init.read_text("utf-8", errors="replace").strip()
            if text:
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
            if name.startswith(".") and name not in (".env.example",):
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
