"""MemForge CLI entry point."""

from __future__ import annotations

import asyncio
import os
import subprocess
from datetime import UTC
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from memforge.config import GLOBAL_ROOT, Config, load_config, save_config
from memforge.core.compiler import Compiler
from memforge.core.indexer import build_index, write_index
from memforge.core.models import Transcript
from memforge.core.pipeline import run_save
from memforge.core.retriever import retrieve
from memforge.core.storage import Store

app = typer.Typer(
    name="mem",
    help="MemForge — local external memory for LLM agents.",
    no_args_is_help=True,
)
console = Console()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_stores(scope: str) -> list[Store]:
    stores: list[Store] = []
    global_store = Store(GLOBAL_ROOT)
    project_store = Store(Path.cwd() / ".memforge")

    if scope in ("global", "both") and global_store.memory.exists():
        stores.append(global_store)
    if scope in ("project", "both") and project_store.memory.exists():
        stores.append(project_store)
    return stores


def _primary_store(scope: str) -> Store:
    if scope == "global":
        return Store(GLOBAL_ROOT)
    return Store(Path.cwd() / ".memforge")


def _load_cfg(scope: str) -> Config:
    project_root = Path.cwd() if scope != "global" else None
    return load_config(project_root)


# ---------------------------------------------------------------------------
# mem init
# ---------------------------------------------------------------------------


@app.command()
def init(
    global_: Annotated[bool, typer.Option("--global", help="Initialize global store")] = False,
    no_git: Annotated[bool, typer.Option("--no-git", help="Skip git init")] = False,
) -> None:
    """Initialize a MemForge store (global or project)."""
    if global_:
        root = GLOBAL_ROOT
    else:
        root = Path.cwd() / ".memforge"

    store = Store(root)
    store.ensure_dirs()

    config_path = root / "config.yaml"
    if not config_path.exists():
        save_config(Config(), config_path)
        console.print(f"[green]✓[/] Created config: {config_path}")

    if not no_git:
        try:
            store.init_git()
            console.print(f"[green]✓[/] Git repository at {root}")
        except Exception as e:
            console.print(f"[yellow]⚠[/] Git init skipped: {e}")

    console.print(f"[green]✓[/] MemForge store ready: {root}")


# ---------------------------------------------------------------------------
# mem save
# ---------------------------------------------------------------------------


@app.command()
def save(
    source: Annotated[
        str, typer.Option("--source", help="Source: auto|claude|cursor|chatgpt|stdin|file")
    ] = "auto",
    file: Annotated[Path | None, typer.Option("--file", help="Input file path")] = None,
    note: Annotated[str | None, typer.Option("--note", help="User note for extractor")] = None,
    type_hint: Annotated[
        str | None,
        typer.Option("--type", help="Hint type: decision|pattern|gotcha|contract|glossary|todo"),
    ] = None,
    scope: Annotated[str, typer.Option("--scope", help="global|project|both")] = "both",
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Extract but don't write drafts")
    ] = False,
) -> None:
    """Extract knowledge from latest session and put drafts in inbox."""
    cfg = _load_cfg(scope)
    store = _primary_store(scope)

    if not store.memory.exists():
        console.print("[red]✗[/] Store not initialised. Run `mem init` first.")
        raise typer.Exit(1)

    transcript = _load_transcript(source, file, cfg)
    if transcript is None:
        console.print("[red]✗[/] No transcript found. Use --file or --source.")
        raise typer.Exit(1)

    console.print(
        f"[dim]Extracting from {transcript.agent} session {transcript.session_id[:8]}…[/]"
    )

    result = asyncio.run(
        run_save(transcript, store, cfg, note=note, hint_type=type_hint, dry_run=dry_run)
    )
    console.print(f"[dim]Backend: {result.backend_used}[/]")

    if dry_run:
        console.print(
            f"[yellow]dry-run:[/] would create {result.units_extracted} draft(s). Nothing written."
        )
        return

    if not result.drafts:
        console.print("[yellow]⚠[/] No knowledge units extracted.")
        return

    console.print(f"\n[green]✓[/] Created {len(result.drafts)} draft(s) in inbox:\n")
    for d in result.drafts:
        tag = " [red](quarantined)[/]" if d.quarantine else ""
        console.print(f"  • [bold]{d.unit.title}[/] ({d.unit.type}){tag}")
        console.print(f"    id: {d.draft_id}")

    if cfg.git.auto_commit:
        short = transcript.session_id[:8]
        store.git_commit(
            f"save(inbox): {len(result.drafts)} draft(s) from {transcript.agent} session {short}"
        )


