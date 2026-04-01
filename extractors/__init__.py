"""
Extractor registry — auto-detect source type and dispatch to the right extractor.

All extractors produce a unified intermediate format:
{
    "source_type": "pdf|docx|pptx|url|code|csv|notion",
    "source_path": "/path/or/url",
    "title": "...",
    "author": "...",
    "sections": [
        {"title": "...", "content": "...", "depth": 0, "metadata": {}}
    ],
    "metadata": {"total_sections": N, "total_tokens": N, "extracted_at": "ISO8601"}
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
}


def _detect_source_type(source: str) -> str:
    """Detect the source type from a path or URL."""
    if source.startswith("http://") or source.startswith("https://"):
        return "web"

    path = Path(source)
    if path.is_dir():
        return "code"

    suffix = path.suffix.lower()
    if suffix in _EXTENSION_MAP:
        return _EXTENSION_MAP[suffix]

    raise ValueError(
        f"Cannot detect source type for: {source}\n"
        f"Supported: {', '.join(sorted(_EXTENSION_MAP.keys()))}, URLs, directories"
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
    else:
        raise ValueError(f"Unknown source type: {source_type}")


def extract_source(source: str, output_dir: str | None = None) -> dict:
    """
    Extract content from a source (auto-detected type).

    Args:
        source: File path, URL, or directory path.
        output_dir: If provided, save the intermediate JSON there.

    Returns:
        The intermediate JSON dict.
    """
    source_type = _detect_source_type(source)
    extractor = _get_extractor(source_type)
    result = extractor(source)

    # Ensure metadata has extracted_at timestamp
    result.setdefault("metadata", {})
    result["metadata"]["extracted_at"] = datetime.now(timezone.utc).isoformat()

    if output_dir:
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)

        # Generate filename from source
        if source_type == "web":
            # Use URL domain + path as filename
            from urllib.parse import urlparse
            parsed = urlparse(source)
            safe_name = f"{parsed.netloc}{parsed.path}".replace("/", "_").strip("_")
            filename = f"{safe_name}.json"
        elif source_type == "code":
            filename = f"{Path(source).name}.json"
        else:
            filename = f"{Path(source).stem}.json"

        output_file = out_path / filename
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)

    return result
