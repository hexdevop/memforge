# MemForge ⚒

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

**Local external memory for LLM agents.** Turns your AI coding sessions into a structured, versioned, human-readable knowledge base in plain markdown.

> **Philosophy:** no hidden magic, no auto-saves, no timers. Every change to your knowledge base is the result of an explicit command you ran. Human-in-the-loop at every step.

---

## Agent support

| Agent | Status |
|---|---|
| **Claude Code** | Fully supported and verified |
| Cursor | Not yet verified — may work, treat as experimental |
| ChatGPT (export) | Not yet verified — may work, treat as experimental |

All other agents and sources are currently unverified. Contributions and test reports are welcome.

---

## Quick start

```bash
# Install
pip install memforge           # or: pipx install memforge

# Initialise global knowledge base
mem init --global

# After a productive Claude Code session:
mem save                       # extract drafts → inbox/
mem review                     # open drafts in $EDITOR (optional)
mem commit --all               # promote drafts → daily log + git commit

# Weekly: merge daily logs into permanent knowledge
mem compile

# Load your knowledge into Claude's context (run inside Claude Code with !)
! mem context                            # pinned articles + full index
! mem context "authentication flow"     # search + relevant snippets
! mem context "auth" --full             # search + full article bodies

# Search and inspect from terminal
mem recall "how did we handle rate limiting"
mem load rate-limiting-pattern          # print article to stdout

# Launch the web UI
mem ui --open
```

---

## Installation

**Requirements:** Python 3.11+

```bash
pip install memforge

# Option A: API key (paid, any Claude model)
export ANTHROPIC_API_KEY=sk-ant-...

# Option B: Claude Code CLI subscription (free if you have Claude Code)
# Just make sure `claude` is in your PATH — MemForge detects it automatically

mem init --global              # creates ~/.memforge/ with git repo
mem init                       # creates .memforge/ in current project (optional)
mem doctor                     # verify everything is set up correctly
```

---

## The knowledge flow

```
Claude Code session
        ↓  mem save
    inbox/draft-<uuid>.md       ← preview, edit, or discard
        ↓  mem commit
    daily/YYYY-MM-DD.md         ← git commit
        ↓  mem compile
    knowledge/<type>/<slug>.md  ← git commit
        ↓
    index.md                    ← BM25 retrieval
        ↓  mem context / mem recall / mem load
    LLM context
```

All files are plain markdown with YAML front-matter. No database, no binary formats, no cloud.

---

## Loading memory into Claude

The key command is `mem context`. Run it with `!` inside Claude Code to inject knowledge directly into the conversation:

```
# In Claude Code chat:
! mem context                          # everything: pinned + index
! mem context "rate limiting"          # targeted: search + snippets
! mem context "rate limiting" --full   # targeted: search + full bodies
! mem context --no-index               # pinned articles only
```

The output is clean markdown — no ANSI codes — ready to be read by the model.

You can also pin the most important articles so they always appear first:

```bash
mem pin my-architecture-decision
mem pin api-contract-v2
```

---

## Commands

### Core workflow

| Command | Description |
|---|---|
| `mem init [--global] [--no-git]` | Initialise store. Creates directory structure and a git repo. |
| `mem save [--source auto\|claude\|cursor\|chatgpt\|stdin\|file] [--file PATH] [--note "…"] [--type TYPE]` | Extract knowledge from latest session → `inbox/`. Nothing is committed to daily yet. |
| `mem review` | Open inbox drafts in `$EDITOR`. |
| `mem commit [DRAFT_ID…] [--all] [-m MESSAGE]` | Promote drafts from inbox → `daily/YYYY-MM-DD.md`. Makes a git commit. |
| `mem discard <DRAFT_ID>` | Delete a draft without saving. |
| `mem compile [--since DATE] [--dry-run]` | Merge daily logs → `knowledge/`. Rebuilds `index.md`. Makes a git commit. |

### Context injection

| Command | Description |
|---|---|
| `mem context [QUERY] [--top N] [--full] [--no-pinned] [--no-index]` | **Dump knowledge to stdout for LLM context.** Run with `!` in Claude Code. |
| `mem recall "<query>" [--top N] [--type TYPE]` | BM25 search — shows titles and snippets in the terminal. |
| `mem load <slug>` | Print a single article to stdout. |

### Knowledge management

