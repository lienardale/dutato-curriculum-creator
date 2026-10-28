"""
Stage 2 of the lesson pipeline — knowledge units (QUALIFY).

PURE DATA UTILITY — no AI calls. Three mechanical commands around the
script-owned ``units/`` directory and the authored ``qualification/<source>.json``
and ``concepts.json`` files (contract: docs/artifacts.md "Stage 2"; enums,
thresholds and id formats come from shared/contracts.py and are never redefined
here):

  prepare               extracted/*.json -> units/<source>.json + units/index.json.
                        Each kept section is cleaned (shared/clean_text: page
                        furniture recurring across the whole source, watermarks,
                        ``N | Chapter`` footers, ligatures, de-hyphenation,
                        hard-wrap rejoin) and segmented into typed knowledge
                        units: fenced/indented code, pipe tables, bullet/numbered
                        lists and figure captions are atomic; prose is grouped at
                        blank-line boundaries up to UNIT_MAX_TOKENS, a ``#``
                        heading always starts a unit, a prose run under
                        UNIT_MIN_TOKENS merges into the previous prose unit (or,
                        when a typed block follows, becomes that unit's lead-in),
                        and a single paragraph over the maximum is split at
                        sentence boundaries. Every unit carries heading_path, raw_span
                        (offsets into the raw section text), an estimated page,
                        the images whose position falls inside it and cleaning
                        counts. Idempotent: same inputs, byte-identical output.
  scope-from-structure  legacy structure.json + --area titles -> scope.json
                        (include_sources / include_sections) by resolving the
                        leaves' source_sections to the extracted files that
                        contain them.
  check                 gate units/*.json (ids, enums, token counts), the authored
                        qualification maps (every unit qualified, quotes verbatim,
                        enums, drop reasons, Bloom-ceiling overrides, gleaning) and
                        concepts.json (ids, labels, DAG on depends_on, thin / too
                        broad / merge candidates, gaps, ordering, merges); detects
                        duplicate prose units across sources and sections and
                        writes units/dedup.json. Prints units-per-concept and
                        drop statistics.

Usage:
    python qualify.py prepare output/<name>/ [--scope scope.json] [--source KEY ...]
    python qualify.py scope-from-structure output/<name>/legacy/structure.json \\
        --area "<depth-0 title>" [--area ...] --extracted output/<name>/extracted/ \\
        -o output/<name>/scope.json
    python qualify.py check output/<name>/ [--source KEY] [--strict]

Without ``--scope``, ``prepare`` applies ``output/<name>/scope.json`` when that
file exists and otherwise processes every extracted source.

Module API (called by validate.py / critique.py):
    prepare(input_dir, scope_path=None, sources=None)   -> list[Finding]
    scope_from_structure(structure_path, areas, extracted_dir, out_path)
                                                        -> (scope dict | None, findings)
    check(input_dir, source=None)                       -> list[Finding]
    check_item(input_dir, source_key)                   -> list[Finding]  (one source, no dedup)
    stats(input_dir)                                    -> dict  (units per concept, drop statistics)
    segment_text(cleaned_text)                          -> list[Segment]
    main(argv)                                          -> 0 when no errors, 1 otherwise
                                                           (warnings also fail with --strict)

Findings print as "[ERROR] <check_id> @ <location>: <message>" ([WARN]/[INFO])
followed by "<n> errors, <m> warnings".
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from bisect import bisect_right
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from mimetypes import guess_type
from pathlib import Path

import _compat  # noqa: F401

from chunk import count_tokens, is_code_block
from clean_text import clean_text_with_map, find_recurring_lines
from contracts import (
    BLOCK_TYPES,
    BLOOM_CEILING_DEFAULT,
    BLOOM_LEVELS,
    CONCEPT_BROAD_SHARE,
    CONCEPT_ID_RE,
    CONCEPT_LABEL_WORDS,
    CONCEPT_MERGE_RATIO,
    CONCEPT_THIN_UNITS,
    CONCEPTS_FILE,
    CONTRACT_VERSION,
    COVERAGE_LEVELS,
    DROP_REASONS,
    GAP_RESOLUTION_RE,
    GAP_SEVERITIES,
    KNOWLEDGE_LEVELS,
    LEARNING_STEPS,
    QUALIFICATION_DIR,
    SCOPE_FILE,
    TOO_BROAD_CONCEPT_LABELS,
    UNIT_DEDUP_BAND,
    UNIT_DEDUP_RATIO,
    UNIT_ID_RE,
    UNIT_MAX_TOKENS,
    UNIT_MIN_TOKENS,
    UNIT_TYPES,
    UNITS_DIR,
    UNITS_INDEX,
    Finding,
    dump_json,
    list_item_files,
    load_json,
    normalize_ws,
    parse_unit_id,
    quote_in_unit,
    source_key,
    unit_id,
    word_count,
)

EXTRACTED_DIR = "extracted"
DEDUP_FILE = f"{UNITS_DIR}/dedup.json"

# Files inside units/ that are not per-source units files.
_NON_SOURCE_UNIT_FILES = {"index.json", "dedup.json"}

# Block classification on cleaned text (clean_text separates blocks with blank lines).
_FENCE_RE = re.compile(r"^```")
_HEADING_RE = re.compile(r"^#{1,6}\s")
_TABLE_ROW_RE = re.compile(r"^\|.*\|$")
_LIST_ITEM_RE = re.compile(r"^(?:[-*•+–]|\d{1,3}[.)])\s+\S")
_CAPTION_RE = re.compile(r"^(Figure|Fig\.|Table)\s+\d")
_INDENT_CODE_RE = re.compile(r"^(?: {4,}|\t)")
_SENTENCE_END_RE = re.compile(r"(?<=[.!?])\s+")

# raw_span fallback: locate the unit by its first / last normalized characters.
_SPAN_PROBE_CHARS = 40

# Duplicate detection: candidate pairs share word shingles before SequenceMatcher runs.
_DEDUP_SHINGLE_WORDS = 4
_DEDUP_SHINGLE_MAX_POSTINGS = 50     # shingles in more units than this are too common to index
_DEDUP_SHARED_SHINGLE_SHARE = 0.30   # of the smaller unit's shingles
_DEDUP_TOKEN_DIFF_MAX = 0.40         # pairs whose token counts differ by more are skipped
_WORD_RE = re.compile(r"[a-z0-9]+")

_PLAIN_DROP_REASONS = tuple(r for r in DROP_REASONS if r != "duplicate")
_DROP_REASON_RE = re.compile(r"^(?:" + "|".join(re.escape(r) for r in _PLAIN_DROP_REASONS) + r"|duplicate_of:(?P<unit>\S+))$")

_SEVERITY_TAG = {"error": "[ERROR]", "warning": "[WARN]", "info": "[INFO]"}
_SEVERITY_RANK = {"error": 0, "warning": 1, "info": 2}


# ---------------------------------------------------------------------------
# Segmentation (cleaned text -> typed units)
# ---------------------------------------------------------------------------


@dataclass
class Segment:
    """One knowledge unit inside a cleaned section: ``text == cleaned[start:end]``."""

    block_type: str
    start: int
    end: int
    text: str
    token_count: int


def _blocks(cleaned: str) -> list[tuple[int, int]]:
    """(start, end) offsets of every blank-line-separated block; a fenced code block is one block."""
    out: list[tuple[int, int]] = []
    offset = 0
    start: int | None = None
    prev_end = 0
    in_fence = False
    for line in cleaned.split("\n"):
        line_start, line_end = offset, offset + len(line)
        offset = line_end + 1
        stripped = line.strip()
        if in_fence:
            prev_end = line_end
            if _FENCE_RE.match(stripped):
                in_fence = False
                out.append((start, prev_end))  # type: ignore[arg-type]
                start = None
            continue
        if not stripped:
            if start is not None:
                out.append((start, prev_end))
                start = None
            continue
        if _FENCE_RE.match(stripped):
            if start is not None:
                out.append((start, prev_end))
            start = line_start
            in_fence = True
        elif start is None:
            start = line_start
        prev_end = line_end
    if start is not None:
        out.append((start, prev_end))
    return out


def _classify_block(text: str) -> str:
    """Block type of one block; ``prose`` also covers ``#`` headings."""
    lines = text.split("\n")
    first = lines[0].strip()
    if _FENCE_RE.match(first):
        return "code"
    if all(_INDENT_CODE_RE.match(ln) for ln in lines) or is_code_block(text):
        return "code"
    if all(_TABLE_ROW_RE.match(ln.strip()) for ln in lines):
        return "table"
    if _LIST_ITEM_RE.match(first) and 2 * sum(1 for ln in lines if _LIST_ITEM_RE.match(ln.strip())) >= len(lines):
        return "list"
    if _CAPTION_RE.match(first):
        return "figure_caption"
    return "prose"


