"""
Web URL extractor — fetches a URL and extracts article content
using trafilatura for clean text extraction.

Supports optional multi-page crawling for documentation sites where
the entry page is a table of contents linking to subpages.
"""

import re
import sys
from collections import deque
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.request import urlopen, Request


# ---------------------------------------------------------------------------
# Path-prefix computation
# ---------------------------------------------------------------------------

def _compute_path_prefix(url: str) -> str:
    """Derive a path prefix from the entry URL for scoping link-following.

    Examples:
        /docs/current/tutorial.html  ->  /docs/current/tutorial
        /docs/current/               ->  /docs/current/
        /guide                       ->  /guide
    """
    parsed = urlparse(url)
    path = parsed.path

    if path.endswith("/"):
        return path

    # Strip extension to get the stem prefix
    stem = Path(path).stem          # "tutorial" from "tutorial.html"
    parent = str(Path(path).parent) # "/docs/current"
    if parent == "/":
        return f"/{stem}"
    return f"{parent}/{stem}"


# ---------------------------------------------------------------------------
# Link extraction & filtering
# ---------------------------------------------------------------------------

def _extract_links_from_html(
    html: str,
    source_url: str,
    path_prefix: str,
    include_paths: list[str] | None = None,
) -> list[str]:
    """Extract and filter links from raw HTML.

    Returns only same-origin links whose path starts with *path_prefix*
    OR with any of the additional *include_paths* (if provided),
    excluding the entry URL itself and fragment-only anchors.
    """
    from courlan import extract_links as courlan_extract

    all_links = courlan_extract(
        html,
        url=source_url,
        no_filter=True,
        with_nav=True,
    )

    parsed_source = urlparse(source_url)
    source_origin = f"{parsed_source.scheme}://{parsed_source.netloc}"
    # Normalize entry URL (strip fragment)
    entry_normalized = f"{source_origin}{parsed_source.path}"

    allowed_prefixes = [path_prefix, *(include_paths or [])]

    filtered: list[str] = []
    for link in all_links:
        parsed = urlparse(link)
        # Same origin only
        if f"{parsed.scheme}://{parsed.netloc}" != source_origin:
            continue
        # Must match entry path prefix or one of the include paths
        if not any(parsed.path.startswith(p) for p in allowed_prefixes):
            continue
        # Normalize (strip fragment)
        normalized = f"{source_origin}{parsed.path}"
        if normalized == entry_normalized:
            continue
        filtered.append(normalized)

    return sorted(set(filtered))


# ---------------------------------------------------------------------------
# Multi-page crawl
# ---------------------------------------------------------------------------

def _crawl_subpages(
    entry_url: str,
    entry_html: str,
    *,
    max_pages: int = 50,
    max_depth: int = 1,
    max_tokens: int = 200_000,
    include_paths: list[str] | None = None,
) -> tuple[list[dict], str]:
    """BFS crawl of subpages linked from the entry page.

    Returns (sections_list, limited_by) where *limited_by* is one of
    "none", "max_pages", "max_depth", "max_tokens", or "exhausted".
    """
    import trafilatura

    path_prefix = _compute_path_prefix(entry_url)
    seed_links = _extract_links_from_html(
        entry_html, entry_url, path_prefix, include_paths=include_paths,
    )

    if not seed_links:
        return [], "none"

    # BFS state
    queue: deque[tuple[str, int]] = deque()  # (url, depth)
    for link in seed_links:
        queue.append((link, 1))

    visited: set[str] = {urlparse(entry_url).path}
    sections: list[dict] = []
    pages_fetched = 0
    total_tokens = 0
    limited_by = "exhausted"

    while queue:
        url, depth = queue.popleft()

        # Normalize for dedup
        normalized = urlparse(url).path
        if normalized in visited:
            continue
        visited.add(normalized)

        # Check limits
        if pages_fetched >= max_pages:
            limited_by = "max_pages"
            break
        if depth > max_depth:
            continue  # skip this URL but keep draining queue at valid depths
        if total_tokens >= max_tokens:
            limited_by = "max_tokens"
            break

        # Fetch & extract
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            continue

        text = trafilatura.extract(
            downloaded,
            include_links=False,
            include_tables=True,
            include_comments=False,
            favor_precision=True,
        )
        if not text:
            continue

        pages_fetched += 1

        # Get page title from metadata
        page_title = ""
        meta_json = trafilatura.extract(
            downloaded, output_format="json", include_links=False,
        )
        if meta_json:
            import json
            try:
                page_title = json.loads(meta_json).get("title", "")
            except (json.JSONDecodeError, TypeError):
                pass
        if not page_title:
            slug = urlparse(url).path.split("/")[-1]
            page_title = slug.replace("-", " ").replace(".html", "").title()

        # Split into sections and shift depth
        page_sections = _split_into_sections(text, page_title)
        for sec in page_sections:
            sec["depth"] = min(sec["depth"] + 1, 2)
            sec["metadata"]["source_url"] = url

        # Insert a chapter heading for this subpage
        sections.append({
            "title": page_title,
            "content": "",
            "depth": 0,
            "metadata": {"source_url": url},
        })
        sections.extend(page_sections)

        token_count = sum(len(s["content"].split()) for s in page_sections)
        total_tokens += token_count

        if total_tokens >= max_tokens:
            limited_by = "max_tokens"
            break

        # If depth allows, discover more links from this subpage
        if depth < max_depth:
            sub_links = _extract_links_from_html(
                downloaded, url, path_prefix, include_paths=include_paths,
            )
            for sub_link in sub_links:
                if urlparse(sub_link).path not in visited:
                    queue.append((sub_link, depth + 1))

    return sections, limited_by