| Command | Description |
|---|---|
| `mem pin <slug>` / `mem unpin <slug>` | Mark articles as always-load in `pinned.md`. |
| `mem forget <slug>` | Soft-delete: moves article to `archive/`, git commit. |
| `mem restore <slug>` | Restore from archive. |
| `mem revert <git-sha>` | Revert a knowledge-base commit via `git revert`. |
| `mem diff [--since DATE]` | Show git diff of `knowledge/` since a date. |
| `mem export --to obsidian [--out PATH]` | Export to Obsidian vault (wiki-links, YAML properties, tag syntax). |

### Diagnostics

| Command | Description |
|---|---|
| `mem stats [--days N]` | Terminal dashboard: article counts, drift, LLM cost summary. |
| `mem lint` | Check integrity: unparseable files, empty bodies. |
| `mem doctor` | Check API key / CLI, git, dependencies. |
| `mem ui [--port 7777] [--open] [--token TOKEN]` | Launch local web UI. |

---

## Sources

`mem save --source auto` tries sources in order: Claude Code → Cursor → ChatGPT → stdin.

| Source | What it reads | Status |
|---|---|---|
| `claude` | `~/.claude/projects/<hash>/*.jsonl` — latest Claude Code session | **Verified** |
| `cursor` | `%APPDATA%/Cursor/User/workspaceStorage/*/state.vscdb` — Cursor chat SQLite | Not yet verified |
| `chatgpt` | `~/Downloads/conversations.json` — ChatGPT export | Not yet verified |
| `stdin` | Pipe or `--file PATH` — any markdown/plain text | **Verified** |

---

## Record types

| Type | When to use |
|---|---|
| `decision` | An architectural or design choice with rationale and trade-offs |
| `pattern` | A reusable solution approach |
| `gotcha` | A trap, unexpected behaviour, or lesson learned the hard way |
| `contract` | An API, DB schema, or event contract |
| `glossary` | Domain term definition |
| `todo` | An open question or pending investigation |

---

## Store structure

```
~/.memforge/                   # global (personal)
├── config.yaml
├── memory/
│   ├── inbox/                 # drafts awaiting review
│   ├── daily/                 # YYYY-MM-DD.md logs
│   ├── knowledge/             # permanent articles
│   │   ├── index.md
│   │   ├── decisions/
│   │   ├── concepts/
│   │   ├── gotchas/
│   │   ├── contracts/
│   │   ├── glossary/
│   │   └── todo-knowledge/
│   ├── archive/               # soft-deleted
│   └── pinned.md
└── logs/
    ├── memforge.log
    └── llm.jsonl              # per-call cost tracking

<repo>/.memforge/              # project overlay (same structure)
```

---

## Configuration

`~/.memforge/config.yaml` is merged with `<repo>/.memforge/config.yaml` (project wins on conflict). Edit via `mem ui` → Settings or directly:

```yaml
extractor:
  model: claude-sonnet-4-6     # model used for extraction + compilation
  backend: auto                # auto | api | cli
  language: en                 # language for extracted text (en, ru, …)
  max_units_per_save: 10

scrubber:
  regex: default               # default | strict | off
  detect_secrets: false        # optional deep scan (pip install memforge[optional])
  corporate_email_domains: []

index:
  max_tokens: 4000             # index.md size cap

git:
  auto_commit: true            # commit on every write
```

---

## Web UI

```bash
mem ui --open                  # opens http://127.0.0.1:7777
```

Pages: **Dashboard** · **Inbox** (Accept/Discard drafts) · **Knowledge** (search, filter) · **Daily** (timeline) · **Stats** (LLM cost, drift) · **Settings** (config editor)

The UI is read/write — you can accept drafts, edit articles, pin/forget, all with git commits happening automatically.

---

## Secret scrubbing

Every draft passes through the scrubber before hitting disk:

- **Default mode:** AWS/GCP/Azure keys, GitHub/GitLab PATs, private keys, JWTs, generic `secret=…` patterns.
- **Strict mode** (`scrubber.regex: strict`): also redacts emails, private IPs, phone numbers.
- **detect-secrets** (optional): `pip install memforge[optional]` enables a deeper scan as a second pass.

Redacted values become `[REDACTED:<kind>]`. Drafts that were modified are flagged `quarantine: true` and excluded from git commits by default.

---

## Development

```bash
git clone <repo>
cd mem_forge
pip install -e ".[dev]"
pytest                         # 57 tests
ruff check .
```

---

## License

MIT — see [LICENSE](LICENSE).