def _load_transcript(source: str, file: Path | None, cfg: Config) -> Transcript | None:
    from memforge.sources.claude_code import ClaudeCodeSource
    from memforge.sources.codex import CodexSource
    from memforge.sources.cursor import CursorSource
    from memforge.sources.stdin import FileSource, StdinSource

    if source in ("auto", "claude"):
        s_claude = ClaudeCodeSource()
        if s_claude.detect():
            t_claude = s_claude.latest_session()
            if t_claude:
                return t_claude

    if source in ("auto", "cursor"):
        s_cursor = CursorSource()
        if s_cursor.detect():
            t_cursor = s_cursor.latest_session()
            if t_cursor:
                return t_cursor

    if source in ("auto", "chatgpt"):
        s_codex = CodexSource()
        if s_codex.detect():
            t_codex = s_codex.latest_session()
            if t_codex:
                return t_codex

    if source in ("auto", "stdin") and not file:
        s_stdin = StdinSource()
        if s_stdin.detect():
            return s_stdin.latest_session()

    if file or source == "file":
        p = file or Path("-")
        if p == Path("-"):
            return StdinSource().latest_session()
        return FileSource(p).latest_session()

    return None


# ---------------------------------------------------------------------------
# mem scan
# ---------------------------------------------------------------------------


@app.command()
def scan(
    path: Annotated[
        Path, typer.Option("--path", "-p", help="Project root to scan (default: cwd)")
    ] = Path("."),
    note: Annotated[
        str | None, typer.Option("--note", help="Extra context for the extractor")
    ] = None,
    scope: Annotated[str, typer.Option("--scope", help="global|project|both")] = "both",
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
) -> None:
    """Scan project files and extract architectural knowledge into inbox."""
    from memforge.sources.project import ProjectScanner

    cfg = _load_cfg(scope)
    store = _primary_store(scope)

    if not store.memory.exists():
        console.print("[red]✗[/] Store not initialised. Run `mem init` first.")
        raise typer.Exit(1)

    root = path.resolve()
    scanner = ProjectScanner(root)
    if not scanner.detect():
        console.print(f"[red]✗[/] Directory not found: {root}")
        raise typer.Exit(1)

    console.print(f"[dim]Scanning project at {root}…[/]")
    transcript = scanner.latest_session()
    console.print(f"[dim]Collected {len(transcript.messages)} context block(s)[/]")

    result = asyncio.run(
        run_save(transcript, store, cfg, note=note, hint_type=None, dry_run=dry_run)
    )
    console.print(f"[dim]Backend: {result.backend_used}[/]")

    if dry_run:
        console.print(
            f"[yellow]dry-run:[/] would create {result.units_extracted} draft(s). Nothing written."
        )
        return

    if not result.drafts:
        console.print("[yellow]⚠[/] No knowledge units extracted.")
        return

    console.print(f"\n[green]✓[/] Created {len(result.drafts)} draft(s) in inbox:\n")
    for d in result.drafts:
        tag = " [red](quarantined)[/]" if d.quarantine else ""
        console.print(f"  • [bold]{d.unit.title}[/] ({d.unit.type}){tag}")
        console.print(f"    id: {d.draft_id}")

    if cfg.git.auto_commit:
        store.git_commit(f"scan(inbox): {len(result.drafts)} draft(s) from {root.name}")


# ---------------------------------------------------------------------------
# mem review
# ---------------------------------------------------------------------------


@app.command()
def review(
    scope: Annotated[str, typer.Option("--scope")] = "both",
) -> None:
    """Open inbox drafts in $EDITOR."""
    stores = _get_stores(scope)
    draft_paths: list[Path] = []
    for store in stores:
        for draft in store.list_drafts():
            draft_paths.append(store.inbox / f"draft-{draft.draft_id}.md")

    if not draft_paths:
        console.print("[yellow]Inbox is empty.[/]")
        return

    editor = os.environ.get("EDITOR", "notepad" if os.name == "nt" else "vim")
    for path in draft_paths:
        if path.exists():
            subprocess.run([editor, str(path)])


# ---------------------------------------------------------------------------
# mem commit
# ---------------------------------------------------------------------------


