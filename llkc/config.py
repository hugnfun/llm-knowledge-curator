"""Central configuration — all paths, API keys, and settings from env vars."""

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # Exported environment variables still work without this optional package.
    load_dotenv = None

if load_dotenv:
    load_dotenv(Path(__file__).parent.parent / ".env")


def _expand(p: str) -> Path:
    return Path(os.path.expanduser(p))


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


# --- Paths ---
PROJ_ROOT: Path = _expand(_env("LLKC_PROJ", "~/Documents/Project/llm-knowledge-curator"))
VAULT_ROOT: Path = _expand(_env(
    "LLKC_VAULT",
    "~/Library/Mobile Documents/iCloud~md~obsidian/Documents/LLM知识库",
))

INBOX_ROOT: Path = VAULT_ROOT / "00-Inbox"
SEED_ROOT: Path = VAULT_ROOT / "01-灵感库"
THINKING_ROOT: Path = VAULT_ROOT / "02-思考"
DRAFTS_ROOT: Path = VAULT_ROOT / "02-Drafts"
ASSET_ROOT: Path = VAULT_ROOT / "03-Assets"
ARCHIVE_ROOT: Path = VAULT_ROOT / "04-Archive"
TASKS_ROOT: Path = VAULT_ROOT / "05-Tasks"

OUTPUT_DIR: Path = PROJ_ROOT / "output"
PROMPTS_DIR: Path = PROJ_ROOT / "prompts"
DB_PATH: Path = _expand(_env("LLKC_DB", str(OUTPUT_DIR / "llkc.db")))
if not DB_PATH.is_absolute():
    DB_PATH = PROJ_ROOT / DB_PATH

# --- LLM API ---
LLM_API_BASE: str = _env("LLM_API_BASE", "https://ark.cn-beijing.volces.com/api/coding/v3")
LLM_API_KEY: str = _env("LLM_API_KEY", "")
LLM_MODEL: str = _env("LLM_MODEL", "ark-code-latest")

# Writer-specific (falls back to LLM_* if unset)
WRITER_API_KEY: str = _env("WRITER_API_KEY", LLM_API_KEY)
WRITER_API_BASE: str = _env("WRITER_API_BASE", LLM_API_BASE)
WRITER_MODEL: str = _env("WRITER_MODEL", LLM_MODEL)
WRITER_TIMEOUT: int = int(_env("LLKC_WRITER_TIMEOUT", "180"))
WRITER_MAX_TOKENS: int = int(_env("LLKC_WRITER_MAX_TOKENS", "10000"))

# Parser-specific
PARSER_CONCURRENCY: int = int(_env("LLKC_PARSER_CONCURRENCY", "5"))
PARSER_TIMEOUT: int = int(_env("LLKC_PARSER_TIMEOUT", "90"))
PARSER_MAX_INPUT_CHARS: int = int(_env("LLKC_PARSER_MAX_INPUT_CHARS", "12000"))
PARSER_MAX_RETRY: int = 2

# --- API server ---
API_HOST: str = _env("LLKC_API_HOST", "127.0.0.1")
API_PORT: int = int(_env("LLKC_API_PORT", "8765"))


# --- Prompt paths ---
PARSER_PROMPT_PATH: Path = PROMPTS_DIR / "parser_v0.2.md"
WRITER_PROMPT_PATH: Path = PROMPTS_DIR / "writer_v0.1.md"


def ensure_dirs():
    """Create output + DB directories if missing."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
