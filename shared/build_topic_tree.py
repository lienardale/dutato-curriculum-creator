"""
Build a hierarchical topic tree from extracted PDF data.

Uses TOC bookmarks when available, falls back to heuristic heading detection.
Outputs a JSON tree structure ready for Supabase insertion.
"""

import json
import re
import sys
from pathlib import Path


# Front-matter titles to skip (case-insensitive exact match)
_FRONT_MATTER_TITLES = {
    "title page", "copyright page", "copyright", "dedication",
    "contents", "table of contents", "foreword", "preface",
    "acknowledgments", "acknowledgements", "about the author",
    "about the authors", "on the cover", "praise for",
    "other books", "more praise", "endorsements",
}


def _is_front_matter(title: str) -> bool:
    """Check if a TOC entry is front-matter that should be skipped."""
    lower = title.lower().strip()
    if lower in _FRONT_MATTER_TITLES:
        return True
    # Also match patterns like "Praise for Clean Code"
    for prefix in ("praise for", "other books by", "also by"):
        if lower.startswith(prefix):
            return True
    return False


def build_from_toc(toc: list[dict], total_pages: int) -> list[dict]:
    """Build topic tree from PDF bookmarks/TOC."""
    if not toc:
        return []

    # Filter out citation references, very short fragments, and front-matter
    toc = [
        entry for entry in toc
        if not _CITATION_PATTERN.match(entry["title"].strip())
        and len(entry["title"].strip()) >= 3
        and not _is_front_matter(entry["title"].strip())
    ]
    if not toc:
        return []

    # Normalize levels to start at 1
    min_level = min(entry["level"] for entry in toc)
    for entry in toc:
        entry["level"] = entry["level"] - min_level + 1

    # Build tree
    root_nodes = []
    stack: list[dict] = []  # stack of (node, level) for parent tracking

    for i, entry in enumerate(toc):
        # Calculate page_end from next entry at same or higher level
        page_end = total_pages
        for j in range(i + 1, len(toc)):
            if toc[j]["level"] <= entry["level"]:
                page_end = toc[j]["page"] - 1
                break

        node = {
            "title": entry["title"],
            "depth": entry["level"] - 1,  # 0-indexed depth
            "page_start": entry["page"],
            "page_end": max(page_end, entry["page"]),
            "sort_order": i,
            "children": [],
        }

        # Find parent: pop stack until we find a node with smaller depth
        while stack and stack[-1][1] >= entry["level"]:
            stack.pop()

        if stack:
            stack[-1][0]["children"].append(node)
        else:
            root_nodes.append(node)

        stack.append((node, entry["level"]))

    return root_nodes


def _merge_fragmented_lines(lines: list[str]) -> list[str]:
    """
    Merge lines that are fragments of split words from PDF layout.

    PDFs often split "SOFTWARE" across lines as "S\\nOFTWARE" or
    "TE\\nMPLATE". This merges short uppercase fragments with the next line.
    """
    merged = []
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        # If line is a short (1-3 chars) uppercase fragment and the next line
        # continues with uppercase, it's likely a fragmented word from PDF layout.
        if (
            1 <= len(line) <= 3
            and line.isalpha()
            and line.isupper()
            and i + 1 < len(lines)
            and lines[i + 1].strip()
            and lines[i + 1].strip()[0].isupper()
        ):
            # Merge: "S" + "OFTWARE" → "SOFTWARE", "TE" + "MPLATE" → "TEMPLATE"
            merged.append(line + lines[i + 1].strip())
            i += 2
        else:
            merged.append(line)
            i += 1
    return merged


# Pattern for citation references like [Gof96], [KP88], [Mar96] etc.
_CITATION_PATTERN = re.compile(r"^\[.+\]\.?$")

# Pattern for fragments that are clearly not real headings:
# single words that start lowercase, very short fragments, etc.
_FRAGMENT_PATTERN = re.compile(r"^[a-z]")


