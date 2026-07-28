"""Configuration for GN2O — loads environment variables and exports module-level constants."""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Project root is the directory containing this file
PROJECT_ROOT = Path(__file__).resolve().parent

# Load .env from project root
load_dotenv(PROJECT_ROOT / ".env")


def _get_required(key: str) -> str:
    """Get a required environment variable or exit with a clear error."""
    value = os.environ.get(key)
    if not value or not value.strip():
        print(f"ERROR: Missing required environment variable '{key}'.")
        print(f"       Copy {PROJECT_ROOT / '.env.example'} to {PROJECT_ROOT / '.env'}")
        print(f"       and fill in the value for '{key}'.")
        sys.exit(1)
    return value.strip()


def _resolve_path(value: str) -> Path:
    """Expand ~ and resolve relative paths relative to PROJECT_ROOT."""
    expanded = os.path.expanduser(value)
    path = Path(expanded)
    if not path.is_absolute():
        path = (PROJECT_ROOT / path).resolve()
    return path


# ── OpenAI / LLM ──────────────────────────────────────────────────────────
OPENAI_API_KEY: str = _get_required("OPENAI_API_KEY")
OPENAI_BASE_URL: str = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").strip()
MODEL_NAME: str = os.environ.get("MODEL_NAME", "gpt-4o").strip()

# ── Google Drive ──────────────────────────────────────────────────────────
GOOGLE_DRIVE_FOLDER_ID: str = _get_required("GOOGLE_DRIVE_FOLDER_ID")

# ── Paths ─────────────────────────────────────────────────────────────────
OBSIDIAN_VAULT_PATH: Path = _resolve_path(_get_required("OBSIDIAN_VAULT_PATH"))
NOTES_DIR: Path = _resolve_path(os.environ.get("NOTES_DIR", "./Notes"))

# ── Chunking / rate-limit avoidance ───────────────────────────────────────
SKIP_FIRST_PAGE: bool = os.environ.get("SKIP_FIRST_PAGE", "true").strip().lower() == "true"
PAGES_PER_CHUNK: int = int(os.environ.get("PAGES_PER_CHUNK", "1"))
REQUEST_DELAY_SECONDS: int = int(os.environ.get("REQUEST_DELAY_SECONDS", "15"))
ANKI_CONNECT_URL: str = os.environ.get("ANKI_CONNECT_URL", "http://localhost:8765").strip()
BACKUP_RETENTION: int = int(os.environ.get("BACKUP_RETENTION", "5"))