@app.command()
def commit(
    draft_ids: Annotated[list[str] | None, typer.Argument()] = None,
    all_: Annotated[bool, typer.Option("--all", help="Commit all inbox drafts")] = False,
    message: Annotated[str | None, typer.Option("--message", "-m")] = None,
    scope: Annotated[str, typer.Option("--scope")] = "both",
) -> None:
    """Promote inbox drafts to daily log."""
    from datetime import datetime

    from memforge.core.models import Article, ArticleFrontMatter
    from memforge.core.storage import slugify

    stores = _get_stores(scope)
    if not stores:
        console.print("[red]✗[/] No store found.")
        raise typer.Exit(1)

    promoted = 0
    for store in stores:
        drafts = (
            store.list_drafts()
            if all_
            else [d for d in store.list_drafts() if d.draft_id in (draft_ids or [])]
        )

        if not drafts:
            continue

        daily_paths = []
        for draft in drafts:
            fm = ArticleFrontMatter(
                id=f"{draft.unit.type[:3]}-{draft.draft_id[:8]}",
                type=draft.unit.type,
                title=draft.unit.title,
                slug=slugify(draft.unit.title),
                tags=draft.unit.tags,
                confidence=draft.unit.confidence,
                source_agent=draft.source_agent,
                source_session_id=draft.source_session_id,
                transcript_hash=draft.transcript_hash,
            )
            article = Article(front_matter=fm, body=draft.unit.body_md)
            path = store.append_to_daily(article)
            daily_paths.append(path)
            store.delete_draft(draft.draft_id)
            console.print(f"  [green]✓[/] {draft.unit.title}")
            promoted += 1

        today = datetime.now(UTC).strftime("%Y-%m-%d")
        git_msg = message or f"commit(daily): promote {len(drafts)} draft(s) to {today}"
        store.git_commit(git_msg, daily_paths)

    console.print(f"\n[green]Promoted {promoted} draft(s) to daily log.[/]")


# ---------------------------------------------------------------------------
# mem discard
# ---------------------------------------------------------------------------


@app.command()
def discard(
    draft_id: Annotated[str, typer.Argument()],
    scope: Annotated[str, typer.Option("--scope")] = "both",
) -> None:
    """Delete a draft from inbox without saving."""
    for store in _get_stores(scope):
        if store.delete_draft(draft_id):
            console.print(f"[green]✓[/] Discarded draft {draft_id}")
            return
    console.print(f"[red]✗[/] Draft not found: {draft_id}")
    raise typer.Exit(1)


# ---------------------------------------------------------------------------
# mem compile
# ---------------------------------------------------------------------------


@app.command()
def compile(
    since: Annotated[str | None, typer.Option("--since", help="ISO date e.g. 2026-04-01")] = None,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    scope: Annotated[str, typer.Option("--scope")] = "both",
) -> None:
    """Merge daily logs into knowledge/ and rebuild index.md."""
    from datetime import datetime

    cfg = _load_cfg(scope)
    compiler = Compiler(
        model=cfg.extractor.model,
        prompt_version=cfg.compiler.prompt_version,
        backend=cfg.extractor.backend,
    )

    since_dt: datetime | None = None
    if since:
        try:
            since_dt = datetime.fromisoformat(since).replace(tzinfo=UTC)
        except ValueError:
            console.print(f"[red]✗[/] Invalid date: {since}")
            raise typer.Exit(1)

    total_created = total_updated = 0
    for store in _get_stores(scope):
        created, updated = compiler.compile(store, since=since_dt, dry_run=dry_run)
        total_created += len(created)
        total_updated += len(updated)

        # Rebuild index
        articles = list(store.iter_articles())
        pinned = store.pinned_slugs()
        index_content = build_index(articles, pinned, cfg.index.max_tokens, cfg.index.order)
        if not dry_run:
            write_index(store.knowledge, index_content)

        if cfg.git.auto_commit and not dry_run:
            store.git_commit(f"compile(knowledge): {len(created)} new, {len(updated)} updated")

    if dry_run:
        console.print("[yellow]dry-run: no changes written.[/]")
    else:
        console.print(
            f"[green]✓[/] Compiled: {total_created} new, {total_updated} updated articles."
        )


# ---------------------------------------------------------------------------
# mem recall
# ---------------------------------------------------------------------------


