"""OpenAI LLM client for transcription and review."""

import base64
import json
import logging
from pathlib import Path
from typing import List

from openai import OpenAI

from config import OPENAI_API_KEY, OPENAI_BASE_URL, MODEL_NAME

logger = logging.getLogger(__name__)

# Reusable client instance
_client: OpenAI | None = None


def _get_client() -> OpenAI:
    """Get or create the OpenAI client."""
    global _client
    if _client is None:
        _client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)
    return _client


def _encode_image(image_bytes: bytes) -> str:
    """Encode raw PNG bytes to a base64 data URL."""
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    return f"data:image/png;base64,{b64}"


def transcribe(images: List[bytes], prompt_path: str | Path) -> str:
    """Transcribe handwritten pages via the vision API.

    Args:
        images: List of PNG image bytes (one per page).
        prompt_path: Path to the transcription system prompt file.

    Returns:
        The transcribed Markdown text, or empty string on failure.
    """
    prompt_path = Path(prompt_path)
    try:
        system_prompt = prompt_path.read_text(encoding="utf-8")
    except OSError as e:
        logger.error(f"Failed to read transcription prompt: {e}")
        return ""

    if not images:
        logger.warning("No images to transcribe")
        return ""

    # Build user content: text + all images as base64
    user_content: list = [
        {"type": "text", "text": "Transcribe these handwritten notes:"},
    ]
    for img in images:
        user_content.append({
            "type": "image_url",
            "image_url": {"url": _encode_image(img)},
        })

    client = _get_client()
    try:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            max_tokens=16000,
        )
        content = response.choices[0].message.content
        return content or ""
    except Exception as e:
        logger.error(f"Transcription API call failed: {e}")
        return ""


def review(markdown_text: str, prompt_path: str | Path, goal: str = "") -> str:
    """Critically review transcribed notes via a standard chat completion.

    Args:
        markdown_text: The transcribed notes in Markdown.
        prompt_path: Path to the review system prompt file.

    Returns:
        The review text, or empty string on failure.
    """
    prompt_path = Path(prompt_path)
    try:
        system_prompt = prompt_path.read_text(encoding="utf-8")
    except OSError as e:
        logger.error(f"Failed to read review prompt: {e}")
        return ""

    if not markdown_text or not markdown_text.strip():
        logger.warning("No markdown text to review")
        return ""

    client = _get_client()
    try:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"{('Evaluate these notes against this learning goal:\n' + goal + '\n\n' if goal else '')}Review the following notes critically:\n\n{markdown_text}"},
            ],
            max_tokens=8000,
        )
        content = response.choices[0].message.content
        return content or ""
    except Exception as e:
        logger.error(f"Review API call failed: {e}")
        return ""


def format_markdown(markdown_text: str, prompt_path: str | Path) -> str:
    """Clean an existing Markdown transcription while preserving its content."""
    prompt_path = Path(prompt_path)
    try:
        system_prompt = prompt_path.read_text(encoding="utf-8")
    except OSError as e:
        logger.error(f"Failed to read formatting prompt: {e}")
        return ""


def generate_card_proposals(context: str, prompt_path: str | Path) -> list[dict]:
    prompt = Path(prompt_path).read_text(encoding="utf-8")
    response = _get_client().chat.completions.create(
        model=MODEL_NAME,
        messages=[{"role": "system", "content": prompt}, {"role": "user", "content": context}],
        max_tokens=12000,
        response_format={"type": "json_object"},
    )
    try:
        return json.loads(response.choices[0].message.content or "{}").get("cards", [])
    except (json.JSONDecodeError, AttributeError):
        return []

    if not markdown_text or not markdown_text.strip():
        logger.warning("No markdown text to format")
        return ""

    client = _get_client()
    try:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": markdown_text},
            ],
            max_tokens=16000,
        )
        content = response.choices[0].message.content
        return content or ""
    except Exception as e:
        logger.error(f"Markdown formatting API call failed: {e}")
        return ""
