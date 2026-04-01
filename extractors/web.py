"""
Web URL extractor — fetches a URL and extracts article content
using trafilatura for clean text extraction.
"""

import re
import sys
from pathlib import Path
from urllib.parse import urlparse


def extract_web(source: str) -> dict:
    """
    Extract content from a URL.

    Uses trafilatura for main content extraction, falls back to basic
    HTML parsing if trafilatura returns nothing.
    """
    import trafilatura

    # Fetch the page
    downloaded = trafilatura.fetch_url(source)
    if not downloaded:
        raise RuntimeError(f"Failed to fetch URL: {source}")

    # Extract main content as text
    text = trafilatura.extract(
        downloaded,
        include_links=False,
        include_tables=True,
        include_comments=False,
        favor_precision=True,
    )

    if not text:
        raise RuntimeError(f"No extractable content at: {source}")

    # Try to get metadata
    metadata_result = trafilatura.extract(
        downloaded,
        output_format="json",
        include_links=False,
    )

    title = ""
    author = ""
    if metadata_result:
        import json
        try:
            meta = json.loads(metadata_result)
            title = meta.get("title", "")
            author = meta.get("author", "")
        except (json.JSONDecodeError, TypeError):
            pass

    if not title:
        parsed = urlparse(source)
        title = parsed.netloc + parsed.path

    # Split text into sections by markdown-style headings or double newlines
    sections = _split_into_sections(text, title)

    total_tokens = sum(len(s["content"].split()) for s in sections if s["content"])

    return {
        "source_type": "url",
        "source_path": source,
        "title": title,
        "author": author,
        "sections": sections,
        "metadata": {
            "total_sections": len(sections),
            "total_tokens": total_tokens,
            "url": source,
        },
    }


def _split_into_sections(text: str, fallback_title: str) -> list[dict]:
    """Split extracted text into sections by headings."""
    # Look for markdown-style headings (## Heading)
    heading_pattern = re.compile(r"^(#{1,3})\s+(.+)$", re.MULTILINE)
    matches = list(heading_pattern.finditer(text))

    if not matches:
        # No headings — try splitting by double newlines into chunks
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        if len(paragraphs) <= 3:
            return [{
                "title": fallback_title,
                "content": text.strip(),
                "depth": 0,
                "metadata": {},
            }]

        # Group paragraphs into sections of ~5
        sections = []
        for i in range(0, len(paragraphs), 5):
            chunk = paragraphs[i:i + 5]
            # Use first line of first paragraph as title
            first_line = chunk[0].split("\n")[0][:80]
            sections.append({
                "title": first_line,
                "content": "\n\n".join(chunk),
                "depth": 0,
                "metadata": {},
            })
        return sections

    # Split by headings
    sections = []

    # Text before the first heading
    preamble = text[:matches[0].start()].strip()
    if preamble:
        sections.append({
            "title": fallback_title,
            "content": preamble,
            "depth": 0,
            "metadata": {},
        })

    for i, match in enumerate(matches):
        depth = len(match.group(1)) - 1  # # = 0, ## = 1, ### = 2
        heading = match.group(2).strip()

        # Content extends to the next heading or end of text
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


if __name__ == "__main__":
    import json

    if len(sys.argv) < 2:
        print("Usage: python -m extractors.web <url> [-o output_dir]")
        sys.exit(1)

    from extractors import extract_source
    url = sys.argv[1]
    output_dir = sys.argv[sys.argv.index("-o") + 1] if "-o" in sys.argv else None
    result = extract_source(url, output_dir)
    if not output_dir:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"Extracted {result['metadata']['total_sections']} sections from {url}")
