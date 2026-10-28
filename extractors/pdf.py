"""
PDF extractor — delegates to pdf_pipeline's extract.py and build_topic_tree.py,
then converts to the unified intermediate format.

Section text is cut at heading boundaries, not page boundaries. A section's
content starts at its own heading on its first page and stops at the heading
of the NEXT section in document order, on the page where that section starts.
Adjacent sections therefore no longer share whole pages, and a parent whose
children cover its page range keeps only its own text before the first child
heading instead of duplicating every child page.

Heading matching is fuzzy: whitespace and case are normalized, numbering
prefixes ("3.2", "Chapter 4:", "Part II") are stripped on both sides, a title
may be split over two consecutive lines, and a candidate line (or line pair)
matches when ``difflib.SequenceMatcher`` ratio >= HEADING_MATCH_RATIO. When a
heading is not found the extractor falls back to the full page on that side.
Each section's ``metadata`` records what happened:

    {"page_start": 12, "page_end": 14,      # pages the kept text comes from
     "cut_start": true, "cut_end": false,   # heading cut applied on that side
     "parent_trimmed": true}                # only on parents cut before their first child

Usage:
    python -m extractors.pdf <input.pdf> [-o output_dir]
"""

from __future__ import annotations

import re
import sys
from difflib import SequenceMatcher
from pathlib import Path

# Ensure pdf_pipeline or shared/ is importable
import _compat  # noqa: F401

from extract import extract_pdf as _raw_extract_pdf, extract_pdf_images
from build_topic_tree import build_topic_tree

# A page line (or two-line window) is accepted as a section heading when its
# normalized form reaches this SequenceMatcher ratio against the TOC title.
HEADING_MATCH_RATIO = 0.85

# Tied best matches: one in the top lines of a page loses to a later one, since
# a title repeated at the very top of a page is almost always a running header.
_RUNNING_HEADER_LINES = 2

# A bare page number / chapter number line never opens a two-line heading
# window ("17" + "Layering" is the heading "Layering", not "17 Layering").
_BARE_NUMBER_RE = re.compile(r"^\s*\d{1,4}\s*$")

# Numbering / structure prefixes stripped before comparing headings.
_NUMBERING_PREFIX_RE = re.compile(
    r"^(?:"
    r"(?:chapter|part|section|appendix)\s+(?:\d+|[ivxlc]+|[a-z])\b[\s.:\-–—]*"
    r"|\d+(?:\.\d+)*[.):]?\s+"
    r")",
    re.IGNORECASE,
)


def _flatten_topics(topics: list[dict], depth: int = 0) -> list[dict]:
    """Flatten a nested topic tree into a flat list (document order) with depth."""
    flat = []
    for topic in topics:
        flat.append({
            "title": topic["title"],
            "depth": depth,
            "page_start": topic.get("page_start"),
            "page_end": topic.get("page_end"),
            "sort_order": topic.get("sort_order", 0),
            "has_children": bool(topic.get("children")),
        })
        for child in topic.get("children", []):
            flat.extend(_flatten_topics([child], depth + 1))
    return flat


# ---------------------------------------------------------------------------
# Heading detection
# ---------------------------------------------------------------------------

def _normalize_heading(text: str) -> str:
    """Comparison form of a heading: lowercase, single-spaced, numbering
    prefixes and surrounding punctuation removed."""
    s = re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip().lower()
    for _ in range(3):  # "Part II: 3.1 Foo" carries two prefixes
        stripped = _NUMBERING_PREFIX_RE.sub("", s).strip()
        if stripped == s or not stripped:  # "Section A" / "Chapter 1" are whole titles
            break
        s = stripped
    return re.sub(r"^[\W_]+|[\W_]+$", "", s)


def _heading_ratio(target: str, candidate: str) -> float:
    """SequenceMatcher ratio with cheap upper-bound pruning."""
    if not target or not candidate:
        return 0.0
    if target == candidate:
        return 1.0
    sm = SequenceMatcher(None, target, candidate, autojunk=False)
    if sm.real_quick_ratio() < HEADING_MATCH_RATIO or sm.quick_ratio() < HEADING_MATCH_RATIO:
        return 0.0
    return sm.ratio()


