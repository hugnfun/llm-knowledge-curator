# LLM Knowledge Curator (LLKC)

An event-driven, visualized content factory for managing an Obsidian-based knowledge base. Collects raw materials from multiple sources, classifies them via LLM, manages the creative pipeline from daily thinking to draft generation, and provides a Web GUI for full visibility.

## Architecture

```
Connectors (Obsidian Inbox) --> SQLite State Layer --> Pipeline Orchestrator --> FastAPI API --> Web GUI
                                    |
                                    +--> Obsidian Vault (archive + read surface)
```

Four-layer design per the SOP:

| Layer | Component | Tech |
|-------|-----------|------|
| Collection | `llkc/connectors/` | Python (Obsidian vault scanner) |
| Data | `llkc/db.py` | SQLite (items, pipeline_runs, events, daily_thinking, drafts) |
| Orchestration | `llkc/pipeline.py`, `llkc/stages/` | Python event-driven state machine |
| Presentation | `llkc/api/server.py`, `web/` | FastAPI + vanilla JS SPA |

## Project Structure

```
llm-knowledge-curator/
├── llkc/                        # Python package (the runtime)
│   ├── config.py                # Central config from env vars
│   ├── db.py                    # SQLite schema + CRUD
│   ├── models.py                # Enums + dataclasses (ItemSource, Verdict, PipelineStage, etc.)
│   ├── pipeline.py              # Event-driven pipeline orchestrator
│   ├── llm_client.py            # Shared LLM API client
│   ├── vault.py                 # Shared vault path/content utilities
│   ├── migrate.py               # Import existing verdicts.jsonl into SQLite
│   ├── connectors/
│   │   ├── obsidian_inbox.py    # Inbox scanner (migrated from build_index.py)
│   │   ├── lark_listener.py     # Feishu message -> pending URL queue
│   │   ├── pending_urls.py      # Queue -> URL ingest (cron)
│   │   └── url_ingest.py        # WeChat/Douyin/XHS/web -> Markdown
│   ├── stages/
│   │   ├── parser.py            # LLM classifier (migrated from parser_runner.py)
│   │   ├── write_back.py        # Vault writer (migrated from write_back.py)
│   │   ├── daily_thinking.py    # Daily thinking generator (migrated from daily_thinking.py)
│   │   └── writer.py            # Draft generator (migrated from writer_agent.py)
│   └── api/
│       └── server.py            # FastAPI server (25 endpoints + static file serving)
├── scripts/
│   ├── cli.py                   # Unified CLI entry point
│   ├── cron_incremental_v2.sh   # Cron script (uses llkc package)
│   ├── build_index.py           # Original scripts (kept for backward compat)
│   ├── parser_runner.py
│   ├── write_back.py
│   ├── daily_thinking.py
│   ├── writer_agent.py
│   └── mcp_server.py
├── web/                         # Web GUI (served by FastAPI)
│   ├── index.html
│   ├── css/app.css
│   └── js/app.js
├── prompts/                     # LLM prompts
│   ├── parser_v0.2.md
│   └── writer_v0.1.md
├── output/                      # Runtime data (gitignored)
├── obsidian-plugin/             # Obsidian plugin (kept as-is)
├── .env.example                 # Configuration template
└── .gitignore
```

## Event-Driven State Machine

The pipeline is driven by events. Each stage emits events that trigger the next:

```
RawItem.Created --> Item.Classified --> Item.Pooled --> DailyThinking.Requested
  --> UserThinking.Submitted --> Draft.Generated --> Draft.Selected
  --> Draft.Polished --> Asset.Ready --> Post.Published
```

All events are logged in the `events` table with full payload, enabling audit trails and debugging.

## Pipeline Stages

| Stage | Description | Module |
|-------|-------------|--------|
| collect | Scan Obsidian inbox (Clippings, Telegram, X-Bookmarks) | `connectors/obsidian_inbox.py` |
| classify | LLM classifies each item as seed/asset/archive | `stages/parser.py` |
| pool | Write classified items to vault pools | `stages/write_back.py` |
| daily_thinking | Generate daily thinking doc with 5 random seeds | `stages/daily_thinking.py` |
| user_thinking | User writes free-form thoughts | API PATCH endpoint |
| draft_generate | LLM generates 4 angle draft candidates | `stages/writer.py` |
| draft_select | User selects a draft for polishing | API PATCH endpoint |
| draft_polish | De-AI-ify selected draft | (future) |
| asset_produce | Generate images, podcast, video | (future) |
| publish | Publish to platforms | (future) |

## Quick Start

### 1. Configure

```bash
cp .env.example .env
# Edit .env with your API key and vault path
```

### 2. Initialize database + migrate existing data

```bash
python3 scripts/cli.py migrate
```

This imports existing `output/verdicts.jsonl` (955 items) into SQLite.

### 3. Run the pipeline

```bash
# Full incremental: pending URLs -> scan inbox -> classify new -> pool to vault
python3 scripts/cli.py incremental

# Feishu capture and a manual queue drain
python3 scripts/cli.py lark-listen
python3 scripts/cli.py pending-urls

# Or run stages individually
python3 scripts/cli.py scan        # Scan inbox only
python3 scripts/cli.py classify    # Classify pending items
python3 scripts/cli.py pool        # Write to vault pools

# Daily thinking + drafts
python3 scripts/cli.py thinking --date 2026-07-03
python3 scripts/cli.py writer --date 2026-07-03
```

