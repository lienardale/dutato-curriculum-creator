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
    """Load and merge all extracted intermediate JSONs.

    Preserves the ``images`` list on each section (if present) so that
    downstream chunking can propagate image references into chunks.
    """
    all_sections = []
    for json_file in sorted(extracted_dir.glob("*.json")):
        with open(json_file, encoding="utf-8") as f:
            data = json.load(f)
        source_path = data.get("source_path", str(json_file))
        for section in data.get("sections", []):
            section["_source"] = source_path
            section["_file"] = json_file.name
            # images list is preserved as-is (may be absent)
            all_sections.append(section)
    return all_sections


def _build_section_index(
    sections: list[dict],
) -> tuple[dict[str, str], dict[str, str], dict[str, list[dict]], dict[str, dict]]:
    """Build lookups: normalized_title → content, source_path, images, metadata."""
    content_index: dict[str, str] = {}
    source_index: dict[str, str] = {}
    images_index: dict[str, list[dict]] = {}
    meta_index: dict[str, dict] = {}
    for section in sections:
        title = section.get("title", "")
        if title:
            norm = _normalize(title)
            # Also index a file-qualified key ("file.json::Title") so
            # source_sections can disambiguate titles that repeat across
            # sources
            keys = [norm, norm.lower()]
            src_file = section.get("_file", "")
            if src_file:
                keys += [f"{src_file}::{norm}", f"{src_file}::{norm}".lower()]
            for key in keys:
                content_index[key] = section.get("content", "")
                source_index[key] = section.get("_source", "")
                imgs = section.get("images", [])
                if imgs:
                    images_index[key] = imgs
                meta = section.get("metadata") or {}
                if meta:
                    meta_index[key] = meta
    return content_index, source_index, images_index, meta_index


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
                "split_after_headings": item.get("split_after_headings"),
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


# Target token range used for report flagging (the rubric range; the
# chunker's own splitting bounds are the --max-tokens/--min-tokens args).
REPORT_TOKEN_RANGE = (500, 1500)


def _image_offset(img: dict, sec_start: int, sec_end: int, meta: dict) -> int:
    """Estimate an image's char offset within [sec_start, sec_end).

    PDF images carry ``context: "page:N"`` and PDF sections carry
    ``metadata.page_start/page_end`` — interpolate the page position into
    the section's char span. Without a position signal, anchor at the
    start of the owning section.
    """
    context = img.get("context", "")
    page_start = meta.get("page_start")
    page_end = meta.get("page_end")
    if context.startswith("page:") and page_start and page_end and page_end > page_start:
        try:
            page = int(context.split(":", 1)[1])
        except ValueError:
            return sec_start
        frac = (page - page_start + 0.5) / (page_end - page_start + 1)
        frac = min(max(frac, 0.0), 1.0)
        return sec_start + int(frac * (sec_end - sec_start))
    return sec_start


def _attach_images(
    chunks: list[dict],
    placements: list[tuple[dict, int]],
    total_text_len: int,
) -> None:
    """Assign each image to the chunk covering its source-text offset.

    Chunk contents are paragraph-joins of the same text (modulo whitespace
    normalization), so cumulative content length maps offsets to chunks
    proportionally.
    """
    if not chunks or not placements:
        return
    lengths = [len(c["content"]) + 2 for c in chunks]
    total_chunk_len = sum(lengths)
    for img, offset in placements:
        frac = offset / max(total_text_len, 1)
        target = frac * total_chunk_len
        cum = 0
        idx = len(chunks) - 1
        for i, length in enumerate(lengths):
            cum += length
            if target < cum:
                idx = i
                break
        chunks[idx].setdefault("images", []).append(img)


