#!/usr/bin/env python3
"""GN2O — GoodNotes to Obsidian pipeline.

Converts exported GoodNotes PDFs into Markdown notes and AI reviews
in an Obsidian vault. Supports both one-shot and continuous-watch modes.
"""

import argparse
import hashlib
import json
import logging
import re
import sys
import threading
import time
from pathlib import Path

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# Add project root to path so imports work
sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import (  # noqa: E402
    GOOGLE_DRIVE_FOLDER_ID,
    OBSIDIAN_VAULT_PATH,
    NOTES_DIR,
    PROJECT_ROOT,
    SKIP_FIRST_PAGE,
    PAGES_PER_CHUNK,
    REQUEST_DELAY_SECONDS,
)
from processors.pdf_processor import extract_pages  # noqa: E402
from processors.llm_client import transcribe, review, format_markdown, generate_card_proposals  # noqa: E402
from processors.markdown_writer import save_note, save_review, read_note, replace_note  # noqa: E402
from processors.drive_downloader import download_new_pdfs  # noqa: E402
from processors.diagram_renderer import render_diagrams  # noqa: E402
from processors.page_cache import page_hash, load as load_page_cache, save as save_page_cache, timestamp  # noqa: E402
from processors.anki_client import read_deck, summarize, add_cloze_note, deck_names_and_ids, AnkiConnectionError  # noqa: E402
from processors.bayesian import forecast  # noqa: E402
from processors.hub_manager import update_hub  # noqa: E402
from processors.anki_proposals import parse as parse_proposals, normalize  # noqa: E402

# Watchdog imports (heavy — only import when needed, but fine at module level)
from watchdog.observers import Observer  # noqa: E402
from watchdog.events import FileSystemEventHandler  # noqa: E402

# ── Prompt paths ─────────────────────────────────────────────────────────
PROMPT_TRANSCRIPTION = PROJECT_ROOT / "prompts" / "transcription.txt"
PROMPT_REVIEW = PROJECT_ROOT / "prompts" / "review.txt"
PROMPT_FORMATTING = PROJECT_ROOT / "prompts" / "formatting.txt"
PROMPT_ANKI = PROJECT_ROOT / "prompts" / "anki_proposals.txt"
FORMAT_TRACKING_FILE = NOTES_DIR / ".format_processed.json"
PAGE_CACHE_FILE = NOTES_DIR / ".page_cache.json"

# ── Thread safety for process_pdf ────────────────────────────────────────
_process_lock = threading.Lock()
_processing: set[str] = set()  # absolute paths currently being processed


def _move_to_processed(pdf_path: Path, processed_dir: Path, processed_path: Path) -> None:
    """Move a PDF to the .processed/ subdirectory."""
    try:
        processed_dir.mkdir(parents=True, exist_ok=True)
        pdf_path.replace(processed_path)
        logger.info(f"  Moved to .processed/: {pdf_path.name}")
    except OSError as e:
        logger.error(f"  Failed to move {pdf_path.name} to .processed/: {e}")


