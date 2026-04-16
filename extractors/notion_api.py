"""
Notion API extractor — fetches pages and databases directly from a
Notion workspace via the Notion API.

Supports two authentication modes:
  1. Integration token: NOTION_API_TOKEN env var or --token CLI arg
  2. OAuth: --oauth flag triggers a browser-based flow with a local callback server

Usage:
  python -m extractors.notion_api https://www.notion.so/My-Page-abc123 -o output/name/extracted/
  python -m extractors.notion_api <page_id> --token <token> -o output/name/extracted/
  python -m extractors.notion_api <page_id> --oauth -o output/name/extracted/
"""

import os
import re
import sys
from collections import deque
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen


# ---------------------------------------------------------------------------
# Notion URL / ID parsing
# ---------------------------------------------------------------------------

_NOTION_ID_PATTERN = re.compile(r"[a-f0-9]{32}")
_NOTION_DASHED_ID_PATTERN = re.compile(
    r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}"
)


def _parse_notion_id(source: str) -> str:
    """Extract a Notion page/database ID from a URL or raw ID string."""
    # Strip notion:// scheme
    if source.startswith("notion://"):
        source = source[len("notion://"):]

    # Try dashed UUID first
    m = _NOTION_DASHED_ID_PATTERN.search(source)
    if m:
        return m.group(0)

    # Try 32-char hex (Notion URL format)
    m = _NOTION_ID_PATTERN.search(source)
    if m:
        raw = m.group(0)
        return f"{raw[:8]}-{raw[8:12]}-{raw[12:16]}-{raw[16:20]}-{raw[20:]}"

    raise ValueError(
        f"Cannot extract Notion ID from: {source}\n"
        "Provide a Notion URL or a page/database ID (32-char hex or dashed UUID)."
    )


# ---------------------------------------------------------------------------
# OAuth flow (optional)
# ---------------------------------------------------------------------------

