"""
PDF extractor — delegates to pdf_pipeline's extract.py and build_topic_tree.py,
then converts to the unified intermediate format.
"""

import sys
from pathlib import Path

# Ensure pdf_pipeline or shared/ is importable
import _compat  # noqa: F401

from extract import extract_pdf as _raw_extract_pdf
from build_topic_tree import build_topic_tree


def _flatten_topics(topics: list[dict], depth: int = 0) -> list[dict]:
    """Flatten a nested topic tree into a flat list with depth."""
    flat = []
    for topic in topics:
        flat.append({
            "title": topic["title"],
            "depth": depth,
            "page_start": topic.get("page_start"),
            "page_end": topic.get("page_end"),
            "sort_order": topic.get("sort_order", 0),
        })
        for child in topic.get("children", []):
            flat.extend(_flatten_topics([child], depth + 1))
    return flat


def _get_text_for_pages(pages: list[dict], start: int, end: int) -> str:
    """Extract text for a page range from the pages array."""
    texts = []
    for page in pages:
        if start <= page["page"] <= end:
            texts.append(page["text"])
    return "\n\n".join(texts)


def extract_pdf(source: str) -> dict:
    """Extract a PDF file to the unified intermediate format."""
    file_path = Path(source)
    if not file_path.exists():
        raise FileNotFoundError(f"PDF not found: {file_path}")

    # Use existing pdf_pipeline extraction
    raw = _raw_extract_pdf(file_path)
    pages = raw["pages"]
    metadata = raw["metadata"]

    # Build topic tree from TOC/headings
    topic_tree = build_topic_tree(raw, book_title=metadata.get("title"))
    flat_topics = _flatten_topics(topic_tree)

    # Convert to sections by attaching page text to each topic
    sections = []
    for topic in flat_topics:
        pg_start = topic.get("page_start")
        pg_end = topic.get("page_end")
        content = ""
        if pg_start and pg_end:
            content = _get_text_for_pages(pages, pg_start, pg_end)
        elif pg_start:
            content = _get_text_for_pages(pages, pg_start, pg_start)

        sections.append({
            "title": topic["title"],
            "content": content,
            "depth": topic["depth"],
            "metadata": {
                "page_start": pg_start,
                "page_end": pg_end,
            },
        })

    # Count tokens
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        total_tokens = sum(len(enc.encode(s["content"])) for s in sections if s["content"])
    except ImportError:
        total_tokens = sum(len(s["content"].split()) for s in sections if s["content"])

    return {
        "source_type": "pdf",
        "source_path": str(file_path.resolve()),
        "title": metadata.get("title", file_path.stem),
        "author": metadata.get("author", ""),
        "sections": sections,
        "metadata": {
            "total_sections": len(sections),
            "total_tokens": total_tokens,
            "total_pages": metadata.get("total_pages", len(pages)),
        },
    }


if __name__ == "__main__":
    import json

    if len(sys.argv) < 2:
        print("Usage: python -m extractors.pdf <input.pdf> [-o output_dir]")
        sys.exit(1)

    pdf_path = sys.argv[1]
    output_dir = None
    if "-o" in sys.argv:
        output_dir = sys.argv[sys.argv.index("-o") + 1]

    from extractors import extract_source
    result = extract_source(pdf_path, output_dir)
    if not output_dir:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"Extracted {result['metadata']['total_sections']} sections, "
              f"{result['metadata']['total_tokens']} tokens")