def process_pdf(pdf_path: Path) -> None:
    """Run the full pipeline on a single PDF: extract → transcribe → save → review.

    Processes pages in chunks with delays between API calls to avoid rate limits.
    Skips the GoodNotes cover page if configured.

    Thread-safe — uses a global lock and processing set to prevent duplicate
    processing of the same file.

    Args:
        pdf_path: Absolute path to the PDF file.
    """
    pdf_path = pdf_path.resolve()
    stem = pdf_path.stem

    # Deduplicate with lock
    with _process_lock:
        if str(pdf_path) in _processing:
            logger.debug(f"Already processing: {pdf_path.name}")
            return
        _processing.add(str(pdf_path))

    try:
        # Check if already processed (moved to .processed/)
        processed_dir = NOTES_DIR / ".processed"
        processed_path = processed_dir / pdf_path.name
        if processed_path.exists() and not pdf_path.exists():
            logger.info(f"Already processed (in .processed/): {pdf_path.name}")
            return

        logger.info(f"Processing: {pdf_path.name}")

        # Step 1: Extract pages as images
        try:
            pages = extract_pages(pdf_path)
            logger.info(f"  Extracted {len(pages)} page(s)")
        except Exception as e:
            logger.error(f"  Failed to extract pages from {pdf_path.name}: {e}")
            return

        if not pages:
            logger.warning(f"  No pages extracted from {pdf_path.name} — skipping")
            return

        # Step 2: Skip cover page if configured
        if SKIP_FIRST_PAGE and len(pages) > 1:
            pages = pages[1:]
            logger.info(f"  Skipped cover page, {len(pages)} page(s) remaining")

        if not pages:
            logger.warning(f"  Skipping {pdf_path.name}: only cover page found")
            _move_to_processed(pdf_path, processed_dir, processed_path)
            return

        # Step 3: Transcribe only pages whose rendered image has changed.
        cache = load_page_cache(PAGE_CACHE_FILE)
        note_cache = cache.setdefault(stem, {})
        transcriptions: list[str] = [""] * len(pages)
        changed_pages: list[str] = []
        missing: list[tuple[int, bytes, str]] = []
        for i, image in enumerate(pages, 1):
            digest = page_hash(image)
            entry = note_cache.get(digest)
            if entry and entry.get("transcription"):
                transcriptions[i - 1] = entry["transcription"]
                logger.info(f"  Reusing transcription for page {i}/{len(pages)}")
                continue
            missing.append((i - 1, image, digest))

        for start in range(0, len(missing), 5):
            batch = missing[start:start + 5]
            logger.info(f"  Transcribing pages {batch[0][0] + 1}-{batch[-1][0] + 1} of {len(pages)}...")
            result = transcribe([item[1] for item in batch], PROMPT_TRANSCRIPTION)
            parts = result.split("<!-- GN2O_PAGE_BREAK -->") if result else []
            if len(parts) != len(batch):
                logger.warning("  Batch markers missing; retrying these pages individually")
                for index, image, digest in batch:
                    single = transcribe([image], PROMPT_TRANSCRIPTION)
                    if single:
                        transcriptions[index] = single
                        note_cache[digest] = {"transcription": single, "source_pdf": pdf_path.name, "source_page": index + 1, "transcribed_at": timestamp(), "review": None}
                        changed_pages.append(digest)
                    else:
                        note_cache[digest] = {"source_pdf": pdf_path.name, "source_page": index + 1, "status": "transcription_failed", "failed_at": timestamp()}
                save_page_cache(PAGE_CACHE_FILE, cache)
                continue
            for (index, _, digest), part in zip(batch, parts):
                text = part.strip()
                transcriptions[index] = text
                note_cache[digest] = {"transcription": text, "source_pdf": pdf_path.name, "source_page": index + 1, "transcribed_at": timestamp(), "review": None}
                changed_pages.append(digest)
            save_page_cache(PAGE_CACHE_FILE, cache)
            if start + 5 < len(missing):
                time.sleep(REQUEST_DELAY_SECONDS)

        # Step 5: Concatenate all transcriptions
        full_text = "\n\n".join(filter(None, transcriptions))
        full_text = render_diagrams(full_text, OBSIDIAN_VAULT_PATH / "GN2O" / "diagrams", stem)

        if not full_text:
            logger.warning(f"  No transcription produced for {pdf_path.name}")
            return

        # Step 6: Save note
        try:
            save_note(stem, full_text, OBSIDIAN_VAULT_PATH)
        except OSError as e:
            logger.error(f"  Failed to save note for {stem}: {e}")
            return

        # Step 7: Review only pages newly transcribed or changed.
        review_text = ""
        if changed_pages:
            logger.info(f"  Reviewing changed page(s): {', '.join(changed_pages)}")
            changed_text = "\n\n".join(note_cache[p]["transcription"] for p in changed_pages)
            review_text = review(changed_text, PROMPT_REVIEW, read_hub_goal(stem))
        else:
            logger.info("  No changed pages; skipping review")
        if not review_text and changed_pages:
            logger.warning("  Review returned empty")
        elif review_text:
            logger.info(f"  Review complete ({len(review_text)} chars)")
            for digest in changed_pages:
                note_cache[digest]["review"] = review_text
                note_cache[digest]["reviewed_at"] = timestamp()
            save_page_cache(PAGE_CACHE_FILE, cache)
            hub = OBSIDIAN_VAULT_PATH / "GN2O" / stem / "Hub.md"
            update_hub(hub, {"REVIEW": f"## Latest Review\n\n{review_text}\n\n## Processing Status\n\n- Changed pages reviewed: {len(changed_pages)}\n- Cache entries: {len(note_cache)}"}, f"# {stem} Insights Hub\n")

            # Step 8: Save review
            try:
                save_review(stem, review_text, OBSIDIAN_VAULT_PATH)
            except OSError as e:
                logger.error(f"  Failed to save review for {stem}: {e}")
                return

        # Step 9: Move PDF to .processed/
        _move_to_processed(pdf_path, processed_dir, processed_path)

        logger.info(f"  ✅ Done: {stem}")

    finally:
        with _process_lock:
            _processing.discard(str(pdf_path))