@app.command()
def recall(
    query: Annotated[str, typer.Argument()],
    top: Annotated[int, typer.Option("--top", "-n")] = 5,
    type_filter: Annotated[str | None, typer.Option("--type")] = None,
    scope: Annotated[str, typer.Option("--scope")] = "both",
) -> None:
    """Search the knowledge base."""
    cfg = _load_cfg(scope)
    all_results = []

    for store in _get_stores(scope):
        results = retrieve(
            query=query,
            index_path=store.index_file,
            articles_root=store.memory,
            top_k=top,
            max_snippet_chars=cfg.retriever.max_snippet_chars,
            record_type=type_filter,
        )
        all_results.extend(results)

    if not all_results:
        console.print("[yellow]No results found.[/]")
        return

    # Sort by score desc, dedupe by slug
    seen: set[str] = set()
    deduped = []
    for r in sorted(all_results, key=lambda x: x.score, reverse=True):
        if r.slug not in seen:
            seen.add(r.slug)
            deduped.append(r)

    for i, r in enumerate(deduped[:top], 1):
        console.print(f"\n[bold]{i}. {r.title}[/] [dim]({r.record_type})[/]")
        console.print(f"   slug: {r.slug}  score: {r.score:.2f}")
        if r.snippet:
            console.print(f"   [dim]{r.snippet[:200].replace(chr(10), ' ')}[/]")


# ---------------------------------------------------------------------------
# mem context
# ---------------------------------------------------------------------------


@app.command()
def context(
    query: Annotated[str | None, typer.Argument(help="Optional search query")] = None,
    top: Annotated[int, typer.Option("--top", "-n", help="Max search results to include")] = 5,
    no_pinned: Annotated[bool, typer.Option("--no-pinned", help="Skip pinned articles")] = False,
    no_index: Annotated[bool, typer.Option("--no-index", help="Skip knowledge index")] = False,
    full: Annotated[
        bool, typer.Option("--full", help="Include full article bodies, not just snippets")
    ] = False,
    scope: Annotated[str, typer.Option("--scope")] = "both",
) -> None:
    """Dump knowledge into stdout for LLM context injection.

    In Claude Code run:  ! mem context
    With search query:   ! mem context "authentication flow"
    Full articles:       ! mem context "auth" --full
    """
    cfg = _load_cfg(scope)
    stores = _get_stores(scope)

    if not stores:
        print("<!-- MemForge: No store found. Run: mem init -->")
        raise typer.Exit(1)

    parts: list[str] = []
    parts.append("<!-- MemForge Knowledge Context -->")

    # ── Pinned articles ──────────────────────────────────────────────────────
    if not no_pinned:
        pinned_sections: list[str] = []
        seen_pinned: set[str] = set()
        for store in stores:
            for slug in store.pinned_slugs():
                if slug in seen_pinned:
                    continue
                seen_pinned.add(slug)
                article = store.find_article_by_slug(slug)
                if article:
                    pinned_sections.append(
                        f"### {article.front_matter.title}"
                        f" ({article.front_matter.type})\n\n{article.body.strip()}"
                    )
        if pinned_sections:
            parts.append("## Pinned Knowledge\n\n" + "\n\n---\n\n".join(pinned_sections))

    # ── Knowledge index ──────────────────────────────────────────────────────
    if not no_index:
        for store in stores:
            if store.index_file.exists():
                index_text = store.index_file.read_text("utf-8").strip()
                if index_text:
                    parts.append(f"## Knowledge Index\n\n{index_text}")
                break

    # ── Search results ───────────────────────────────────────────────────────
    if query:
        result_sections: list[str] = []
        seen_slugs: set[str] = set()
        for store in stores:
            results = retrieve(
                query=query,
                index_path=store.index_file,
                articles_root=store.memory,
                top_k=top,
                max_snippet_chars=cfg.retriever.max_snippet_chars,
            )
            for r in results:
                if r.slug in seen_slugs:
                    continue
                seen_slugs.add(r.slug)
                if full:
                    article = store.find_article_by_slug(r.slug)
                    body = article.body.strip() if article else r.snippet
                else:
                    body = r.snippet
                if body:
                    result_sections.append(f"### {r.title} ({r.record_type})\n\n{body}")
                else:
                    result_sections.append(f"### {r.title} ({r.record_type})")
        if result_sections:
            parts.append(f'## Search Results: "{query}"\n\n' + "\n\n---\n\n".join(result_sections))
        elif not any(p.startswith("## ") for p in parts):
            parts.append(f'<!-- No results found for: "{query}" -->')

    # ── Nothing to show ──────────────────────────────────────────────────────
    if len(parts) == 1:
        parts.append(
            "<!-- MemForge: Knowledge base is empty."
            " Run: mem scan && mem commit --all && mem compile -->"
        )

    print("\n\n".join(parts))