def _sentence_spans(cleaned: str, start: int, end: int) -> list[tuple[int, int]]:
    """Split an oversized prose block into sentence spans packed up to UNIT_MAX_TOKENS."""
    text = cleaned[start:end]
    cuts = [0] + [m.end() for m in _SENTENCE_END_RE.finditer(text)] + [len(text)]
    sentences = [(start + a, start + b) for a, b in zip(cuts, cuts[1:]) if text[a:b].strip()]
    pieces: list[tuple[int, int]] = []
    piece_start: int | None = None
    for s_start, s_end in sentences:
        if piece_start is None:
            piece_start = s_start
        elif count_tokens(cleaned[piece_start:s_end]) > UNIT_MAX_TOKENS:
            pieces.append((piece_start, _rstrip_end(cleaned, piece_start, s_start)))
            piece_start = s_start
    if piece_start is not None:
        pieces.append((piece_start, end))
    return pieces


def _rstrip_end(cleaned: str, start: int, end: int) -> int:
    while end > start and cleaned[end - 1].isspace():
        end -= 1
    return end


def _typed_blocks(cleaned: str) -> list[tuple[str, int, int]]:
    """(kind, start, end) per block, with oversized prose paragraphs split into sentence runs."""
    out: list[tuple[str, int, int]] = []
    for start, end in _blocks(cleaned):
        text = cleaned[start:end]
        kind = _classify_block(text)
        if kind == "prose" and not _HEADING_RE.match(text) and count_tokens(text) > UNIT_MAX_TOKENS:
            out.extend(("prose", s, e) for s, e in _sentence_spans(cleaned, start, end))
        else:
            out.append((kind, start, end))
    return out


def segment_text(cleaned: str) -> list[Segment]:
    """Segment cleaned section text into knowledge units (see the module docstring).

    Prose buffers under UNIT_MIN_TOKENS are never emitted alone when a neighbour
    can take them: one flushed by a typed block becomes the lead-in of that
    typed unit ("Common problems include:" stays with its list); one flushed by
    a heading or the end of the section merges into the previous prose unit.
    A heading-started buffer never merges backwards.
    """
    units: list[Segment] = []
    buf: tuple[int, int] | None = None

    def flush(before_typed: bool) -> tuple[int, int] | None:
        """Emit the buffer; return it instead when it should lead the next typed unit."""
        nonlocal buf
        if buf is None:
            return None
        start, end = buf
        buf = None
        text = cleaned[start:end]
        tokens = count_tokens(text)
        if tokens >= UNIT_MIN_TOKENS:
            units.append(Segment("prose", start, end, text, tokens))
            return None
        if before_typed:
            return start, end
        prev = units[-1] if units else None
        if prev is not None and prev.block_type == "prose" and not _HEADING_RE.match(text):
            merged = cleaned[prev.start:end]
            units[-1] = Segment("prose", prev.start, end, merged, count_tokens(merged))
        else:
            units.append(Segment("prose", start, end, text, tokens))
        return None

    for kind, start, end in _typed_blocks(cleaned):
        if kind != "prose":
            lead = flush(before_typed=True)
            if lead is not None:
                start = lead[0]
            text = cleaned[start:end]
            units.append(Segment(kind, start, end, text, count_tokens(text)))
            continue
        if _HEADING_RE.match(cleaned[start:end]):
            flush(before_typed=False)
        if buf is None:
            buf = (start, end)
        elif count_tokens(cleaned[buf[0]:end]) > UNIT_MAX_TOKENS:
            flush(before_typed=False)
            buf = (start, end)
        else:
            buf = (buf[0], end)
    flush(before_typed=False)
    return units


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------


@dataclass
class _Scope:
    raw: dict
    include_sources: set[str] | None
    include_sections: dict[str, set[str]]   # source -> normalized lowercase titles

    def section_filter(self, source: str) -> set[str] | None:
        return self.include_sections.get(source)


def _parse_scope(data, location: str) -> tuple[_Scope | None, list[Finding]]:
    findings: list[Finding] = []
    if not isinstance(data, dict):
        return None, [Finding("units.scope_shape", "error", location, "scope must be a JSON object")]
    sources = data.get("include_sources")
    include_sources: set[str] | None = None
    if sources is not None:
        if not isinstance(sources, list) or not all(isinstance(s, str) for s in sources):
            findings.append(Finding("units.scope_shape", "error", location, "include_sources must be a list of source keys or null"))
            return None, findings
        include_sources = set(sources)
    sections = data.get("include_sections")
    include_sections: dict[str, set[str]] = {}
    if sections is not None:
        if not isinstance(sections, dict):
            findings.append(Finding("units.scope_shape", "error", location, "include_sections must be an object {source: [titles]}"))
            return None, findings
        for source, titles in sections.items():
            if not isinstance(titles, list) or not all(isinstance(t, str) for t in titles):
                findings.append(Finding("units.scope_shape", "error", f"{location} include_sections[{source}]", "titles must be a list of strings"))
                return None, findings
            include_sections[source] = {normalize_ws(t).lower() for t in titles}
    return _Scope(raw=data, include_sources=include_sources, include_sections=include_sections), findings


def _resolve_scope(input_dir: Path, scope_path: Path | None) -> tuple[_Scope | None, list[Finding], str | None]:
    """Explicit --scope, else output/<name>/scope.json when present, else no scope."""
    path = Path(scope_path) if scope_path else input_dir / SCOPE_FILE
    if not path.exists():
        if scope_path:
            return None, [Finding("units.scope_missing", "error", str(path), "scope file not found")], None
        return None, [], None
    try:
        data = load_json(path)
    except (OSError, json.JSONDecodeError) as e:
        return None, [Finding("units.scope_invalid_json", "error", str(path), str(e))], None
    scope, findings = _parse_scope(data, str(path))
    return scope, findings, str(path)


# ---------------------------------------------------------------------------
# prepare
# ---------------------------------------------------------------------------


def _heading_paths(sections: list[dict]) -> list[list[str]]:
    """Ancestor titles by depth + own title, per section."""
    paths: list[list[str]] = []
    stack: list[tuple[int, str]] = []
    for i, section in enumerate(sections):
        title = _section_title(section, i)
        depth = section.get("depth") if isinstance(section, dict) else None
        depth = depth if isinstance(depth, int) and not isinstance(depth, bool) and depth >= 0 else 0
        while stack and stack[-1][0] >= depth:
            stack.pop()
        paths.append([t for _, t in stack] + [title])
        stack.append((depth, title))
    return paths


def _section_title(section, index: int) -> str:
    title = section.get("title") if isinstance(section, dict) else None
    return normalize_ws(title) if isinstance(title, str) and normalize_ws(title) else f"Section {index}"


def _probe_pattern(fragment: str) -> re.Pattern:
    return re.compile(r"\s+".join(re.escape(tok) for tok in normalize_ws(fragment).split(" ")), re.IGNORECASE)


def _locate_span(raw: str, text: str) -> tuple[int, int] | None:
    """Find ``text`` in ``raw`` by its first and last normalized characters (whitespace-flexible)."""
    norm = normalize_ws(text)
    if not norm:
        return None
    head = _probe_pattern(norm[:_SPAN_PROBE_CHARS]).search(raw)
    if not head:
        return None
    if len(norm) <= _SPAN_PROBE_CHARS:
        return head.start(), head.end()
    tail = _probe_pattern(norm[-_SPAN_PROBE_CHARS:]).search(raw, head.start())
    if not tail:
        return None
    return head.start(), tail.end()


def _raw_span(seg: Segment, to_raw, raw: str) -> tuple[int, int] | None:
    start, end = to_raw(seg.start), to_raw(seg.end - 1) + 1
    if 0 <= start < end <= len(raw):
        return start, end
    return _locate_span(raw, seg.text)


def _page_of(raw_start: int | None, raw_len: int, meta: dict) -> int | None:
    page_start, page_end = meta.get("page_start"), meta.get("page_end")
    if not isinstance(page_start, int) or isinstance(page_start, bool):
        return None
    if not isinstance(page_end, int) or isinstance(page_end, bool) or page_end <= page_start or raw_start is None:
        return page_start
    frac = raw_start / max(raw_len, 1)
    return min(page_end, page_start + int(frac * (page_end - page_start + 1)))