# ---------------------------------------------------------------------------
# Image extraction from HTML
# ---------------------------------------------------------------------------

class _ImgTagParser(HTMLParser):
    """Extract <img> src and alt attributes from HTML."""

    def __init__(self):
        super().__init__()
        self.images: list[dict] = []

    def handle_starttag(self, tag, attrs):
        if tag == "img":
            attr_dict = dict(attrs)
            src = attr_dict.get("src", "")
            if src:
                self.images.append({
                    "src": src,
                    "alt": attr_dict.get("alt", ""),
                })


def _download_image(
    url: str,
    images_dir: Path,
    img_id: str,
) -> dict | None:
    """Download an image URL and return a registry entry, or None on failure."""
    try:
        req = Request(url, headers={"User-Agent": "DuTaTo-Extractor/1.0"})
        with urlopen(req, timeout=10) as resp:
            content_type = resp.headers.get("Content-Type", "image/png")
            if "image" not in content_type:
                return None
            data = resp.read(10 * 1024 * 1024)  # 10MB max
            if len(data) < 5120:  # Skip icons/spacers
                return None

            ext = content_type.split("/")[-1].split(";")[0].replace("jpeg", "jpg")
            if ext not in ("png", "jpg", "gif", "webp", "svg+xml"):
                ext = "png"
            ext = ext.replace("svg+xml", "svg")
            filename = f"{img_id}.{ext}"
            filepath = images_dir / filename
            filepath.write_bytes(data)

            return {
                "id": img_id,
                "local_path": f"images/{filename}",
                "mime_type": content_type.split(";")[0],
                "size_bytes": len(data),
                "width": 0,
                "height": 0,
            }
    except Exception:
        return None


def _extract_web_images(
    html: str,
    source_url: str,
    images_dir: str,
) -> list[dict]:
    """Extract images from HTML, download them, and return registry entries."""
    out = Path(images_dir)
    out.mkdir(parents=True, exist_ok=True)

    parser = _ImgTagParser()
    parser.feed(html)

    source_origin = urlparse(source_url)
    registry: list[dict] = []
    seen_srcs: set[str] = set()

    for idx, img in enumerate(parser.images):
        src = img["src"]
        # Skip data URIs
        if src.startswith("data:"):
            continue
        # Resolve relative URLs
        abs_url = urljoin(source_url, src)
        if abs_url in seen_srcs:
            continue
        seen_srcs.add(abs_url)

        img_id = f"web_img{idx}"
        entry = _download_image(abs_url, out, img_id)
        if entry:
            entry["alt_text"] = img.get("alt", "")
            registry.append(entry)

    return registry


# ---------------------------------------------------------------------------
# Main extraction entry point
# ---------------------------------------------------------------------------

