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

from chunk import chunk_by_topics, count_tokens


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


def _build_section_index(sections: list[dict]) -> dict[str, str]:
    """Build a lookup: section_title → section_content."""
    index = {}
    for section in sections:
        title = section.get("title", "")
        if title:
            index[title] = section.get("content", "")
            # Also store lowercase version for fuzzy matching
            index[title.lower()] = section.get("content", "")
    return index


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

        # Try to find matching section content
        content = section_index.get(title, "") or section_index.get(title.lower(), "")

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


def bridge_and_chunk(structure_path: Path, extracted_dir: Path) -> list[dict]:
    """
    Main bridge function: reads structure + extracted data,
    converts formats, runs chunking, returns chunks.
    """
    # Load structure
    with open(structure_path, encoding="utf-8") as f:
        structure = json.load(f)

    # Handle both flat and nested structure formats
    topics = structure if isinstance(structure, list) else structure.get("topics", [])

    # Load all extracted sections
    sections = _load_extracted_sections(extracted_dir)
    section_index = _build_section_index(sections)

    # Convert structure to topic tree format
    topic_tree, _ = _convert_structure_to_topic_tree(topics, section_index)

    # Create synthetic pages from sections
    pages = _sections_to_pages(sections)

    if not pages:
        return []

    # Run chunking
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