# ---------------------------------------------------------------------------
# mem load
# ---------------------------------------------------------------------------


@app.command()
def load(
    slug: Annotated[str, typer.Argument()],
    scope: Annotated[str, typer.Option("--scope")] = "both",
) -> None:
    """Print an article to stdout."""
    for store in _get_stores(scope):
        article = store.find_article_by_slug(slug)
        if article:
            console.print(article.body)
            return
    console.print(f"[red]✗[/] Article not found: {slug}")
    raise typer.Exit(1)


# ---------------------------------------------------------------------------
# mem pin / unpin
# ---------------------------------------------------------------------------


@app.command()
def pin(
    slug: Annotated[str, typer.Argument()],
    scope: Annotated[str, typer.Option("--scope")] = "project",
) -> None:
    """Add a slug to pinned.md."""
    store = _primary_store(scope)
    store.pin(slug)
    console.print(f"[green]✓[/] Pinned: {slug}")


@app.command()
def unpin(
    slug: Annotated[str, typer.Argument()],
    scope: Annotated[str, typer.Option("--scope")] = "project",
) -> None:
    """Remove a slug from pinned.md."""
    store = _primary_store(scope)
    store.unpin(slug)
    console.print(f"[green]✓[/] Unpinned: {slug}")


# ---------------------------------------------------------------------------
# mem forget / restore
# ---------------------------------------------------------------------------


@app.command()
def forget(
    article_id: Annotated[str, typer.Argument()],
    scope: Annotated[str, typer.Option("--scope")] = "both",
) -> None:
    """Soft-delete an article (move to archive/)."""
    import shutil

    for store in _get_stores(scope):
        for article_path in store.knowledge.rglob("*.md"):
            article = store.read_article(article_path)
            if article and (article.slug == article_id or article.id == article_id):
                dest = store.archive / article_path.name
                shutil.move(str(article_path), str(dest))
                store.git_commit(f"forget(archive): {article_id}")
                console.print(f"[green]✓[/] Archived: {article_id}")
                return
    console.print(f"[red]✗[/] Article not found: {article_id}")
    raise typer.Exit(1)


@app.command()
def restore(
    article_id: Annotated[str, typer.Argument()],
    scope: Annotated[str, typer.Option("--scope")] = "both",
) -> None:
    """Restore an article from archive."""
    import shutil

    for store in _get_stores(scope):
        for archived in store.archive.glob("*.md"):
            article = store.read_article(archived)
            if article and (article.slug == article_id or article.id == article_id):
                dest = store.article_dir(article.front_matter.type) / archived.name
                shutil.move(str(archived), str(dest))
                store.git_commit(f"restore: {article_id}")
                console.print(f"[green]✓[/] Restored: {article_id}")
                return
    console.print(f"[red]✗[/] Article not found in archive: {article_id}")
    raise typer.Exit(1)


# ---------------------------------------------------------------------------
# mem stats
# ---------------------------------------------------------------------------


