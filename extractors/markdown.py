"""
Markdown extractor — extracts a local Markdown file (or a directory of
Markdown files) into the unified intermediate format, splitting sections
by headings.

Reuses the Notion extractor's markdown section parser so heading
splitting, image placeholders, and section/image association behave
identically across markdown-based sources.

Useful for documentation repositories (e.g. cloned from GitHub) where
the canonical content is .md files on disk — extracting the rendered
HTML pages through the web extractor loses headings and code blocks.
"""

import re
import sys
from pathlib import Path

# Reuse the Notion markdown parser (and its image-ref regex, so the
# image_map keys built here match the lookups it performs)
from extractors.notion import _IMAGE_MD_PATTERN, _parse_markdown_sections

# Files that are repo plumbing, not learning content
_DEFAULT_EXCLUDE_STEMS = {
    "license", "code_of_conduct", "contributing", "changelog",
    "security", "support", "funding", "codeowners", "toc",
}

_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}

_MIME_BY_EXT = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml",
}


_FRONTMATTER_PATTERN = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)
_INCLUDE_PATTERN = re.compile(r"^\[!INCLUDE\s*\[[^\]]*\]\([^)]*\)\]\s*$", re.MULTILINE)
_DOCFX_IMAGE_PATTERN = re.compile(r":::image\s+([^:]*?):::", re.DOTALL)
_DOCFX_IMAGE_END_PATTERN = re.compile(r":::image-end:::")
_ALERT_PATTERN = re.compile(r"\[!(NOTE|TIP|WARNING|IMPORTANT|CAUTION)\]")
_ATTR_PATTERN = re.compile(r"(\w[\w-]*)=\"([^\"]*)\"")
_SETEXT_H1_PATTERN = re.compile(r"^([^\s#>|-][^\n]*)\n=+[ \t]*$", re.MULTILINE)
_SETEXT_H2_PATTERN = re.compile(r"^([^\s#>|-][^\n]*)\n-{2,}[ \t]*$", re.MULTILINE)


def _clean_markdown(text: str) -> str:
    """Normalize common doc-toolchain syntax into plain markdown:
    YAML frontmatter, docfx includes/alerts, and ``:::image`` directives."""
    text = _FRONTMATTER_PATTERN.sub("", text)
    text = _INCLUDE_PATTERN.sub("", text)

    def _docfx_image(match):
        attrs = dict(_ATTR_PATTERN.findall(match.group(1)))
        src = attrs.get("source", "")
        if not src:
            return ""
        return f"![{attrs.get('alt-text', '')}]({src})"

    text = _DOCFX_IMAGE_PATTERN.sub(_docfx_image, text)
    text = _DOCFX_IMAGE_END_PATTERN.sub("", text)
    text = _ALERT_PATTERN.sub(lambda m: f"**{m.group(1).title()}:**", text)
    # Setext headings (Title\n====) → ATX so the heading splitter sees them
    text = _SETEXT_H1_PATTERN.sub(lambda m: f"# {m.group(1).strip()}", text)
    text = _SETEXT_H2_PATTERN.sub(lambda m: f"## {m.group(1).strip()}", text)
    return text


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _collect_files(root: Path, include: str | None, exclude: set[str]) -> list[Path]:
    pattern = include or "**/*.md"
    files = []
    for path in sorted(root.glob(pattern)):
        if not path.is_file():
            continue
        if any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        if path.stem.lower() in _DEFAULT_EXCLUDE_STEMS or path.stem.lower() in exclude:
            continue
        files.append(path)
    return files


def _register_images(
    text: str,
    md_file: Path,
    images_dir: Path | None,
    registry: list[dict],
    id_prefix: str,
    image_root: Path | None = None,
) -> dict[str, dict]:
    """Copy images referenced from *md_file* into *images_dir* and return an
    image_map keyed by the exact reference strings found in the markdown."""
    image_map: dict[str, dict] = {}
    if images_dir is None:
        return image_map

    for match in _IMAGE_MD_PATTERN.finditer(text):
        ref = match.group(2)
        if ref in image_map or ref.startswith(("http://", "https://", "data:")):
            continue
        # Drop an optional trailing markdown title: (path "title")
        ref_path = ref.split(' "')[0].strip()
        if ref_path.startswith("/"):
            # Site-root-relative ref — resolve against image_root if given
            base = image_root or md_file.parent
            candidate = (base / ref_path.lstrip("/")).resolve()
        else:
            candidate = (md_file.parent / ref_path).resolve()
        if not candidate.is_file() or candidate.suffix.lower() not in _IMAGE_EXTENSIONS:
            continue
        img_id = f"{id_prefix}_img{len(registry):03d}"
        filename = f"{img_id}{candidate.suffix.lower()}"
        images_dir.mkdir(parents=True, exist_ok=True)
        data = candidate.read_bytes()
        (images_dir / filename).write_bytes(data)
        entry = {
            "id": img_id,
            "local_path": f"images/{filename}",
            "mime_type": _MIME_BY_EXT.get(candidate.suffix.lower(), "image/png"),
            "size_bytes": len(data),
            "width": 0,
            "height": 0,
        }
        registry.append(entry)
        image_map[ref] = entry
    return image_map