# ── Watchdog handler ─────────────────────────────────────────────────────


class PDFHandler(FileSystemEventHandler):
    """Watchdog handler that processes new PDF files.

    Uses a debounce timer to avoid processing incomplete copies.
    """

    def __init__(self) -> None:
        super().__init__()
        self._timers: dict[str, threading.Timer] = {}

    def on_created(self, event) -> None:
        """Called when a file is created in the watched directory."""
        if event.is_directory:
            return

        src_path = Path(event.src_path)

        # Only process .pdf files (case-insensitive)
        if src_path.suffix.lower() != ".pdf":
            return

        # Skip hidden/temp files
        if src_path.name.startswith(".") or src_path.name.startswith("~"):
            return

        # Debounce: cancel existing timer for this path
        path_str = str(src_path.resolve())
        if path_str in self._timers:
            self._timers[path_str].cancel()

        timer = threading.Timer(2.0, self._process_with_logging, args=[src_path])
        timer.daemon = True
        self._timers[path_str] = timer
        timer.start()

    def _process_with_logging(self, pdf_path: Path) -> None:
        """Callback for the debounce timer — processes the PDF."""
        # Clean up timer reference
        path_str = str(pdf_path.resolve())
        self._timers.pop(path_str, None)

        try:
            process_pdf(pdf_path)
        except Exception as e:
            logger.error(f"Error processing {pdf_path.name}: {e}")


# ── Drive polling ────────────────────────────────────────────────────────


def drive_poll_loop() -> None:
    """Background thread: polls Google Drive for new PDFs every 60 seconds."""
    logger.info("Drive polling thread started (interval: 60s)")
    # Let the system settle on startup
    time.sleep(5)

    while True:
        try:
            downloaded = download_new_pdfs(GOOGLE_DRIVE_FOLDER_ID, NOTES_DIR)
            if downloaded:
                logger.info(f"Downloaded {len(downloaded)} new PDF(s) from Drive")
                for path in downloaded:
                    process_pdf(path)
        except Exception as e:
            logger.error(f"Drive poll cycle failed: {e}")

        time.sleep(60)


# ── One-shot processing ──────────────────────────────────────────────────


def process_existing_pdfs() -> None:
    """Process all unprocessed PDFs currently in NOTES_DIR."""
    processed_dir = NOTES_DIR / ".processed"

    for pdf_path in sorted(NOTES_DIR.glob("*.pdf")):
        # Skip hidden files
        if pdf_path.name.startswith("."):
            continue

        # Skip already-processed files
        if (processed_dir / pdf_path.name).exists():
            continue

        process_pdf(pdf_path)


