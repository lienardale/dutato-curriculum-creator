"""
Chunking bridge — converts the unified intermediate format into the
page-based format that pdf_pipeline's chunk.chunk_by_topics() expects,
then runs the chunking.

Input:
  - structure.json: agent-generated topic hierarchy
  - extracted/*.json: intermediate JSON files from extractors

Output:
  - chunks.json: flat list of chunks with topic_path

Usage:
  uv run python chunk_bridge.py \
    --structure output/my-curriculum/structure.json \
    --extracted output/my-curriculum/extracted/ \
    -o output/my-curriculum/chunks.json
"""

import argparse
import json
import sys
from pathlib import Path

import _compat  # noqa: F401

from chunk import chunk_by_topics, chunk_text, count_tokens


def _normalize(text: str) -> str:
    """Normalize whitespace (including non-breaking spaces) for matching."""
    return text.replace("\xa0", " ").strip()


def _load_extracted_sections(extracted_dir: Path) -> list[dict]:
    """Load and merge all extracted intermediate JSONs."""
    all_sections = []
    for json_file in sorted(extracted_dir.glob("*.json")):
        with open(json_file, encoding="utf-8") as f:
            data = json.load(f)
        source_path = data.get("source_path", str(json_file))
        for section in data.get("sections", []):
            section["_source"] = source_path
            all_sections.append(section)
    return all_sections


def _build_section_index(
    sections: list[dict],
) -> tuple[dict[str, str], dict[str, str]]:
    """Build lookups: normalized_title → content, and normalized_title → source_path."""
    content_index: dict[str, str] = {}
    source_index: dict[str, str] = {}
    for section in sections:
        title = section.get("title", "")
        if title:
            norm = _normalize(title)
            content_index[norm] = section.get("content", "")
            content_index[norm.lower()] = section.get("content", "")
            source_index[norm] = section.get("_source", "")
            source_index[norm.lower()] = section.get("_source", "")
    return content_index, source_index


def _assign_synthetic_pages(sections: list[dict]) -> list[dict]:
    """Assign synthetic page numbers to sections (1-indexed)."""
    pages = []
    for i, section in enumerate(sections, 1):
        content = section.get("content", "")
        if content.strip():
            pages.append({
                "page": i,
                "text": content,
            })
    return pages