def _image_offset(img: dict, raw_len: int, meta: dict) -> int:
    """Raw offset an image anchors to: interpolated from ``context: "page:N"``, else the section start."""
    context = img.get("context")
    page_start, page_end = meta.get("page_start"), meta.get("page_end")
    if (isinstance(context, str) and context.startswith("page:") and isinstance(page_start, int)
            and isinstance(page_end, int) and page_end > page_start):
        try:
            page = int(context.split(":", 1)[1])
        except ValueError:
            return 0
        frac = min(max((page - page_start + 0.5) / (page_end - page_start + 1), 0.0), 1.0)
        return int(frac * raw_len)
    return 0


def _unit_for_offset(spans: list[tuple[int, int] | None], offset: int) -> int | None:
    """Index of the unit whose raw span contains ``offset`` (gaps resolve to the next unit)."""
    known = [(span[0], i) for i, span in enumerate(spans) if span is not None]
    if not known:
        return 0 if spans else None
    starts = [s for s, _ in known]
    k = bisect_right(starts, offset) - 1
    if k < 0:
        return known[0][1]
    i = known[k][1]
    if offset < spans[i][1] or k + 1 >= len(known):  # type: ignore[index]
        return i
    return known[k + 1][1]


def _unit_image(img: dict, registry: dict[str, dict]) -> dict:
    local_path = img.get("local_path") or ""
    mime = (registry.get(img.get("id")) or {}).get("mime_type") or img.get("mime_type") or guess_type(local_path)[0]
    return {"id": img.get("id"), "local_path": local_path, "alt_text": img.get("alt_text") or "", "mime_type": mime}


def _prepare_source(source: str, filename: str, data, scope: _Scope | None) -> tuple[dict | None, list[Finding]]:
    """Units file content + index stats for one extracted source."""
    loc = f"{EXTRACTED_DIR}/{filename}"
    sections = data.get("sections") if isinstance(data, dict) else None
    if not isinstance(sections, list):
        return None, [Finding("units.invalid_extracted", "error", loc, "extracted file has no \"sections\" list")]
    findings: list[Finding] = []
    source_type = data.get("source_type") if isinstance(data.get("source_type"), str) else "unknown"
    registry = {img.get("id"): img for img in (data.get("images") or []) if isinstance(img, dict)}
    recurring = find_recurring_lines([s.get("content") or "" for s in sections if isinstance(s, dict)])
    paths = _heading_paths(sections)
    section_filter = scope.section_filter(source) if scope else None

    units: list[dict] = []
    skipped = 0
    for index, section in enumerate(sections):
        title = _section_title(section, index)
        if not isinstance(section, dict):
            findings.append(Finding("units.invalid_extracted", "warning", f"{loc} sections[{index}]", "section is not an object — skipped"))
            skipped += 1
            continue
        if section_filter is not None and normalize_ws(title).lower() not in section_filter:
            skipped += 1
            continue
        raw = section.get("content") if isinstance(section.get("content"), str) else ""
        if not raw.strip():
            skipped += 1
            continue
        cleaned, report, to_raw = clean_text_with_map(raw, recurring=recurring, source_type=source_type, section_title=title)
        segments = segment_text(cleaned) if cleaned.strip() else []
        if not segments:
            skipped += 1
            continue
        meta = section.get("metadata") if isinstance(section.get("metadata"), dict) else {}
        spans = [_raw_span(seg, to_raw, raw) for seg in segments]
        for n, (seg, span) in enumerate(zip(segments, spans)):
            if span is None:
                findings.append(Finding(
                    "units.raw_span_unresolved", "warning", unit_id(source, index, n),
                    "could not map the unit back to the raw section text; raw_span is [null, null]",
                ))
        cleaning = [Counter() for _ in segments]
        for kind, offsets in report["events"].items():
            for offset in offsets:
                target = _unit_for_offset(spans, offset)
                if target is not None:
                    cleaning[target][kind] += 1
        images: list[list[dict]] = [[] for _ in segments]
        for img in section.get("images") or []:
            if not isinstance(img, dict):
                continue
            target = _unit_for_offset(spans, _image_offset(img, len(raw), meta))
            if target is None:
                findings.append(Finding("units.image_unattached", "warning", f"{loc} sections[{index}]", f"image {img.get('id')!r} could not be attached to a unit"))
            else:
                images[target].append(_unit_image(img, registry))
        for n, (seg, span) in enumerate(zip(segments, spans)):
            units.append({
                "id": unit_id(source, index, n),
                "section_index": index,
                "section_title": title,
                "heading_path": paths[index],
                "block_type": seg.block_type,
                "text": seg.text,
                "token_count": seg.token_count,
                "raw_span": [span[0], span[1]] if span else [None, None],
                "page": _page_of(span[0] if span else None, len(raw), meta),
                "images": images[n],
                "cleaning": {kind: cleaning[n][kind] for kind in report["events"]},
            })

    file = {
        "contract_version": CONTRACT_VERSION,
        "source": source,
        "source_file": filename,
        "source_type": source_type,
        "title": data.get("title") if isinstance(data.get("title"), str) else source,
        "scope_applied": scope is not None,
        "units": units,
    }
    stats = {
        "units": len(units),
        "tokens": sum(u["token_count"] for u in units),
        "sections": len(sections),
        "skipped_sections": skipped,
    }
    return {"file": file, "stats": stats}, findings


def _existing_index(path: Path) -> dict:
    try:
        data = load_json(path)
    except (OSError, json.JSONDecodeError):
        return {}
    sources = data.get("sources") if isinstance(data, dict) else None
    return {k: v for k, v in sources.items() if isinstance(v, dict)} if isinstance(sources, dict) else {}


def prepare(input_dir: Path, scope_path: Path | None = None, sources: list[str] | None = None) -> list[Finding]:
    """Write units/<source>.json for every (selected) extracted source and units/index.json."""
    input_dir = Path(input_dir)
    extracted_dir = input_dir / EXTRACTED_DIR
    if not extracted_dir.is_dir():
        return [Finding("units.missing_extracted", "error", f"{EXTRACTED_DIR}/", "extracted directory not found — run the extractors first")]
    scope, findings, scope_label = _resolve_scope(input_dir, scope_path)
    if any(f.severity == "error" for f in findings):
        return findings
    available = {source_key(p.name): p for p in sorted(extracted_dir.glob("*.json"))}
    if not available:
        findings.append(Finding("units.missing_extracted", "error", f"{EXTRACTED_DIR}/", "no extracted/*.json files found"))
        return findings
    if sources:
        for key in sources:
            if key not in available:
                findings.append(Finding("units.unknown_source", "error", key, f"no {EXTRACTED_DIR}/{key}.json — known sources: {', '.join(sorted(available))}"))
        if any(f.severity == "error" for f in findings):
            return findings

    units_dir = input_dir / UNITS_DIR
    index_path = input_dir / UNITS_INDEX
    index_sources = _existing_index(index_path) if sources else {}
    produced: list[str] = []
    for key, path in available.items():
        if sources and key not in sources:
            continue
        if scope and scope.include_sources is not None and key not in scope.include_sources:
            continue
        try:
            data = load_json(path)
        except (OSError, json.JSONDecodeError) as e:
            findings.append(Finding("units.invalid_extracted", "error", f"{EXTRACTED_DIR}/{path.name}", str(e)))
            continue
        result, source_findings = _prepare_source(key, path.name, data, scope)
        findings.extend(source_findings)
        if result is None:
            continue
        dump_json(units_dir / f"{key}.json", result["file"])
        index_sources[key] = result["stats"]
        produced.append(key)
        st = result["stats"]
        print(f"{key}: {st['units']} units, {st['tokens']} tokens, {st['sections']} sections ({st['skipped_sections']} skipped)")

    if not sources:
        for path in list_item_files(units_dir):
            if path.name in _NON_SOURCE_UNIT_FILES or source_key(path.name) in produced:
                continue
            path.unlink()
            findings.append(Finding("units.stale_removed", "info", f"{UNITS_DIR}/{path.name}", "units file for a source outside the scope / extracted set was removed"))
    index_sources = dict(sorted(index_sources.items()))
    dump_json(index_path, {
        "contract_version": CONTRACT_VERSION,
        "sources": index_sources,
        "total_units": sum(s.get("units", 0) for s in index_sources.values()),
        "total_tokens": sum(s.get("tokens", 0) for s in index_sources.values()),
        "scope": scope.raw if scope else None,
    })
    if scope_label:
        print(f"scope: {scope_label}")
    print(f"wrote {UNITS_INDEX}: {len(index_sources)} sources, "
          f"{sum(s.get('units', 0) for s in index_sources.values())} units, "
          f"{sum(s.get('tokens', 0) for s in index_sources.values())} tokens")
    return findings


