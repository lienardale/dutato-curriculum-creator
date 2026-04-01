"""
Tabular data extractor — CSV and TSV files to the unified intermediate format.

Rows are grouped into sections (default: 50 rows per section) with column
headers preserved. Good for glossaries, Q&A datasets, vocabulary lists.
"""

import csv
import sys
from pathlib import Path

_DEFAULT_ROWS_PER_SECTION = 50


def extract_tabular(source: str, rows_per_section: int = _DEFAULT_ROWS_PER_SECTION) -> dict:
    """Extract a CSV or TSV file to the unified intermediate format."""
    file_path = Path(source)
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    suffix = file_path.suffix.lower()
    delimiter = "\t" if suffix == ".tsv" else ","

    with open(file_path, encoding="utf-8", errors="replace", newline="") as f:
        # Sniff dialect if CSV
        if suffix == ".csv":
            sample = f.read(8192)
            f.seek(0)
            try:
                dialect = csv.Sniffer().sniff(sample)
                reader = csv.DictReader(f, dialect=dialect)
            except csv.Error:
                reader = csv.DictReader(f, delimiter=delimiter)
        else:
            reader = csv.DictReader(f, delimiter=delimiter)

        headers = reader.fieldnames or []
        rows = list(reader)

    if not rows:
        return {
            "source_type": "csv" if suffix == ".csv" else "tsv",
            "source_path": str(file_path.resolve()),
            "title": file_path.stem,
            "author": "",
            "sections": [],
            "metadata": {"total_sections": 0, "total_tokens": 0, "total_rows": 0},
        }

    # Group rows into sections
    sections = []
    for i in range(0, len(rows), rows_per_section):
        chunk_rows = rows[i:i + rows_per_section]
        start = i + 1
        end = i + len(chunk_rows)

        # Format as markdown table
        content_lines = []
        content_lines.append("| " + " | ".join(headers) + " |")
        content_lines.append("| " + " | ".join("---" for _ in headers) + " |")
        for row in chunk_rows:
            values = [str(row.get(h, "")).replace("|", "\\|") for h in headers]
            content_lines.append("| " + " | ".join(values) + " |")

        # Also add a plain text representation for better LLM consumption
        plain_lines = []
        for row in chunk_rows:
            parts = [f"{h}: {row.get(h, '')}" for h in headers]
            plain_lines.append("; ".join(parts))

        content = "\n".join(content_lines) + "\n\n---\n\n" + "\n".join(plain_lines)

        sections.append({
            "title": f"Rows {start}-{end}",
            "content": content,
            "depth": 0,
            "metadata": {
                "row_start": start,
                "row_end": end,
                "row_count": len(chunk_rows),
            },
        })

    total_tokens = sum(len(s["content"].split()) for s in sections if s["content"])

    return {
        "source_type": "csv" if suffix == ".csv" else "tsv",
        "source_path": str(file_path.resolve()),
        "title": file_path.stem,
        "author": "",
        "sections": sections,
        "metadata": {
            "total_sections": len(sections),
            "total_tokens": total_tokens,
            "total_rows": len(rows),
            "columns": headers,
        },
    }


if __name__ == "__main__":
    import json

    if len(sys.argv) < 2:
        print("Usage: python -m extractors.tabular <input.csv|tsv> [-o output_dir]")
        sys.exit(1)

    from extractors import extract_source
    file_path = sys.argv[1]
    output_dir = sys.argv[sys.argv.index("-o") + 1] if "-o" in sys.argv else None
    result = extract_source(file_path, output_dir)
    if not output_dir:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"Extracted {result['metadata']['total_rows']} rows, "
              f"{result['metadata']['total_sections']} sections")
