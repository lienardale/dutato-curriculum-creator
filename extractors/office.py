"""
Office document extractor — DOCX and PPTX to the unified intermediate format.

DOCX: Delegates to pdf_pipeline's extract.extract_docx() then converts.
PPTX: Uses python-pptx to extract slide content as sections.
"""

import sys
from pathlib import Path

import _compat  # noqa: F401


def _extract_docx(file_path: Path) -> dict:
    """Extract a DOCX file."""
    from extract import extract_docx as _raw_extract_docx

    raw = _raw_extract_docx(file_path)
    pages = raw["pages"]
    toc = raw["toc"]

    # Convert TOC entries to sections with content
    sections = []
    if toc:
        # Assign page text to TOC entries
        for i, entry in enumerate(toc):
            # Find content for this section (simple: page text near the heading)
            content_parts = []
            for page in pages:
                if entry["title"] in page["text"]:
                    content_parts.append(page["text"])
            sections.append({
                "title": entry["title"],
                "content": "\n\n".join(content_parts),
                "depth": entry["level"] - 1,
                "metadata": {},
            })
    else:
        # No headings — treat entire document as one section
        full_text = "\n\n".join(p["text"] for p in pages)
        sections.append({
            "title": file_path.stem,
            "content": full_text,
            "depth": 0,
            "metadata": {},
        })

    total_tokens = sum(len(s["content"].split()) for s in sections if s["content"])

    return {
        "source_type": "docx",
        "source_path": str(file_path.resolve()),
        "title": file_path.stem,
        "author": "",
        "sections": sections,
        "metadata": {
            "total_sections": len(sections),
            "total_tokens": total_tokens,
        },
    }


def _extract_pptx(file_path: Path) -> dict:
    """Extract a PPTX file — each slide becomes a section."""
    from pptx import Presentation

    prs = Presentation(str(file_path))

    sections = []
    for i, slide in enumerate(prs.slides, 1):
        # Get slide title
        title = f"Slide {i}"
        if slide.shapes.title:
            title = slide.shapes.title.text.strip() or title

        # Collect all text from the slide
        texts = []
        for shape in slide.shapes:
            if shape.has_text_frame:
                for paragraph in shape.text_frame.paragraphs:
                    text = paragraph.text.strip()
                    if text:
                        texts.append(text)

        content = "\n\n".join(texts)

        # Collect notes if present
        notes = ""
        if slide.has_notes_slide:
            notes_frame = slide.notes_slide.notes_text_frame
            notes = notes_frame.text.strip()

        sections.append({
            "title": title,
            "content": content,
            "depth": 0,
            "metadata": {
                "slide_number": i,
                "has_notes": bool(notes),
                "notes": notes,
            },
        })

    total_tokens = sum(len(s["content"].split()) for s in sections if s["content"])

    return {
        "source_type": "pptx",
        "source_path": str(file_path.resolve()),
        "title": file_path.stem,
        "author": "",
        "sections": sections,
        "metadata": {
            "total_sections": len(sections),
            "total_tokens": total_tokens,
            "total_slides": len(prs.slides),
        },
    }


def extract_office(source: str) -> dict:
    """Extract DOCX or PPTX file."""
    file_path = Path(source)
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    suffix = file_path.suffix.lower()
    if suffix == ".docx":
        return _extract_docx(file_path)
    elif suffix == ".pptx":
        return _extract_pptx(file_path)
    else:
        raise ValueError(f"Unsupported office format: {suffix}")


if __name__ == "__main__":
    import json

    if len(sys.argv) < 2:
        print("Usage: python -m extractors.office <input.docx|pptx> [-o output_dir]")
        sys.exit(1)

    from extractors import extract_source
    source_path = sys.argv[1]
    output_dir = sys.argv[sys.argv.index("-o") + 1] if "-o" in sys.argv else None
    result = extract_source(source_path, output_dir)
    if not output_dir:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"Extracted {result['metadata']['total_sections']} sections")
