"""
Notion export extractor — processes Notion export ZIP files containing
markdown files and converts them to the unified intermediate format.

Notion exports as ZIP contain:
- Markdown (.md) files with page content
- Directories for nested pages
- Image files referenced by the markdown
- UUIDs in filenames (e.g., "My Page abc123def456.md")
"""

import re
import sys
import zipfile
from pathlib import Path, PurePosixPath

_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}
_IMAGE_MD_PATTERN = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


def _clean_notion_title(filename: str) -> str:
    """Remove Notion's UUID suffix from filenames."""
    name = Path(filename).stem
    cleaned = re.sub(r"\s+[a-f0-9]{16,}$", "", name)
    return cleaned or name


def _extract_zip_images(
    zf: zipfile.ZipFile,
    images_dir: str,
) -> dict[str, dict]:
    """Extract image files from the ZIP and return a mapping of zip_path → registry entry."""
    out = Path(images_dir)
    out.mkdir(parents=True, exist_ok=True)

    mapping: dict[str, dict] = {}
    img_idx = 0

    for name in zf.namelist():
        suffix = PurePosixPath(name).suffix.lower()
        if suffix not in _IMAGE_EXTENSIONS:
            continue
        try:
            data = zf.read(name)
        except (KeyError, RuntimeError):
            continue
        if len(data) < 2048:
            continue

        ext = suffix.lstrip(".")
        mime = f"image/{ext}" if ext != "jpg" else "image/jpeg"
        img_id = f"notion_img{img_idx}"
        filename = f"{img_id}.{ext}"
        filepath = out / filename
        filepath.write_bytes(data)

        entry = {
            "id": img_id,
            "local_path": f"images/{filename}",
            "mime_type": mime,
            "size_bytes": len(data),
            "width": 0,
            "height": 0,
        }
        # Map both the full path and just the filename for flexible matching
        mapping[name] = entry
        mapping[PurePosixPath(name).name] = entry
        img_idx += 1

    return mapping


def _parse_markdown_sections(
    text: str,
    base_depth: int = 0,
    *,
    image_map: dict[str, dict] | None = None,
    md_dir: str = "",
) -> tuple[list[dict], list[dict]]:
    """Parse markdown text into sections based on headings.

    Returns (sections, section_images) where section_images are image refs
    found in the markdown.
    """
    # Replace markdown image references with placeholders and collect images
    section_images: list[dict] = []
    processed_text = text

    if image_map:
        def _replace_image(match):
            alt_text = match.group(1)
            ref_path = match.group(2)
            # Try to find the image in our extracted files
            # Notion uses relative paths from the markdown file
            candidates = [
                ref_path,
                PurePosixPath(ref_path).name,
            ]
            if md_dir:
                candidates.insert(0, f"{md_dir}/{ref_path}")

            for candidate in candidates:
                entry = image_map.get(candidate)
                if entry:
                    section_images.append({
                        "id": entry["id"],
                        "local_path": entry["local_path"],
                        "alt_text": alt_text,
                        "context": f"markdown_ref:{ref_path}",
                    })
                    return f"[IMAGE: {entry['id']}]"
            # Image not found in ZIP — leave original markdown
            return match.group(0)

        processed_text = _IMAGE_MD_PATTERN.sub(_replace_image, text)

    heading_pattern = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
    matches = list(heading_pattern.finditer(processed_text))

    if not matches:
        sec = {
            "title": "Content",
            "content": processed_text.strip(),
            "depth": base_depth,
            "metadata": {},
        }
        if section_images:
            sec["images"] = section_images
        return [sec], section_images

    sections = []

    preamble = processed_text[:matches[0].start()].strip()
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
        end = matches[i + 1].start() if i + 1 < len(matches) else len(processed_text)
        content = processed_text[start:end].strip()

        sections.append({
            "title": heading,
            "content": content,
            "depth": depth,
            "metadata": {},
        })

    # Distribute collected images to sections containing their placeholders
    if section_images:
        for img in section_images:
            placeholder = f"[IMAGE: {img['id']}]"
            for sec in sections:
                if placeholder in sec.get("content", ""):
                    sec.setdefault("images", []).append(img)
                    break

    return sections, section_images


def extract_notion(source: str, *, images_dir: str | None = None) -> dict:
    """Extract a Notion export ZIP to the unified intermediate format."""
    zip_path = Path(source)
    if not zip_path.exists():
        raise FileNotFoundError(f"ZIP file not found: {zip_path}")

    if not zipfile.is_zipfile(str(zip_path)):
        raise ValueError(f"Not a valid ZIP file: {zip_path}")

    sections = []
    file_count = 0
    all_images: list[dict] = []
    image_map: dict[str, dict] = {}

    with zipfile.ZipFile(str(zip_path), "r") as zf:
        # Extract images first if images_dir provided
        if images_dir:
            image_map = _extract_zip_images(zf, images_dir)

        md_files = sorted(
            [n for n in zf.namelist() if n.endswith(".md")],
            key=lambda x: x.lower(),
        )

        for md_name in md_files:
            parts = PurePosixPath(md_name).parts
            depth = len(parts) - 1
            md_dir = str(PurePosixPath(md_name).parent) if len(parts) > 1 else ""

            title = _clean_notion_title(PurePosixPath(md_name).name)

            try:
                content = zf.read(md_name).decode("utf-8", errors="replace")
            except (KeyError, UnicodeDecodeError):
                continue

            if not content.strip():
                continue

            file_count += 1

            sub_sections, sec_images = _parse_markdown_sections(
                content, base_depth=depth,
                image_map=image_map if images_dir else None,
                md_dir=md_dir,
            )
            all_images.extend(sec_images)

            if len(sub_sections) > 1:
                sections.append({
                    "title": title,
                    "content": "",
                    "depth": depth,
                    "metadata": {"file": md_name},
                })
                sections.extend(sub_sections)
            elif sub_sections:
                sub_sections[0]["title"] = title
                sub_sections[0]["depth"] = depth
                sub_sections[0]["metadata"]["file"] = md_name
                sections.extend(sub_sections)

    total_tokens = sum(len(s["content"].split()) for s in sections if s["content"])

    title = zip_path.stem
    if sections:
        first_depth0 = next(
            (s for s in sections if s["depth"] == 0 and s["title"] != "Content"),
            None,
        )
        if first_depth0:
            title = first_depth0["title"]

    # Build deduplicated image registry from the map
    image_registry = list({v["id"]: v for v in image_map.values()}.values()) if image_map else []

    result: dict = {
        "source_type": "notion",
        "source_path": str(zip_path.resolve()),
        "title": title,
        "author": "",
        "sections": sections,
        "metadata": {
            "total_sections": len(sections),
            "total_tokens": total_tokens,
            "markdown_files": file_count,
            "total_images": len(image_registry),
        },
    }
    if image_registry:
        result["images"] = image_registry
    return result


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
