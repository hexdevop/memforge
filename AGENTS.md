# MemForge — Agent & Developer Reference

This file describes the internal architecture of MemForge for agents and contributors working on the codebase.

---

## Repository layout

```
memforge/
├── cli/
│   └── main.py              # All 19 Typer commands. Entry point: mem
├── config.py                # Pydantic config models, load_config(), save_config()
├── core/
│   ├── models.py            # Transcript, ExtractedUnit, Article, Draft, IndexEntry
│   ├── pipeline.py          # run_save(): extract → scrub → write drafts
│   ├── compiler.py          # Compiler.compile(): daily → knowledge (LLM merge)
│   ├── indexer.py           # build_index(): deterministic index.md builder
│   ├── retriever.py         # retrieve(): BM25 + substring search over index.md
│   ├── storage.py           # Store: all file I/O + GitPython operations
│   └── llm_log.py           # record_call() context manager, logs/llm.jsonl writer
├── extractors/
│   └── claude_sdk.py        # ClaudeExtractor: async, Anthropic API, logs cost
├── scrubber/
│   └── regex.py             # scrub(): regex pass + optional detect-secrets pass
├── sources/
│   ├── claude_code.py       # ClaudeCodeSource: reads ~/.claude/projects/*/session.jsonl
│   ├── cursor.py            # CursorSource: reads state.vscdb SQLite
│   ├── codex.py             # CodexSource: reads ChatGPT conversations.json export
│   └── stdin.py             # StdinSource, FileSource
├── exporters/
│   └── obsidian.py          # export_to_obsidian(): writes Obsidian-compatible vault
├── prompts/
│   ├── extractor.v1.md      # System prompt for extraction (returns JSON array)
│   └── compiler.v1.md       # System prompt for merge (returns markdown body only)
└── web/
    ├── app.py               # create_app(token) → FastAPI, optional TokenMiddleware
    ├── routes/
    │   ├── views.py         # Page handlers (SSR, Starlette 1.0 TemplateResponse API)
    │   └── api.py           # HTMX API endpoints (HTMLResponse)
    └── templates/           # Jinja2 templates (base, dashboard, inbox, knowledge,
                             #   article, daily, stats, editor, settings)
```

---

## Key data models (`core/models.py`)

```python
Transcript          session_id, agent, messages: list[Message], cwd, started_at
                    .hash → sha256 of full text  .text → joined messages

ExtractedUnit       type, title, body_md, tags, confidence, refs, links
Article             front_matter: ArticleFrontMatter, body: str
                    .slug → front_matter.slug  .id → front_matter.id

Draft               unit, draft_id (uuid4), transcript_hash, source_agent,
                    source_session_id, created_at, quarantine

ArticleFrontMatter  id, type, title, slug, scope, tags, created, updated,
                    status, confidence, source_agent, source_session_id,
                    transcript_hash, refs, links, supersedes, fingerprint, quarantine
```

---

## Store layout (`core/storage.py`)

```
Store(root)
  .memory/
    inbox/          draft-<uuid>.md
    daily/          YYYY-MM-DD.md       (separator: \n\n---\n\n)
    knowledge/
      index.md
      decisions/    <slug>.md
      concepts/     <slug>.md
      gotchas/      <slug>.md
      contracts/    <slug>.md
      glossary/     <slug>.md
      todo-knowledge/ <slug>.md
    archive/        moved-here by forget()
    pinned.md       list of slugs
  logs/
    memforge.log
    llm.jsonl       one JSON object per line: {timestamp, operation, model,
                    prompt_version, input_tokens, output_tokens, cost_usd, latency_ms}
  .git/             (global store only — project store lives in the project's git)
  .gitignore        excludes: logs/, memory/sessions/, *.quarantine
```

---

## Save pipeline (`core/pipeline.py`)

```
run_save(transcript, store, config) → SaveResult
  1. transcript.hash          → dedup key
  2. ClaudeExtractor.extract() → list[ExtractedUnit]   (LLM, logged to llm.jsonl)
  3. scrub(unit.body_md)      → ScrubResult            (regex + optional detect-secrets)
  4. store.write_draft()      → Draft                  (inbox/draft-<uuid>.md)
  5. quarantine=True if scrubber modified the text
```

---

## Compile pipeline (`core/compiler.py`)

```
Compiler.compile(store, since, dry_run) → (created, updated)
  1. Read all daily/*.md, split on \n\n---\n\n, parse front-matter
  2. Group entries by slug
  3. For each slug:
     - existing article → LLM merge (logged to llm.jsonl)
     - no existing article → create from combined entries
  4. write_article() for each result
```

---

## Retrieval (`core/retriever.py`)

```
retrieve(query, index_path, articles_root, top_k) → list[SearchResult]
  1. Parse index.md with regex: "- slug (type, confidence) — Title [tags: …]"
  2. BM25 score all entries against query tokens
  3. If all scores == 0: substring fallback
  4. Top-k → read article files → extract snippet
```

---

## Web UI notes

- **Starlette 1.0 API:** `TemplateResponse(request, name, context)` — request is first positional arg, NOT in context dict. The `_tr()` helper in `views.py` enforces this.
- **HTMX responses:** API endpoints return `HTMLResponse` fragments, not JSON. HTMX swaps them inline.
- **Settings POST:** validates YAML against `Config.model_validate()` before writing.
- **Security:** `PUT /api/article` checks that the target path starts with a known store root.

---

## Adding a new source adapter

1. Create `memforge/sources/<name>.py`
2. Implement the `Source` protocol:
   ```python
   class MySource:
       name = "my-source"
       def detect(self) -> bool: ...
       def latest_session(self) -> Transcript | None: ...
       def by_id(self, session_id: str) -> Transcript: ...
   ```
3. Import and probe in `cli/main.py → _load_transcript()` under `source in ("auto", "my-source")`.

---

## Adding a new CLI command

All commands are Typer functions in `cli/main.py`. Pattern:

```python
@app.command()
def my_command(
    scope: Annotated[str, typer.Option("--scope")] = "both",
) -> None:
    """One-line docstring shown in mem --help."""
    for store in _get_stores(scope):
        ...
```

---

## LLM cost tracking

Every Anthropic API call in the extractor and compiler is wrapped with:

```python
with record_call(log_dir, model, prompt_version, operation) as meta:
    response = client.messages.create(...)
    meta["input_tokens"] = response.usage.input_tokens
    meta["output_tokens"] = response.usage.output_tokens
```

`record_call` appends to `logs/llm.jsonl` on exit, computing `cost_usd` from the model pricing table in `core/llm_log.py`. `mem stats` reads this log to show cost summaries.

---

## Prompt versioning

Prompts live in `memforge/prompts/extractor.v1.md` and `compiler.v1.md`. The version is stored in `config.yaml` (`extractor.prompt_version`, `compiler.prompt_version`). When changing a prompt, create a new version file (`extractor.v2.md`) and bump the config default — old files are never deleted for reproducibility.

---

## Testing

```bash
pytest tests/unit/        # 28 fast tests, no API calls
pytest tests/integration/ # 29 tests: pipeline mocks LLM, web uses TestClient
pytest                    # all 57
```

Integration tests mock `ClaudeExtractor` with `unittest.mock.patch` so no API key is needed. Compiler tests inject a mock `anthropic.Anthropic()` client directly on the `Compiler` instance.

---

## Git commit conventions

```
save(inbox): N drafts from <agent> session <short-id>
commit(daily): promote N drafts to YYYY-MM-DD
compile(knowledge): N new, N updated
forget(archive): <slug>
restore: <slug>
edit: <filename>
```
