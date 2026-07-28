"""Google Drive downloader — OAuth authentication and PDF downloading."""

import json
import logging
import os
import pickle
from pathlib import Path
from typing import Dict, List, Set

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import Resource, build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseDownload

from config import PROJECT_ROOT, NOTES_DIR

logger = logging.getLogger(__name__)

# ── Helpers ──────────────────────────────────────────────────────────────

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
TRACKING_FILE = NOTES_DIR / ".drive_processed.json"
CREDENTIALS_FILE = PROJECT_ROOT / "credentials.json"
TOKEN_FILE = PROJECT_ROOT / "token.pickle"


def _get_credentials() -> Credentials | None:
    """Obtain valid Google Drive API credentials.

    Tries to load a cached token from ``token.pickle`` first.  If missing or
    expired, runs the OAuth flow using ``credentials.json``.

    Returns:
        A ``Credentials`` object, or ``None`` if ``credentials.json`` is
        missing and no cached token is available.
    """
    creds: Credentials | None = None

    # 1. Load cached token
    if TOKEN_FILE.exists():
        try:
            with TOKEN_FILE.open("rb") as f:
                creds = pickle.load(f)
        except (pickle.UnpicklingError, EOFError, OSError) as e:
            logger.warning(f"Failed to load token.pickle: {e}")
            creds = None

    # 2. Refresh if expired
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            # Save refreshed token
            with TOKEN_FILE.open("wb") as f:
                pickle.dump(creds, f)
            return creds
        except Exception as e:
            logger.warning(f"Token refresh failed, re-authenticating: {e}")
            creds = None

    # 3. If still no valid creds, run OAuth flow
    if not creds or not creds.valid:
        if not CREDENTIALS_FILE.exists():
            logger.error(
                f"Google Drive credentials file not found: {CREDENTIALS_FILE}\n"
                "Download your OAuth 2.0 Client ID credentials from:\n"
                "  https://console.cloud.google.com/apis/credentials\n"
                "and save the JSON as 'credentials.json' in the project root."
            )
            return None

        logger.info("Opening browser for Google authentication...")
        flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
        creds = flow.run_local_server(port=0, open_browser=True)

        # Save token for future runs
        with TOKEN_FILE.open("wb") as f:
            pickle.dump(creds, f)
        logger.info(f"Authentication successful. Token saved to {TOKEN_FILE}")

    return creds


def _get_drive_service() -> Resource | None:
    """Build and return the Google Drive API service.

    Returns:
        A Drive v3 ``Resource``, or ``None`` if authentication failed.
    """
    creds = _get_credentials()
    if creds is None:
        return None
    return build("drive", "v3", credentials=creds)


# ── Public API ───────────────────────────────────────────────────────────


def list_pdfs(folder_id: str) -> List[Dict]:
    """List all non-trashed PDF files in the given Drive folder.

    Args:
        folder_id: The Google Drive folder ID.

    Returns:
        A list of dicts with keys ``id``, ``name``, ``createdTime``.
    """
    service = _get_drive_service()
    if service is None:
        return []

    try:
        query = f"'{folder_id}' in parents and mimeType='application/pdf' and trashed=false"
        results = (
            service.files()
            .list(q=query, fields="files(id, name, createdTime)")
            .execute()
        )
        return results.get("files", [])
    except HttpError as e:
        logger.error(f"Failed to list PDFs in Drive folder: {e}")
        return []


def download_file(file_id: str, dest_path: str | Path) -> None:
    """Download a single file from Google Drive to *dest_path*.

    Writes to a ``.tmp`` sibling first, then atomically renames to the final
    path so the watchdog never sees a partially-written file.

    Args:
        file_id: The Google Drive file ID.
        dest_path: Local destination path.

    Raises:
        RuntimeError: If the download fails.
    """
    service = _get_drive_service()
    if service is None:
        raise RuntimeError("Drive service not available — authentication failed")

    dest_path = Path(dest_path)
    tmp_path = dest_path.with_suffix(dest_path.suffix + ".tmp")

    try:
        request = service.files().get_media(fileId=file_id)
        with tmp_path.open("wb") as f:
            downloader = MediaIoBaseDownload(f, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()

        # Atomic rename
        os.replace(str(tmp_path), str(dest_path))
        logger.debug(f"Downloaded: {dest_path.name}")
    except HttpError as e:
        # Clean up temp file on failure
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise RuntimeError(f"Failed to download file {file_id}: {e}") from e


def get_processed_ids(tracking_file: Path | None = None) -> Set[str]:
    """Read the set of already-processed file IDs from the tracking file.

    Args:
        tracking_file: Path to the JSON tracking file (defaults to
                       ``{NOTES_DIR}/.drive_processed.json``).

    Returns:
        A set of file ID strings.
    """
    if tracking_file is None:
        tracking_file = TRACKING_FILE

    if not tracking_file.exists():
        return set()

    try:
        data = json.loads(tracking_file.read_text(encoding="utf-8"))
        return set(data.get("processed_ids", []))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"Failed to read tracking file: {e}")
        return set()


def mark_processed(file_id: str, tracking_file: Path | None = None) -> None:
    """Record a file ID as processed.

    Args:
        file_id: The Google Drive file ID to mark.
        tracking_file: Path to the JSON tracking file (defaults to
                       ``{NOTES_DIR}/.drive_processed.json``).
    """
    if tracking_file is None:
        tracking_file = TRACKING_FILE

    processed = get_processed_ids(tracking_file)
    processed.add(file_id)

    try:
        tracking_file.write_text(
            json.dumps({"processed_ids": list(processed)}, indent=2),
            encoding="utf-8",
        )
    except OSError as e:
        logger.error(f"Failed to write tracking file: {e}")


def download_new_pdfs(folder_id: str, dest_dir: str | Path) -> List[Path]:
    """Download new (unprocessed) PDFs from a Google Drive folder.

    Args:
        folder_id: The Google Drive folder ID.
        dest_dir: Local directory to save PDFs into.

    Returns:
        A list of ``Path`` objects for newly downloaded files.
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    if not CREDENTIALS_FILE.exists():
        logger.error(
            f"credentials.json not found at {CREDENTIALS_FILE}.\n"
            "Skipping Drive download. Place your OAuth client credentials file "
            "in the project root to enable Drive syncing."
        )
        return []

    pdfs = list_pdfs(folder_id)
    if not pdfs:
        logger.info("No PDFs found in the Drive folder.")
        return []

    processed_ids = get_processed_ids()
    downloaded: List[Path] = []

    for pdf in pdfs:
        file_id = pdf["id"]
        filename = pdf["name"]
        dest_path = dest_dir / filename

        if file_id in processed_ids:
            logger.info(f"Already processed: {filename}")
            continue

        logger.info(f"Downloading: {filename}")
        try:
            download_file(file_id, dest_path)
            mark_processed(file_id)
            downloaded.append(dest_path)
        except Exception as e:
            logger.error(f"Failed to download {filename}: {e}")
            continue

    return downloaded
