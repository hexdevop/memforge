"""Configuration loading and validation."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator


class UserConfig(BaseModel):
    name: str = "user"
    email: str = ""
    timezone: str = "UTC"


class ExtractorConfig(BaseModel):
    backend: Literal["auto", "api", "cli"] = "auto"
    prompt_version: str = "v1"
    max_units_per_save: int = 10
    language: str = "en"
    model: str = "claude-sonnet-4-6"


class CompilerConfig(BaseModel):
    prompt_version: str = "v1"
    conflict_strategy: Literal["interactive", "prefer-new", "prefer-existing", "llm-merge"] = (
        "interactive"
    )

    class Dedupe(BaseModel):
        semantic_threshold: float = 0.85

    dedupe: Dedupe = Field(default_factory=Dedupe)


class RetrieverConfig(BaseModel):
    top_k: int = 5
    bm25: bool = True
    max_snippet_chars: int = 400


class IndexConfig(BaseModel):
    max_tokens: int = 4000
    order: list[str] = Field(default_factory=lambda: ["pinned", "recent", "cited"])


class ScrubberConfig(BaseModel):
    regex: Literal["default", "strict", "off"] = "default"
    detect_secrets: bool = False
    corporate_email_domains: list[str] = Field(default_factory=list)


class SourceClaudeCode(BaseModel):
    enabled: bool = True
    root: str = "~/.claude/projects"


class SourceCursor(BaseModel):
    enabled: bool = True


class SourceCodex(BaseModel):
    enabled: bool = False


class SourceStdin(BaseModel):
    enabled: bool = True
    save_sessions: bool = False


class SourcesConfig(BaseModel):
    claude_code: SourceClaudeCode = Field(default_factory=SourceClaudeCode)
    cursor: SourceCursor = Field(default_factory=SourceCursor)
    codex: SourceCodex = Field(default_factory=SourceCodex)
    stdin: SourceStdin = Field(default_factory=SourceStdin)


class UIConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 7777
    theme: Literal["auto", "light", "dark"] = "auto"


class GitConfig(BaseModel):
    auto_commit: bool = True
    sign_commits: bool = False


class LoggingConfig(BaseModel):
    level: str = "info"
    file: str = "logs/memforge.log"


class Config(BaseModel):
    version: int = 1
    user: UserConfig = Field(default_factory=UserConfig)
    extractor: ExtractorConfig = Field(default_factory=ExtractorConfig)
    compiler: CompilerConfig = Field(default_factory=CompilerConfig)
    retriever: RetrieverConfig = Field(default_factory=RetrieverConfig)
    index: IndexConfig = Field(default_factory=IndexConfig)
    scrubber: ScrubberConfig = Field(default_factory=ScrubberConfig)
    sources: SourcesConfig = Field(default_factory=SourcesConfig)
    ui: UIConfig = Field(default_factory=UIConfig)
    git: GitConfig = Field(default_factory=GitConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    @field_validator("version")
    @classmethod
    def check_version(cls, v: int) -> int:
        if v != 1:
            raise ValueError(f"Unsupported config version: {v}")
        return v


def _load_yaml(path: Path) -> dict:  # type: ignore[type-arg]
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _deep_merge(base: dict, override: dict) -> dict:  # type: ignore[type-arg]
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


GLOBAL_ROOT = Path(os.environ.get("MEMFORGE_GLOBAL", "~/.memforge")).expanduser()


def load_config(project_root: Path | None = None) -> Config:
    """Load and merge global + project configs."""
    global_cfg = _load_yaml(GLOBAL_ROOT / "config.yaml")
    project_cfg: dict = {}  # type: ignore[type-arg]
    if project_root is not None:
        project_cfg = _load_yaml(project_root / ".memforge" / "config.yaml")
    merged = _deep_merge(global_cfg, project_cfg)
    return Config.model_validate(merged)


def save_config(config: Config, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        yaml.dump(config.model_dump(), f, allow_unicode=True, sort_keys=False)