def _chunk_by_source_sections(
    topics: list[dict],
    section_index: dict[str, str],
    source_index: dict[str, str],
    images_index: dict[str, list[dict]] | None = None,
    meta_index: dict[str, dict] | None = None,
    max_tokens: int = 1500,
    min_tokens: int = 200,
    report: dict | None = None,
) -> list[dict]:
    """
    Chunk by directly mapping source_sections to extracted content.

    This approach bypasses the synthetic page system and works correctly
    regardless of how many sections exist in the extracted data.

    When *report* is given, coverage problems (unmatched source_sections,
    leaves with zero chunks, out-of-range token counts) are recorded in it
    instead of being silently dropped.
    """
    leaves = _collect_leaf_topics(topics)
    all_chunks = []
    images_index = images_index or {}
    meta_index = meta_index or {}

    def _lookup(title: str):
        """Resolve one section title -> (content, source, images, meta) or None."""
        norm = _normalize(title)
        content = section_index.get(norm) or section_index.get(norm.lower())
        if not content:
            return None
        if report is not None:
            report["_used_keys"].add(norm.lower())
            if "::" in norm:
                # Qualified ref — mark the bare title as used too so the
                # orphan check (which is title-keyed) sees it
                report["_used_keys"].add(norm.split("::", 1)[1].strip().lower())
        return (
            content,
            source_index.get(norm) or source_index.get(norm.lower(), ""),
            images_index.get(norm) or images_index.get(norm.lower(), []),
            meta_index.get(norm) or meta_index.get(norm.lower(), {}),
        )

    for leaf in leaves:
        parts: list[str] = []
        sources = set()
        # (image, char offset into full_text)
        image_placements: list[tuple[dict, int]] = []
        unmatched: list[str] = []
        offset = 0

        def _add_part(content: str, src: str, imgs: list[dict], meta: dict) -> None:
            nonlocal offset
            sec_start = offset
            sec_end = offset + len(content)
            parts.append(content)
            if src:
                sources.add(src)
            for img in imgs:
                image_placements.append(
                    (img, _image_offset(img, sec_start, sec_end, meta))
                )
            offset = sec_end + 2  # account for the "\n\n" join

        # Collect content from all source_sections
        for sec_title in leaf["source_sections"]:
            found = _lookup(sec_title)
            if found:
                _add_part(*found)
            else:
                unmatched.append(sec_title)

        # Fallback: try matching the topic title itself
        if not parts:
            found = _lookup(leaf["title"])
            if found:
                _add_part(*found)

        if unmatched and report is not None:
            report["unmatched_source_sections"].append({
                "topic_path": leaf["topic_path"],
                "missing": unmatched,
            })

        if not parts:
            if report is not None:
                report["empty_topics"].append(leaf["topic_path"])
            continue

        full_text = "\n\n".join(parts)
        source_path = next(iter(sources), "")
        chunks = chunk_text(
            full_text,
            max_tokens=max_tokens,
            min_tokens=min_tokens,
            split_after_headings=leaf.get("split_after_headings"),
        )

        for i, chunk in enumerate(chunks):
            chunk["topic_path"] = leaf["topic_path"]
            chunk["chunk_index"] = i
            chunk["page_number"] = 1
            if source_path:
                chunk["_source"] = source_path
            if report is not None:
                lo, hi = REPORT_TOKEN_RANGE
                if not (lo <= chunk["token_count"] <= hi):
                    report["out_of_range_chunks"].append({
                        "topic_path": leaf["topic_path"],
                        "chunk_index": i,
                        "token_count": chunk["token_count"],
                    })
            all_chunks.append(chunk)

        # Attach images to the chunk nearest their source position
        _attach_images(chunks, image_placements, len(full_text))

    return all_chunks


def _new_report() -> dict:
    return {
        "unmatched_source_sections": [],
        "empty_topics": [],
        "orphan_sections": [],
        "out_of_range_chunks": [],
        "totals": {},
        "_used_keys": set(),
    }


def _finalize_report(report: dict, sections: list[dict], chunks: list[dict]) -> dict:
    """Compute orphan sections + totals; strip internal bookkeeping."""
    used = report.pop("_used_keys")
    seen: set[str] = set()
    for section in sections:
        title = _normalize(section.get("title", ""))
        if not title or title.lower() in seen:
            continue
        seen.add(title.lower())
        if title.lower() not in used:
            report["orphan_sections"].append({
                "source": section.get("_source", ""),
                "title": title,
            })
    report["totals"] = {
        "sections": len(seen),
        "sections_used": len(used),
        "chunks": len(chunks),
        "tokens": sum(c.get("token_count", 0) for c in chunks),
    }
    return report