def detect_headings_heuristic(pages: list[dict]) -> list[dict]:
    """
    Detect chapter/section headings using text patterns when no TOC is available.

    Patterns detected:
    - "Chapter N" or "CHAPTER N"
    - "Part N" or "PART N"
    - Numbered sections: "1.", "1.1", "1.1.1"
    - ALL CAPS lines (likely headings)
    """
    headings = []

    chapter_pattern = re.compile(
        r"^(?:chapter|part)\s+(\d+|[ivxlc]+)[:\.\s]*(.*)",
        re.IGNORECASE,
    )
    # Require a period or colon after the number to avoid matching footnotes.
    # "1 will also cause..." is a footnote. "1. Introduction" or "1.1 Scope" are sections.
    numbered_section = re.compile(
        r"^(\d+(?:\.\d+)*)[:\.][\s]+(.+)",
    )

    for page_info in pages:
        page_num = page_info["page"]
        raw_lines = page_info["text"].split("\n")
        lines = _merge_fragmented_lines(raw_lines)

        for line in lines:
            line = line.strip()
            if not line or len(line) < 3 or len(line) > 200:
                continue

            # Skip citation references like [Gof96]
            if _CITATION_PATTERN.match(line):
                continue

            # Check for "Chapter N" pattern
            match = chapter_pattern.match(line)
            if match:
                title = match.group(2).strip() if match.group(2) else line
                headings.append({
                    "title": title or line,
                    "level": 1,
                    "page": page_num,
                })
                continue

            # Check for numbered sections
            match = numbered_section.match(line)
            if match:
                number = match.group(1)
                title = match.group(2).strip()
                depth = number.count(".") + 1
                # Filter out footnotes: real section titles are short and
                # title-cased, not full sentences.
                is_footnote = (
                    len(title) > 80  # Section titles are usually short
                    or (title and title[0].islower())  # Starts lowercase
                    or title.endswith((".", ",", ";"))  # Sentence ending
                )
                if depth <= 3 and not is_footnote:
                    headings.append({
                        "title": f"{number} {title}",
                        "level": depth,
                        "page": page_num,
                    })
                continue

            # Check for ALL CAPS short lines (likely section headers)
            # Additional filters: must have at least 2 words and no single-word fragments
            words = line.split()
            if (
                line.isupper()
                and 2 <= len(words) <= 8
                and len(line) > 8
                and not line.startswith(("HTTP", "URL", "API", "SQL", "HTML", "["))
                and not _CITATION_PATTERN.match(line)
            ):
                headings.append({
                    "title": line.title(),
                    "level": 1,
                    "page": page_num,
                })

    return headings


def build_topic_tree(
    extracted_data: dict, book_title: str | None = None
) -> list[dict]:
    """
    Build topic tree from extracted PDF/DOCX data.
    Uses TOC if available, otherwise falls back to heuristics.

    Args:
        extracted_data: Extracted PDF data with pages, toc, metadata.
        book_title: Override title for single-topic fallback (avoids ugly
                    PDF metadata like "SRP.fm" or "Patterns.PDF").
    """
    toc = extracted_data.get("toc", [])
    total_pages = extracted_data["metadata"]["total_pages"]

    if toc and len(toc) >= 3:
        # Use TOC bookmarks
        return build_from_toc(toc, total_pages)

    if total_pages > 20:
        # Fall back to heuristic heading detection for longer docs
        headings = detect_headings_heuristic(extracted_data["pages"])
        if headings:
            return build_from_toc(headings, total_pages)

    # Short documents or no headings detected: single topic for the whole book
    title = book_title or extracted_data["metadata"]["title"]
    return [{
        "title": title,
        "depth": 0,
        "page_start": 1,
        "page_end": total_pages,
        "sort_order": 0,
        "children": [],
    }]


def count_nodes(tree: list[dict]) -> int:
    """Count total nodes in tree."""
    count = 0
    for node in tree:
        count += 1
        count += count_nodes(node.get("children", []))
    return count


def main():
    if len(sys.argv) < 2:
        print("Usage: python build_topic_tree.py <extracted.json> [output.json]")
        sys.exit(1)

    input_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2]) if len(sys.argv) > 2 else input_path.with_suffix(".topics.json")

    from rich.console import Console
    console = Console()

    with open(input_path, encoding="utf-8") as f:
        data = json.load(f)

    console.print(f"[bold blue]Building topic tree:[/] {input_path.name}")
    tree = build_topic_tree(data)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(tree, f, ensure_ascii=False, indent=2)

    total = count_nodes(tree)
    root_count = len(tree)
    console.print(
        f"  [green]✓[/] {total} topics ({root_count} root chapters) → {output_path}"
    )


if __name__ == "__main__":
    main()