def format_existing_notes() -> None:
    """Format each existing transcribed note once, without feeding it back into the pipeline."""
    try:
        tracking = json.loads(FORMAT_TRACKING_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        tracking = {}

    note_dir = OBSIDIAN_VAULT_PATH / "GN2O"
    if not note_dir.exists():
        logger.warning(f"Obsidian GN2O directory not found: {note_dir}")
        return

    for note_path in sorted(note_dir.glob("*.md")):
        stem = note_path.stem
        note = read_note(stem, OBSIDIAN_VAULT_PATH)
        if note is None:
            continue
        _, original = note
        digest = hashlib.sha256(original.encode("utf-8")).hexdigest()
        if tracking.get(str(note_path)) == digest:
            logger.info(f"Already formatted: {note_path.name}")
            continue

        logger.info(f"Formatting: {note_path.name}")
        formatted = format_markdown(original, PROMPT_FORMATTING)
        if not formatted.strip():
            logger.warning(f"No formatting returned; leaving unchanged: {note_path.name}")
            continue

        replace_note(note_path, formatted)
        tracking[str(note_path)] = hashlib.sha256(formatted.encode("utf-8")).hexdigest()
        FORMAT_TRACKING_FILE.write_text(json.dumps(tracking, indent=2), encoding="utf-8")


def sync_anki_hub(subject: str, deck: str, anki_only: bool = False) -> None:
    """Read an existing Anki deck and write a subject hub in Obsidian."""
    data = read_deck(deck)
    summary = summarize(data)
    hub_dir = OBSIDIAN_VAULT_PATH / "GN2O" / subject
    hub_dir.mkdir(parents=True, exist_ok=True)
    tags = "\n".join(f"- `{tag}`: {count}" for tag, count in summary["tags"].most_common()) or "- No tags found"
    hub = hub_dir / "Hub.md"
    existing_goal = read_hub_goal(subject) if hub.exists() else "Set a learning goal for this hub."
    existing_criteria = "- Add observable evidence of mastery."
    if hub.exists():
        old = hub.read_text(encoding="utf-8")
        if "## Success Criteria" in old:
            existing_criteria = old.split("## Success Criteria", 1)[1].split("## ", 1)[0].strip()
    header = "\n".join([
        "---", "gn2o_type: insights-hub", f"subject: {subject}",
        f'anki_deck: "{deck}"', "anki_read_only_sync: true", f"source_mode: {'anki_only' if anki_only else 'notes_and_anki'}", "target_mastery: 0.90", "---", "",
        f"# {subject} Insights Hub", "", "## Goal", "", existing_goal, "",
        "## Success Criteria", "", existing_criteria, "",
        "## Manual Context", "", "Add syllabus, exam, or personal context here. GN2O will not modify this section.", "",
        "## Source Status", "", f"- Notes available: {'No' if anki_only else 'Not checked'}", "- Anki deck available: Yes", f"- Analysis mode: {'Anki-only' if anki_only else 'Notes and Anki'}", ""
    ])
    anki = "\n".join([
        f"- Deck: `{deck}`", f"- Notes: {summary['notes']}", f"- Cards: {summary['cards']}",
        f"- Status: {'Deck found with cards' if summary['cards'] else 'Deck found, but no cards were returned'}", "",
        "### Existing Tags", "", tags
    ])
    estimates = summary.get("estimates", [])
    projection = forecast(estimates)
    concepts = "\n".join(f"| {item['concept']} | {item['mastery_probability']:.0%} | {item['status']} | {item['reviews']} |" for item in estimates) or "| No review data | — | insufficient-data | 0 |"
    concept_section = "## Concept Mastery\n\n| Concept | Mastery | Status | Reviews |\n|---|---:|---|---:|\n" + concepts
    status_section = f"## Current Status\n\n- Concepts with review data: {len(estimates)}\n- Concepts mastered: {sum(1 for x in estimates if x['status'] == 'mastered')}\n- Concepts needing data: {sum(1 for x in estimates if x['status'] == 'insufficient-data')}"
    if projection["status"] == "projected":
        graph_dir = hub_dir / "Graphs"
        graph_dir.mkdir(parents=True, exist_ok=True)
        graph = graph_dir / "mastery-forecast.svg"
        current_y = 180 - int(projection["current"] * 140)
        target_y = 180 - int(projection["target"] * 140)
        graph.write_text(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 600 220"><rect width="600" height="220" fill="white"/><line x1="40" y1="180" x2="560" y2="180" stroke="#888"/><line x1="40" y1="{target_y}" x2="560" y2="{target_y}" stroke="#d66" stroke-dasharray="6 4"/><polyline points="40,{current_y} 300,{current_y - 35} 560,{target_y}" fill="none" stroke="#28a9e0" stroke-width="4"/><text x="45" y="20" font-family="sans-serif">Bayesian mastery trend forecast</text><text x="45" y="205" font-family="sans-serif">Today</text><text x="500" y="205" font-family="sans-serif">Forecast</text></svg>', encoding="utf-8")
        forecast_section = f"## Mastery Forecast\n\n![[GN2O/{subject}/Graphs/mastery-forecast.svg]]\n\nCurrent estimated mastery: {projection['current']:.0%}\n\nTarget: {projection['target']:.0%}\n\nProjected timeline: approximately {projection['weeks']} weeks at the current trend.\n\n> This is a conservative trend estimate, not a guaranteed outcome."
    else:
        forecast_section = "## Mastery Forecast\n\nInsufficient data for a defensible forecast. Each concept needs at least five reviews, with at least two concepts containing usable data."
    update_hub(hub, {
        "STATUS": "## Current Status\n\n- Overall mastery: insufficient data\n- Active weaknesses: not yet calculated\n- Status: collecting data",
        "FORECAST": forecast_section,
        "CONCEPTS": "## Concept Mastery\n\nConcept-level mastery will appear after review data is available.",
        "PRIORITIES": "## Current Priorities\n\nNo priorities calculated yet.",
        "ISSUES": "## Active Review Problems\n\nNo active problems recorded.",
        "ANKI": anki,
        "ANKI_PROPOSALS": "## Proposed Cloze Cards\n\nNo proposals generated.",
        "REVIEW_HISTORY": "## Review History\n\nNo review history recorded yet.",
        "PROCESSING": "## Processing State\n\n- Cached pages: 0\n- Failed pages: 0\n- Last update: not yet processed"
    }, header)
    update_hub(hub, {"ANKI": anki, "CONCEPTS": concept_section, "STATUS": status_section})
    logger.info(f"Anki hub written: {hub}")


def sync_anki_tree(parent: str) -> None:
    decks = deck_names_and_ids()
    prefix = parent + "::"
    children = sorted(name for name in decks if name == parent or name.startswith(prefix))
    if not children:
        raise AnkiConnectionError(f"Deck not found: {parent}")
    for deck in children:
        relative = deck[len(parent):].lstrip(":") or parent
        subject = parent if deck == parent else parent + "/" + relative.replace("::", "/")
        sync_anki_hub(subject, deck, anki_only=True)
    logger.info(f"Synchronized {len(children)} deck hub(s) under {parent}")


def show_status() -> None:
    cache = load_page_cache(PAGE_CACHE_FILE)
    cached_pages = sum(len(pages) for pages in cache.values())
    notes_dir = OBSIDIAN_VAULT_PATH / "GN2O"
    notes = list(notes_dir.glob("*.md")) if notes_dir.exists() else []
    reviews = list((notes_dir / "Reviews").glob("*.md")) if (notes_dir / "Reviews").exists() else []
    print(f"GN2O status\n  Cached pages: {cached_pages}\n  Notes: {len(notes)}\n  Reviews: {len(reviews)}\n  Vault: {OBSIDIAN_VAULT_PATH}")


def validate_setup() -> bool:
    checks = [("Obsidian vault", OBSIDIAN_VAULT_PATH.exists()), ("Notes directory", NOTES_DIR.exists()), ("Prompt files", PROMPT_TRANSCRIPTION.exists() and PROMPT_REVIEW.exists())]
    ok = True
    for name, passed in checks:
        print(f"{'OK' if passed else 'MISSING'}: {name}")
        ok &= passed
    return ok


def read_hub_goal(stem: str) -> str:
    """Read the human-defined goal section from a subject hub."""
    hub = OBSIDIAN_VAULT_PATH / "GN2O" / stem / "Hub.md"
    try:
        lines = hub.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    try:
        start = lines.index("## Goal") + 1
        end = next((i for i in range(start, len(lines)) if lines[i].startswith("## ")), len(lines))
        return "\n".join(lines[start:end]).strip()
    except ValueError:
        return ""


def sync_approved_cards(subject: str, confirm: bool) -> None:
    hub = OBSIDIAN_VAULT_PATH / "GN2O" / subject / "Hub.md"
    hub_text = hub.read_text(encoding="utf-8")
    deck_match = re.search(r"anki_deck:\s*[\"']?([^\"'\n]+)", hub_text)
    proposals = parse_proposals(hub, deck_match.group(1).strip() if deck_match else "")
    print(f"Approved Cloze-card proposals: {len(proposals)}")
    existing = read_deck(proposals[0]["deck"]) if proposals else {"notes": []}
    existing_text = {normalize(note.get("fields", {}).get("Text", {}).get("value", "")) for note in existing["notes"]}
    duplicates = [p for p in proposals if normalize(p["text"]) in existing_text]
    if duplicates:
        print(f"Potential exact duplicates: {len(duplicates)}")
        for proposal in duplicates:
            print(f"  DUPLICATE: {proposal['title']}")
        proposals = [p for p in proposals if p not in duplicates]
    for proposal in proposals:
        print(f"- {proposal['title']} → {proposal['deck']}")
    if not confirm:
        print("Dry preview only. Re-run with --confirm to write cards to Anki.")
        return
    created = []
    for proposal in proposals:
        note_id = add_cloze_note(proposal["deck"], proposal["text"], proposal["extra"], ["gn2o", f"gn2o-card::{proposal['id']}"])
        created.append(f"- {proposal['title']}: created Anki note `{note_id}`")
    update_hub(hub, {"ANKI_SYNC": "## Latest Card Sync\n\n" + ("\n".join(created) or "No cards created.")})


def generate_anki_proposals(subject: str) -> None:
    hub = OBSIDIAN_VAULT_PATH / "GN2O" / subject / "Hub.md"
    note = OBSIDIAN_VAULT_PATH / "GN2O" / f"{subject}.md"
    review_path = OBSIDIAN_VAULT_PATH / "GN2O" / "Reviews" / f"{subject} Review.md"
    context = "\n\n".join([f"GOAL:\n{read_hub_goal(subject)}", f"NOTES:\n{note.read_text(encoding='utf-8') if note.exists() else ''}", f"REVIEW:\n{review_path.read_text(encoding='utf-8') if review_path.exists() else ''}"])
    cards = generate_card_proposals(context, PROMPT_ANKI)
    blocks = []
    for card in cards:
        tags = ", ".join(card.get("tags", []))
        blocks.append(f"### Card: {card.get('title', 'Untitled')}\n\n- [ ] Approve\n- Status: Proposed\n- Deck: `ADD_DECK_NAME`\n- Tags: `{tags}`\n- Source page: {card.get('source_page', 'unknown')}\n- Reason: {card.get('reason', '')}\n\n**Text**\n\n{card.get('text', '')}\n\n**Extra**\n\n{card.get('extra', '')}")
    update_hub(hub, {"ANKI_PROPOSALS": "## Proposed Cloze Cards\n\n" + ("\n\n".join(blocks) or "No proposals generated.")})
    logger.info(f"Generated {len(cards)} Anki proposal(s) in {hub}")
    try:
        start = lines.index("## Goal") + 1
        end = next((i for i in range(start, len(lines)) if lines[i].startswith("## ")), len(lines))
        return "\n".join(lines[start:end]).strip()
    except ValueError:
        return ""


# ── Entry point ──────────────────────────────────────────────────────────


def main() -> None:
    """Parse arguments and run the pipeline."""
    parser = argparse.ArgumentParser(
        description="GN2O — GoodNotes to Obsidian pipeline"
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Download from Drive, process existing PDFs, and exit",
    )
    parser.add_argument(
        "--format-existing",
        action="store_true",
        help="Format existing Obsidian GN2O notes once and exit",
    )
    parser.add_argument("--sync-anki", action="store_true", help="Read an existing Anki deck into an Obsidian hub")
    parser.add_argument("--sync-anki-tree", action="store_true", help="Create separate hubs for a deck and all subdecks")
    parser.add_argument("--subject", type=str, help="Subject name for --sync-anki")
    parser.add_argument("--deck", type=str, help="Anki deck name for --sync-anki")
    parser.add_argument("--sync-approved-cards", action="store_true", help="Preview or create checked Cloze-card proposals")
    parser.add_argument("--anki-only", action="store_true", help="Create an Anki-only hub without requiring notes")
    parser.add_argument("--generate-anki-proposals", action="store_true", help="Generate unchecked Cloze proposals from a subject hub")
    parser.add_argument("--confirm", action="store_true", help="Confirm a card-writing operation")
    parser.add_argument("--status", action="store_true", help="Show cached pages and generated artifacts")
    parser.add_argument("--check-setup", action="store_true", help="Validate local GN2O configuration")
    parser.add_argument("--dry-run", action="store_true", help="Validate inputs without calling APIs or writing notes")
    parser.add_argument(
        "--file",
        type=str,
        metavar="FILENAME",
        help="Process a single PDF by name (skips Drive download). File must exist in Notes/.",
    )
    parser.add_argument(
        "--transcribe-only",
        action="store_true",
        help="Only transcribe to Markdown (skip AI review). Requires --file.",
    )
    parser.add_argument(
        "--review-only",
        action="store_true",
        help="Only generate review from existing Markdown. Requires --file.",
    )
    args = parser.parse_args()

    logger.info("GN2O starting...")
    logger.info(f"  Notes dir: {NOTES_DIR}")
    logger.info(f"  Vault:     {OBSIDIAN_VAULT_PATH}")

    if args.status:
        show_status()
        return
    if args.check_setup:
        sys.exit(0 if validate_setup() else 1)
    if args.dry_run:
        if args.file:
            target = NOTES_DIR / args.file
            if target.exists():
                preview_pages = extract_pages(target)
                if SKIP_FIRST_PAGE and len(preview_pages) > 1:
                    preview_pages = preview_pages[1:]
                preview_cache = load_page_cache(PAGE_CACHE_FILE).get(target.stem, {})
                cached = sum(1 for page in preview_pages if page_hash(page) in preview_cache)
                print(f"DRY RUN: {len(preview_pages)} pages; {cached} cached; {len(preview_pages) - cached} require transcription and review")
            else:
                print(f"DRY RUN: file not found: {target}")
        else:
            print(f"DRY RUN: would scan {NOTES_DIR} and write to {OBSIDIAN_VAULT_PATH}")
        return

    # Ensure required directories exist
    NOTES_DIR.mkdir(parents=True, exist_ok=True)
    (NOTES_DIR / ".processed").mkdir(parents=True, exist_ok=True)

    if args.file:
        NOTES_DIR.mkdir(parents=True, exist_ok=True)
        (NOTES_DIR / ".processed").mkdir(parents=True, exist_ok=True)
        pdf_path = NOTES_DIR / args.file
        stem = pdf_path.stem

        # ── --review-only: skip PDF, review existing markdown ───────────
        if args.review_only:
            note_path = OBSIDIAN_VAULT_PATH / "GN2O" / f"{stem}.md"
            if not note_path.exists():
                logger.error(f"No transcription found at {note_path}")
                logger.info("Run --file without --review-only to transcribe first.")
                sys.exit(1)
            logger.info(f"Reviewing from existing note: {note_path.name}")
            review_text = review(note_path.read_text(), PROMPT_REVIEW, read_hub_goal(stem))
            if review_text:
                save_review(stem, review_text, OBSIDIAN_VAULT_PATH)
                logger.info(f"  ✅ Review saved to GN2O/Reviews/{stem} Review.md")
            else:
                logger.warning("  Review returned empty")
            logger.info("--file --review-only complete. Exiting.")
            return

        # ── File must exist for transcribe-only or normal mode ──────────
        if not pdf_path.exists():
            logger.error(f"File not found: {pdf_path}")
            logger.info(f"Place the file in {NOTES_DIR} first, or run --once to download from Drive.")
            sys.exit(1)

        # ── --transcribe-only: extract + transcribe, skip review ────────
        if args.transcribe_only:
            try:
                pages = extract_pages(pdf_path)
            except Exception as e:
                logger.error(f"Failed to extract pages: {e}")
                sys.exit(1)
            logger.info(f"  Extracted {len(pages)} page(s)")
            if SKIP_FIRST_PAGE and len(pages) > 1:
                pages = pages[1:]
                logger.info(f"  Skipped cover page, {len(pages)} page(s) remaining")
            chunks = [pages[i : i + PAGES_PER_CHUNK] for i in range(0, len(pages), PAGES_PER_CHUNK)]
            transcriptions = []
            for i, chunk in enumerate(chunks, 1):
                logger.info(f"  Transcribing chunk {i}/{len(chunks)}...")
                result = transcribe(chunk, PROMPT_TRANSCRIPTION)
                if result:
                    transcriptions.append(result)
                    logger.info(f"  Chunk {i}/{len(chunks)} complete ({len(result)} chars)")
                else:
                    logger.warning(f"  Chunk {i}/{len(chunks)} returned empty")
                if i < len(chunks):
                    time.sleep(REQUEST_DELAY_SECONDS)
            full_text = "\n\n".join(filter(None, transcriptions))
            full_text = render_diagrams(full_text, OBSIDIAN_VAULT_PATH / "GN2O" / "diagrams", stem)
            if full_text:
                try:
                    save_note(stem, full_text, OBSIDIAN_VAULT_PATH)
                    logger.info(f"  ✅ Note saved to GN2O/{stem}.md")
                except OSError as e:
                    logger.error(f"Failed to save note: {e}")
                    sys.exit(1)
            else:
                logger.warning("  No transcription produced — nothing saved")
            logger.info("--file --transcribe-only complete. Exiting.")
            return

        # ── Normal mode: full pipeline (existing behavior) ──────────────
        process_pdf(pdf_path)
        logger.info("--file complete. Exiting.")
        return

    if args.once:
        # Download from Drive first
        try:
            drive_downloads = download_new_pdfs(GOOGLE_DRIVE_FOLDER_ID, NOTES_DIR)
            logger.info(f"Downloaded {len(drive_downloads)} PDF(s) from Google Drive")
        except Exception as e:
            logger.warning(f"Drive download skipped: {e}")

        process_existing_pdfs()
        logger.info("--once complete. Exiting.")
        return

    if args.format_existing:
        format_existing_notes()
        logger.info("--format-existing complete. Exiting.")
        return

    if args.sync_anki:
        if not args.subject or not args.deck:
            parser.error("--sync-anki requires --subject and --deck")
        try:
            sync_anki_hub(args.subject, args.deck, args.anki_only)
        except AnkiConnectionError as exc:
            logger.error(str(exc))
            sys.exit(1)
        return

    if args.sync_anki_tree:
        if not args.deck:
            parser.error("--sync-anki-tree requires --deck")
        try:
            sync_anki_tree(args.deck)
        except AnkiConnectionError as exc:
            logger.error(str(exc))
            sys.exit(1)
        return

    if args.generate_anki_proposals:
        if not args.subject:
            parser.error("--generate-anki-proposals requires --subject")
        generate_anki_proposals(args.subject)
        return

    if args.sync_approved_cards:
        if not args.subject:
            parser.error("--sync-approved-cards requires --subject")
        try:
            sync_approved_cards(args.subject, args.confirm)
        except AnkiConnectionError as exc:
            logger.error(str(exc))
            sys.exit(1)
        return

    # ── Continuous mode ──────────────────────────────────────────────
    # Start Drive polling in background thread
    drive_thread = threading.Thread(target=drive_poll_loop, daemon=True)
    drive_thread.start()

    # Start watchdog
    event_handler = PDFHandler()
    observer = Observer()
    observer.schedule(event_handler, str(NOTES_DIR), recursive=False)
    observer.start()
    logger.info("Watching for new PDFs... (Ctrl+C to stop)")

    try:
        while observer.is_alive():
            observer.join(1)
    except KeyboardInterrupt:
        logger.info("\nShutting down...")
        observer.stop()

    observer.join()
    logger.info("GN2O stopped.")


if __name__ == "__main__":
    main()