def _find_heading(lines: list[str], title: str, start: int = 0) -> tuple[int, int] | None:
    """Locate *title* as a heading among page *lines*, searching from index *start*.

    Candidates are each line alone and each pair of consecutive lines (a title
    split over two lines). Returns ``(first_line, line_after_heading)`` for the
    best match, or None when nothing reaches HEADING_MATCH_RATIO.
    """
    target = _normalize_heading(title)
    if len(target) < 3:
        return None

    best: list[tuple[int, int]] = []
    best_ratio = 0.0
    for i in range(start, len(lines)):
        for span in (1, 2):
            if i + span > len(lines):
                break
            if span == 2 and _BARE_NUMBER_RE.match(lines[i]):
                break
            candidate = _normalize_heading(" ".join(lines[i:i + span]))
            ratio = _heading_ratio(target, candidate)
            if ratio < HEADING_MATCH_RATIO:
                continue
            if ratio > best_ratio + 1e-9:
                best_ratio, best = ratio, [(i, span)]
            elif abs(ratio - best_ratio) <= 1e-9:
                best.append((i, span))
    if not best:
        return None

    # Overlapping ties (a two-line window and its second line alone) are one
    # heading: keep the earliest start. Distinct ties in the top lines of the
    # page lose to the next one down (running-header avoidance).
    spans: list[tuple[int, int]] = []
    for line, span in sorted(best):
        if spans and line < spans[-1][1]:
            continue
        spans.append((line, line + span))
    if len(spans) > 1 and spans[0][0] < _RUNNING_HEADER_LINES:
        return spans[1]
    return spans[0]


def _locate_headings(flat_topics: list[dict], page_lines: dict[int, list[str]]) -> list[tuple[int, int] | None]:
    """Find every section's heading on its first page.

    Sections that start on the same page are searched in document order, each
    one after the previous section's heading, so two headings on one page
    cannot resolve to the same (or an out-of-order) line.
    """
    positions: list[tuple[int, int] | None] = []
    search_from: dict[int, int] = {}  # page -> line index after the last heading found there
    for topic in flat_topics:
        page = topic.get("page_start")
        if not page:
            positions.append(None)
            continue
        found = _find_heading(page_lines.get(page, []), topic["title"], search_from.get(page, 0))
        positions.append(found)
        if found:
            search_from[page] = found[1]
    return positions


def _slice_pages(
    page_lines: dict[int, list[str]],
    first_page: int,
    last_page: int,
    start_line: int | None,
    end_line: int | None,
) -> str:
    """Join the text of pages first_page..last_page, cutting the first page at
    *start_line* and the last page before *end_line* (None = whole page)."""
    parts: list[str] = []
    for page in range(first_page, last_page + 1):
        lines = page_lines.get(page)
        if lines is None:
            continue
        lo = start_line if (page == first_page and start_line is not None) else 0
        hi = end_line if (page == last_page and end_line is not None) else len(lines)
        segment = "\n".join(lines[lo:hi])
        if segment.strip():
            parts.append(segment)
    return "\n\n".join(parts)


def _section_bounds(
    flat_topics: list[dict],
    index: int,
    positions: list[tuple[int, int] | None],
) -> tuple[int, int, int | None, int | None]:
    """Compute (first_page, last_page, start_line, end_line) for one section.

    The section ends at the next section's heading when that heading was found
    on the page where the next section starts. Without a heading match the end
    falls back to the full page when the TOC places both sections on it, and to
    the section's own TOC page range otherwise (so a later page is never pulled
    in whole).
    """
    topic = flat_topics[index]
    first_page = topic["page_start"]
    toc_end = topic.get("page_end") or first_page
    last_page = max(toc_end, first_page)
    own = positions[index]
    start_line = own[0] if own else None
    end_line: int | None = None

    nxt = flat_topics[index + 1] if index + 1 < len(flat_topics) else None
    next_start = nxt.get("page_start") if nxt else None
    if next_start and next_start >= first_page:
        next_heading = positions[index + 1]
        if next_heading is not None:
            last_page = next_start
            end_line = next_heading[0]
        elif next_start <= toc_end:
            last_page = next_start
    return first_page, last_page, start_line, end_line