def extract_web(
    source: str,
    *,
    crawl: bool = False,
    max_pages: int = 50,
    max_depth: int = 1,
    max_tokens: int = 200_000,
    images_dir: str | None = None,
    include_paths: list[str] | None = None,
) -> dict:
    """
    Extract content from a URL.

    Args:
        source: The URL to extract.
        crawl: If True, follow links from the entry page to extract
               subpages (useful for documentation TOC pages).
        max_pages: Maximum number of subpages to crawl.
        max_depth: Maximum link-following depth (1 = direct links only).
        max_tokens: Stop crawling when accumulated tokens exceed this.
        images_dir: If provided, download images to this directory.
        include_paths: Extra same-origin URL path prefixes to follow
            during crawl, in addition to the entry URL's path prefix.
            Useful for category-index pages that link outside the
            index's own path (e.g. /categories/foo/ → /blog/*).
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

    # Extract images from the HTML
    image_registry: list[dict] = []
    if images_dir:
        image_registry = _extract_web_images(downloaded, source, images_dir)

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

    # Split entry page text into sections
    sections = _split_into_sections(text, title)

    # Distribute images across sections (best-effort by order)
    if image_registry and sections:
        per_section = max(1, len(image_registry) // len(sections))
        img_iter = iter(image_registry)
        for sec in sections:
            sec_images = []
            for _ in range(per_section):
                img = next(img_iter, None)
                if img:
                    sec_images.append({
                        "id": img["id"],
                        "local_path": img["local_path"],
                        "alt_text": img.get("alt_text", ""),
                        "context": f"url:{source}",
                    })
            if sec_images:
                sec["images"] = sec_images
        remaining = list(img_iter)
        if remaining and sections:
            sections[-1].setdefault("images", []).extend(
                {"id": img["id"], "local_path": img["local_path"],
                 "alt_text": img.get("alt_text", ""), "context": f"url:{source}"}
                for img in remaining
            )

    # Crawl subpages if requested
    pages_crawled = 1
    crawl_limited_by = "none"

    if crawl:
        sub_sections, crawl_limited_by = _crawl_subpages(
            source,
            downloaded,
            max_pages=max_pages,
            max_depth=max_depth,
            max_tokens=max_tokens,
            include_paths=include_paths,
        )
        if sub_sections:
            sections.extend(sub_sections)
            pages_crawled += sum(
                1 for s in sub_sections
                if s["depth"] == 0 and not s["content"]
            )

    total_tokens = sum(len(s["content"].split()) for s in sections if s["content"])

    result: dict = {
        "source_type": "url",
        "source_path": source,
        "title": title,
        "author": author,
        "sections": sections,
        "metadata": {
            "total_sections": len(sections),
            "total_tokens": total_tokens,
            "total_images": len(image_registry),
            "url": source,
        },
    }

    if image_registry:
        result["images"] = image_registry
    if crawl:
        result["metadata"]["pages_crawled"] = pages_crawled
        result["metadata"]["crawl_limited_by"] = crawl_limited_by

    return result


# ---------------------------------------------------------------------------
# Section splitting (unchanged logic)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="Extract content from a web URL",
    )
    parser.add_argument("url", help="URL to extract")
    parser.add_argument("-o", "--output-dir", help="Save extracted JSON to this directory")
    parser.add_argument(
        "--crawl", action="store_true",
        help="Follow links from entry page to extract subpages",
    )
    parser.add_argument("--max-pages", type=int, default=50, help="Max subpages to crawl (default: 50)")
    parser.add_argument("--max-depth", type=int, default=1, help="Max link-following depth (default: 1)")
    parser.add_argument("--max-tokens", type=int, default=200_000, help="Token budget for crawling (default: 200000)")
    parser.add_argument(
        "--include-paths", action="append", default=None,
        help="Extra URL path prefix to follow during crawl (repeatable, e.g. --include-paths /blog/). "
             "In addition to the entry URL's own path prefix.",
    )
    args = parser.parse_args()

    from extractors import extract_source
    result = extract_source(
        args.url,
        args.output_dir,
        crawl=args.crawl,
        max_pages=args.max_pages,
        max_depth=args.max_depth,
        max_tokens=args.max_tokens,
        include_paths=args.include_paths,
    )
    if not args.output_dir:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        meta = result["metadata"]
        msg = f"Extracted {meta['total_sections']} sections from {args.url}"
        if args.crawl and "pages_crawled" in meta:
            msg += f" ({meta['pages_crawled']} pages, limited by: {meta['crawl_limited_by']})"
        print(msg)