@app.command()
def stats(
    scope: Annotated[str, typer.Option("--scope")] = "both",
    days: Annotated[int, typer.Option("--days", help="LLM cost window in days")] = 30,
) -> None:
    """Show knowledge base statistics."""
    from collections import Counter

    from memforge.core.llm_log import read_log, summarise

    for store in _get_stores(scope):
        articles = list(store.iter_articles())
        type_counts = Counter(a.front_matter.type for a in articles)
        tag_counts: Counter[str] = Counter()
        for a in articles:
            tag_counts.update(a.front_matter.tags)
        drafts = store.list_drafts()
        daily_files = list(store.daily.glob("*.md"))

        # Drift: daily entries not yet compiled
        compiled_slugs = {a.front_matter.slug for a in articles}
        uncompiled = 0
        for f in daily_files:
            text = f.read_text("utf-8")
            for block in text.split("\n\n---\n\n"):
                block = block.strip()
                if block:
                    import frontmatter as fm_mod

                    try:
                        post = fm_mod.loads(block)
                        from memforge.core.storage import slugify

                        slug = post.get("slug") or slugify(post.get("title", ""))
                        if slug not in compiled_slugs:
                            uncompiled += 1
                    except Exception:
                        pass

        # LLM cost
        llm_entries = read_log(store.logs)
        llm_summary = summarise(llm_entries, since_days=days)

        # KB size
        kb_size = sum(p.stat().st_size for p in store.knowledge.rglob("*.md")) // 1024

        table = Table(title=f"MemForge Stats — {store.root}")
        table.add_column("Metric", style="dim")
        table.add_column("Value", justify="right")

        table.add_row("Total articles", str(len(articles)))
        table.add_row("Inbox drafts", str(len(drafts)))
        table.add_row("Pinned", str(len(store.pinned_slugs())))
        table.add_row("Knowledge base size", f"{kb_size} KB")
        table.add_row("Daily logs", str(len(daily_files)))
        table.add_row("Uncompiled entries (drift)", str(uncompiled))
        table.add_row("", "")
        for rtype, count in sorted(type_counts.items()):
            table.add_row(f"  {rtype}", str(count))
        table.add_row("", "")
        top_tags = tag_counts.most_common(5)
        if top_tags:
            table.add_row("Top tags", ", ".join(f"{t}({c})" for t, c in top_tags))
        table.add_row("", "")
        table.add_row(f"LLM calls (last {days}d)", str(llm_summary["calls"]))
        table.add_row(f"LLM tokens (last {days}d)", f"{llm_summary['tokens']:,}")
        table.add_row(f"LLM cost (last {days}d)", f"${llm_summary['cost_usd']:.4f}")

        console.print(table)


# ---------------------------------------------------------------------------
# mem diff
# ---------------------------------------------------------------------------


@app.command("diff")
def diff_cmd(
    since: Annotated[str | None, typer.Option("--since", help="ISO date e.g. 2026-04-01")] = None,
    scope: Annotated[str, typer.Option("--scope")] = "both",
) -> None:
    """Show git diff of knowledge/ since a date or last compile."""
    import subprocess

    for store in _get_stores(scope):
        if not (store.root / ".git").exists():
            console.print(f"[yellow]⚠[/] No git repo at {store.root}")
            continue

        cmd = ["git", "-C", str(store.root), "log", "--oneline", "-10", "--", "memory/knowledge/"]
        if since:
            cmd += [f"--after={since}"]

        console.print(f"\n[bold]Commits — {store.root}[/]")
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.stdout.strip():
            console.print(result.stdout.strip())
        else:
            console.print("[dim]No commits found.[/]")

        if since:
            diff_cmd_args = [
                "git",
                "-C",
                str(store.root),
                "diff",
                f"HEAD@{{'{since}'}}",
                "--",
                "memory/knowledge/",
            ]
        else:
            diff_cmd_args = [
                "git",
                "-C",
                str(store.root),
                "diff",
                "HEAD~1",
                "HEAD",
                "--",
                "memory/knowledge/",
            ]

        diff = subprocess.run(diff_cmd_args, capture_output=True, text=True)
        if diff.stdout.strip():
            console.print(diff.stdout[:4000])
        else:
            console.print("[dim]No diff to show.[/]")


# ---------------------------------------------------------------------------
# mem lint
# ---------------------------------------------------------------------------


@app.command()
def lint(
    scope: Annotated[str, typer.Option("--scope")] = "both",
) -> None:
    """Check knowledge base integrity."""
    issues = 0
    for store in _get_stores(scope):
        for article_path in store.knowledge.rglob("*.md"):
            if article_path.name == "index.md":
                continue
            article = store.read_article(article_path)
            if article is None:
                console.print(f"[red]✗[/] Cannot parse: {article_path}")
                issues += 1
            elif not article.body.strip():
                console.print(f"[yellow]⚠[/] Empty body: {article_path}")
                issues += 1

    if issues == 0:
        console.print("[green]✓[/] No issues found.")
    else:
        console.print(f"[red]{issues} issue(s) found.[/]")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# mem revert
# ---------------------------------------------------------------------------


@app.command()
def revert(
    sha: Annotated[str, typer.Argument(help="Git SHA to revert")],
    scope: Annotated[str, typer.Option("--scope")] = "project",
) -> None:
    """Revert a git commit in the store."""
    store = _primary_store(scope)
    try:
        store.git_revert(sha)
        console.print(f"[green]✓[/] Reverted {sha}")
    except Exception as e:
        console.print(f"[red]✗[/] Revert failed: {e}")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# mem doctor