def _qualify_duplicate_titles(sections: list[dict]) -> None:
    """Disambiguate repeated section titles (generic doc-site headings like
    "Benefits" / "Challenges") so title-keyed matching downstream
    (chunk_bridge, condense, source_sections) stays unambiguous.

    Colliding titles are qualified with their parent heading, then with
    their file name, then with a numeric suffix as a last resort.

    Book/doc-meta titles ("Next steps", "Related resources", …) are left
    untouched so normalize_titles.py can still recognize and drop them.
    """
    from normalize_titles import is_book_meta

    sections = [s for s in sections if not is_book_meta(s["title"])]

    def _key(title: str) -> str:
        return " ".join(title.split()).lower()

    def _counts() -> dict[str, int]:
        c: dict[str, int] = {}
        for s in sections:
            c[_key(s["title"])] = c.get(_key(s["title"]), 0) + 1
        return c

    # Round 1: qualify with parent heading
    counts = _counts()
    for sec in sections:
        parent = sec.get("metadata", {}).get("parent_title", "")
        if counts[_key(sec["title"])] > 1 and parent:
            sec["title"] = f"{parent}: {sec['title']}"

    # Round 2: qualify with the file name
    counts = _counts()
    for sec in sections:
        file_path = sec.get("metadata", {}).get("file_path", "")
        if counts[_key(sec["title"])] > 1 and file_path:
            stem = Path(file_path).stem.replace("-", " ").replace("_", " ").strip()
            if _key(sec["title"]) != _key(stem):
                sec["title"] = f"{stem.title()}: {sec['title']}"

    # Round 3: numeric suffix
    counts = _counts()
    seen: dict[str, int] = {}
    for sec in sections:
        k = _key(sec["title"])
        if counts[k] > 1:
            seen[k] = seen.get(k, 0) + 1
            if seen[k] > 1:
                sec["title"] = f"{sec['title']} ({seen[k]})"


def extract_markdown(
    source: str,
    *,
    images_dir: str | None = None,
    include: str | None = None,
    exclude: set[str] | None = None,
    title: str | None = None,
    image_root: str | None = None,
) -> dict:
    """Extract a markdown file or directory of markdown files.

    Args:
        source: Path to a .md file or a directory containing .md files.
        images_dir: If provided, copy referenced local images there.
        include: Glob pattern (relative to the directory) selecting which
            files to extract. Defaults to ``**/*.md``.
        exclude: Extra lowercase file stems to skip.
        title: Override for the source title (defaults to directory/file name).
    """
    root = Path(source).resolve()
    if root.is_dir():
        files = _collect_files(root, include, exclude or set())
        if not files:
            raise FileNotFoundError(f"No markdown files found in {root}")
    elif root.is_file():
        files = [root]
    else:
        raise FileNotFoundError(f"Not a file or directory: {root}")

    img_dir_path = Path(images_dir) if images_dir else None
    id_prefix = f"md_{_slugify(root.stem if root.is_file() else root.name)}"

    registry: list[dict] = []
    sections: list[dict] = []
    img_root_path = Path(image_root).resolve() if image_root else None

    for md_file in files:
        text = _clean_markdown(md_file.read_text(encoding="utf-8", errors="replace"))
        image_map = _register_images(text, md_file, img_dir_path, registry, id_prefix,
                                     image_root=img_root_path)
        file_sections, _ = _parse_markdown_sections(text, 0, image_map=image_map)
        rel = str(md_file.relative_to(root)) if root.is_dir() else md_file.name
        ancestors: list[dict] = []  # heading stack, shallowest first
        for sec in file_sections:
            while ancestors and ancestors[-1]["depth"] >= sec["depth"]:
                ancestors.pop()
            parent_title = ancestors[-1]["title"] if ancestors else ""
            ancestors.append(sec)
            # Container headings with no body of their own (e.g. a Part
            # heading directly followed by a Chapter heading) can't be
            # referenced by source_sections — drop them
            if not sec["content"].strip() and not sec.get("images"):
                continue
            sec.setdefault("metadata", {})["file_path"] = rel
            if parent_title:
                sec["metadata"]["parent_title"] = parent_title
            sections.append(sec)

    _qualify_duplicate_titles(sections)

    total_tokens = sum(len(s["content"].split()) for s in sections if s["content"])

    result = {
        "source_type": "markdown",
        "source_path": str(root),
        "title": title or (root.stem if root.is_file() else root.name),
        "author": "",
        "sections": sections,
        "metadata": {
            "total_sections": len(sections),
            "total_tokens": total_tokens,
            "total_images": len(registry),
            "file_count": len(files),
        },
    }
    if registry:
        result["images"] = registry
    return result


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="Extract local Markdown files to the intermediate format")
    parser.add_argument("source", help="Path to a .md file or directory")
    parser.add_argument("-o", "--output-dir", help="Save extracted JSON here")
    parser.add_argument("--include", help="Glob pattern for files within a directory "
                        "(default: **/*.md)")
    parser.add_argument("--exclude", action="append", default=[],
                        help="File stem to skip (repeatable)")
    parser.add_argument("--title", help="Override source title")
    parser.add_argument("--name", help="Override output JSON filename (without .json)")
    parser.add_argument("--image-root", help="Directory for resolving site-root-relative "
                        "image refs like /images/foo.png")
    args = parser.parse_args()

    from extractors import extract_source

    kwargs = {}
    if args.include:
        kwargs["include"] = args.include
    if args.exclude:
        kwargs["exclude"] = {e.lower() for e in args.exclude}
    if args.title:
        kwargs["title"] = args.title
    if args.image_root:
        kwargs["image_root"] = args.image_root

    if args.output_dir and args.name:
        kwargs["images_dir"] = str(Path(args.output_dir) / "images")
        result = extract_markdown(args.source, **kwargs)
        out = Path(args.output_dir)
        out.mkdir(parents=True, exist_ok=True)
        from datetime import datetime, timezone
        result["metadata"]["extracted_at"] = datetime.now(timezone.utc).isoformat()
        with open(out / f"{args.name}.json", "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"Extracted {result['metadata']['total_sections']} sections "
              f"from {args.source}")
    else:
        result = extract_source(args.source, args.output_dir, **kwargs)
        if not args.output_dir:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print(f"Extracted {result['metadata']['total_sections']} sections "
                  f"from {args.source}")