def extract_pdf(source: str, *, images_dir: str | None = None, ocr: bool = True) -> dict:
    """Extract a PDF file to the unified intermediate format.

    Args:
        source: Path to the PDF file.
        images_dir: If provided, extract images to this directory.
        ocr: If True, run OCR on pages that appear scanned/image-based.
    """
    file_path = Path(source)
    if not file_path.exists():
        raise FileNotFoundError(f"PDF not found: {file_path}")

    # Use existing pdf_pipeline extraction
    raw = _raw_extract_pdf(file_path)
    pages = raw["pages"]
    metadata = raw["metadata"]

    # OCR scanned pages when text extraction yields little/no content
    ocr_pages = 0
    if ocr:
        try:
            from ocr import is_ocr_available, is_scanned_page, ocr_pdf_page
            if is_ocr_available():
                import fitz
                doc = fitz.open(str(file_path))
                total_pages = len(doc)

                for page_num in range(total_pages):
                    pg_1indexed = page_num + 1
                    existing = next(
                        (p for p in pages if p["page"] == pg_1indexed), None
                    )
                    existing_text = existing["text"] if existing else ""

                    if is_scanned_page(existing_text):
                        ocr_text = ocr_pdf_page(doc, page_num)
                        if ocr_text and len(ocr_text.strip()) > len(existing_text.strip()):
                            if existing:
                                existing["text"] = ocr_text
                            else:
                                pages.append({"page": pg_1indexed, "text": ocr_text})
                            ocr_pages += 1

                doc.close()
                # Re-sort pages after OCR additions
                if ocr_pages:
                    pages.sort(key=lambda p: p["page"])
        except ImportError:
            pass  # OCR not available — continue without

    # Extract images if images_dir provided
    image_registry: list[dict] = []
    if images_dir:
        image_registry = extract_pdf_images(file_path, images_dir)

    # Build topic tree from TOC/headings
    topic_tree = build_topic_tree(raw, book_title=metadata.get("title"))
    flat_topics = _flatten_topics(topic_tree)

    # Index images by page for assignment to sections
    images_by_page: dict[int, list[dict]] = {}
    for img in image_registry:
        pg = img.get("page", 0)
        images_by_page.setdefault(pg, []).append(img)

    page_lines = {p["page"]: p["text"].split("\n") for p in pages}
    positions = _locate_headings(flat_topics, page_lines)

    # Convert to sections, cutting each one at heading boundaries
    sections = []
    for i, topic in enumerate(flat_topics):
        section_meta: dict = {
            "page_start": topic.get("page_start"),
            "page_end": topic.get("page_end"),
            "cut_start": False,
            "cut_end": False,
        }
        content = ""
        section_images: list[dict] = []
        if topic.get("page_start"):
            first_page, last_page, start_line, end_line = _section_bounds(flat_topics, i, positions)
            content = _slice_pages(page_lines, first_page, last_page, start_line, end_line)
            toc_end = max(topic.get("page_end") or first_page, first_page)
            section_meta.update({
                "page_start": first_page,
                "page_end": last_page,
                "cut_start": start_line is not None,
                "cut_end": end_line is not None,
            })
            if topic["has_children"] and (last_page < toc_end or end_line is not None):
                section_meta["parent_trimmed"] = True

            # Collect images that fall within the pages the section's text comes from
            for pg in range(first_page, last_page + 1):
                for img in images_by_page.get(pg, []):
                    section_images.append({
                        "id": img["id"],
                        "local_path": img["local_path"],
                        "alt_text": "",
                        "context": f"page:{pg}",
                    })

        section: dict = {
            "title": topic["title"],
            "content": content,
            "depth": topic["depth"],
            "metadata": section_meta,
        }
        if section_images:
            section["images"] = section_images
        sections.append(section)

    # Count tokens
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        total_tokens = sum(len(enc.encode(s["content"])) for s in sections if s["content"])
    except ImportError:
        total_tokens = sum(len(s["content"].split()) for s in sections if s["content"])

    result: dict = {
        "source_type": "pdf",
        "source_path": str(file_path.resolve()),
        "title": metadata.get("title", file_path.stem),
        "author": metadata.get("author", ""),
        "sections": sections,
        "metadata": {
            "total_sections": len(sections),
            "total_tokens": total_tokens,
            "total_pages": metadata.get("total_pages", len(pages)),
            "total_images": len(image_registry),
            "ocr_pages": ocr_pages,
        },
    }
    if image_registry:
        result["images"] = image_registry
    return result


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