# ---------------------------------------------------------------------------
# scope-from-structure
# ---------------------------------------------------------------------------


def _leaves(topics: list) -> list[dict]:
    out: list[dict] = []
    for topic in topics:
        if not isinstance(topic, dict):
            continue
        children = topic.get("children") or []
        if children:
            out.extend(_leaves(children))
        else:
            out.append(topic)
    return out


def _extracted_title_index(extracted_dir: Path) -> tuple[dict[str, list[tuple[str, str]]], list[Finding]]:
    """normalized title -> [(source key, extracted title)] over every extracted file."""
    index: dict[str, list[tuple[str, str]]] = defaultdict(list)
    findings: list[Finding] = []
    for path in sorted(Path(extracted_dir).glob("*.json")):
        try:
            data = load_json(path)
        except (OSError, json.JSONDecodeError) as e:
            findings.append(Finding("scope.invalid_extracted", "warning", f"{EXTRACTED_DIR}/{path.name}", f"{e} — skipped"))
            continue
        key = source_key(path.name)
        for i, section in enumerate((data.get("sections") or []) if isinstance(data, dict) else []):
            title = _section_title(section, i)
            entry = (key, title)
            if entry not in index[normalize_ws(title).lower()]:
                index[normalize_ws(title).lower()].append(entry)
    return index, findings


def scope_from_structure(structure_path: Path, areas: list[str], extracted_dir: Path, out_path: Path | None) -> tuple[dict | None, list[Finding]]:
    """Build a scope from the leaves' source_sections under the named depth-0 areas of a legacy structure."""
    findings: list[Finding] = []
    structure_path = Path(structure_path)
    try:
        structure = load_json(structure_path)
    except (OSError, json.JSONDecodeError) as e:
        return None, [Finding("scope.invalid_structure", "error", str(structure_path), str(e))]
    topics = structure if isinstance(structure, list) else structure.get("topics") if isinstance(structure, dict) else None
    if not isinstance(topics, list):
        return None, [Finding("scope.invalid_structure", "error", str(structure_path), "structure must be a list of topics (or {\"topics\": [...]})")]
    if not Path(extracted_dir).is_dir():
        return None, [Finding("scope.missing_extracted", "error", str(extracted_dir), "extracted directory not found")]

    wanted = {normalize_ws(a).lower(): a for a in areas}
    matched: list[dict] = []
    found: set[str] = set()
    for topic in topics:
        if not isinstance(topic, dict):
            continue
        key = normalize_ws(str(topic.get("title") or "")).lower()
        if key in wanted:
            matched.append(topic)
            found.add(key)
    available = [str(t.get("title")) for t in topics if isinstance(t, dict)]
    for key, area in wanted.items():
        if key not in found:
            findings.append(Finding("scope.unknown_area", "error", str(structure_path), f"no depth-0 topic titled {area!r}; available: {available}"))
    if any(f.severity == "error" for f in findings):
        return None, findings

    titles: list[str] = []
    for leaf in _leaves(matched):
        for title in leaf.get("source_sections") or []:
            if isinstance(title, str) and title not in titles:
                titles.append(title)
    if not titles:
        findings.append(Finding("scope.empty", "warning", str(structure_path), "the selected areas list no source_sections"))

    index, index_findings = _extracted_title_index(extracted_dir)
    findings.extend(index_findings)
    include_sections: dict[str, list[str]] = {}
    for title in titles:
        entries = index.get(normalize_ws(title).lower())
        if not entries:
            findings.append(Finding("scope.unresolved_section", "warning", title, "no extracted section carries this title — left out of the scope"))
            continue
        if len(entries) > 1:
            findings.append(Finding("scope.ambiguous_section", "info", title, f"title found in {len(entries)} sources: {', '.join(k for k, _ in entries)} — all included"))
        for key, extracted_title in entries:
            bucket = include_sections.setdefault(key, [])
            if extracted_title not in bucket:
                bucket.append(extracted_title)
    include_sections = dict(sorted(include_sections.items()))
    scope = {"include_sources": list(include_sections), "include_sections": include_sections}
    if out_path is not None:
        dump_json(Path(out_path), scope)
    return scope, findings


# ---------------------------------------------------------------------------
# check — inputs
# ---------------------------------------------------------------------------


@dataclass
class _Units:
    by_source: dict[str, list[dict]] = field(default_factory=dict)   # source -> units in file order
    texts: dict[str, str] = field(default_factory=dict)              # unit id -> text
    order: dict[str, int] = field(default_factory=dict)              # unit id -> global position
    sections: dict[str, set[int]] = field(default_factory=dict)      # source -> section indices with units
    findings: list[Finding] = field(default_factory=list)


def _read(path: Path, loc: str, check_prefix: str, findings: list[Finding]):
    try:
        return load_json(path)
    except (OSError, json.JSONDecodeError) as e:
        findings.append(Finding(f"{check_prefix}.invalid_json", "error", loc, str(e)))
        return None


def _check_units_file(source: str, data, loc: str, ctx: _Units) -> None:
    f = ctx.findings
    if not isinstance(data, dict) or not isinstance(data.get("units"), list):
        f.append(Finding("units.shape", "error", loc, "units file must be an object with a \"units\" list"))
        return
    if data.get("contract_version") != CONTRACT_VERSION:
        f.append(Finding("units.contract_version", "warning", loc, f"contract_version={data.get('contract_version')!r}, expected {CONTRACT_VERSION}"))
    if data.get("source") != source:
        f.append(Finding("units.source_mismatch", "error", loc, f"source={data.get('source')!r} does not match the file name ({source})"))
    units: list[dict] = []
    for i, unit in enumerate(data["units"]):
        uloc = f"{loc} units[{i}]"
        if not isinstance(unit, dict):
            f.append(Finding("units.shape", "error", uloc, "unit must be an object"))
            continue
        uid = unit.get("id")
        parsed = parse_unit_id(uid) if isinstance(uid, str) else None
        if parsed is None:
            f.append(Finding("units.id", "error", uloc, f"id {uid!r} does not match u:<source>:<section>:<n>"))
            continue
        if parsed[0] != source or unit.get("section_index") != parsed[1]:
            f.append(Finding("units.id", "error", uid, f"id disagrees with source {source!r} / section_index {unit.get('section_index')!r}"))
        if uid in ctx.texts:
            f.append(Finding("units.duplicate_id", "error", uid, "unit id appears more than once"))
            continue
        if unit.get("block_type") not in BLOCK_TYPES:
            f.append(Finding("units.block_type", "error", uid, f"block_type={unit.get('block_type')!r} not in {list(BLOCK_TYPES)}"))
        tc = unit.get("token_count")
        if not isinstance(tc, int) or isinstance(tc, bool) or tc <= 0:
            f.append(Finding("units.token_count", "error", uid, f"token_count={tc!r} must be a positive integer"))
        text = unit.get("text")
        if not isinstance(text, str) or not text.strip():
            f.append(Finding("units.empty_text", "error", uid, "unit text is empty"))
            text = ""
        span = unit.get("raw_span")
        if not (isinstance(span, list) and len(span) == 2 and all(v is None or (isinstance(v, int) and not isinstance(v, bool)) for v in span)):
            f.append(Finding("units.shape", "error", uid, f"raw_span={span!r} must be [start, end] (ints or nulls)"))
        if not isinstance(unit.get("heading_path"), list) or not isinstance(unit.get("images"), list):
            f.append(Finding("units.shape", "error", uid, "heading_path and images must be lists"))
        ctx.texts[uid] = text
        ctx.order[uid] = len(ctx.order)
        ctx.sections.setdefault(source, set()).add(parsed[1])
        units.append(unit)
    ctx.by_source[source] = units


def _load_units(input_dir: Path, only_source: str | None = None) -> _Units:
    ctx = _Units()
    units_dir = input_dir / UNITS_DIR
    files = [p for p in list_item_files(units_dir) if p.name not in _NON_SOURCE_UNIT_FILES]
    if only_source is not None:
        files = [p for p in files if source_key(p.name) == only_source]
        if not files:
            ctx.findings.append(Finding("units.missing", "error", f"{UNITS_DIR}/{only_source}.json", f"no units file for source {only_source!r} — run `qualify.py prepare`"))
            return ctx
    if not files:
        ctx.findings.append(Finding("units.missing", "error", f"{UNITS_DIR}/", "no units/<source>.json files found — run `qualify.py prepare` first"))
        return ctx
    for path in files:
        loc = f"{UNITS_DIR}/{path.name}"
        data = _read(path, loc, "units", ctx.findings)
        if data is not None:
            _check_units_file(source_key(path.name), data, loc, ctx)
    return ctx


