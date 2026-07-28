"""PDF processing: renders each page to a PNG image in memory."""

from pathlib import Path
from typing import List

import fitz  # PyMuPDF


def extract_pages(pdf_path: str | Path) -> List[bytes]:
    """Open a PDF and render each page as a PNG image (200 DPI).

    Args:
        pdf_path: Path to the PDF file.

    Returns:
        A list of PNG image bytes, one per page.

    Raises:
        FileNotFoundError: If the PDF does not exist.
        fitz.FileDataError: If the PDF is invalid or corrupted.
    """
    pdf_path = Path(pdf_path)

    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    doc = fitz.open(str(pdf_path))
    try:
        images: List[bytes] = []
        for page_num in range(len(doc)):
            page = doc.load_page(page_num)
            # Render at 200 DPI (72 DPI is the default, so 200/72 ≈ 2.78)
            zoom = 200 / 72
            mat = fitz.Matrix(zoom, zoom)
            pix = page.get_pixmap(matrix=mat)

            images.append(pix.tobytes(output="png"))

        return images
    finally:
        doc.close()