# ---------------------------------------------------------------------------


@app.command()
def doctor() -> None:
    """Diagnose MemForge installation."""
    import shutil

    ok = True

    # ── LLM backend ──────────────────────────────────────────────────────────
    has_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    has_cli = shutil.which("claude") is not None

    if has_key:
        console.print("[green]✓[/] ANTHROPIC_API_KEY is set → backend: [bold]api[/]")
    elif has_cli:
        import subprocess

        ver = subprocess.run(["claude", "--version"], capture_output=True, text=True)
        ver_str = ver.stdout.strip()
        console.print(
            f"[green]✓[/] Claude Code CLI found ({ver_str}) → backend: [bold]cli[/] (subscription)"
        )
    else:
        console.print(
            "[red]✗[/] No LLM backend available.\n"
            "   Set ANTHROPIC_API_KEY  OR  install Claude Code: https://claude.ai/code"
        )
        ok = False

    # ── Stores ───────────────────────────────────────────────────────────────
    global_store = Store(GLOBAL_ROOT)
    if global_store.memory.exists():
        console.print(f"[green]✓[/] Global store: {GLOBAL_ROOT}")
    else:
        console.print("[yellow]⚠[/] Global store not initialised — run: mem init --global")

    project_store = Store(Path.cwd() / ".memforge")
    if project_store.memory.exists():
        console.print(f"[green]✓[/] Project store: {project_store.root}")
    else:
        console.print("[dim]-  No project store in cwd (optional)[/]")

    # ── Dependencies ─────────────────────────────────────────────────────────
    try:
        import git  # noqa: F401

        console.print("[green]✓[/] GitPython available")
    except ImportError:
        console.print("[red]✗[/] GitPython not installed")
        ok = False

    try:
        import anthropic  # noqa: F401

        console.print("[green]✓[/] anthropic SDK available")
    except ImportError:
        if has_key:
            console.print("[red]✗[/] anthropic SDK missing — pip install anthropic")
            ok = False
        else:
            console.print("[dim]-  anthropic SDK not installed (not needed for CLI backend)[/]")

    # ── detect-secrets (optional) ─────────────────────────────────────────────
    try:
        import detect_secrets  # noqa: F401

        console.print("[green]✓[/] detect-secrets available (deep scrubbing enabled)")
    except ImportError:
        console.print(
            "[dim]-  detect-secrets not installed (optional, pip install memforge[optional])[/]"
        )

    if ok:
        console.print("\n[green]All checks passed.[/]")
    else:
        console.print("\n[red]Some checks failed.[/]")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# mem export
# ---------------------------------------------------------------------------


@app.command("export")
def export_cmd(
    to: Annotated[str, typer.Option("--to", help="Export format: obsidian")] = "obsidian",
    out: Annotated[Path, typer.Option("--out", help="Output directory")] = Path(
        "./memforge-export"
    ),
    scope: Annotated[str, typer.Option("--scope")] = "both",
) -> None:
    """Export knowledge base to an external format."""
    if to != "obsidian":
        console.print(f"[red]✗[/] Unsupported format: {to}. Only 'obsidian' is supported.")
        raise typer.Exit(1)

    from memforge.exporters.obsidian import export_to_obsidian

    stores = _get_stores(scope)
    if not stores:
        console.print("[red]✗[/] No store found.")
        raise typer.Exit(1)

    out.mkdir(parents=True, exist_ok=True)
    total = 0
    for store in stores:
        count = export_to_obsidian(store, out)
        total += count

    console.print(f"[green]✓[/] Exported {total} article(s) to {out}")


# ---------------------------------------------------------------------------
# mem ui
# ---------------------------------------------------------------------------


@app.command()
def ui(
    port: Annotated[int, typer.Option("--port", "-p")] = 7777,
    open_browser: Annotated[bool, typer.Option("--open")] = False,
    token: Annotated[str | None, typer.Option("--token")] = None,
) -> None:
    """Start the MemForge Web UI."""
    import webbrowser

    from memforge.web.app import run

    url = f"http://127.0.0.1:{port}"
    console.print(f"[green]✓[/] Starting MemForge UI at [link={url}]{url}[/link]")

    if open_browser:
        webbrowser.open(url)

    run(host="127.0.0.1", port=port, token=token)