@dataclass
class _Concepts:
    data: dict | None = None
    concepts: dict[str, dict] = field(default_factory=dict)    # id -> concept (first occurrence)
    order: list[str] = field(default_factory=list)
    merged_into: dict[str, str] = field(default_factory=dict)   # merged id -> kept id
    loaded: bool = False
    findings: list[Finding] = field(default_factory=list)

    def known(self, cid) -> bool:
        return isinstance(cid, str) and cid in self.concepts

    def unknown_message(self, cid) -> str:
        if isinstance(cid, str) and cid in self.merged_into:
            return f"concept {cid!r} was merged into {self.merged_into[cid]} (concepts.json merges) — retag with the kept id"
        return f"concept {cid!r} is not in {CONCEPTS_FILE}"


def _load_concepts(input_dir: Path) -> _Concepts:
    ctx = _Concepts()
    path = input_dir / CONCEPTS_FILE
    if not path.exists():
        ctx.findings.append(Finding("concepts.missing", "error", CONCEPTS_FILE, "concepts.json not found — the agent writes it during qualification (rubrics/concepts.md)"))
        return ctx
    data = _read(path, CONCEPTS_FILE, "concepts", ctx.findings)
    if data is None:
        return ctx
    if not isinstance(data, dict) or not isinstance(data.get("concepts"), list):
        ctx.findings.append(Finding("concepts.shape", "error", CONCEPTS_FILE, "concepts.json must be an object with a \"concepts\" list"))
        return ctx
    ctx.data = data
    ctx.loaded = True
    for concept in data["concepts"]:
        cid = concept.get("id") if isinstance(concept, dict) else None
        if isinstance(cid, str) and cid not in ctx.concepts:
            ctx.concepts[cid] = concept
            ctx.order.append(cid)
    for merge in (data.get("merges") or []) if isinstance(data.get("merges"), list) else []:
        if isinstance(merge, dict) and isinstance(merge.get("kept"), str):
            for mid in (merge.get("merged") or []) if isinstance(merge.get("merged"), list) else []:
                if isinstance(mid, str):
                    ctx.merged_into.setdefault(mid, merge["kept"])
    return ctx


# ---------------------------------------------------------------------------
# check — qualification/<source>.json
# ---------------------------------------------------------------------------


def _id_list(ids: list[str], limit: int = 15) -> str:
    shown = ", ".join(ids[:limit])
    return shown + (f" (+{len(ids) - limit} more)" if len(ids) > limit else "")


def _check_entry(uid: str, entry: dict, text: str, concepts: _Concepts, texts: dict[str, str], f: list[Finding]) -> None:
    for key, enum in (("unit_type", UNIT_TYPES), ("knowledge_level", KNOWLEDGE_LEVELS),
                      ("learning_step", LEARNING_STEPS), ("bloom_ceiling", BLOOM_LEVELS)):
        if entry.get(key) not in enum:
            f.append(Finding("qualification.enum", "error", uid, f"{key}={entry.get(key)!r} not in {list(enum)}"))

    tags = entry.get("concepts")
    if not isinstance(tags, list):
        f.append(Finding("qualification.concepts", "error", uid, "concepts must be a list of {concept_id, quote}"))
        tags = []
    for i, tag in enumerate(tags):
        tloc = f"{uid} concepts[{i}]"
        if not isinstance(tag, dict):
            f.append(Finding("qualification.concepts", "error", tloc, "concept tag must be an object {concept_id, quote}"))
            continue
        cid = tag.get("concept_id")
        if not isinstance(cid, str) or not CONCEPT_ID_RE.match(cid):
            f.append(Finding("qualification.concepts", "error", tloc, f"concept_id {cid!r} does not match c:<slug>"))
        elif concepts.loaded and not concepts.known(cid):
            f.append(Finding("concepts.unknown_concept", "error", tloc, concepts.unknown_message(cid)))
        quote = tag.get("quote")
        if not isinstance(quote, str) or not quote_in_unit(quote, text):
            f.append(Finding("qualification.quote_mismatch", "error", tloc, f"quote {str(quote)[:80]!r} is not a verbatim substring of the unit"))

    keep = entry.get("keep")
    reason = entry.get("drop_reason")
    if not isinstance(keep, bool):
        f.append(Finding("qualification.keep", "error", uid, f"keep={keep!r} must be true or false"))
    elif keep:
        if reason is not None:
            f.append(Finding("qualification.drop_reason", "warning", uid, f"drop_reason={reason!r} on a kept unit — set it to null or keep=false"))
        if not tags:
            f.append(Finding("qualification.no_concepts", "warning", uid, "kept unit tags no concept — regroup will leave it unassigned"))
        if entry.get("unit_type") in ("noise", "reference"):
            f.append(Finding("qualification.noise_kept", "warning", uid, f"unit_type={entry.get('unit_type')!r} is normally keep=false"))
    else:
        m = _DROP_REASON_RE.match(reason) if isinstance(reason, str) else None
        if not m:
            f.append(Finding("qualification.drop_reason", "error", uid, f"drop_reason={reason!r} must be one of {list(_PLAIN_DROP_REASONS)} or duplicate_of:<unit_id>"))
        elif m.group("unit") is not None:
            target = m.group("unit")
            if target == uid:
                f.append(Finding("qualification.drop_reason", "error", uid, "duplicate_of names the unit itself"))
            elif target not in texts:
                f.append(Finding("qualification.drop_reason", "error", uid, f"duplicate_of target {target!r} is not a unit"))

    level, ceiling = entry.get("knowledge_level"), entry.get("bloom_ceiling")
    if level in BLOOM_CEILING_DEFAULT and ceiling in BLOOM_LEVELS and ceiling != BLOOM_CEILING_DEFAULT[level]:
        if not (isinstance(entry.get("bloom_ceiling_reason"), str) and entry["bloom_ceiling_reason"].strip()):
            f.append(Finding("qualification.bloom_ceiling_reason", "warning", uid,
                             f"bloom_ceiling {ceiling} overrides the {level} default {BLOOM_CEILING_DEFAULT[level]} without bloom_ceiling_reason"))

    quality = entry.get("quality")
    if not isinstance(quality, int) or isinstance(quality, bool) or not 1 <= quality <= 5:
        f.append(Finding("qualification.quality", "error", uid, f"quality={quality!r} must be an integer 1-5"))

    deps = entry.get("depends_on")
    if deps is None:
        deps = []
    if not isinstance(deps, list):
        f.append(Finding("qualification.depends_on", "error", uid, "depends_on must be a list of concept ids"))
        deps = []
    for dep in deps:
        if not isinstance(dep, str) or not CONCEPT_ID_RE.match(dep):
            f.append(Finding("qualification.depends_on", "error", uid, f"depends_on entry {dep!r} does not match c:<slug>"))
        elif concepts.loaded and not concepts.known(dep):
            f.append(Finding("concepts.unknown_concept", "error", f"{uid} depends_on", concepts.unknown_message(dep)))


def _check_gleaning(gleaning, section_indices: set[int], loc: str, f: list[Finding]) -> None:
    if gleaning is None:
        gleaning = {}
    if not isinstance(gleaning, dict):
        f.append(Finding("qualification.gleaning", "warning", loc, "gleaning must be an object keyed by section index"))
        return
    seen: set[int] = set()
    for key, entry in gleaning.items():
        try:
            index = int(key)
        except (TypeError, ValueError):
            f.append(Finding("qualification.gleaning", "warning", f"{loc} gleaning[{key!r}]", "key must be a section index"))
            continue
        if index not in section_indices:
            f.append(Finding("qualification.gleaning", "warning", f"{loc} gleaning[{key!r}]", "no unit of this source has that section index"))
        seen.add(index)
        if not isinstance(entry, dict) or not isinstance(entry.get("missed"), bool):
            f.append(Finding("qualification.gleaning", "warning", f"{loc} gleaning[{key!r}]", "entry must be {\"missed\": bool, \"note\": str}"))
    missing = sorted(section_indices - seen)
    if missing:
        f.append(Finding("qualification.gleaning", "warning", loc, f"no gleaning entry for section index(es) {_id_list([str(i) for i in missing])}"))