def bridge_and_chunk(
    structure_path: Path,
    extracted_dir: Path,
    max_tokens: int = 1500,
    min_tokens: int = 200,
) -> tuple[list[dict], dict | None]:
    """
    Main bridge function: reads structure + extracted data,
    converts formats, runs chunking, returns (chunks, coverage_report).

    Uses source_sections-based direct chunking when available (works for
    any document size). Falls back to synthetic page alignment for legacy
    structures without source_sections (no coverage report in that mode).
    """
    # Load structure
    with open(structure_path, encoding="utf-8") as f:
        structure = json.load(f)

    # Handle both flat and nested structure formats
    topics = structure if isinstance(structure, list) else structure.get("topics", [])

    # Load all extracted sections
    sections = _load_extracted_sections(extracted_dir)
    section_index, source_index, images_index, meta_index = _build_section_index(sections)

    # Prefer source_sections-based chunking (handles large documents correctly)
    if _has_source_sections(topics):
        report = _new_report()
        chunks = _chunk_by_source_sections(
            topics, section_index, source_index, images_index, meta_index,
            max_tokens=max_tokens, min_tokens=min_tokens, report=report,
        )
        return chunks, _finalize_report(report, sections, chunks)

    # Legacy fallback: synthetic page alignment (for structures without source_sections)
    topic_tree, _ = _convert_structure_to_topic_tree(topics, section_index)
    pages = _sections_to_pages(sections)

    if not pages:
        return [], None

    chunks = chunk_by_topics(pages, topic_tree)
    return chunks, None


def main():
    parser = argparse.ArgumentParser(description="Bridge intermediate format to chunking")
    parser.add_argument("--structure", required=True, help="Path to structure.json")
    parser.add_argument("--extracted", required=True, help="Path to extracted/ directory")
    parser.add_argument("-o", "--output", required=True, help="Output chunks.json path")
    parser.add_argument("--max-tokens", type=int, default=1500,
                        help="Maximum tokens per chunk (default: 1500)")
    parser.add_argument("--min-tokens", type=int, default=200,
                        help="Minimum tokens before a chunk may be split off (default: 200)")
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

    chunks, report = bridge_and_chunk(
        structure_path, extracted_dir,
        max_tokens=args.max_tokens, min_tokens=args.min_tokens,
    )

    # Write output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(chunks, f, ensure_ascii=False, indent=2)

    total_tokens = sum(c.get("token_count", 0) for c in chunks)
    console.print(
        f"  [green]✓[/] {len(chunks)} chunks, {total_tokens:,} tokens → {output_path}"
    )

    # Write the coverage report and surface its findings
    if report is not None:
        report_path = output_path.parent / "chunk_report.json"
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        console.print(f"  Coverage report → {report_path}")

        for entry in report["unmatched_source_sections"]:
            console.print(
                f"  [yellow]Warning:[/] {' > '.join(entry['topic_path'])} — "
                f"unmatched source_sections: {entry['missing']}"
            )
        for topic_path in report["empty_topics"]:
            console.print(
                f"  [yellow]Warning:[/] {' > '.join(topic_path)} produced NO "
                f"chunks — its content is missing from the curriculum"
            )
        for orphan in report["orphan_sections"]:
            console.print(
                f"  [yellow]Warning:[/] extracted section {orphan['title']!r} "
                f"({orphan['source']}) is referenced by no topic — dropped"
            )
        if report["out_of_range_chunks"]:
            console.print(
                f"  [yellow]Warning:[/] {len(report['out_of_range_chunks'])} "
                f"chunk(s) outside the {REPORT_TOKEN_RANGE[0]}-"
                f"{REPORT_TOKEN_RANGE[1]} token target range"
            )


if __name__ == "__main__":
    main()
