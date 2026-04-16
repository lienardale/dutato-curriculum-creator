"""
OCR utility for extracting text from images and scanned PDFs.

Uses EasyOCR (optional dependency) for local, offline text recognition.
Falls back gracefully when EasyOCR is not installed.

Usage:
    from ocr import ocr_image, ocr_pdf_page, is_ocr_available

    if is_ocr_available():
        text = ocr_image("path/to/image.png")
        text = ocr_pdf_page(doc, page_num)
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

_reader = None
_ocr_checked = False
_ocr_available = False


def is_ocr_available() -> bool:
    """Check if EasyOCR is installed and usable."""
    global _ocr_checked, _ocr_available
    if _ocr_checked:
        return _ocr_available
    _ocr_checked = True
    try:
        import easyocr  # noqa: F401
        _ocr_available = True
    except ImportError:
        _ocr_available = False
    return _ocr_available


def _get_reader():
    """Lazy-load the EasyOCR reader (downloads models on first use)."""
    global _reader
    if _reader is not None:
        return _reader
    if not is_ocr_available():
        raise RuntimeError(
            "EasyOCR not installed. Run: uv add easyocr\n"
            "Or: pip install easyocr"
        )
    import easyocr
    _reader = easyocr.Reader(["en"], gpu=False)
    return _reader


def ocr_image(image_path: str | Path, *, detail: bool = False) -> str:
    """Extract text from an image file using OCR.

    Args:
        image_path: Path to the image file.
        detail: If True, return per-region results with bounding boxes.

    Returns:
        Extracted text as a single string (paragraphs joined by newlines).
    """
    reader = _get_reader()
    results = reader.readtext(str(image_path), detail=1)

    if not results:
        return ""

    # Sort by vertical position (top to bottom), then horizontal (left to right)
    results.sort(key=lambda r: (r[0][0][1], r[0][0][0]))

    lines: list[str] = []
    for bbox, text, confidence in results:
        if confidence < 0.15:
            continue
        lines.append(text.strip())

    return "\n".join(lines)


def ocr_image_bytes(image_bytes: bytes) -> str:
    """Extract text from raw image bytes using OCR."""
    reader = _get_reader()
    results = reader.readtext(image_bytes, detail=1)

    if not results:
        return ""

    results.sort(key=lambda r: (r[0][0][1], r[0][0][0]))
    return "\n".join(
        text.strip()
        for _, text, confidence in results
        if confidence >= 0.15
    )


def ocr_pdf_page(doc, page_num: int) -> str:
    """Extract text from a scanned/image-based PDF page using OCR.

    Args:
        doc: An open PyMuPDF (fitz) document.
        page_num: 0-indexed page number.

    Returns:
        OCR'd text for the page.
    """
    page = doc[page_num]

    # Render page to an image at 200 DPI for OCR
    pix = page.get_pixmap(dpi=200)
    img_bytes = pix.tobytes("png")

    return ocr_image_bytes(img_bytes)


def is_scanned_page(page_text: str, page_image_count: int = 0) -> bool:
    """Heuristic: is this PDF page likely scanned (image-based)?

    A page is considered scanned if:
    - It has very little text (< 50 chars) AND at least some visual content, OR
    - The text is mostly garbage (high ratio of non-alphanumeric chars)
    """
    stripped = page_text.strip()

    # Very short text on a page that likely has content
    if len(stripped) < 50:
        return True

    # Check for garbage text (common in scanned PDFs with broken extraction)
    if stripped:
        alpha_ratio = sum(c.isalnum() or c.isspace() for c in stripped) / len(stripped)
        if alpha_ratio < 0.5:
            return True

    return False


def ocr_extracted_images(
    images_dir: str | Path,
    image_registry: list[dict],
) -> dict[str, str]:
    """Run OCR on all extracted images and return id → text mapping.

    Only processes images that appear to contain text (> threshold area).
    Returns a dict mapping image_id to OCR'd text.
    """
    if not is_ocr_available():
        return {}

    result: dict[str, str] = {}
    base = Path(images_dir)

    for img in image_registry:
        local_path = img.get("local_path", "")
        img_id = img.get("id", "")
        if not local_path or not img_id:
            continue

        file_path = base / local_path
        if not file_path.exists():
            # Try relative to parent
            file_path = base.parent / local_path
        if not file_path.exists():
            continue

        try:
            text = ocr_image(file_path)
            if text and len(text.strip()) > 10:
                result[img_id] = text.strip()
        except Exception as e:
            log.warning("OCR failed for %s: %s", img_id, e)

    return result