def _check_qualification(source: str, units: _Units, concepts: _Concepts, input_dir: Path) -> tuple[dict[str, dict], list[Finding]]:
    """Gate qualification/<source>.json; returns (entries by unit id, findings)."""
    f: list[Finding] = []
    path = input_dir / QUALIFICATION_DIR / f"{source}.json"
    loc = f"{QUALIFICATION_DIR}/{source}.json"
    if not path.exists():
        return {}, [Finding("qualification.missing", "error", loc, f"no qualification for source {source!r} — see rubrics/qualification.md")]
    data = _read(path, loc, "qualification", f)
    if data is None:
        return {}, f
    if not isinstance(data, dict) or not isinstance(data.get("units"), dict):
        f.append(Finding("qualification.shape", "error", loc, "qualification file must be an object with a \"units\" map"))
        return {}, f
    if data.get("contract_version") != CONTRACT_VERSION:
        f.append(Finding("qualification.contract_version", "warning", loc, f"contract_version={data.get('contract_version')!r}, expected {CONTRACT_VERSION}"))
    if data.get("source") != source:
        f.append(Finding("qualification.source_mismatch", "error", loc, f"source={data.get('source')!r} does not match the file name ({source})"))

    unit_ids = [u["id"] for u in units.by_source.get(source, [])]
    entries: dict[str, dict] = {}
    for uid, entry in data["units"].items():
        if uid not in units.texts or parse_unit_id(uid) is None or parse_unit_id(uid)[0] != source:  # type: ignore[index]
            f.append(Finding("qualification.unknown_unit", "error", f"{loc} units[{uid}]", f"{uid!r} is not a unit of source {source!r}"))
            continue
        if not isinstance(entry, dict):
            f.append(Finding("qualification.shape", "error", uid, "qualification entry must be an object"))
            continue
        entries[uid] = entry
        _check_entry(uid, entry, units.texts[uid], concepts, units.texts, f)
    missing = [uid for uid in unit_ids if uid not in data["units"]]
    if missing:
        f.append(Finding("qualification.missing_unit", "error", loc, f"{len(missing)} unit(s) have no qualification entry: {_id_list(missing)}"))
    _check_gleaning(data.get("gleaning"), units.sections.get(source, set()), loc, f)
    return entries, f


# ---------------------------------------------------------------------------
# check — concepts.json
# ---------------------------------------------------------------------------


def _find_cycle(succ: dict[str, list[str]], nodes: list[str]) -> list[str] | None:
    """First dependency cycle found by DFS, as a node path closing on itself."""
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = defaultdict(int)
    stack: list[str] = []

    def dfs(node: str) -> list[str] | None:
        color[node] = GRAY
        stack.append(node)
        for nxt in succ.get(node, []):
            if color[nxt] == GRAY:
                return stack[stack.index(nxt):] + [nxt]
            if color[nxt] == WHITE:
                found = dfs(nxt)
                if found:
                    return found
        stack.pop()
        color[node] = BLACK
        return None

    for node in nodes:
        if color[node] == WHITE:
            found = dfs(node)
            if found:
                return found
    return None


def _phrases(concept: dict) -> list[str]:
    out = [normalize_ws(str(concept.get("label") or "")).lower()]
    out += [normalize_ws(a).lower() for a in (concept.get("aliases") or []) if isinstance(a, str)]
    return [p for p in out if p]


def _check_concept_registry(ctx: _Concepts, unit_sources: set[str]) -> list[Finding]:
    """Checks intrinsic to concepts.json: ids, labels, enums, DAG, gaps, ordering, merges, merge candidates."""
    f: list[Finding] = []
    data = ctx.data
    if data is None:
        return f
    if data.get("contract_version") != CONTRACT_VERSION:
        f.append(Finding("concepts.contract_version", "warning", CONCEPTS_FILE, f"contract_version={data.get('contract_version')!r}, expected {CONTRACT_VERSION}"))

    seen_ids: Counter = Counter()
    labels: dict[str, list[str]] = defaultdict(list)
    for i, concept in enumerate(data["concepts"]):
        loc = f"{CONCEPTS_FILE} concepts[{i}]"
        if not isinstance(concept, dict):
            f.append(Finding("concepts.shape", "error", loc, "concept must be an object"))
            continue
        cid = concept.get("id")
        if not isinstance(cid, str) or not CONCEPT_ID_RE.match(cid):
            f.append(Finding("concepts.id", "error", loc, f"id {cid!r} does not match c:<slug>"))
            continue
        seen_ids[cid] += 1
        label = normalize_ws(str(concept.get("label") or ""))
        words = word_count(label)
        if not CONCEPT_LABEL_WORDS[0] <= words <= CONCEPT_LABEL_WORDS[1]:
            f.append(Finding("concepts.label", "error", cid, f"label {label!r} has {words} words, expected {CONCEPT_LABEL_WORDS[0]}-{CONCEPT_LABEL_WORDS[1]}"))
        if label:
            labels[label.lower()].append(cid)
        if label.lower() in TOO_BROAD_CONCEPT_LABELS:
            f.append(Finding("concepts.too_broad", "warning", cid, f"label {label!r} is on the too-broad stoplist — name the specific idea"))
        if not (isinstance(concept.get("description"), str) and concept["description"].strip()):
            f.append(Finding("concepts.description", "warning", cid, "description is empty — one sentence saying what the concept is"))
        for key in ("aliases", "depends_on", "sources"):
            value = concept.get(key)
            if value is not None and not (isinstance(value, list) and all(isinstance(v, str) for v in value)):
                f.append(Finding("concepts.shape", "error", cid, f"{key} must be a list of strings"))
        if concept.get("learning_step") not in LEARNING_STEPS:
            f.append(Finding("concepts.enum", "error", cid, f"learning_step={concept.get('learning_step')!r} not in {list(LEARNING_STEPS)}"))
        if concept.get("coverage") not in COVERAGE_LEVELS:
            f.append(Finding("concepts.enum", "error", cid, f"coverage={concept.get('coverage')!r} not in {list(COVERAGE_LEVELS)}"))
        for dep in (concept.get("depends_on") or []) if isinstance(concept.get("depends_on"), list) else []:
            if isinstance(dep, str) and not ctx.known(dep):
                f.append(Finding("concepts.unknown_concept", "error", f"{cid} depends_on", ctx.unknown_message(dep)))
        if unit_sources:
            for src in (concept.get("sources") or []) if isinstance(concept.get("sources"), list) else []:
                if isinstance(src, str) and src not in unit_sources:
                    f.append(Finding("concepts.unknown_source", "warning", cid, f"source {src!r} has no units file"))
    for cid, n in seen_ids.items():
        if n > 1:
            f.append(Finding("concepts.duplicate_id", "error", cid, f"concept id appears {n} times"))
    for label, cids in labels.items():
        if len(cids) > 1:
            f.append(Finding("concepts.duplicate_label", "error", ", ".join(cids), f"label {label!r} is used by {len(cids)} concepts — merge them"))

    # depends_on DAG
    succ: dict[str, list[str]] = defaultdict(list)
    for cid in ctx.order:
        for dep in (ctx.concepts[cid].get("depends_on") or []) if isinstance(ctx.concepts[cid].get("depends_on"), list) else []:
            if isinstance(dep, str) and dep in ctx.concepts:
                succ[dep].append(cid)
    cycle = _find_cycle(succ, ctx.order)
    if cycle:
        f.append(Finding("concepts.depends_on_cycle", "error", " -> ".join(cycle), "depends_on must be acyclic"))

    # merge candidates (label / alias similarity)
    phrases = {cid: _phrases(ctx.concepts[cid]) for cid in ctx.order}
    for i, a in enumerate(ctx.order):
        for b in ctx.order[i + 1:]:
            best = 0.0
            pair = ("", "")
            for pa in phrases[a]:
                for pb in phrases[b]:
                    matcher = SequenceMatcher(None, pa, pb)
                    if matcher.real_quick_ratio() < CONCEPT_MERGE_RATIO or matcher.quick_ratio() < CONCEPT_MERGE_RATIO:
                        continue
                    ratio = matcher.ratio()
                    if ratio > best:
                        best, pair = ratio, (pa, pb)
            if best >= CONCEPT_MERGE_RATIO:
                f.append(Finding("concepts.merge_candidate", "info", f"{a}, {b}", f"{pair[0]!r} ~ {pair[1]!r} (ratio {best:.2f}) — merge or state the distinction in both descriptions"))

    # gaps
    gaps = data.get("gaps")
    if gaps is not None and not isinstance(gaps, list):
        f.append(Finding("concepts.gap", "error", f"{CONCEPTS_FILE} gaps", "gaps must be a list"))
        gaps = []
    for i, gap in enumerate(gaps or []):
        loc = f"{CONCEPTS_FILE} gaps[{i}]"
        if not isinstance(gap, dict):
            f.append(Finding("concepts.gap", "error", loc, "gap must be an object {concept, severity, suggestion, resolution}"))
            continue
        if not (isinstance(gap.get("concept"), str) and gap["concept"].strip()):
            f.append(Finding("concepts.gap", "error", loc, "gap has no concept name"))
        if gap.get("severity") not in GAP_SEVERITIES:
            f.append(Finding("concepts.gap", "error", loc, f"severity={gap.get('severity')!r} not in {list(GAP_SEVERITIES)}"))
        if not (isinstance(gap.get("suggestion"), str) and gap["suggestion"].strip()):
            f.append(Finding("concepts.gap", "error", loc, "gap has no suggestion"))
        resolution = gap.get("resolution")
        resolved = isinstance(resolution, str) and bool(GAP_RESOLUTION_RE.match(resolution))
        if resolution is not None and not resolved:
            f.append(Finding("concepts.gap_resolution", "warning", loc, f"resolution={resolution!r} must match user_accepted | descoped | source_added: <source>"))
        if gap.get("severity") == "critical" and not resolved:
            f.append(Finding("concepts.gap_unresolved", "warning", loc, f"critical gap {gap.get('concept')!r} has no resolution — ask the user (user_accepted / descoped / source_added: ...)"))

    # ordering
    ordering = data.get("ordering")
    if ordering is not None and not isinstance(ordering, list):
        f.append(Finding("concepts.ordering", "error", f"{CONCEPTS_FILE} ordering", "ordering must be a list of concept ids"))
        ordering = []
    ordering = [c for c in (ordering or [])]
    position: dict[str, int] = {}
    for i, cid in enumerate(ordering):
        if not isinstance(cid, str) or not ctx.known(cid):
            f.append(Finding("concepts.ordering", "error", f"{CONCEPTS_FILE} ordering[{i}]", ctx.unknown_message(cid)))
        elif cid in position:
            f.append(Finding("concepts.ordering", "error", f"{CONCEPTS_FILE} ordering[{i}]", f"{cid} is listed twice"))
        else:
            position[cid] = i
    if ordering:
        unordered = [cid for cid in ctx.order if cid not in position]
        if unordered:
            f.append(Finding("concepts.ordering", "warning", f"{CONCEPTS_FILE} ordering", f"{len(unordered)} concept(s) missing from ordering: {_id_list(unordered)}"))
        for cid in ctx.order:
            for dep in (ctx.concepts[cid].get("depends_on") or []) if isinstance(ctx.concepts[cid].get("depends_on"), list) else []:
                if dep in position and cid in position and position[dep] > position[cid]:
                    f.append(Finding("concepts.ordering", "warning", cid, f"ordering places {cid} before its dependency {dep}"))

    # merges
    merges = data.get("merges")
    if merges is not None and not isinstance(merges, list):
        f.append(Finding("concepts.merge", "error", f"{CONCEPTS_FILE} merges", "merges must be a list"))
        merges = []
    for i, merge in enumerate(merges or []):
        loc = f"{CONCEPTS_FILE} merges[{i}]"
        if not isinstance(merge, dict):
            f.append(Finding("concepts.merge", "error", loc, "merge must be an object {kept, merged, reason}"))
            continue
        if not ctx.known(merge.get("kept")):
            f.append(Finding("concepts.merge", "error", loc, f"kept={merge.get('kept')!r} is not a concept"))
        merged = merge.get("merged")
        if not isinstance(merged, list) or not merged or not all(isinstance(m, str) for m in merged):
            f.append(Finding("concepts.merge", "error", loc, "merged must be a non-empty list of concept ids"))
            merged = []
        for mid in merged:
            if ctx.known(mid):
                f.append(Finding("concepts.merge", "warning", loc, f"merged id {mid} is still a concept — remove it from concepts or from merges"))
        if not (isinstance(merge.get("reason"), str) and merge["reason"].strip()):
            f.append(Finding("concepts.merge", "warning", loc, "merge has no reason"))
    return f


