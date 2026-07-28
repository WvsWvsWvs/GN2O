"""Write transcribed Markdown notes and AI reviews into the Obsidian vault."""

import logging
from datetime import datetime
import os
from pathlib import Path

logger = logging.getLogger(__name__)
BACKUP_RETENTION = int(os.environ.get("BACKUP_RETENTION", "5"))


def _backup(path: Path) -> None:
    if path.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = path.with_name(f"{path.stem}.backup-{stamp}{path.suffix}")
        backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        backups = sorted(path.parent.glob(f"{path.stem}.backup-*{path.suffix}"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in backups[BACKUP_RETENTION:]:
            old.unlink(missing_ok=True)


def save_note(stem: str, markdown: str, vault_path: str | Path) -> Path:
    """Save transcribed notes to the vault.

    Creates ``{vault_path}/GN2O/{stem}.md``.

    Args:
        stem: The base filename (without extension).
        markdown: The Markdown content to write.
        vault_path: Path to the Obsidian vault.

    Returns:
        The path to the written file.

    Raises:
        OSError: If the file cannot be written (permission, disk full, etc.).
    """
    dest_dir = Path(vault_path) / "GN2O"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / f"{stem}.md"

    if not markdown or not markdown.strip():
        logger.warning(f"Empty markdown content for '{stem}' — writing empty file as signal")

    try:
        _backup(dest_path)
        dest_path.write_text(markdown, encoding="utf-8")
        logger.info(f"Saved note: {dest_path}")
    except OSError:
        logger.error(f"Failed to write note: {dest_path}")
        raise

    return dest_path


def save_review(stem: str, markdown: str, vault_path: str | Path) -> Path:
    """Save an AI review to the vault.

    Creates ``{vault_path}/GN2O/Reviews/{stem} Review.md``.

    Args:
        stem: The base filename (without extension).
        markdown: The review Markdown content to write.
        vault_path: Path to the Obsidian vault.

    Returns:
        The path to the written file.

    Raises:
        OSError: If the file cannot be written (permission, disk full, etc.).
    """
    dest_dir = Path(vault_path) / "GN2O" / "Reviews"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / f"{stem} Review.md"

    if not markdown or not markdown.strip():
        logger.warning(f"Empty review content for '{stem}' — writing empty file as signal")

    try:
        _backup(dest_path)
        dest_path.write_text(markdown, encoding="utf-8")
        logger.info(f"Saved review: {dest_path}")
    except OSError:
        logger.error(f"Failed to write review: {dest_path}")
        raise

    return dest_path


def read_note(stem: str, vault_path: str | Path) -> tuple[Path, str] | None:
    """Read a transcribed note from the vault, if it exists."""
    path = Path(vault_path) / "GN2O" / f"{stem}.md"
    try:
        return path, path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning(f"Note not found: {path}")
        return None


def replace_note(path: Path, markdown: str) -> None:
    """Replace a note only after a successful formatting response."""
    _backup(path)
    path.write_text(markdown, encoding="utf-8")
    logger.info(f"Formatted note: {path}")
