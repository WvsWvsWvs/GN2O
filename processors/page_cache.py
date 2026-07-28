"""Persistent page-level transcription cache."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

def page_hash(image: bytes) -> str:
    return hashlib.sha256(image).hexdigest()

def load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}

def save(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()