def _check_concept_usage(ctx: _Concepts, qual: dict[str, dict]) -> list[Finding]:
    """Thin / too-broad concepts from how kept units tag them."""
    f: list[Finding] = []
    kept_total = 0
    tagged: Counter = Counter()
    for entry in qual.values():
        if entry.get("keep") is not True:
            continue
        kept_total += 1
        cids = {t.get("concept_id") for t in (entry.get("concepts") or []) if isinstance(t, dict)}
        for cid in cids:
            tagged[cid] += 1
    for cid in ctx.order:
        concept = ctx.concepts[cid]
        n = tagged.get(cid, 0)
        if concept.get("coverage") != "gap" and n < CONCEPT_THIN_UNITS:
            f.append(Finding("concepts.thin", "warning", cid, f"{n} kept unit(s) tag this concept (< {CONCEPT_THIN_UNITS}) — merge it, find more units, or mark coverage=gap"))
        if kept_total and n / kept_total > CONCEPT_BROAD_SHARE:
            f.append(Finding("concepts.too_broad", "warning", cid, f"{n}/{kept_total} kept units ({n / kept_total:.0%}) tag this concept (> {CONCEPT_BROAD_SHARE:.0%}) — split it"))
    return f


# ---------------------------------------------------------------------------
# check — duplicate units
# ---------------------------------------------------------------------------


def _shingles(words: list[str]) -> set[tuple[str, ...]]:
    if len(words) < _DEDUP_SHINGLE_WORDS:
        return {tuple(words)} if words else set()
    return {tuple(words[i:i + _DEDUP_SHINGLE_WORDS]) for i in range(len(words) - _DEDUP_SHINGLE_WORDS + 1)}


def dedup_units(units: _Units) -> tuple[list[dict], list[Finding]]:
    """Near-identical prose units across sources and sections -> (dedup entries, findings).

    Candidate pairs must come from different sections, differ by at most
    _DEDUP_TOKEN_DIFF_MAX in tokens and share word shingles; SequenceMatcher on
    the normalized texts then decides: ratio >= UNIT_DEDUP_RATIO makes the later
    unit an ``alternate_of`` the earlier one, [UNIT_DEDUP_BAND, UNIT_DEDUP_RATIO)
    is flagged for the agent.
    """
    rows: list[tuple[str, tuple[str, int], str, int]] = []   # (uid, (source, section), norm text, tokens)
    for source, source_units in units.by_source.items():
        for unit in source_units:
            if unit.get("block_type") != "prose":
                continue
            norm = normalize_ws(unit.get("text") or "").lower()
            if norm:
                rows.append((unit["id"], (source, unit.get("section_index")), norm, unit.get("token_count") or 1))
    rows.sort(key=lambda r: units.order[r[0]])

    postings: dict[tuple[str, ...], list[int]] = defaultdict(list)
    shingle_counts: list[int] = []
    for i, (_, _, norm, _) in enumerate(rows):
        shingles = _shingles(_WORD_RE.findall(norm))
        shingle_counts.append(len(shingles))
        for sh in shingles:
            postings[sh].append(i)
    shared: Counter = Counter()
    for members in postings.values():
        if len(members) > _DEDUP_SHINGLE_MAX_POSTINGS:
            continue
        for a_idx, a in enumerate(members):
            for b in members[a_idx + 1:]:
                if rows[a][1] != rows[b][1]:
                    shared[(a, b)] += 1

    best: dict[str, tuple[str, float]] = {}
    findings: list[Finding] = []
    for (a, b), n in sorted(shared.items()):
        if n < max(2, _DEDUP_SHARED_SHINGLE_SHARE * min(shingle_counts[a], shingle_counts[b])):
            continue
        ta, tb = rows[a][3], rows[b][3]
        if abs(ta - tb) / max(ta, tb, 1) > _DEDUP_TOKEN_DIFF_MAX:
            continue
        ratio = SequenceMatcher(None, rows[a][2], rows[b][2], autojunk=False).ratio()
        if ratio < UNIT_DEDUP_BAND:
            continue
        earlier, later = rows[a][0], rows[b][0]
        if ratio >= UNIT_DEDUP_RATIO:
            if later not in best or ratio > best[later][1]:
                best[later] = (earlier, ratio)
            findings.append(Finding("units.alternate_of", "info", later, f"ratio {ratio:.2f} — alternate of {earlier} (units/dedup.json); drop it with duplicate_of:{earlier} unless it adds something"))
        else:
            findings.append(Finding("units.near_duplicate", "warning", later, f"ratio {ratio:.2f} to {earlier} — decide: duplicate_of or genuinely different"))
    entries = [{"unit_id": uid, "alternate_of": alt, "ratio": round(ratio, 3)} for uid, (alt, ratio) in best.items()]
    entries.sort(key=lambda e: units.order[e["unit_id"]])
    return entries, findings


