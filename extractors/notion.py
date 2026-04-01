"""
Notion export extractor — processes Notion export ZIP files containing
markdown files and converts them to the unified intermediate format.

Notion exports as ZIP contain:
- Markdown (.md) files with page content
- Directories for nested pages
- UUIDs in filenames (e.g., "My Page abc123def456.md")
"""

import re
import sys
import zipfile
from pathlib import Path, PurePosixPath


def _clean_notion_title(filename: str) -> str:
    """Remove Notion's UUID suffix from filenames."""
    # Notion appends a hex UUID like "My Page abc123def456.md"
    name = Path(filename).stem
    # Remove trailing hex UUID (16+ chars)
    cleaned = re.sub(r"\s+[a-f0-9]{16,}$", "", name)
    return cleaned or name


def _parse_markdown_sections(text: str, base_depth: int = 0) -> list[dict]:
    """Parse markdown text into sections based on headings."""
    heading_pattern = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
    matches = list(heading_pattern.finditer(text))

    if not matches:
        return [{
            "title": "Content",
            "content": text.strip(),
            "depth": base_depth,
            "metadata": {},
        }]

    sections = []

    # Text before the first heading
    preamble = text[:matches[0].start()].strip()
    if preamble:
        sections.append({
            "title": "Introduction",
            "content": preamble,
            "depth": base_depth,
            "metadata": {},
        })

    for i, match in enumerate(matches):
        depth = len(match.group(1)) - 1 + base_depth
        heading = match.group(2).strip()

        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        content = text[start:end].strip()

        sections.append({
            "title": heading,
            "content": content,
            "depth": depth,
            "metadata": {},
        })

    return sections


def extract_notion(source: str) -> dict:
    """Extract a Notion export ZIP to the unified intermediate format."""
    zip_path = Path(source)
    if not zip_path.exists():
        raise FileNotFoundError(f"ZIP file not found: {zip_path}")

    if not zipfile.is_zipfile(str(zip_path)):
        raise ValueError(f"Not a valid ZIP file: {zip_path}")

    sections = []
    file_count = 0

    with zipfile.ZipFile(str(zip_path), "r") as zf:
        # Find all markdown files, sorted by path for consistent ordering
        md_files = sorted(
            [n for n in zf.namelist() if n.endswith(".md")],
            key=lambda x: x.lower(),
        )

        for md_name in md_files:
            # Calculate depth from directory nesting
            parts = PurePosixPath(md_name).parts
            depth = len(parts) - 1  # Depth based on directory nesting

            # Clean the title
            title = _clean_notion_title(PurePosixPath(md_name).name)

            # Read content
            try:
                content = zf.read(md_name).decode("utf-8", errors="replace")
            except (KeyError, UnicodeDecodeError):
                continue

            if not content.strip():
                continue

            file_count += 1

            # Parse markdown into sub-sections
            sub_sections = _parse_markdown_sections(content, base_depth=depth)

            # Use the file's title as a parent section if there are sub-sections
            if len(sub_sections) > 1:
                sections.append({
                    "title": title,
                    "content": "",
                    "depth": depth,
                    "metadata": {"file": md_name},
                })
                sections.extend(sub_sections)
            elif sub_sections:
                # Single section — use the file title
                sub_sections[0]["title"] = title
                sub_sections[0]["depth"] = depth
                sub_sections[0]["metadata"]["file"] = md_name
                sections.extend(sub_sections)

    total_tokens = sum(len(s["content"].split()) for s in sections if s["content"])

    # Try to get a title from the top-level directory name
    title = zip_path.stem
    if sections:
        # Use the first section's title if it's at depth 0
        first_depth0 = next(
            (s for s in sections if s["depth"] == 0 and s["title"] != "Content"),
            None,
        )
        if first_depth0:
            title = first_depth0["title"]

    return {
        "source_type": "notion",
        "source_path": str(zip_path.resolve()),
        "title": title,
        "author": "",
        "sections": sections,
        "metadata": {
            "total_sections": len(sections),
            "total_tokens": total_tokens,
            "markdown_files": file_count,
        },
    }


if __name__ == "__main__":
    import json

    if len(sys.argv) < 2:
        print("Usage: python -m extractors.notion <export.zip> [-o output_dir]")
        sys.exit(1)

    from extractors import extract_source
    zip_path = sys.argv[1]
    output_dir = sys.argv[sys.argv.index("-o") + 1] if "-o" in sys.argv else None
    result = extract_source(zip_path, output_dir)
    if not output_dir:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"Extracted {result['metadata']['markdown_files']} files, "
              f"{result['metadata']['total_sections']} sections")