def _convert_structure_to_topic_tree(
    structure: list[dict],
    section_index: dict[str, str],
    page_offset: int = 1,
) -> tuple[list[dict], int]:
    """
    Convert agent-generated structure.json into the nested topic tree format
    that chunk_by_topics() expects (with page_start, page_end, children).

    Also assigns synthetic page numbers based on matching section content.

    Returns: (topic_tree, next_page_number)
    """
    topic_tree = []
    current_page = page_offset

    for item in structure:
        title = item.get("title", "Untitled")

        # Try to find matching section content (normalize for non-breaking spaces)
        norm_title = _normalize(title)
        content = (
            section_index.get(norm_title, "")
            or section_index.get(norm_title.lower(), "")
        )

        # Calculate page range
        if content:
            # Estimate how many synthetic pages this content spans
            tokens = count_tokens(content)
            # ~500 tokens per synthetic page
            num_pages = max(1, tokens // 500)
            page_start = current_page
            page_end = current_page + num_pages - 1
            current_page = page_end + 1
        else:
            page_start = current_page
            page_end = current_page
            # Don't advance page counter for empty sections

        topic = {
            "title": title,
            "depth": item.get("depth", 0),
            "sort_order": item.get("sort_order", 0),
            "page_start": page_start,
            "page_end": page_end,
        }

        # Process children recursively
        children = item.get("children", [])
        if children:
            child_topics, current_page = _convert_structure_to_topic_tree(
                children, section_index, current_page
            )
            topic["children"] = child_topics
            # Parent's page_end extends to cover all children
            if child_topics:
                topic["page_end"] = max(
                    topic["page_end"],
                    max(c["page_end"] for c in child_topics),
                )
        else:
            topic["children"] = []

        topic_tree.append(topic)

    return topic_tree, current_page


def _sections_to_pages(sections: list[dict], tokens_per_page: int = 500) -> list[dict]:
    """
    Convert sections into synthetic pages.

    Each section's content is split into pages of ~tokens_per_page tokens.
    """
    pages = []
    page_num = 1

    for section in sections:
        content = section.get("content", "")
        if not content.strip():
            continue

        # Split content into chunks of ~tokens_per_page
        words = content.split()
        # Rough estimate: ~1.3 tokens per word
        words_per_page = int(tokens_per_page / 1.3)

        for i in range(0, len(words), words_per_page):
            page_text = " ".join(words[i:i + words_per_page])
            pages.append({
                "page": page_num,
                "text": page_text,
            })
            page_num += 1

    return pages


def _collect_leaf_topics(
    structure: list[dict],
    parent_path: list[str] | None = None,
) -> list[dict]:
    """Flatten structure into leaf topics with topic_path and source_sections."""
    if parent_path is None:
        parent_path = []
    leaves = []
    for item in structure:
        path = parent_path + [item["title"]]
        children = item.get("children", [])
        if children:
            leaves.extend(_collect_leaf_topics(children, path))
        else:
            leaves.append({
                "topic_path": path,
                "source_sections": item.get("source_sections", []),
                "title": item["title"],
            })
    return leaves


def _has_source_sections(topics: list[dict]) -> bool:
    """Check if any leaf topic in the structure has source_sections."""
    for item in topics:
        children = item.get("children", [])
        if children:
            if _has_source_sections(children):
                return True
        elif item.get("source_sections"):
            return True
    return False


def _chunk_by_source_sections(
    topics: list[dict],
    section_index: dict[str, str],
    source_index: dict[str, str],
) -> list[dict]:
    """
    Chunk by directly mapping source_sections to extracted content.

    This approach bypasses the synthetic page system and works correctly
    regardless of how many sections exist in the extracted data.
    """
    leaves = _collect_leaf_topics(topics)
    all_chunks = []

    for leaf in leaves:
        parts = []
        sources = set()

        # Collect content from all source_sections
        for sec_title in leaf["source_sections"]:
            norm = _normalize(sec_title)
            content = (
                section_index.get(norm)
                or section_index.get(norm.lower())
            )
            if content:
                parts.append(content)
                src = (
                    source_index.get(norm)
                    or source_index.get(norm.lower(), "")
                )
                if src:
                    sources.add(src)

        # Fallback: try matching the topic title itself
        if not parts:
            norm = _normalize(leaf["title"])
            content = (
                section_index.get(norm)
                or section_index.get(norm.lower())
            )
            if content:
                parts.append(content)
                src = (
                    source_index.get(norm)
                    or source_index.get(norm.lower(), "")
                )
                if src:
                    sources.add(src)

        if not parts:
            continue

        full_text = "\n\n".join(parts)
        source_path = next(iter(sources), "")
        chunks = chunk_text(full_text, max_tokens=1500, min_tokens=200)

        for i, chunk in enumerate(chunks):
            chunk["topic_path"] = leaf["topic_path"]
            chunk["chunk_index"] = i
            chunk["page_number"] = 1
            if source_path:
                chunk["_source"] = source_path
            all_chunks.append(chunk)

    return all_chunks


def bridge_and_chunk(structure_path: Path, extracted_dir: Path) -> list[dict]:
    """
    Main bridge function: reads structure + extracted data,
    converts formats, runs chunking, returns chunks.

    Uses source_sections-based direct chunking when available (works for
    any document size). Falls back to synthetic page alignment for legacy
    structures without source_sections.
    """
    # Load structure
    with open(structure_path, encoding="utf-8") as f:
        structure = json.load(f)

    # Handle both flat and nested structure formats
    topics = structure if isinstance(structure, list) else structure.get("topics", [])

    # Load all extracted sections
    sections = _load_extracted_sections(extracted_dir)
    section_index, source_index = _build_section_index(sections)

    # Prefer source_sections-based chunking (handles large documents correctly)
    if _has_source_sections(topics):
        return _chunk_by_source_sections(topics, section_index, source_index)

    # Legacy fallback: synthetic page alignment (for structures without source_sections)
    topic_tree, _ = _convert_structure_to_topic_tree(topics, section_index)
    pages = _sections_to_pages(sections)

    if not pages:
        return []

    chunks = chunk_by_topics(pages, topic_tree)
    return chunks


def main():
    parser = argparse.ArgumentParser(description="Bridge intermediate format to chunking")
    parser.add_argument("--structure", required=True, help="Path to structure.json")
    parser.add_argument("--extracted", required=True, help="Path to extracted/ directory")
    parser.add_argument("-o", "--output", required=True, help="Output chunks.json path")
    args = parser.parse_args()

    structure_path = Path(args.structure)
    extracted_dir = Path(args.extracted)
    output_path = Path(args.output)

    if not structure_path.exists():
        print(f"Error: structure file not found: {structure_path}")
        sys.exit(1)
    if not extracted_dir.exists():
        print(f"Error: extracted directory not found: {extracted_dir}")
        sys.exit(1)

    from rich.console import Console
    console = Console()

    console.print(f"[bold blue]Chunking:[/] {structure_path.name}")
    console.print(f"  Extracted dir: {extracted_dir}")

    chunks = bridge_and_chunk(structure_path, extracted_dir)

    # Write output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(chunks, f, ensure_ascii=False, indent=2)

    total_tokens = sum(c.get("token_count", 0) for c in chunks)
    console.print(
        f"  [green]✓[/] {len(chunks)} chunks, {total_tokens:,} tokens → {output_path}"
    )


if __name__ == "__main__":
    main()