# ---------------------------------------------------------------------------
# check / check_item / stats
# ---------------------------------------------------------------------------


def check(input_dir: Path, source: str | None = None, **_opts) -> list[Finding]:
    """Validate units/, qualification/ and concepts.json; write units/dedup.json (full run only)."""
    input_dir = Path(input_dir)
    if source is not None:
        return check_item(input_dir, source)
    units = _load_units(input_dir)
    findings = list(units.findings)
    if not units.by_source:
        return findings
    concepts = _load_concepts(input_dir)
    findings.extend(concepts.findings)

    qual: dict[str, dict] = {}
    for src in units.by_source:
        entries, qf = _check_qualification(src, units, concepts, input_dir)
        qual.update(entries)
        findings.extend(qf)
    for path in list_item_files(input_dir / QUALIFICATION_DIR):
        if source_key(path.name) not in units.by_source:
            findings.append(Finding("qualification.unknown_source", "warning", f"{QUALIFICATION_DIR}/{path.name}", "qualification for a source that has no units file"))

    if concepts.loaded:
        findings.extend(_check_concept_registry(concepts, set(units.by_source)))
        findings.extend(_check_concept_usage(concepts, qual))

    entries, dedup_findings = dedup_units(units)
    findings.extend(dedup_findings)
    dump_json(input_dir / DEDUP_FILE, entries)
    return findings


def check_item(input_dir: Path, item_id: str, **_opts) -> list[Finding]:
    """Per-source checks (units file + qualification map) for one source key; no dedup, no registry gates."""
    input_dir = Path(input_dir)
    units = _load_units(input_dir, only_source=item_id)
    findings = list(units.findings)
    if item_id not in units.by_source:
        return findings
    concepts = _load_concepts(input_dir)
    findings.extend(concepts.findings)
    _, qf = _check_qualification(item_id, units, concepts, input_dir)
    findings.extend(qf)
    return findings


def stats(input_dir: Path) -> dict:
    """Units-per-concept and drop statistics (pure read; no findings)."""
    input_dir = Path(input_dir)
    units = _load_units(input_dir)
    concepts = _load_concepts(input_dir)
    per_source: dict[str, dict] = {}
    tagged: Counter = Counter()
    primary: Counter = Counter()
    for source, source_units in units.by_source.items():
        row = {"source": source, "units": len(source_units), "qualified": 0, "kept": 0,
               **{f"drop_{r}": 0 for r in DROP_REASONS}, "drop_other": 0}
        path = input_dir / QUALIFICATION_DIR / f"{source}.json"
        try:
            entries = load_json(path).get("units") or {}
        except (OSError, json.JSONDecodeError, AttributeError):
            entries = {}
        for unit in source_units:
            entry = entries.get(unit["id"]) if isinstance(entries, dict) else None
            if not isinstance(entry, dict):
                continue
            row["qualified"] += 1
            if entry.get("keep") is True:
                row["kept"] += 1
                cids = [t.get("concept_id") for t in (entry.get("concepts") or []) if isinstance(t, dict) and isinstance(t.get("concept_id"), str)]
                for cid in dict.fromkeys(cids):
                    tagged[cid] += 1
                if cids:
                    primary[cids[0]] += 1
            else:
                reason = entry.get("drop_reason")
                m = _DROP_REASON_RE.match(reason) if isinstance(reason, str) else None
                if m is None:
                    row["drop_other"] += 1
                elif m.group("unit") is not None:
                    row["drop_duplicate"] += 1
                else:
                    row[f"drop_{reason}"] += 1
        row["unqualified"] = row["units"] - row["qualified"]
        per_source[source] = row
    concept_rows = [
        {"concept_id": cid, "label": concepts.concepts[cid].get("label"), "kept_units": tagged.get(cid, 0), "primary_units": primary.get(cid, 0)}
        for cid in concepts.order
    ]
    for cid in sorted(set(tagged) - set(concepts.order)):
        concept_rows.append({"concept_id": cid, "label": None, "kept_units": tagged[cid], "primary_units": primary.get(cid, 0)})
    concept_rows.sort(key=lambda r: (-r["kept_units"], r["concept_id"]))
    return {"sources": per_source, "concepts": concept_rows}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def print_findings(findings: list[Finding]) -> tuple[int, int]:
    """Print findings (errors first) and the summary line; returns (errors, warnings)."""
    for fd in sorted(findings, key=lambda x: (_SEVERITY_RANK.get(x.severity, 3), x.location, x.check_id)):
        print(f"{_SEVERITY_TAG.get(fd.severity, '[INFO]')} {fd.check_id} @ {fd.location}: {fd.message}")
    errors = sum(1 for fd in findings if fd.severity == "error")
    warnings = sum(1 for fd in findings if fd.severity == "warning")
    print(f"{errors} errors, {warnings} warnings")
    return errors, warnings


def _print_table(title: str, rows: list[dict]) -> None:
    from rich.console import Console
    from rich.table import Table

    console = Console()
    if not rows:
        console.print(f"{title}: nothing to show")
        return
    table = Table(title=title)
    for key in rows[0]:
        table.add_column(key)
    for row in rows:
        table.add_row(*(str(v) for v in row.values()))
    console.print(table)


def _cmd_prepare(args: argparse.Namespace) -> int:
    if not args.input_dir.is_dir():
        print(f"error: {args.input_dir} is not a directory", file=sys.stderr)
        return 2
    findings = prepare(args.input_dir, scope_path=args.scope, sources=args.source)
    errors, _ = print_findings(findings)
    return 1 if errors else 0


def _cmd_scope(args: argparse.Namespace) -> int:
    scope, findings = scope_from_structure(args.structure, args.area, args.extracted, args.output)
    errors, _ = print_findings(findings)
    if scope is not None:
        n_sections = sum(len(v) for v in scope["include_sections"].values())
        print(f"wrote {args.output}: {len(scope['include_sources'])} sources, {n_sections} sections")
    return 1 if errors else 0


def _cmd_check(args: argparse.Namespace) -> int:
    if not args.input_dir.is_dir():
        print(f"error: {args.input_dir} is not a directory", file=sys.stderr)
        return 2
    findings = check(args.input_dir, source=args.source)
    errors, warnings = print_findings(findings)
    if args.source is None:
        summary = stats(args.input_dir)
        _print_table("Units and drops per source", list(summary["sources"].values()))
        _print_table("Kept units per concept", summary["concepts"])
    return 1 if errors or (args.strict and warnings) else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Lesson pipeline stage 2: knowledge units (prepare / scope-from-structure / check)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_prep = sub.add_parser("prepare", help=f"clean + segment extracted/*.json into {UNITS_DIR}/<source>.json")
    p_prep.add_argument("input_dir", type=Path, help="output/<name>/ directory")
    p_prep.add_argument("--scope", type=Path, metavar="scope.json", help=f"scope file (default: {SCOPE_FILE} in input_dir when present)")
    p_prep.add_argument("--source", action="append", metavar="KEY", help="only this source key (repeatable); other index entries are kept")
    p_prep.set_defaults(func=_cmd_prepare)

    p_scope = sub.add_parser("scope-from-structure", help="build scope.json from a legacy structure's areas")
    p_scope.add_argument("structure", type=Path, help="legacy structure.json")
    p_scope.add_argument("--area", action="append", required=True, metavar="TITLE", help="depth-0 topic title (repeatable)")
    p_scope.add_argument("--extracted", type=Path, required=True, metavar="DIR", help="extracted/ directory")
    p_scope.add_argument("-o", "--output", type=Path, required=True, metavar="scope.json", help="where to write the scope")
    p_scope.set_defaults(func=_cmd_scope)

    p_check = sub.add_parser("check", help="gate units/, qualification/ and concepts.json; write units/dedup.json")
    p_check.add_argument("input_dir", type=Path, help="output/<name>/ directory")
    p_check.add_argument("--source", metavar="KEY", help="check one source only (no dedup, no registry gates)")
    p_check.add_argument("--strict", action="store_true", help="warnings fail too")
    p_check.set_defaults(func=_cmd_check)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