def _oauth_get_token(client_id: str, client_secret: str) -> str:
    """Run a local OAuth flow to get a Notion access token.

    Starts a temporary HTTP server on localhost:9876, prints an auth URL,
    and waits for the redirect with the authorization code.
    """
    import http.server
    import json
    import threading
    from base64 import b64encode
    from urllib.parse import parse_qs, urlparse as oauth_urlparse

    redirect_uri = "http://localhost:9876/callback"
    auth_url = (
        f"https://api.notion.com/v1/oauth/authorize"
        f"?client_id={client_id}"
        f"&response_type=code"
        f"&owner=user"
        f"&redirect_uri={redirect_uri}"
    )

    print(f"\nOpen this URL in your browser to authorize:\n\n  {auth_url}\n")

    auth_code = None

    class _CallbackHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            nonlocal auth_code
            qs = parse_qs(oauth_urlparse(self.path).query)
            auth_code = qs.get("code", [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h2>Authorization complete. You can close this tab.</h2>")

        def log_message(self, format, *args):
            pass  # Suppress default logging

    server = http.server.HTTPServer(("localhost", 9876), _CallbackHandler)
    server.timeout = 120
    server.handle_request()
    server.server_close()

    if not auth_code:
        raise RuntimeError("OAuth flow failed — no authorization code received.")

    # Exchange code for token
    credentials = b64encode(f"{client_id}:{client_secret}".encode()).decode()
    body = json.dumps({
        "grant_type": "authorization_code",
        "code": auth_code,
        "redirect_uri": redirect_uri,
    }).encode()

    req = Request(
        "https://api.notion.com/v1/oauth/token",
        data=body,
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urlopen(req) as resp:
        data = json.loads(resp.read())

    token = data.get("access_token")
    if not token:
        raise RuntimeError(f"OAuth token exchange failed: {data}")

    # Cache token for reuse
    cache_path = Path(".notion_oauth_token")
    cache_path.write_text(token)
    print(f"Token cached to {cache_path}")

    return token


# ---------------------------------------------------------------------------
# Block → markdown conversion
# ---------------------------------------------------------------------------

def _rich_text_to_md(rich_texts: list[dict]) -> str:
    """Convert Notion rich_text array to markdown string."""
    parts = []
    for rt in rich_texts:
        text = rt.get("plain_text", "")
        annotations = rt.get("annotations", {})

        if annotations.get("code"):
            text = f"`{text}`"
        if annotations.get("bold"):
            text = f"**{text}**"
        if annotations.get("italic"):
            text = f"*{text}*"
        if annotations.get("strikethrough"):
            text = f"~~{text}~~"

        href = rt.get("href")
        if href:
            text = f"[{text}]({href})"

        parts.append(text)
    return "".join(parts)


def _block_to_md(block: dict) -> str:
    """Convert a single Notion block to markdown."""
    btype = block.get("type", "")
    data = block.get(btype, {})

    if btype in ("paragraph", "quote", "callout"):
        text = _rich_text_to_md(data.get("rich_text", []))
        if btype == "quote":
            return "\n".join(f"> {line}" for line in text.split("\n"))
        if btype == "callout":
            icon = data.get("icon", {}).get("emoji", "")
            return f"> {icon} {text}"
        return text

    if btype.startswith("heading_"):
        level = int(btype[-1])
        text = _rich_text_to_md(data.get("rich_text", []))
        return f"{'#' * level} {text}"

    if btype == "bulleted_list_item":
        text = _rich_text_to_md(data.get("rich_text", []))
        return f"- {text}"

    if btype == "numbered_list_item":
        text = _rich_text_to_md(data.get("rich_text", []))
        return f"1. {text}"

    if btype == "to_do":
        text = _rich_text_to_md(data.get("rich_text", []))
        checked = "x" if data.get("checked") else " "
        return f"- [{checked}] {text}"

    if btype == "code":
        text = _rich_text_to_md(data.get("rich_text", []))
        lang = data.get("language", "")
        return f"```{lang}\n{text}\n```"

    if btype == "divider":
        return "---"

    if btype == "toggle":
        text = _rich_text_to_md(data.get("rich_text", []))
        return f"<details><summary>{text}</summary></details>"

    if btype == "table_row":
        cells = data.get("cells", [])
        row = " | ".join(_rich_text_to_md(cell) for cell in cells)
        return f"| {row} |"

    if btype == "image":
        # Return a marker — actual download happens separately
        caption = _rich_text_to_md(data.get("caption", []))
        img_data = data.get("file", data.get("external", {}))
        url = img_data.get("url", "")
        return f"![{caption}]({url})"

    return ""


# ---------------------------------------------------------------------------
# Image download helper
# ---------------------------------------------------------------------------

def _download_notion_image(
    url: str,
    images_dir: Path,
    img_id: str,
) -> dict | None:
    """Download an image from a Notion URL and return a registry entry."""
    try:
        req = Request(url, headers={"User-Agent": "DuTaTo-Extractor/1.0"})
        with urlopen(req, timeout=15) as resp:
            content_type = resp.headers.get("Content-Type", "image/png")
            if "image" not in content_type:
                return None
            data = resp.read(10 * 1024 * 1024)
            if len(data) < 2048:
                return None

            ext = content_type.split("/")[-1].split(";")[0].replace("jpeg", "jpg")
            if ext not in ("png", "jpg", "gif", "webp"):
                ext = "png"
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


# ---------------------------------------------------------------------------
# Page traversal
# ---------------------------------------------------------------------------

def _get_client(token: str):
    """Create a Notion client."""
    from notion_client import Client
    return Client(auth=token)


def _fetch_page_blocks(
    client,
    page_id: str,
    *,
    images_dir: Path | None = None,
) -> tuple[list[str], list[dict], list[dict]]:
    """Fetch all blocks from a page, convert to markdown lines.

    Returns (md_lines, child_page_refs, image_registry).
    """
    md_lines: list[str] = []
    child_pages: list[dict] = []
    image_registry: list[dict] = []
    img_idx = 0

    cursor = None
    while True:
        kwargs = {"block_id": page_id, "page_size": 100}
        if cursor:
            kwargs["start_cursor"] = cursor

        response = client.blocks.children.list(**kwargs)
        blocks = response.get("results", [])

        for block in blocks:
            btype = block.get("type", "")

            # Child page — record for recursive traversal
            if btype == "child_page":
                child_pages.append({
                    "id": block["id"],
                    "title": block["child_page"].get("title", ""),
                })
                continue

            # Child database — record for traversal
            if btype == "child_database":
                child_pages.append({
                    "id": block["id"],
                    "title": block["child_database"].get("title", ""),
                    "is_database": True,
                })
                continue

            # Image block — download if images_dir provided
            if btype == "image" and images_dir:
                data = block.get("image", {})
                img_data = data.get("file", data.get("external", {}))
                url = img_data.get("url", "")
                if url:
                    caption = _rich_text_to_md(data.get("caption", []))
                    img_id = f"notion_api_img{img_idx}"
                    entry = _download_notion_image(url, images_dir, img_id)
                    if entry:
                        entry["alt_text"] = caption
                        image_registry.append(entry)
                        md_lines.append(f"[IMAGE: {img_id}]")
                        img_idx += 1
                        continue

            md = _block_to_md(block)
            if md:
                md_lines.append(md)

        if not response.get("has_more"):
            break
        cursor = response.get("next_cursor")

    return md_lines, child_pages, image_registry


def _fetch_database_pages(client, database_id: str) -> list[dict]:
    """Fetch all pages from a Notion database."""
    pages = []
    cursor = None
    while True:
        kwargs = {"database_id": database_id, "page_size": 100}
        if cursor:
            kwargs["start_cursor"] = cursor

        response = client.databases.query(**kwargs)
        for page in response.get("results", []):
            # Extract title from properties
            title = ""
            for prop in page.get("properties", {}).values():
                if prop.get("type") == "title":
                    title = _rich_text_to_md(prop.get("title", []))
                    break
            pages.append({"id": page["id"], "title": title or page["id"][:8]})

        if not response.get("has_more"):
            break
        cursor = response.get("next_cursor")

    return pages


# ---------------------------------------------------------------------------
# Main extraction
# ---------------------------------------------------------------------------

def extract_notion_api(
    source: str,
    *,
    token: str | None = None,
    images_dir: str | None = None,
    max_pages: int = 100,
    max_depth: int = 5,
    oauth: bool = False,
) -> dict:
    """Extract content from a Notion page or database via the API."""
    root_id = _parse_notion_id(source)

    # Resolve token
    if not token:
        if oauth:
            client_id = os.getenv("NOTION_CLIENT_ID", "")
            client_secret = os.getenv("NOTION_CLIENT_SECRET", "")
            if not client_id or not client_secret:
                raise RuntimeError(
                    "OAuth requires NOTION_CLIENT_ID and NOTION_CLIENT_SECRET env vars."
                )
            # Check cached token first
            cache_path = Path(".notion_oauth_token")
            if cache_path.exists():
                token = cache_path.read_text().strip()
            else:
                token = _oauth_get_token(client_id, client_secret)
        else:
            token = os.getenv("NOTION_API_TOKEN", "")
            if not token:
                raise RuntimeError(
                    "No Notion token. Set NOTION_API_TOKEN env var, use --token, or use --oauth."
                )

    client = _get_client(token)

    imgs_path = Path(images_dir) if images_dir else None
    if imgs_path:
        imgs_path.mkdir(parents=True, exist_ok=True)

    # Determine if root is a page or database
    is_database = False
    root_title = ""
    try:
        page = client.pages.retrieve(page_id=root_id)
        # Extract title from page properties
        for prop in page.get("properties", {}).values():
            if prop.get("type") == "title":
                root_title = _rich_text_to_md(prop.get("title", []))
                break
    except Exception:
        try:
            db = client.databases.retrieve(database_id=root_id)
            root_title = _rich_text_to_md(db.get("title", []))
            is_database = True
        except Exception:
            raise RuntimeError(f"Cannot access Notion page or database: {root_id}")

    # BFS traversal
    sections: list[dict] = []
    all_images: list[dict] = []
    pages_visited = 0
    queue: deque[tuple[str, str, int, bool]] = deque()  # (id, title, depth, is_db)

    if is_database:
        # Seed queue with database pages
        db_pages = _fetch_database_pages(client, root_id)
        for dp in db_pages[:max_pages]:
            queue.append((dp["id"], dp["title"], 0, False))
    else:
        queue.append((root_id, root_title or "Root", 0, False))

    visited: set[str] = set()

    while queue:
        page_id, page_title, depth, is_db = queue.popleft()

        if page_id in visited:
            continue
        visited.add(page_id)

        if pages_visited >= max_pages:
            break
        if depth > max_depth:
            continue

        if is_db:
            db_pages = _fetch_database_pages(client, page_id)
            for dp in db_pages:
                if dp["id"] not in visited:
                    queue.append((dp["id"], dp["title"], depth + 1, False))
            continue

        md_lines, child_refs, page_images = _fetch_page_blocks(
            client, page_id, images_dir=imgs_path,
        )
        pages_visited += 1
        all_images.extend(page_images)

        content = "\n\n".join(md_lines)

        # Split content into sections by headings
        from extractors.notion import _parse_markdown_sections
        sub_sections, _ = _parse_markdown_sections(content, base_depth=min(depth, 2))

        # Attach images to sections containing their placeholders
        if page_images:
            for img in page_images:
                placeholder = f"[IMAGE: {img['id']}]"
                for sec in sub_sections:
                    if placeholder in sec.get("content", ""):
                        sec.setdefault("images", []).append({
                            "id": img["id"],
                            "local_path": img["local_path"],
                            "alt_text": img.get("alt_text", ""),
                            "context": f"notion_page:{page_id}",
                        })
                        break

        if len(sub_sections) > 1:
            sections.append({
                "title": page_title,
                "content": "",
                "depth": min(depth, 2),
                "metadata": {"notion_page_id": page_id},
            })
            sections.extend(sub_sections)
        elif sub_sections:
            sub_sections[0]["title"] = page_title
            sub_sections[0]["depth"] = min(depth, 2)
            sub_sections[0]["metadata"]["notion_page_id"] = page_id
            sections.extend(sub_sections)

        # Enqueue children
        for child in child_refs:
            if child["id"] not in visited:
                queue.append((
                    child["id"],
                    child["title"],
                    depth + 1,
                    child.get("is_database", False),
                ))

    total_tokens = sum(len(s["content"].split()) for s in sections if s["content"])

    result: dict = {
        "source_type": "notion_api",
        "source_path": source,
        "title": root_title or "Notion Page",
        "author": "",
        "sections": sections,
        "metadata": {
            "total_sections": len(sections),
            "total_tokens": total_tokens,
            "total_images": len(all_images),
            "pages_visited": pages_visited,
            "notion_root_id": root_id,
        },
    }
    if all_images:
        result["images"] = all_images
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="Extract content from a Notion page or database via API",
    )
    parser.add_argument("source", help="Notion page URL, page ID, or database ID")
    parser.add_argument("-o", "--output-dir", help="Save extracted JSON to this directory")
    parser.add_argument("--token", help="Notion integration token (overrides NOTION_API_TOKEN)")
    parser.add_argument("--oauth", action="store_true", help="Use OAuth flow for authentication")
    parser.add_argument("--max-pages", type=int, default=100, help="Max pages to visit (default: 100)")
    parser.add_argument("--max-depth", type=int, default=5, help="Max traversal depth (default: 5)")
    args = parser.parse_args()

    from extractors import extract_source
    result = extract_source(
        args.source,
        args.output_dir,
        token=args.token,
        oauth=args.oauth,
        max_pages=args.max_pages,
        max_depth=args.max_depth,
    )
    if not args.output_dir:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        meta = result["metadata"]
        print(
            f"Extracted {meta['total_sections']} sections from "
            f"{meta['pages_visited']} pages, "
            f"{meta.get('total_images', 0)} images"
        )
