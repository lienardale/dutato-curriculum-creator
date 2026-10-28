"""
Chunking bridge — converts the unified intermediate format into the
page-based format that pdf_pipeline's chunk.chunk_by_topics() expects,
then runs the chunking.

Input:
  - structure.json: agent-generated topic hierarchy
  - extracted/*.json: intermediate JSON files from extractors

Output:
  - chunks.json: flat list of chunks with topic_path
  - chunk_report.json (next to chunks.json): coverage report

Usage:
  uv run python chunk_bridge.py \\
    --structure output/my-curriculum/structure.json \\
    --extracted output/my-curriculum/extracted/ \\
    -o output/my-curriculum/chunks.json

Section matching
----------------
A ``source_sections`` entry is a section title, matched after whitespace
normalization, case-insensitively, against every extracted file. Sections are
indexed twice: by ``(file, title)`` and by title alone.

* ``"Service Layer"`` — title-only form. When several sections carry the title
  (in one file or across files) ALL matches are used, in file order (files
  sorted by name, sections in document order), and the entry is reported in
  ``chunk_report.json`` under ``ambiguous_titles``.
* ``"peaa.json::Service Layer"`` — disambiguated form: only that extracted
  file's section(s). The prefix is the extracted filename (``.json`` optional).

Every chunk carries ``_source`` = the ``source_path`` of the extracted section
whose text the chunk starts in, so multi-source leaves attribute each chunk to
the right book. The report also lists ``duplicate_titles_in_file`` so the agent
can see which titles are not unique inside one source.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import _compat  # noqa: F401

from chunk import chunk_by_topics, chunk_text, count_tokens

# Separator of the disambiguated "<file.json>::<title>" source_sections form.
FILE_TITLE_SEP = "::"


def _normalize(text: str) -> str:
    """Normalize whitespace (including non-breaking spaces) for matching."""
    return (text or "").replace("\xa0", " ").strip()


def _key(title: str) -> str:
    """Case-insensitive matching key of a section title."""
    return _normalize(title).lower()


@dataclass
class _Section:
    """One extracted section with its origin."""

    file: str          # extracted filename, e.g. "peaa.json"
    index: int         # position in that file's sections list
    title: str
    content: str
    source: str        # source_path of the extracted file
    images: list[dict]
    meta: dict

    def ref(self) -> dict:
        return {"file": self.file, "section_index": self.index}


def _load_extracted_sections(extracted_dir: Path) -> list[_Section]:
    """Load every section of every extracted JSON, in file order."""
    sections: list[_Section] = []
    for json_file in sorted(extracted_dir.glob("*.json")):
        with open(json_file, encoding="utf-8") as f:
            data = json.load(f)
        source_path = data.get("source_path", str(json_file))
        for index, section in enumerate(data.get("sections", [])):
            sections.append(_Section(
                file=json_file.name,
                index=index,
                title=section.get("title", "") or "",
                content=section.get("content", "") or "",
                source=source_path,
                images=section.get("images") or [],
                meta=section.get("metadata") or {},
            ))
    return sections


class _SectionIndex:
    """Title lookups over the extracted sections (only titled, non-empty ones)."""

    def __init__(self, sections: list[_Section]):
        self.sections = sections
        self.files = {s.file for s in sections}
        self.by_title: dict[str, list[_Section]] = {}
        self.by_file_title: dict[tuple[str, str], list[_Section]] = {}
        for s in sections:
            if not s.title or not s.content.strip():
                continue
            k = _key(s.title)
            self.by_title.setdefault(k, []).append(s)
            self.by_file_title.setdefault((s.file, k), []).append(s)

    @property
    def indexed(self) -> list[_Section]:
        return [s for s in self.sections if s.title and s.content.strip()]

    def split_ref(self, ref: str) -> tuple[str | None, str]:
        """``"peaa.json::Title"`` -> ("peaa.json", "Title"); plain titles -> (None, ref).

        The prefix must name an extracted file (``.json`` optional) — a title
        that merely contains ``::`` (``std::vector``) stays a title.
        """
        if FILE_TITLE_SEP in ref:
            prefix, title = ref.split(FILE_TITLE_SEP, 1)
            prefix = _normalize(prefix)
            for candidate in (prefix, prefix + ".json"):
                if candidate in self.files:
                    return candidate, title
        return None, ref

    def lookup(self, ref: str) -> list[_Section]:
        """All sections matching one source_sections entry, in file order."""
        file, title = self.split_ref(ref)
        if file is not None:
            return list(self.by_file_title.get((file, _key(title)), []))
        return list(self.by_title.get(_key(title), []))

    def duplicate_titles_in_file(self) -> list[dict]:
        return [
            {"file": file, "title": matches[0].title, "section_indices": [m.index for m in matches]}
            for (file, _), matches in self.by_file_title.items()
            if len(matches) > 1
        ]


def _legacy_content_index(sections: list[_Section]) -> dict[str, str]:
    """title -> content lookup for the synthetic-page fallback (last title wins)."""
    index: dict[str, str] = {}
    for s in sections:
        if s.title:
            norm = _normalize(s.title)
            index[norm] = s.content
            index[norm.lower()] = s.content
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


def _sections_to_pages(sections: list[_Section], tokens_per_page: int = 500) -> list[dict]:
    """
    Convert sections into synthetic pages.

    Each section's content is split into pages of ~tokens_per_page tokens.
    """
    pages = []
    page_num = 1

    for section in sections:
        content = section.content
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


@dataclass
class _Part:
    """One section's span inside a leaf's joined text."""

    start: int
    end: int
    section: _Section


def _attribute_sources(chunks: list[dict], parts: list[_Part], full_text: str) -> None:
    """Set each chunk's ``_source`` to the section covering the chunk's start.

    Chunk text is the joined text with whitespace normalized, so a chunk's
    first line is found verbatim at or after the previous chunk's end; when it
    is not (unexpected normalization) the start is estimated proportionally.
    """
    if not parts:
        return
    lengths = [len(c["content"]) + 2 for c in chunks]
    total_chunk_len = max(sum(lengths), 1)
    search_pos = 0
    consumed = 0
    for chunk, length in zip(chunks, lengths):
        first_line = chunk["content"].split("\n", 1)[0].strip()
        pos = full_text.find(first_line, search_pos) if first_line else -1
        if pos < 0:
            pos = int(consumed / total_chunk_len * len(full_text))
        part = parts[0]
        for candidate in parts:
            if candidate.start <= pos:
                part = candidate
        if part.section.source:
            chunk["_source"] = part.section.source
        # normalization only shrinks text, so this never overshoots the true end
        search_pos = pos + len(chunk["content"])
        consumed += length


def _chunk_by_source_sections(
    topics: list[dict],
    index: _SectionIndex,
    max_tokens: int = 1500,
    min_tokens: int = 200,
    report: dict | None = None,
) -> list[dict]:
    """
    Chunk by directly mapping source_sections to extracted content.

    This approach bypasses the synthetic page system and works correctly
    regardless of how many sections exist in the extracted data.

    When *report* is given, coverage problems (unmatched or ambiguous
    source_sections, leaves with zero chunks, out-of-range token counts) are
    recorded in it instead of being silently dropped.
    """
    leaves = _collect_leaf_topics(topics)
    all_chunks = []

    for leaf in leaves:
        parts: list[_Part] = []
        # (image, char offset into full_text)
        image_placements: list[tuple[dict, int]] = []
        unmatched: list[str] = []
        offset = 0

        def _add_part(section: _Section) -> None:
            nonlocal offset
            sec_start = offset
            sec_end = offset + len(section.content)
            parts.append(_Part(sec_start, sec_end, section))
            for img in section.images:
                image_placements.append(
                    (img, _image_offset(img, sec_start, sec_end, section.meta))
                )
            if report is not None:
                report["_used"].add((section.file, section.index))
            offset = sec_end + 2  # account for the "\n\n" join

        def _resolve(ref: str) -> bool:
            """Add every section matching *ref*; False when nothing matched."""
            matches = index.lookup(ref)
            if not matches:
                return False
            if len(matches) > 1 and report is not None:
                report["ambiguous_titles"].append({
                    "topic_path": leaf["topic_path"],
                    "title": ref,
                    "matches": [m.ref() for m in matches],
                })
            for section in matches:
                _add_part(section)
            return True

        # Collect content from all source_sections
        for sec_title in leaf["source_sections"]:
            if not _resolve(sec_title):
                unmatched.append(sec_title)

        # Fallback: try matching the topic title itself
        if not parts:
            _resolve(leaf["title"])

        if unmatched and report is not None:
            report["unmatched_source_sections"].append({
                "topic_path": leaf["topic_path"],
                "missing": unmatched,
            })

        if not parts:
            if report is not None:
                report["empty_topics"].append(leaf["topic_path"])
            continue

        full_text = "\n\n".join(p.section.content for p in parts)
        chunks = chunk_text(
            full_text,
            max_tokens=max_tokens,
            min_tokens=min_tokens,
            split_after_headings=leaf.get("split_after_headings"),
        )
        _attribute_sources(chunks, parts, full_text)

        for i, chunk in enumerate(chunks):
            chunk["topic_path"] = leaf["topic_path"]
            chunk["chunk_index"] = i
            chunk["page_number"] = 1
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
        "ambiguous_titles": [],
        "duplicate_titles_in_file": [],
        "empty_topics": [],
        "orphan_sections": [],
        "out_of_range_chunks": [],
        "totals": {},
        "_used": set(),
    }


def _finalize_report(report: dict, index: _SectionIndex, chunks: list[dict]) -> dict:
    """Compute orphan sections, duplicate titles + totals; strip bookkeeping."""
    used: set[tuple[str, int]] = report.pop("_used")
    indexed = index.indexed
    for section in indexed:
        if (section.file, section.index) not in used:
            report["orphan_sections"].append({
                "source": section.source,
                "file": section.file,
                "section_index": section.index,
                "title": _normalize(section.title),
            })
    report["duplicate_titles_in_file"] = index.duplicate_titles_in_file()
    report["totals"] = {
        "sections": len(indexed),
        "sections_used": len(used),
        "empty_sections": len(index.sections) - len(indexed),
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

    # Prefer source_sections-based chunking (handles large documents correctly)
    if _has_source_sections(topics):
        index = _SectionIndex(sections)
        report = _new_report()
        chunks = _chunk_by_source_sections(
            topics, index, max_tokens=max_tokens, min_tokens=min_tokens, report=report,
        )
        return chunks, _finalize_report(report, index, chunks)

    # Legacy fallback: synthetic page alignment (for structures without source_sections)
    topic_tree, _ = _convert_structure_to_topic_tree(topics, _legacy_content_index(sections))
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
        for entry in report["ambiguous_titles"]:
            files = ", ".join(f"{m['file']}#{m['section_index']}" for m in entry["matches"])
            console.print(
                f"  [yellow]Warning:[/] {' > '.join(entry['topic_path'])} — "
                f"{entry['title']!r} matches {len(entry['matches'])} sections ({files}); "
                f"all were used — disambiguate with '<file.json>::<title>'"
            )
        for topic_path in report["empty_topics"]:
            console.print(
                f"  [yellow]Warning:[/] {' > '.join(topic_path)} produced NO "
                f"chunks — its content is missing from the curriculum"
            )
        for orphan in report["orphan_sections"]:
            console.print(
                f"  [yellow]Warning:[/] extracted section {orphan['title']!r} "
                f"({orphan['file']}) is referenced by no topic — dropped"
            )
        for dup in report["duplicate_titles_in_file"]:
            console.print(
                f"  [yellow]Note:[/] {dup['file']} has {len(dup['section_indices'])} sections "
                f"titled {dup['title']!r} (indices {dup['section_indices']})"
            )
        if report["out_of_range_chunks"]:
            console.print(
                f"  [yellow]Warning:[/] {len(report['out_of_range_chunks'])} "
                f"chunk(s) outside the {REPORT_TOKEN_RANGE[0]}-"
                f"{REPORT_TOKEN_RANGE[1]} token target range"
            )


if __name__ == "__main__":
    main()
