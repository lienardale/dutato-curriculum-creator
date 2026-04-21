"""
Extractor registry — auto-detect source type and dispatch to the right extractor.

All extractors produce a unified intermediate format:
{
    "source_type": "pdf|docx|pptx|url|code|csv|notion|notion_api|video|video_playlist",
    "source_path": "/path/or/url",
    "title": "...",
    "author": "...",
    "sections": [
        {
            "title": "...", "content": "...", "depth": 0, "metadata": {},
            "images": [                          # optional
                {"id": "img_001", "local_path": "images/img_001.png",
                 "alt_text": "", "context": "after:paragraph:2"}
            ]
        }
    ],
    "images": [                                  # optional — master registry
        {"id": "img_001", "local_path": "images/img_001.png",
         "mime_type": "image/png", "size_bytes": 45230,
         "width": 800, "height": 600}
    ],
    "metadata": {"total_sections": N, "total_tokens": N, "total_images": 0,
                 "extracted_at": "ISO8601"}
}
"""

import json
from datetime import datetime, timezone
from pathlib import Path

# Lazy imports to avoid loading all extractors at once
_EXTENSION_MAP = {
    ".pdf": "pdf",
    ".docx": "office",
    ".pptx": "office",
    ".csv": "tabular",
    ".tsv": "tabular",
    ".zip": "notion",
    ".mp4": "video",
    ".m4a": "video",
    ".mkv": "video",
    ".webm": "video",
    ".mov": "video",
    ".mp3": "video",
    ".wav": "video",
}

_VIDEO_HOSTS = frozenset({
    "www.youtube.com", "youtube.com", "youtu.be",
    "m.youtube.com", "music.youtube.com",
    "www.vimeo.com", "vimeo.com", "player.vimeo.com",
    "www.twitch.tv", "twitch.tv",
})


def _detect_source_type(source: str) -> str:
    """Detect the source type from a path or URL."""
    # Notion API sources: notion:// scheme or www.notion.so URLs
    if source.startswith("notion://"):
        return "notion_api"
    if source.startswith("http://") or source.startswith("https://"):
        from urllib.parse import urlparse
        host = urlparse(source).netloc.lower()
        if host in ("www.notion.so", "notion.so"):
            return "notion_api"
        if host in _VIDEO_HOSTS:
            return "video"
        return "web"

    path = Path(source)
    if path.is_dir():
        return "code"

    suffix = path.suffix.lower()
    if suffix in _EXTENSION_MAP:
        return _EXTENSION_MAP[suffix]

    raise ValueError(
        f"Cannot detect source type for: {source}\n"
        f"Supported: {', '.join(sorted(_EXTENSION_MAP.keys()))}, URLs, directories, Notion URLs"
    )


def _get_extractor(source_type: str):
    """Lazy-load the extractor module for a source type."""
    if source_type == "pdf":
        from extractors.pdf import extract_pdf
        return extract_pdf
    elif source_type == "office":
        from extractors.office import extract_office
        return extract_office
    elif source_type == "web":
        from extractors.web import extract_web
        return extract_web
    elif source_type == "code":
        from extractors.code import extract_code
        return extract_code
    elif source_type == "tabular":
        from extractors.tabular import extract_tabular
        return extract_tabular
    elif source_type == "notion":
        from extractors.notion import extract_notion
        return extract_notion
    elif source_type == "notion_api":
        from extractors.notion_api import extract_notion_api
        return extract_notion_api
    elif source_type == "video":
        from extractors.video import extract_video
        return extract_video
    else:
        raise ValueError(f"Unknown source type: {source_type}")


def extract_source(source: str, output_dir: str | None = None, **kwargs) -> dict:
    """
    Extract content from a source (auto-detected type).

    Args:
        source: File path, URL, or directory path.
        output_dir: If provided, save the intermediate JSON there.
        **kwargs: Extra arguments forwarded to the extractor (e.g. crawl
                  params for the web extractor, images_dir for image
                  extraction).

    Returns:
        The intermediate JSON dict.
    """
    source_type = _detect_source_type(source)
    extractor = _get_extractor(source_type)

    # Default images_dir to <output_dir>/images/ when output_dir is provided
    if output_dir and "images_dir" not in kwargs:
        kwargs["images_dir"] = str(Path(output_dir) / "images")

    # Extractors that accept kwargs
    _kwarg_extractors = {"web", "notion_api", "pdf", "office", "notion", "video"}
    if source_type in _kwarg_extractors and kwargs:
        result = extractor(source, **kwargs)
    else:
        result = extractor(source)

    # Ensure metadata has extracted_at timestamp
    result.setdefault("metadata", {})
    result["metadata"]["extracted_at"] = datetime.now(timezone.utc).isoformat()
    result["metadata"].setdefault("total_images", len(result.get("images", [])))

    if output_dir:
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)

        # Generate filename from source
        if source_type in ("web", "notion_api"):
            from urllib.parse import urlparse
            parsed = urlparse(source)
            safe_name = f"{parsed.netloc}{parsed.path}".replace("/", "_").strip("_")
            filename = f"{safe_name}.json"
        elif source_type == "code":
            filename = f"{Path(source).name}.json"
        elif source_type == "video":
            # Use video/playlist ID for URL-based videos so multiple videos from
            # the same host don't collide (e.g. youtube.com/watch?v=...).
            video_id = (
                result.get("metadata", {}).get("video_id")
                or result.get("metadata", {}).get("playlist_id")
                or ""
            )
            if video_id:
                prefix = "playlist" if result.get("source_type") == "video_playlist" else "video"
                filename = f"{prefix}_{video_id}.json"
            else:
                filename = f"{Path(source).stem}.json"
        else:
            filename = f"{Path(source).stem}.json"

        output_file = out_path / filename
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

    return result
