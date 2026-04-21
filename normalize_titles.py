"""Normalize section titles in extracted JSONs before structure authoring.

Extraction tools often surface titles that leak book/publication structure
into the curriculum: "Chapter 4: Working with Images", "1. Meet Kafka",
"Part II: Designing Systems", "Cover", "Foreword", etc. The learning app
should show semantic topics, not book TOC entries — so we strip these
prefixes and drop book-meta sections before Stage 4 (Structure) is authored.

This is a PURE DATA UTILITY — it rewrites `output/<name>/extracted/*.json`
in place and prints a diff. No AI calls.

Run AFTER Stage 2 (Extract) and BEFORE Stage 4 (Structure). Idempotent.

Usage:
    python normalize_titles.py output/<name>/
    python normalize_titles.py output/<name>/ --dry-run
    python normalize_titles.py output/<name>/ --remap remap.json

The optional `--remap remap.json` file provides explicit replacements for
low-fidelity extractions where PDF extraction lost chapter titles and left
only "Chapter 1" … "Chapter N" (no trailing semantic name). Example:

    {
      "Docker_up_and_running.json": {
        "Chapter 1": "Introduction to Docker",
        "Chapter 4": "Working with Docker Images"
      }
    }
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# -- Prefix strippers ---------------------------------------------------------
# Run in order; only one pattern strips per title (longest/most-specific first).
PREFIX_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^Chapter\s+\d+(\.\d+)?[:.]?\s+", re.I),  # "Chapter 4: X" / "Chapter 4. X"
    re.compile(r"^Ch\.\s*\d+\s+"),                         # "Ch. 4 X"
    re.compile(r"^\d+\.\d+\.\d+\s+"),                      # "1.1.1 X"
    re.compile(r"^\d+\.\d+\s+"),                           # "1.1 X"
    re.compile(r"^\d+\.\s+(?=[A-Z])"),                     # "1. Meet Kafka" -> "Meet Kafka"
    re.compile(r"^\d+:\s+(?=[A-Z])"),                      # "5: The Docker Engine" -> "The Docker Engine"
    re.compile(r"^\d+\s+(?=[A-Z])"),                       # "3 Pods: ..." -> "Pods: ..."
    re.compile(r"^Part\s+[IVX0-9]+[:.]?\s*", re.I),        # "Part I. Setting the Stage" -> "Setting the Stage"
    re.compile(r"^Section\s+\d+[:.]?\s*", re.I),           # "Section 1: X" -> "X"
]

# -- Book-meta matcher --------------------------------------------------------
# Two categories:
#   EXACT_META: single-word titles that ONLY make sense as book preamble/
#     end-matter. Match when the ENTIRE title (case-insensitive, whitespace-
#     trimmed) equals one of these. Avoids false positives on chart/code
#     paths like "bitnami/foo/templates/index-gateway".
#   PHRASE_META: multi-word phrases almost never appearing in real content
#     titles — safe to match as a substring.
EXACT_META: set[str] = {
    "cover", "credits", "foreword", "dedication", "index", "colophon",
    "epilogue", "acknowledgments", "acknowledgements", "errata",
    "contributors", "brief contents", "table of contents", "toc",
    "about packt", "about the publisher", "about the author",
    "about this book", "about the book", "about the cover",
    "book forum", "back of the book",
}

PHRASE_META_RE = re.compile(
    r"(?i)\b("
    r"who should read|how (this|the) book"
    r"|why we (wrote|updated)"
    r"|navigating this book|conventions used"
    r"|using code examples"
    r"|online (learning|resources)"
    r"|how to contact"
    r"|further reading"
    r"|technical requirements"
    r"|what you.?ll need"
    r")\b"
)

# -- End-of-chapter marker (standalone title) --------------------------------
# "Questions" / "Summary" as a standalone chapter section is usually the
# end-of-chapter quiz / recap; drop only when the title IS one of these
# words (not when it's embedded in a larger title like "Questions About X").
EOC_STANDALONE = {"questions", "summary", "exercises"}

# -- Whitespace normalizer ----------------------------------------------------
def _clean_ws(t: str) -> str:
    return t.replace("\xa0", " ").replace("\n", " ").replace("  ", " ").strip()


def strip_prefix(title: str) -> str:
    """Strip one leading book-structure prefix (Chapter N:, N., Part X, …)."""
    t = _clean_ws(title)
    for pat in PREFIX_PATTERNS:
        new = pat.sub("", t)
        if new != t:
            return new.strip()
    return t


def is_book_meta(title: str) -> bool:
    """True if this title is book-preamble / end-matter / colophon material.

    Uses:
    - Exact match against EXACT_META (case-insensitive) for single-word titles
      that have NO content meaning outside book framing ("Cover", "Index", …).
    - Substring match against PHRASE_META_RE for multi-word phrases that are
      almost always book-meta ("Who Should Read This Book?", …).
    - Exact match against EOC_STANDALONE for end-of-chapter quiz/recap sections.
    """
    t = _clean_ws(title).lower().rstrip("?.!")
    if not t:
        return False
    if t in EOC_STANDALONE:
        return True
    if t in EXACT_META:
        return True
    return bool(PHRASE_META_RE.search(t))


# -- Per-file processing ------------------------------------------------------
def process_file(path: Path, remap: dict[str, str] | None = None, dry_run: bool = False) -> dict:
    """Rewrite titles in one extracted JSON file. Returns a summary dict."""
    with path.open(encoding="utf-8") as f:
        data = json.load(f)

    sections = data.get("sections", [])
    renamed: list[tuple[str, str]] = []
    dropped: list[str] = []
    new_sections: list[dict] = []
    remap = remap or {}

    for s in sections:
        old = (s.get("title") or "").strip()

        # Drop book-meta outright (keeps content out of all topics).
        if old and is_book_meta(old):
            dropped.append(old)
            continue

        # Explicit remap takes precedence (for "Chapter N"-only titles).
        if old in remap:
            new = remap[old]
        else:
            new = strip_prefix(old) if old else old

        if new != old:
            renamed.append((old, new))
            s["title"] = new

        new_sections.append(s)

    data["sections"] = new_sections
    if not dry_run and (renamed or dropped):
        with path.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    return {"file": path.name, "renamed": renamed, "dropped": dropped}


# -- CLI ----------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("curriculum_dir", type=Path, help="output/<name>/ directory")
    ap.add_argument("--remap", type=Path, help="JSON file of per-file explicit remaps")
    ap.add_argument("--dry-run", action="store_true", help="Show changes without writing")
    args = ap.parse_args()

    extracted = args.curriculum_dir / "extracted"
    if not extracted.is_dir():
        print(f"error: {extracted} not found. Run Stage 2 (extract) first.", file=sys.stderr)
        return 1

    remap_all: dict[str, dict[str, str]] = {}
    if args.remap and args.remap.is_file():
        remap_all = json.loads(args.remap.read_text())

    total_renamed = 0
    total_dropped = 0
    for path in sorted(extracted.glob("*.json")):
        result = process_file(
            path,
            remap=remap_all.get(path.name),
            dry_run=args.dry_run,
        )
        r, d = len(result["renamed"]), len(result["dropped"])
        if r or d:
            print(f"== {path.name} — {r} renamed, {d} dropped ==")
            for old, new in result["renamed"][:5]:
                print(f"   rename: {old!r:60s} -> {new!r}")
            if r > 5:
                print(f"   ... +{r - 5} more renames")
            for old in result["dropped"][:5]:
                print(f"   drop:   {old!r}")
            if d > 5:
                print(f"   ... +{d - 5} more drops")
            total_renamed += r
            total_dropped += d

    action = "would rewrite" if args.dry_run else "rewrote"
    print(f"\n{action} {total_renamed} titles; dropped {total_dropped} book-meta sections.")
    if args.dry_run:
        print("(dry run — no files changed)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