### 4. Start the Web GUI

```bash
python3 scripts/cli.py serve
```

Then open http://127.0.0.1:8765/ in your browser.

### 5. View stats

```bash
python3 scripts/cli.py stats
python3 scripts/cli.py items --verdict seed --limit 10
```

## Web GUI Pages

### Material Pool (`/`)
Trello-style kanban with three columns: Seed / Asset / Archive. Each card shows source, category, trigger, and priority. Click a card to see full details and event history.

### Pipeline (`/` -> Pipeline tab)
Horizontal kanban showing all 10 pipeline stages. Each stage column shows recent runs with status indicators (running/done/failed). Trigger pipeline actions directly from the UI.

### Daily Thinking (`/` -> Daily Thinking tab)
Left panel shows the 5 randomly sampled seeds for the day. Right panel is a free-write editor that saves to the database. Generate a new thinking session or view past dates.

### Drafts (`/` -> Drafts tab)
Grid of draft cards showing angle, headline, and body preview. Select or dismiss drafts with one click.

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/items` | List items (filter by verdict, source, status, priority) |
| GET | `/api/items/{id}` | Get item detail + event history |
| PATCH | `/api/items/{id}/verdict` | Update item verdict |
| PATCH | `/api/items/{id}/status` | Update item status |
| POST | `/api/items/{id}/override` | Override LLM verdict (logged) |
| GET | `/api/pipeline/runs` | List pipeline runs |
| POST | `/api/pipeline/run` | Trigger a pipeline action |
| GET | `/api/pipeline/overview` | Pipeline kanban overview |
| GET | `/api/pipeline/stages` | List all stages |
| GET | `/api/daily-thinking` | List daily thinking sessions |
| GET | `/api/daily-thinking/{date}` | Get session with seed details |
| POST | `/api/daily-thinking/generate` | Generate new session |
| PATCH | `/api/daily-thinking/{date}/free-write` | Save free write |
| GET | `/api/drafts` | List drafts |
| POST | `/api/drafts/generate` | Generate 4-angle drafts |
| PATCH | `/api/drafts/{id}/status` | Update draft status |
| GET | `/api/stats` | Database statistics |
| GET | `/api/inbox/scan` | Scan inbox + persist to DB |
| GET | `/api/health` | Health check |
| GET | `/` | Web GUI |
| GET | `/docs` | FastAPI auto docs |

## SQLite Schema

```sql
items          -- All inbox items with verdict, category, status
pipeline_runs  -- Every pipeline execution with stage, status, duration, artifacts
events         -- Event log for audit trail
daily_thinking -- Daily thinking sessions with seed_ids and free_write
drafts         -- Draft candidates with angle, headline, body, status
overrides      -- Manual verdict overrides (for future few-shot tuning)
pending_urls   -- Durable, retryable queue captured by the Feishu bot
```

## Configuration

All configuration is via environment variables (see `.env.example`):

| Variable | Default | Description |
|----------|---------|-------------|
| `LLKC_VAULT` | `~/Library/Mobile Documents/.../LLM知识库` | Obsidian vault path |
| `LLKC_PROJ` | `~/Documents/Project/llm-knowledge-curator` | Project root |
| `LLKC_DB` | `output/llkc.db` | SQLite database path |
| `LLM_API_BASE` | `https://ark.cn-beijing.volces.com/api/coding/v3` | LLM API base URL |
| `LLM_API_KEY` | (empty) | LLM API key |
| `LLM_MODEL` | `ark-code-latest` | Model name |
| `LLKC_API_PORT` | `8765` | API server port |
| `LLKC_LARK_CLI` | `lark-cli` | Feishu event CLI executable |
| `LLKC_LARK_CHAT_IDS` | (empty) | Optional comma-separated chat allowlist |
| `LLKC_LARK_SENDER_IDS` | (empty) | Optional comma-separated sender allowlist |
| `LLKC_PENDING_URL_LIMIT` | `20` | URLs claimed per incremental run |
| `LLKC_PENDING_URL_MAX_ATTEMPTS` | `3` | Retry limit before a URL becomes dead |

## Cron Setup

```bash
# The same incremental command drains pending_urls before scanning the inbox:
0 6 * * * cd ~/Documents/Project/llm-knowledge-curator && python3 scripts/cli.py incremental >> output/cron.log 2>&1
```

Feishu listener setup and lifecycle details: [`docs/LARK_URL_CAPTURE.md`](docs/LARK_URL_CAPTURE.md).

## Backward Compatibility

The original scripts in `scripts/` (build_index.py, parser_runner.py, write_back.py, daily_thinking.py, writer_agent.py, mcp_server.py) are kept as-is. They continue to work independently. The new `llkc/` package provides the same functionality with:

- Centralized configuration (no hardcoded API keys)
- SQLite state tracking (no more flat JSONL files)
- Event logging for audit trails
- REST API for programmatic access
- Web GUI for visualization
- Unified CLI entry point

## Obsidian Vault

The vault remains the final archive and reading surface. The runtime state (items, pipeline runs, events) lives in SQLite. Vault write-back is idempotent — existing files are protected unless `--rewrite` is passed.

Vault structure:
```
00-Inbox/{Clippings,Telegram,X-Bookmarks}   # Raw materials
01-灵感库/                                    # Seeds (classified by LLM)
02-思考/                                      # Daily thinking + drafts
03-Assets/                                    # Assets (reference materials)
04-Archive/                                   # Archived items (metadata only)
```
