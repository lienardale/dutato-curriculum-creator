"""
Stage 5 of the lesson pipeline — DESIGN LESSONS (lessons/).

PURE DATA UTILITY — no AI calls. The operating agent (or its sub-agents) writes
one lesson per group in ``lessons/<item>.json`` (shape: docs/artifacts.md
"Stage 5"), first as an outline (every page ``markdown: null``), then with full
pages. This script prepares the work file the author reads, gates the result
deterministically, and assembles ``structure.json`` + ``chunks.json`` for the
uploader. Every threshold, enum and id format comes from shared/contracts.py.

Subcommands:

  prepare  Writes ``lessons/work/<item>.json`` per group: the group entry
           (title, generalization, knowledge points, objectives, watch, budget,
           prerequisites with status), the brief (overview, items, examples
           with the referenced unit text, terminology), concept labels and
           aliases, neighbours (prerequisite lessons with their recap page,
           the next two titles), the images the pages may place
           (``![alt](image:<id>)``), a page template derived from
           ``budget.pages``, the anchor exemplar when ``lessons/anchor.json``
           names one, the outline file for the full pass, lessons_learned, the
           rubric (rubrics/lesson_design.md) under ``instructions`` and the
           metric thresholds.
  check    Gates ``lessons/<item>.json``. ``--outline`` validates the skeleton
           only (roles, order, page count, objectives, knowledge points, brief
           items, units, titles, prerequisites). The full check adds the page
           metrics (words, bold terms, term density, undefined terms,
           sentences, paragraphs, example markers, verbatim copy, page
           furniture, dangling references, page starts, images, pitfalls and
           recap shape, estimated minutes) and writes ``lesson_metrics.json``.
  build    Requires full pages and (unless ``--allow-failing``) an error-free
           check; writes ``structure.json`` (areas > modules > lessons) and
           ``chunks.json`` (one chunk per page) and prints a summary table.

Usage:
    python lessons.py prepare output/<name>/ [--lesson g:ID ...] [--force] [--pass outline|full]
    python lessons.py check   output/<name>/ [--lesson g:ID] [--outline] [--strict]
    python lessons.py build   output/<name>/ [--allow-failing]

Module API (used by validate.py / critique.py):
    check(input_dir, lesson_id=None, outline=False) -> list[Finding]
    check_item(input_dir, item_id, outline=False)   -> list[Finding]
    prepare(input_dir, lesson_ids=None, force=False, pass_=None) -> list[Finding]
    build(input_dir, allow_failing=False)           -> (result | None, list[Finding])
    main(argv) -> 0 clean, 1 errors (or warnings with --strict), 2 usage

Findings print as "[ERROR] <check_id> @ <location>: <message>" ([WARN]/[INFO])
followed by "<n> errors, <m> warnings".

Knowledge points and page roles: a core page carries exactly one knowledge
point; a worked_example or pitfalls page may carry one (the budget gives
``knowledge_points + 2`` pages, so with three fixed roles not every knowledge
point fits on a core page); orientation and recap carry none; every knowledge
point sits on exactly one page.

Term density: a registry term (concept label or alias, brief terminology,
lesson key_terms) is "introduced" on the first page that bolds it; un-bolded
mentions never introduce a term (see _introductions). A core page's
density is the token mass of all mentions of the terms it introduces divided
by the page's prose tokens.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import _compat  # noqa: F401

from chunk import count_tokens
from contracts import (
    BLOOM_LEVELS,
    CONCEPTS_FILE,
    CONTRACT_VERSION,
    DANGLING_REFERENCE_PATTERNS,
    DEFINITION_WINDOW_CHARS,
    EXAMPLE_REQUIRED_OVER_WORDS,
    GROUP_ID_RE,
    GROUPS_FILE,
    IMAGE_PLACEHOLDER_RE,
    LESSON_METRICS_FILE,
    LESSON_WORDS,
    LESSONS_DIR,
    NEW_TERMS_PER_PAGE_MAX,
    PAGE_FURNITURE_PATTERNS,
    PAGE_ROLE_ORDER,
    PAGE_ROLES,
    PAGE_WORDS,
    PAGE_WORDS_TOLERANCE,
    PARAGRAPH_MAX_SENTENCES,
    PITFALLS_RANGE,
    QUALIFICATION_DIR,
    RECAP_TAKEAWAYS,
    SENTENCE_FLAG_WORDS,
    SENTENCE_MEAN_WORDS_MAX,
    SYNTHESES_DIR,
    TERM_DENSITY_RANGE,
    UNDEFINED_TERM_RATIO_MAX,
    UNITS_DIR,
    VERBATIM_DENSITY_MAX,
    VERBATIM_FRAGMENT_MAX,
    VERBATIM_JACCARD5_MAX,
    WORK_SUBDIR,
    Finding,
    budget_for,
    dump_json,
    filename_to_item_id,
    item_id_to_filename,
    list_item_files,
    load_json,
    normalize_ws,
    parse_unit_id,
    word_count,
)
from verbatim import GATE_SEVERITY, gate, verbatim_report

STAGE = "lessons"
RUBRIC_PATH = Path(__file__).resolve().parent / "rubrics" / "lesson_design.md"

# lessons/anchor.json -> {"lesson_id": "g:..."} names the human-approved exemplar lesson.
ANCHOR_FILE = "anchor.json"
NEIGHBOURS_FOLLOWING = 2
PASSES = ("outline", "full")

# Silent reading speed behind estimated_minutes, and the tolerated deviation.
READING_WORDS_PER_MINUTE = 238
ESTIMATED_MINUTES_TOLERANCE = 0.50

# Files in units/ that are not per-source unit files.
UNITS_NON_SOURCE_FILES = {"index.json", "dedup.json"}
# Units that justify a worked_example page (code is embedded, never paraphrased).
EXAMPLE_UNIT_TYPES = {"code", "example"}
EXAMPLE_BLOCK_TYPES = {"code"}

# Fixed-role pages every lesson has; the remaining budget goes to core / worked_example.
FIXED_ROLES = ("orientation", "pitfalls", "recap")
# Roles that may carry a knowledge point without being core pages.
KP_OPTIONAL_ROLES = ("worked_example", "pitfalls")

EXAMPLE_MARKER_RE = re.compile(
    r"(?<!\w)(?:for example|for instance|consider|suppose|imagine|in practice|e\.g\.|say you|picture)(?!\w)",
    re.IGNORECASE,
)
_DEFINER_RE = re.compile(r"\b(?:is|are|means|refers to)\b|—|–|:")
_BOLD_RE = re.compile(r"\*\*([^*\n]+?)\*\*")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s")
_SUBHEADING_RE = re.compile(r"^\s{0,3}#{3,6}\s")
_LIST_ITEM_RE = re.compile(r"^\s{0,3}(?:[-*+]|\d+[.)])\s+")
_TOP_LIST_ITEM_RE = re.compile(r"^(?:[-*+]|\d+[.)])\s+")
_TABLE_ROW_RE = re.compile(r"^\s*\|")
_BLOCKQUOTE_RE = re.compile(r"^\s{0,3}>\s?")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_QUOTED_SPAN_RE = re.compile(r'["“]([^"“”]{3,}?)["”]')
_MARKDOWN_MARKS_RE = re.compile(r"[*_`]")
_LEADING_MARKS = " \t\r\n*_>-"
_TRAILING_CLOSERS = ')"”\'’*_]'
_FIGURE_PATTERN = next(p for p in DANGLING_REFERENCE_PATTERNS if "Figure" in p.pattern)

LESSON_REQUIRED_KEYS = ("lesson_id", "group_id", "title", "objectives", "pages")
PAGE_REQUIRED_KEYS = ("index", "role", "title", "lo_ids", "knowledge_point_id", "unit_ids", "brief_item_ids", "markdown")

_UNORDERED = 10**6  # sort position for entries without a usable sort_order


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


@dataclass
class Context:
    """Everything prepare/check/build need from output/<name>/, loaded once."""

    input_dir: Path
    areas: list[dict] = field(default_factory=list)
    groups: dict[str, dict] = field(default_factory=dict)
    group_order: list[str] = field(default_factory=list)   # curriculum order
    reduced_edges: list[dict] | None = None                # groups.json prerequisite_edges_reduced
    critiqued: bool = False                                # any prerequisite carries a critique status
    units: dict[str, dict] = field(default_factory=dict)   # unit id -> unit (+ "source")
    qualification: dict[str, dict] = field(default_factory=dict)
    concepts: dict[str, dict] = field(default_factory=dict)
    briefs: dict[str, dict | None] = field(default_factory=dict)   # lazy cache
    lessons: dict[str, dict | None] = field(default_factory=dict)  # lazy cache
    findings: list[Finding] = field(default_factory=list)

    @property
    def fatal(self) -> bool:
        return any(f.severity == "error" for f in self.findings)

    # -- per-group derived data -------------------------------------------------

    def budget(self, group: dict) -> dict:
        explicit = group.get("budget")
        if isinstance(explicit, dict) and explicit:
            return explicit
        return budget_for(
            len(group.get("knowledge_points") or []),
            len(group.get("objectives") or []),
            group.get("learning_step") or "building_block",
            bool(self.committed_prerequisites(group)),
        )

    def committed_prerequisites(self, group: dict) -> list[dict]:
        """Prerequisite entries the pipeline treats as committed: status ``committed``
        once any edge has been critiqued, every ``proposed`` edge before that
        (same provisional rule as regroup.py / questions.py)."""
        out = []
        for p in group.get("prerequisites") or []:
            if not isinstance(p, dict) or not isinstance(p.get("group_id"), str):
                continue
            status = p.get("status")
            accepted = status == "committed" if self.critiqued else status in (None, "proposed")
            if accepted and p["group_id"] in self.groups and p["group_id"] != group.get("id"):
                out.append(p)
        return out

    def build_prerequisites(self, group: dict) -> list[dict]:
        """Committed edges restricted to the transitive reduction when regroup.py wrote one."""
        prereqs = self.committed_prerequisites(group)
        if self.reduced_edges is None:
            return prereqs
        kept = {(e.get("from"), e.get("to")) for e in self.reduced_edges if isinstance(e, dict)}
        return [p for p in prereqs if (p["group_id"], group.get("id")) in kept]

    def group_units(self, group: dict) -> tuple[list[str], list[str]]:
        primary = [u for u in (group.get("unit_ids") or []) if isinstance(u, str)]
        related = [u for u in (group.get("related_unit_ids") or []) if isinstance(u, str) and u not in primary]
        return primary, related

    def group_images(self, group: dict) -> dict[str, dict]:
        """image id -> {id, unit_id, local_path, alt_text, mime_type} over the group's units."""
        out: dict[str, dict] = {}
        primary, related = self.group_units(group)
        for uid in primary + related:
            for img in (self.units.get(uid) or {}).get("images") or []:
                if isinstance(img, dict) and img.get("id") and img["id"] not in out:
                    out[img["id"]] = {
                        "id": img["id"], "unit_id": uid, "local_path": img.get("local_path"),
                        "alt_text": img.get("alt_text") or "", "mime_type": img.get("mime_type"),
                    }
        return out

    def is_example_unit(self, uid: str) -> bool:
        unit = self.units.get(uid) or {}
        entry = self.qualification.get(uid) or {}
        return unit.get("block_type") in EXAMPLE_BLOCK_TYPES or entry.get("unit_type") in EXAMPLE_UNIT_TYPES

    def concept_terms(self, group: dict) -> list[str]:
        terms: list[str] = []
        for cid in group.get("concept_ids") or []:
            c = self.concepts.get(cid) or {}
            if c.get("label"):
                terms.append(c["label"])
            terms.extend(a for a in (c.get("aliases") or []) if isinstance(a, str))
        return terms

    # -- lazy per-item files ------------------------------------------------------

    def brief(self, gid: str) -> dict | None:
        if gid not in self.briefs:
            path = self.input_dir / SYNTHESES_DIR / item_id_to_filename(gid)
            data = _read(path, f"{SYNTHESES_DIR}/{path.name}", self.findings, f"{STAGE}.brief_invalid") if path.exists() else None
            self.briefs[gid] = data if isinstance(data, dict) else None
        return self.briefs[gid]

    def lesson(self, gid: str) -> dict | None:
        if gid not in self.lessons:
            path = self.input_dir / LESSONS_DIR / item_id_to_filename(gid)
            data = _read(path, f"{LESSONS_DIR}/{path.name}", self.findings, f"{STAGE}.invalid_json") if path.exists() else None
            self.lessons[gid] = data if isinstance(data, dict) else None
        return self.lessons[gid]

    def lesson_files(self) -> list[Path]:
        """Authored lesson files: work/ excluded, and only names that map back to a
        ``g:<slug>`` id (so the anchor pointer ``lessons/anchor.json`` is not a lesson —
        every consumer of lessons/ applies the same rule)."""
        return [p for p in list_item_files(self.input_dir / LESSONS_DIR)
                if p.name != ANCHOR_FILE and GROUP_ID_RE.match(filename_to_item_id(p.name))]


def _read(path: Path, location: str, findings: list[Finding], check_id: str):
    """Load JSON or append a finding; returns None on failure."""
    try:
        return load_json(path)
    except FileNotFoundError:
        findings.append(Finding(check_id, "error", location, f"{path.name} not found"))
    except json.JSONDecodeError as e:
        findings.append(Finding(check_id, "error", location, f"invalid JSON: {e}"))
    except OSError as e:
        findings.append(Finding(check_id, "error", location, str(e)))
    return None


def _order(entry: dict, default: int = _UNORDERED) -> int:
    value = entry.get("sort_order")
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _group_sort_key(groups: dict[str, dict], areas: list[dict]) -> Callable[[str], tuple]:
    """Curriculum order: area sort_order, module sort_order, group sort_order, then id."""
    area_order = {a.get("id"): _order(a, i) for i, a in enumerate(areas)}
    module_order = {
        m.get("id"): (_order(a, i), _order(m, j))
        for i, a in enumerate(areas)
        for j, m in enumerate(a.get("modules") or [])
    }

    def key(gid: str) -> tuple:
        g = groups[gid]
        return (
            area_order.get(g.get("area_id"), _UNORDERED),
            module_order.get(g.get("module_id"), (_UNORDERED, _UNORDERED)),
            _order(g),
            gid,
        )

    return key


def _unit_sort_key(uid: str) -> tuple:
    parsed = parse_unit_id(uid)
    return (parsed[0], parsed[1], parsed[2]) if parsed else ("￿", 0, 0, uid)


def load_context(input_dir: Path) -> Context:
    """Load groups, units, qualification and concepts. Missing groups/units are
    errors (nothing can be prepared or checked); the rest degrades with warnings."""
    input_dir = Path(input_dir)
    ctx = Context(input_dir=input_dir)

    data = _read(input_dir / GROUPS_FILE, GROUPS_FILE, ctx.findings, f"{STAGE}.groups_missing")
    if isinstance(data, dict):
        ctx.areas = [a for a in (data.get("areas") or []) if isinstance(a, dict)]
        for i, g in enumerate(data.get("groups") or []):
            gid = g.get("id") if isinstance(g, dict) else None
            if not gid:
                ctx.findings.append(Finding(f"{STAGE}.groups_invalid", "error", f"{GROUPS_FILE} groups[{i}]", "group has no id"))
                continue
            ctx.groups[gid] = g
        ctx.group_order = sorted(ctx.groups, key=_group_sort_key(ctx.groups, ctx.areas))
        reduced = data.get("prerequisite_edges_reduced")
        ctx.reduced_edges = reduced if isinstance(reduced, list) else None
        ctx.critiqued = any(
            isinstance(p, dict) and p.get("status") in ("committed", "uncertain", "rejected")
            for g in ctx.groups.values() for p in (g.get("prerequisites") or [])
        )
    elif data is not None:
        ctx.findings.append(Finding(f"{STAGE}.groups_invalid", "error", GROUPS_FILE, "expected an object with a 'groups' list"))

    units_dir = input_dir / UNITS_DIR
    if not units_dir.is_dir():
        ctx.findings.append(Finding(f"{STAGE}.units_missing", "error", f"{UNITS_DIR}/", "units directory not found — run `qualify.py prepare` first"))
    else:
        for path in sorted(units_dir.glob("*.json")):
            if path.name in UNITS_NON_SOURCE_FILES:
                continue
            udata = _read(path, f"{UNITS_DIR}/{path.name}", ctx.findings, f"{STAGE}.units_invalid")
            if not isinstance(udata, dict) or not isinstance(udata.get("units"), list):
                if udata is not None:
                    ctx.findings.append(Finding(f"{STAGE}.units_invalid", "warning", f"{UNITS_DIR}/{path.name}", "not a units file (no 'units' list) — skipped"))
                continue
            source = udata.get("source") or path.stem
            for unit in udata["units"]:
                if isinstance(unit, dict) and unit.get("id"):
                    ctx.units[unit["id"]] = {**unit, "source": source}

    qual_dir = input_dir / QUALIFICATION_DIR
    if qual_dir.is_dir():
        for path in sorted(qual_dir.glob("*.json")):
            qdata = _read(path, f"{QUALIFICATION_DIR}/{path.name}", ctx.findings, f"{STAGE}.qualification_invalid")
            if isinstance(qdata, dict) and isinstance(qdata.get("units"), dict):
                ctx.qualification.update(qdata["units"])

    concepts_path = input_dir / CONCEPTS_FILE
    if concepts_path.exists():
        cdata = _read(concepts_path, CONCEPTS_FILE, ctx.findings, f"{STAGE}.concepts_invalid")
        if isinstance(cdata, dict):
            for c in cdata.get("concepts") or []:
                if isinstance(c, dict) and c.get("id"):
                    ctx.concepts[c["id"]] = c
    else:
        ctx.findings.append(Finding(f"{STAGE}.concepts_missing", "warning", CONCEPTS_FILE, "concepts.json not found — concept labels cannot be whitelisted or counted as terms"))

    return ctx


def _select(ctx: Context, lesson_ids: list[str] | None) -> tuple[list[str], list[Finding]]:
    if not lesson_ids:
        return list(ctx.group_order), []
    findings = [
        Finding(f"{STAGE}.unknown_lesson", "error", GROUPS_FILE, f"lesson {lid!r} is not a group in groups.json")
        for lid in lesson_ids if lid not in ctx.groups
    ]
    wanted = set(lesson_ids)
    return [gid for gid in ctx.group_order if gid in wanted], findings


# ---------------------------------------------------------------------------
# Markdown analysis helpers
# ---------------------------------------------------------------------------


def _prose(markdown: str) -> str:
    """What a learner reads as text: fenced code blocks and image placeholders removed."""
    parts = (markdown or "").split("```")
    text = "\n".join(parts[0::2])  # odd-indexed parts are inside fences
    return IMAGE_PLACEHOLDER_RE.sub(" ", text)


def _norm_text(text: str) -> str:
    """Lowercase, markdown marks removed, whitespace collapsed, curly quotes straightened."""
    text = _MARKDOWN_MARKS_RE.sub("", text or "").replace("’", "'").replace("‘", "'")
    return normalize_ws(text).lower()


def _norm_term(text: str) -> str:
    return _norm_text(text).strip(" .,:;!?")


def _blocks(prose: str) -> list[str]:
    return [b.strip() for b in re.split(r"\n\s*\n", prose or "") if b.strip()]


def _block_kind(block: str) -> str:
    """heading | table | list | text."""
    lines = [l for l in block.splitlines() if l.strip()]
    if not lines:
        return "text"
    if _HEADING_RE.match(lines[0]) and len(lines) == 1:
        return "heading"
    if all(_TABLE_ROW_RE.match(l) for l in lines):
        return "table"
    if all(_LIST_ITEM_RE.match(l) or _BLOCKQUOTE_RE.match(l) or l.startswith((" ", "\t")) for l in lines):
        return "list"
    return "text"


def _sentences(text: str) -> list[str]:
    return [s for s in _SENTENCE_SPLIT_RE.split(normalize_ws(text)) if word_count(s)]


def _sentence_stats(prose: str) -> tuple[list[int], int]:
    """(words per sentence over the prose, max sentences in one plain paragraph)."""
    lengths: list[int] = []
    max_paragraph = 0
    for block in _blocks(prose):
        kind = _block_kind(block)
        if kind in ("heading", "table"):
            continue
        if kind == "list":
            for line in block.splitlines():
                item = _BLOCKQUOTE_RE.sub("", _LIST_ITEM_RE.sub("", line))
                lengths.extend(word_count(s) for s in _sentences(item))
            continue
        body = "\n".join(l for l in block.splitlines() if not _HEADING_RE.match(l))
        sents = _sentences(body)
        lengths.extend(word_count(s) for s in sents)
        max_paragraph = max(max_paragraph, len(sents))
    return lengths, max_paragraph


def _bold_terms(prose: str) -> list[tuple[str, int]]:
    """(normalized bold term, offset just after its closing **) for every bold span."""
    out = []
    for m in _BOLD_RE.finditer(prose):
        term = _norm_term(m.group(1))
        if term:
            out.append((term, m.end()))
    return out


def _is_defined(prose: str, end: int) -> bool:
    """A definer (is/are/means/refers to/—/:) follows the bold term within the window."""
    return bool(_DEFINER_RE.search(prose[end:end + DEFINITION_WINDOW_CHARS]))


def _term_pattern(term: str) -> re.Pattern:
    return re.compile(r"(?<![\w-])" + re.escape(term) + r"(?:e?s)?(?![\w-])")


def _term_occurrences(prose_norm: str, terms: list[str]) -> Counter:
    """Non-overlapping mentions per term: longest terms are matched first and masked
    so an alias that contains the label is not counted twice."""
    counts: Counter = Counter()
    text = prose_norm
    for term in sorted(set(terms), key=len, reverse=True):
        text, n = _term_pattern(term).subn(lambda m: " " * len(m.group(0)), text)
        if n:
            counts[term] = n
    return counts


def _count_items(prose: str) -> int:
    """Pitfalls/recap items: ### sub-headings, else top-level list items, else bold-led paragraphs."""
    lines = prose.splitlines()
    headings = sum(1 for l in lines if _SUBHEADING_RE.match(l))
    if headings:
        return headings
    bullets = sum(1 for l in lines if _TOP_LIST_ITEM_RE.match(l))
    if bullets:
        return bullets
    return sum(1 for b in _blocks(prose) if b.startswith("**"))


def _list_items(prose: str) -> int:
    return sum(1 for l in prose.splitlines() if _TOP_LIST_ITEM_RE.match(l))


def _starts_cleanly(markdown: str) -> bool:
    """First character (after list/emphasis marks) is a heading, an uppercase letter,
    an image placeholder or a code fence."""
    text = (markdown or "").lstrip(_LEADING_MARKS)
    if not text:
        return False
    return text[0] in "#!`" or text[0].isupper()


def _ends_cleanly(markdown: str) -> bool:
    """Ends with terminal punctuation (closers allowed), a code fence, a table row or an image."""
    text = (markdown or "").rstrip()
    if not text:
        return False
    last_line = text.splitlines()[-1].strip()
    if last_line.startswith("```") or last_line.startswith("|") or IMAGE_PLACEHOLDER_RE.fullmatch(last_line):
        return True
    return text.rstrip(_TRAILING_CLOSERS)[-1:] in (".", "!", "?", ":")


def _words_deviation(words: int, role: str) -> float:
    """Relative distance outside the role's word band (0 when inside)."""
    lo, hi = PAGE_WORDS[role]
    if words < lo:
        return (lo - words) / lo
    if words > hi:
        return (words - hi) / hi
    return 0.0


# ---------------------------------------------------------------------------
# prepare
# ---------------------------------------------------------------------------


def _rubric_text(findings: list[Finding]) -> str:
    try:
        return RUBRIC_PATH.read_text(encoding="utf-8")
    except OSError:
        findings.append(Finding(f"{STAGE}.prepare.rubric_missing", "warning", str(RUBRIC_PATH), "rubric file not found — work files carry empty instructions"))
        return ""


def _thresholds() -> dict:
    return {
        "lesson_words": list(LESSON_WORDS),
        "page_words": {role: list(band) for role, band in PAGE_WORDS.items()},
        "page_words_tolerance": PAGE_WORDS_TOLERANCE,
        "new_terms_per_page_max": NEW_TERMS_PER_PAGE_MAX,
        "term_density_range": list(TERM_DENSITY_RANGE),
        "undefined_term_ratio_max": UNDEFINED_TERM_RATIO_MAX,
        "definition_window_chars": DEFINITION_WINDOW_CHARS,
        "sentence_mean_words_max": SENTENCE_MEAN_WORDS_MAX,
        "sentence_flag_words": SENTENCE_FLAG_WORDS,
        "paragraph_max_sentences": PARAGRAPH_MAX_SENTENCES,
        "example_required_over_words": EXAMPLE_REQUIRED_OVER_WORDS,
        "example_markers": ["for example", "for instance", "consider", "suppose", "imagine", "in practice", "e.g.", "say you", "picture", "a fenced code block"],
        "pitfalls_range": list(PITFALLS_RANGE),
        "recap_takeaways": list(RECAP_TAKEAWAYS),
        "verbatim": {"fragment_max_words": VERBATIM_FRAGMENT_MAX, "jaccard5_max": VERBATIM_JACCARD5_MAX["page"], "density_max": VERBATIM_DENSITY_MAX["page"]},
        "reading_words_per_minute": READING_WORDS_PER_MINUTE,
    }


def page_template(budget_pages: int, knowledge_point_ids: list[str], has_example: bool) -> tuple[list[dict], list[str]]:
    """Role sequence for ``budget_pages`` pages with knowledge points assigned.

    orientation, core x k, worked_example (when an example unit exists and a
    slot is free), pitfalls, recap. Knowledge points go to core pages first,
    then to the worked_example and pitfalls pages. Returns (pages, unassigned
    knowledge point ids).
    """
    slots = max(0, budget_pages - len(FIXED_ROLES))
    worked = 1 if has_example and slots >= 2 else 0
    roles = ["orientation"] + ["core"] * (slots - worked) + ["worked_example"] * worked + ["pitfalls", "recap"]
    remaining = list(knowledge_point_ids)
    pages = []
    for index, role in enumerate(roles):
        kp = remaining.pop(0) if remaining and role in ("core", *KP_OPTIONAL_ROLES) else None
        pages.append({"index": index, "role": role, "knowledge_point_id": kp, "words": list(PAGE_WORDS[role])})
    return pages, remaining


def _neighbours(ctx: Context, gid: str, findings: list[Finding]) -> dict:
    group = ctx.groups[gid]
    prereqs = []
    for p in ctx.committed_prerequisites(group):
        pid = p["group_id"]
        lesson = ctx.lesson(pid)
        recap = None
        if lesson is not None:
            recap = next((pg.get("markdown") for pg in lesson.get("pages") or [] if isinstance(pg, dict) and pg.get("role") == "recap"), None)
        else:
            findings.append(Finding(f"{STAGE}.prepare.prerequisite_unwritten", "info", f"{GROUPS_FILE} {gid}", f"prerequisite lesson {pid} has no lessons/ file yet — no recap to hand the author"))
        prereqs.append({
            "lesson_id": pid,
            "title": (lesson or {}).get("title") or ctx.groups[pid].get("title", pid),
            "strength": p.get("strength"), "status": p.get("status") or "proposed",
            "recap_markdown": recap,
        })
    pos = ctx.group_order.index(gid)
    following = [ctx.groups[g].get("title", g) for g in ctx.group_order[pos + 1:pos + 1 + NEIGHBOURS_FOLLOWING]]
    return {"prerequisites": prereqs, "next": following}


def _anchor(ctx: Context, gid: str, findings: list[Finding]) -> dict | None:
    path = ctx.input_dir / LESSONS_DIR / ANCHOR_FILE
    if not path.exists():
        return None
    data = _read(path, f"{LESSONS_DIR}/{ANCHOR_FILE}", findings, f"{STAGE}.prepare.anchor_invalid")
    anchor_id = data.get("lesson_id") if isinstance(data, dict) else None
    if not isinstance(anchor_id, str) or anchor_id not in ctx.groups:
        if data is not None:
            findings.append(Finding(f"{STAGE}.prepare.anchor_invalid", "warning", f"{LESSONS_DIR}/{ANCHOR_FILE}", f"lesson_id {anchor_id!r} is not a group — anchor ignored"))
        return None
    if anchor_id == gid:
        return None
    lesson = ctx.lesson(anchor_id)
    if lesson is None:
        findings.append(Finding(f"{STAGE}.prepare.anchor_missing", "warning", f"{LESSONS_DIR}/{ANCHOR_FILE}", f"anchor lesson {anchor_id} has no lessons/ file"))
        return None
    return lesson


def _brief_view(ctx: Context, gid: str, findings: list[Finding]) -> dict | None:
    brief = ctx.brief(gid)
    if brief is None:
        findings.append(Finding(f"{STAGE}.prepare.brief_missing", "warning", f"{SYNTHESES_DIR}/{item_id_to_filename(gid)}", "no brief for this group — pages have nothing to cite; run synthesize.py first"))
        return None
    examples = []
    for ex in brief.get("examples") or []:
        if not isinstance(ex, dict):
            continue
        unit = ctx.units.get(ex.get("unit_id")) or {}
        examples.append({**ex, "text": unit.get("text", ""), "block_type": unit.get("block_type"), "images": unit.get("images") or []})
    return {
        "overview": brief.get("overview"),
        "items": brief.get("items") or [],
        "examples": examples,
        "terminology": brief.get("terminology") or [],
    }


def build_work_file(ctx: Context, gid: str, pass_: str, rubric: str, findings: list[Finding]) -> dict:
    """Assemble the author's work file for one group (pure function of the context)."""
    group = ctx.groups[gid]
    loc = f"{GROUPS_FILE} {gid}"
    budget = ctx.budget(group)
    primary, related = ctx.group_units(group)
    brief = _brief_view(ctx, gid, findings)
    has_example = any(ctx.is_example_unit(u) for u in primary + related) or bool((brief or {}).get("examples"))
    kp_ids = [k.get("id") for k in (group.get("knowledge_points") or []) if isinstance(k, dict) and k.get("id")]
    template, unassigned = page_template(int(budget.get("pages") or 0), kp_ids, has_example)
    if unassigned:
        findings.append(Finding(f"{STAGE}.prepare.kp_overflow", "warning", loc,
                                f"budget.pages={budget.get('pages')} leaves {len(unassigned)} knowledge point(s) without a page: {', '.join(unassigned)}"))
    kp_titles = {k.get("id"): k.get("title") for k in (group.get("knowledge_points") or []) if isinstance(k, dict)}
    for page in template:
        page["title_hint"] = kp_titles.get(page["knowledge_point_id"]) or page["role"].replace("_", " ")

    concepts = []
    for cid in group.get("concept_ids") or []:
        c = ctx.concepts.get(cid)
        if c is None:
            findings.append(Finding(f"{STAGE}.prepare.concept_missing", "warning", loc, f"concept {cid} is not in concepts.json"))
            continue
        concepts.append({"id": cid, "label": c.get("label"), "aliases": c.get("aliases") or [], "description": c.get("description")})

    images = []
    for img in ctx.group_images(group).values():
        images.append({**img, "placeholder": f"![{img['alt_text']}](image:{img['id']})"})

    existing = ctx.lesson(gid)
    if pass_ == "full" and existing is None:
        findings.append(Finding(f"{STAGE}.prepare.outline_missing", "warning", f"{LESSONS_DIR}/{item_id_to_filename(gid)}", "full pass requested but no outline exists — the author must write the outline first"))

    return {
        "contract_version": CONTRACT_VERSION,
        "stage": STAGE,
        "lesson_id": gid,
        "group_id": gid,
        "pass": pass_,
        "file": f"{LESSONS_DIR}/{item_id_to_filename(gid)}",
        "group": {
            "id": gid, "title": group.get("title"), "area_id": group.get("area_id"), "module_id": group.get("module_id"),
            "generalization": group.get("generalization"),
            "knowledge_points": group.get("knowledge_points") or [],
            "objectives": group.get("objectives") or [],
            "watch": group.get("watch") or [],
            "budget": budget, "suggested_level": group.get("suggested_level"), "learning_step": group.get("learning_step"),
            "prerequisites": [
                {"group_id": p.get("group_id"), "strength": p.get("strength"), "status": p.get("status") or "proposed", "reason": p.get("reason")}
                for p in (group.get("prerequisites") or []) if isinstance(p, dict)
            ],
            "unit_ids": primary, "related_unit_ids": related,
        },
        "brief": brief,
        "concepts": concepts,
        "neighbours": _neighbours(ctx, gid, findings),
        "images": images,
        "page_template": template,
        "unassigned_knowledge_points": unassigned,
        "anchor": _anchor(ctx, gid, findings),
        "outline": existing if pass_ == "full" else None,
        "lessons_learned": (existing or {}).get("lessons_learned") or [],
        "instructions": rubric,
        "thresholds": _thresholds(),
    }


def prepare(input_dir: Path, lesson_ids: list[str] | None = None, force: bool = False, pass_: str | None = None) -> list[Finding]:
    """Write lessons/work/<item>.json for the selected groups. ``pass_`` None picks
    ``full`` when the lesson file exists and ``outline`` otherwise."""
    input_dir = Path(input_dir)
    ctx = load_context(input_dir)
    findings = list(ctx.findings)
    if ctx.fatal:
        return findings
    selected, select_findings = _select(ctx, lesson_ids)
    findings.extend(select_findings)
    rubric = _rubric_text(findings)
    work_dir = input_dir / LESSONS_DIR / WORK_SUBDIR

    for gid in selected:
        this_pass = pass_ or ("full" if ctx.lesson(gid) is not None else "outline")
        path = work_dir / item_id_to_filename(gid)
        rel = f"{LESSONS_DIR}/{WORK_SUBDIR}/{path.name}"
        if path.exists() and not force:
            try:
                current = load_json(path)
            except (OSError, json.JSONDecodeError):
                current = None
            if isinstance(current, dict) and current.get("pass") == this_pass:
                findings.append(Finding(f"{STAGE}.prepare.skipped", "info", rel, f"work file for the {this_pass} pass exists (use --force to rewrite)"))
                continue
        work = build_work_file(ctx, gid, this_pass, rubric, findings)
        dump_json(path, work)
        findings.append(Finding(f"{STAGE}.prepare.written", "info", rel,
                                f"{this_pass} pass: {len(work['page_template'])} page template, {len((work['brief'] or {}).get('items') or [])} brief item(s), {len(work['images'])} image(s)"))
    return findings


# ---------------------------------------------------------------------------
# check — outline
# ---------------------------------------------------------------------------


@dataclass
class _Scope:
    """Resolution scope of one lesson, derived from its group and brief."""

    gid: str
    group: dict
    budget: dict
    objective_ids: set[str]
    kp_ids: list[str]
    unit_scope: set[str]
    brief_items: dict[str, dict] | None   # None when the brief is missing
    committed: dict[str, dict]            # prerequisite group id -> entry
    images: dict[str, dict]


def _scope(ctx: Context, gid: str) -> _Scope:
    group = ctx.groups[gid]
    primary, related = ctx.group_units(group)
    brief = ctx.brief(gid)
    items = None
    if brief is not None:
        items = {it["id"]: it for it in (brief.get("items") or []) if isinstance(it, dict) and isinstance(it.get("id"), str)}
    return _Scope(
        gid=gid, group=group, budget=ctx.budget(group),
        objective_ids={o.get("id") for o in (group.get("objectives") or []) if isinstance(o, dict) and o.get("id")},
        kp_ids=[k.get("id") for k in (group.get("knowledge_points") or []) if isinstance(k, dict) and k.get("id")],
        unit_scope=set(primary) | set(related),
        brief_items=items,
        committed={p["group_id"]: p for p in ctx.committed_prerequisites(group)},
        images=ctx.group_images(group),
    )


def _pages_of(lesson: dict) -> list[dict]:
    pages = lesson.get("pages")
    return [p for p in pages if isinstance(p, dict)] if isinstance(pages, list) else []


def _check_outline(ctx: Context, scope: _Scope, lesson: dict, rel: str) -> list[Finding]:
    """Skeleton gates that apply to the outline and to the full file alike."""
    findings: list[Finding] = []
    gid = scope.gid

    def err(cid: str, loc: str, msg: str) -> None:
        findings.append(Finding(f"{STAGE}.{cid}", "error", loc, msg))

    def warn(cid: str, loc: str, msg: str) -> None:
        findings.append(Finding(f"{STAGE}.{cid}", "warning", loc, msg))

    for key in LESSON_REQUIRED_KEYS:
        if key not in lesson:
            err("schema", rel, f"missing required key {key!r}")
    if lesson.get("contract_version") != CONTRACT_VERSION:
        warn("contract_version", rel, f"contract_version={lesson.get('contract_version')!r}, expected {CONTRACT_VERSION}")
    if lesson.get("lesson_id") != gid:
        err("lesson_id_mismatch", rel, f"lesson_id={lesson.get('lesson_id')!r} does not match the file name ({gid})")
    if lesson.get("group_id") != gid:
        err("group_id_mismatch", rel, f"group_id={lesson.get('group_id')!r} must equal the lesson id ({gid})")
    revision = lesson.get("revision", 0)
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        err("schema", rel, f"revision must be an integer >= 0, got {revision!r}")
    if not isinstance(lesson.get("title"), str) or not lesson["title"].strip():
        err("title_empty", rel, "title is empty")
    if not isinstance(lesson.get("description"), str) or not lesson["description"].strip():
        warn("description_empty", rel, "description is empty")
    for key, expected in (("area_id", scope.group.get("area_id")), ("module_id", scope.group.get("module_id"))):
        if key in lesson and lesson.get(key) != expected:
            warn("group_field_mismatch", rel, f"{key}={lesson.get(key)!r} differs from the group's {expected!r}")
    if lesson.get("suggested_level") not in (None, scope.group.get("suggested_level")):
        warn("group_field_mismatch", rel, f"suggested_level={lesson.get('suggested_level')!r} differs from the group's {scope.group.get('suggested_level')!r}")
    key_terms = lesson.get("key_terms", [])
    if not isinstance(key_terms, list) or not all(isinstance(t, str) for t in key_terms):
        err("schema", rel, "key_terms must be a list of strings")
    if "lessons_learned" in lesson and not isinstance(lesson["lessons_learned"], list):
        err("schema", rel, "lessons_learned must be a list")

    # --- objectives ---
    objectives = lesson.get("objectives") if isinstance(lesson.get("objectives"), list) else []
    seen_lo: set[str] = set()
    for i, lo in enumerate(objectives):
        loc = f"{rel} objectives[{i}]"
        if not isinstance(lo, dict):
            err("schema", loc, "objective must be an object")
            continue
        if lo.get("id") not in scope.objective_ids:
            err("objective_unknown", loc, f"objective id {lo.get('id')!r} is not an objective of {gid}")
        else:
            seen_lo.add(lo["id"])
        if not isinstance(lo.get("text"), str) or not lo["text"].strip():
            err("schema", loc, "objective has empty text")
        if lo.get("bloom_level") not in BLOOM_LEVELS:
            err("objective_bloom", loc, f"bloom_level={lo.get('bloom_level')!r} not in {list(BLOOM_LEVELS)}")
    for lo_id in sorted(scope.objective_ids - seen_lo):
        warn("objective_missing", rel, f"group objective {lo_id} is not listed in the lesson's objectives")

    # --- prerequisites ---
    prereqs = lesson.get("prerequisites", [])
    if not isinstance(prereqs, list):
        err("schema", rel, "prerequisites must be a list of lesson ids")
        prereqs = []
    for p in prereqs:
        if p not in scope.committed:
            err("prerequisite_not_committed", rel, f"prerequisite {p!r} is not a committed prerequisite of {gid} in groups.json")
    for p in scope.committed:
        if p not in prereqs:
            warn("prerequisite_missing", rel, f"committed prerequisite {p} is not listed in the lesson's prerequisites")

    # --- pages: shape, roles, order ---
    if not isinstance(lesson.get("pages"), list):
        err("schema", rel, "pages must be a list")
        return findings
    pages = _pages_of(lesson)
    if len(pages) != len(lesson["pages"]):
        err("schema", rel, "every page must be an object")
    expected_pages = scope.budget.get("pages")
    if len(pages) != expected_pages:
        err("outline.page_count", rel, f"{len(pages)} pages, budget allows exactly {expected_pages}")

    roles: list[str] = []
    for pos, page in enumerate(pages):
        loc = f"{rel} pages[{pos}]"
        for key in PAGE_REQUIRED_KEYS:
            if key not in page:
                err("schema", loc, f"page missing required key {key!r}")
        if page.get("index") != pos:
            err("outline.index", loc, f"index={page.get('index')!r} but the page sits at position {pos}")
        role = page.get("role")
        if role not in PAGE_ROLES:
            err("outline.role", loc, f"role={role!r} not in {list(PAGE_ROLES)}")
            continue
        roles.append(role)
        if pos and roles[-2] in PAGE_ROLE_ORDER and PAGE_ROLE_ORDER[role] < PAGE_ROLE_ORDER[roles[-2]]:
            err("outline.order", loc, f"{role} page follows a {roles[-2]} page — required order: orientation, core…, worked_example, pitfalls, recap")
        if not isinstance(page.get("title"), str) or not page["title"].strip():
            err("schema", loc, "page title is empty")
    counts = Counter(roles)
    for role in FIXED_ROLES:
        if counts[role] != 1:
            err("outline.role_count", rel, f"{counts[role]} {role} page(s), expected exactly 1")
    if counts["worked_example"] > 1:
        err("outline.role_count", rel, f"{counts['worked_example']} worked_example pages, expected at most 1")

    # --- pages: objectives, knowledge points, citations ---
    covered_lo: set[str] = set()
    kp_pages: dict[str, list[int]] = {}
    for pos, page in enumerate(pages):
        loc = f"{rel} pages[{pos}]"
        role = page.get("role")
        lo_ids = page.get("lo_ids") if isinstance(page.get("lo_ids"), list) else []
        if not lo_ids:
            err("outline.page_objectives", loc, "page serves no objective (lo_ids is empty)")
        for lo in lo_ids:
            if lo not in scope.objective_ids:
                err("outline.page_objectives", loc, f"lo_id {lo!r} is not an objective of {gid}")
            else:
                covered_lo.add(lo)

        kp = page.get("knowledge_point_id")
        if kp is not None and kp not in scope.kp_ids:
            err("outline.knowledge_point", loc, f"knowledge_point_id {kp!r} is not a knowledge point of {gid}")
        elif role == "core" and kp is None:
            err("outline.knowledge_point", loc, "core page must carry exactly one knowledge_point_id")
        elif role in ("orientation", "recap") and kp is not None:
            err("outline.knowledge_point", loc, f"{role} page must not carry a knowledge point")
        if kp in scope.kp_ids:
            kp_pages.setdefault(kp, []).append(pos)

        unit_ids = page.get("unit_ids") if isinstance(page.get("unit_ids"), list) else []
        for uid in unit_ids:
            if uid not in scope.unit_scope:
                err("unit_out_of_group", loc, f"unit {uid!r} is not in the group's unit_ids or related_unit_ids")
            elif uid not in ctx.units:
                err("unit_unknown", loc, f"unit {uid} is in groups.json but not in units/")

        brief_ids = page.get("brief_item_ids") if isinstance(page.get("brief_item_ids"), list) else []
        if scope.brief_items is not None:
            kinds = []
            for bid in brief_ids:
                item = scope.brief_items.get(bid)
                if item is None:
                    err("brief_item_unknown", loc, f"brief item {bid!r} is not in {SYNTHESES_DIR}/{item_id_to_filename(gid)}")
                else:
                    kinds.append(item.get("kind"))
            if kinds and all(k == "inference" for k in kinds):
                err("inference_only_page", loc, "page is supported only by inference items — cite a map or watch item")
        if role != "recap" and not brief_ids and not unit_ids:
            warn("page_uncited", loc, f"{role} page cites no brief item and no unit")

    for lo_id in sorted(scope.objective_ids - covered_lo):
        err("outline.objective_uncovered", rel, f"objective {lo_id} is taught by no page")
    for kp in scope.kp_ids:
        where = kp_pages.get(kp, [])
        if not where:
            err("outline.kp_uncovered", rel, f"knowledge point {kp} sits on no page")
        elif len(where) > 1:
            err("outline.kp_duplicate", rel, f"knowledge point {kp} sits on pages {where}; exactly one page may carry it")
    if scope.brief_items is None:
        warn("brief_missing", rel, f"no brief in {SYNTHESES_DIR}/ — brief_item_ids and inference-only pages were not checked")
    return findings


# ---------------------------------------------------------------------------
# check — full pages
# ---------------------------------------------------------------------------


def _registry_terms(ctx: Context, scope: _Scope, lesson: dict) -> list[str]:
    terms = ctx.concept_terms(scope.group)
    brief = ctx.brief(scope.gid) or {}
    terms += [t.get("term") for t in (brief.get("terminology") or []) if isinstance(t, dict) and isinstance(t.get("term"), str)]
    terms += [t for t in (lesson.get("key_terms") or []) if isinstance(t, str)]
    return sorted({_norm_term(t) for t in terms if _norm_term(t)})


def _prerequisite_recaps(ctx: Context, lesson: dict) -> str:
    chunks = []
    for pid in lesson.get("prerequisites") or []:
        if not isinstance(pid, str) or pid not in ctx.groups:
            continue
        prereq = ctx.lesson(pid)
        for page in _pages_of(prereq) if prereq else []:
            if page.get("role") == "recap" and isinstance(page.get("markdown"), str):
                chunks.append(page["markdown"])
    return _norm_text(" ".join(chunks))


@dataclass
class _PageText:
    index: int
    role: str
    markdown: str
    prose: str
    prose_norm: str
    words: int
    tokens: int
    bold: list[tuple[str, int]]
    bold_set: set[str]
    occurrences: Counter


def _page_texts(pages: list[dict], registry: list[str]) -> list[_PageText]:
    out = []
    for pos, page in enumerate(pages):
        markdown = page.get("markdown") if isinstance(page.get("markdown"), str) else ""
        prose = _prose(markdown)
        prose_norm = _norm_text(prose)
        bold = _bold_terms(prose)
        out.append(_PageText(
            index=pos, role=page.get("role") or "", markdown=markdown, prose=prose, prose_norm=prose_norm,
            words=word_count(prose), tokens=count_tokens(prose), bold=bold, bold_set={t for t, _ in bold},
            occurrences=_term_occurrences(prose_norm, registry),
        ))
    return out


def _introductions(texts: list[_PageText], registry: list[str]) -> dict[str, int]:
    """registry term -> index of the page that introduces it: the first page that bolds it.

    A registry term that is never bolded is never "introduced" — the lesson is
    expected to define each key term in bold on first use, so un-bolded mentions
    do not count toward a page's term density (the density gate then nudges the
    author to bold and define the knowledge point's terms on that page)."""
    intro: dict[str, int] = {}
    for term in registry:
        page = next((t.index for t in texts if term in t.bold_set), None)
        if page is not None:
            intro[term] = page
    return intro


def _check_pages(ctx: Context, scope: _Scope, lesson: dict, rel: str) -> tuple[list[Finding], dict]:
    """Page metrics and their gates; returns (findings, metrics entry for lesson_metrics.json)."""
    findings: list[Finding] = []
    gid = scope.gid

    def add(sev: str, cid: str, loc: str, msg: str) -> None:
        findings.append(Finding(f"{STAGE}.{cid}", sev, loc, msg))

    pages = _pages_of(lesson)
    registry = _registry_terms(ctx, scope, lesson)
    texts = _page_texts(pages, registry)
    introduced = _introductions(texts, registry)
    recap_text = _prerequisite_recaps(ctx, lesson)
    brief = ctx.brief(gid) or {}
    terminology = [t.get("term") for t in (brief.get("terminology") or []) if isinstance(t, dict) and isinstance(t.get("term"), str)]
    brief_units = {it.get("id"): [u for u in (it.get("unit_ids") or []) if isinstance(u, str)]
                   for it in (brief.get("items") or []) if isinstance(it, dict)}
    generalization = _norm_term(scope.group.get("generalization") or "")

    page_metrics: list[dict] = []
    bold_seen: set[str] = set()
    total_words = 0
    for pos, (page, text) in enumerate(zip(pages, texts)):
        loc = f"{rel} pages[{pos}]"
        role = text.role
        total_words += text.words
        metric: dict = {"index": pos, "role": role, "title": page.get("title"), "words": text.words, "tokens": text.tokens}

        # words by role
        if role in PAGE_WORDS:
            deviation = _words_deviation(text.words, role)
            if deviation:
                lo, hi = PAGE_WORDS[role]
                add("warning" if deviation <= PAGE_WORDS_TOLERANCE else "error", "page_words", loc,
                    f"{text.words} words on a {role} page, expected {lo}-{hi} ({deviation:.0%} outside the band)")

        # bold terms: new per page, undefined
        new_terms = sorted(text.bold_set - bold_seen)
        metric["bold_terms"] = sorted(text.bold_set)
        metric["new_terms"] = new_terms
        if len(new_terms) > NEW_TERMS_PER_PAGE_MAX:
            add("error", "new_terms", loc, f"{len(new_terms)} new bold terms (> {NEW_TERMS_PER_PAGE_MAX}): {', '.join(new_terms)}")
        undefined: list[str] = []
        undefined_tokens = 0
        for term in sorted(text.bold_set):
            defined_here = any(_is_defined(text.prose, end) for t, end in text.bold if t == term)
            if defined_here or term in bold_seen or (recap_text and term in recap_text):
                continue
            undefined.append(term)
            undefined_tokens += _term_occurrences(text.prose_norm, [term])[term] * count_tokens(term)
        metric["undefined_terms"] = undefined
        metric["undefined_ratio"] = round(undefined_tokens / text.tokens, 4) if text.tokens else 0.0
        if metric["undefined_ratio"] > UNDEFINED_TERM_RATIO_MAX:
            add("error", "undefined_terms", loc,
                f"undefined bold terms are {metric['undefined_ratio']:.1%} of the page tokens (> {UNDEFINED_TERM_RATIO_MAX:.0%}): "
                f"{', '.join(undefined)} — define each within {DEFINITION_WINDOW_CHARS} characters (\"**term** is …\")")
        bold_seen |= text.bold_set

        # term density (introduced registry terms)
        own_terms = [t for t, p in introduced.items() if p == pos]
        density_tokens = sum(text.occurrences.get(t, 0) * count_tokens(t) for t in own_terms)
        density = density_tokens / text.tokens if text.tokens else 0.0
        metric["introduced_terms"] = sorted(own_terms)
        metric["term_density"] = round(density, 4)
        if role == "core":
            lo, hi = TERM_DENSITY_RANGE
            if density > hi:
                add("warning", "term_density", loc, f"term density {density:.2f} > {hi} — add an example/elaboration between the terms")
            elif density < lo:
                add("warning", "term_density", loc, f"term density {density:.2f} < {lo} — the page introduces almost no registry term; bold and define the knowledge point's terms here")

        # sentences and paragraphs
        lengths, max_paragraph = _sentence_stats(text.prose)
        mean = sum(lengths) / len(lengths) if lengths else 0.0
        long_sentences = sum(1 for n in lengths if n > SENTENCE_FLAG_WORDS)
        metric.update({"sentences": len(lengths), "mean_sentence_words": round(mean, 1),
                       "long_sentences": long_sentences, "max_paragraph_sentences": max_paragraph})
        if mean > SENTENCE_MEAN_WORDS_MAX:
            add("warning", "sentence_mean", loc, f"mean sentence length {mean:.1f} words > {SENTENCE_MEAN_WORDS_MAX}")
        if long_sentences:
            add("info", "sentence_long", loc, f"{long_sentences} sentence(s) over {SENTENCE_FLAG_WORDS} words")
        if max_paragraph > PARAGRAPH_MAX_SENTENCES:
            add("warning", "paragraph_long", loc, f"a paragraph has {max_paragraph} sentences (> {PARAGRAPH_MAX_SENTENCES})")

        # example marker
        has_example = "```" in text.markdown or bool(EXAMPLE_MARKER_RE.search(text.prose))
        metric["has_example"] = has_example
        if text.words > EXAMPLE_REQUIRED_OVER_WORDS and not has_example:
            add("error", "example_missing", loc,
                f"{text.words} words (> {EXAMPLE_REQUIRED_OVER_WORDS}) without a fenced code block or an example marker (for example, consider, suppose, …)")

        # verbatim vs cited units
        unit_ids = [u for u in (page.get("unit_ids") or []) if isinstance(u, str)]
        for bid in page.get("brief_item_ids") or []:
            unit_ids += brief_units.get(bid, [])
        sources = [ctx.units[u].get("text", "") for u in sorted(set(unit_ids), key=_unit_sort_key) if u in ctx.units]
        metric["verbatim"] = None
        if text.prose.strip() and sources:
            whitelist = set(registry) | set(terminology) | set(_QUOTED_SPAN_RE.findall(text.prose))
            report = verbatim_report(text.markdown, sources, whitelist)
            metric["verbatim"] = {k: (round(report[k], 4) if isinstance(report[k], float) else report[k])
                                  for k in ("coverage", "density", "max_fragment", "jaccard5")}
            for check_id, message in gate(report, "page"):
                findings.append(Finding(f"{STAGE}.{check_id}", GATE_SEVERITY[check_id], loc, message))

        # furniture, dangling references
        furniture = [m.group(0).strip() for pat in PAGE_FURNITURE_PATTERNS for m in pat.finditer(text.prose)]
        metric["furniture_matches"] = len(furniture)
        if furniture:
            add("error", "furniture", loc, f"page furniture / extraction artifact: {', '.join(repr(f) for f in furniture[:3])}")
        has_image = bool(IMAGE_PLACEHOLDER_RE.search(text.markdown))
        dangling = [m.group(0) for pat in DANGLING_REFERENCE_PATTERNS if not (pat is _FIGURE_PATTERN and has_image)
                    for m in pat.finditer(text.prose)]
        metric["dangling_references"] = len(dangling)
        if dangling:
            add("error", "dangling_reference", loc, f"dangling reference(s): {', '.join(repr(d) for d in dangling[:3])}")

        # page starts
        mid = not _starts_cleanly(text.markdown)
        if pos and not _ends_cleanly(texts[pos - 1].markdown):
            mid = True
            add("error", "mid_sentence_start", loc, "previous page does not end with terminal punctuation or a code fence")
        if not _starts_cleanly(text.markdown):
            add("error", "mid_sentence_start", loc, "page must start with a heading or a capitalized sentence")
        metric["mid_sentence_start"] = mid

        # images
        image_ids = [m.group("id") for m in IMAGE_PLACEHOLDER_RE.finditer(text.markdown)]
        metric["image_ids"] = image_ids
        for iid in sorted(set(image_ids) - set(scope.images)):
            add("error", "unknown_image", loc, f"image placeholder {iid!r} matches no image of this lesson's units")

        # role-specific shape
        if role == "pitfalls":
            items = _count_items(text.prose)
            metric["items"] = items
            if not (PITFALLS_RANGE[0] <= items <= PITFALLS_RANGE[1]):
                add("error", "pitfalls_count", loc, f"{items} misconception item(s), expected {PITFALLS_RANGE[0]}-{PITFALLS_RANGE[1]} (one ### heading or one top-level bullet each)")
        if role == "recap":
            items = _list_items(text.prose)
            metric["items"] = items
            if generalization and generalization not in text.prose_norm:
                add("error", "recap_generalization", loc, "recap does not restate the group's generalization verbatim")
            if not (RECAP_TAKEAWAYS[0] <= items <= RECAP_TAKEAWAYS[1]):
                add("warning", "recap_takeaways", loc, f"{items} takeaway bullet(s), expected {RECAP_TAKEAWAYS[0]}-{RECAP_TAKEAWAYS[1]}")
        page_metrics.append(metric)

    if not (LESSON_WORDS[0] <= total_words <= LESSON_WORDS[1]):
        add("error", "lesson_words", rel, f"{total_words} words in the lesson, expected {LESSON_WORDS[0]}-{LESSON_WORDS[1]}")
    expected_minutes = total_words / READING_WORDS_PER_MINUTE
    estimated = lesson.get("estimated_minutes")
    if not isinstance(estimated, (int, float)) or isinstance(estimated, bool):
        add("warning", "estimated_minutes", rel, f"estimated_minutes={estimated!r} is not a number (expected about {expected_minutes:.0f})")
    elif expected_minutes and abs(estimated - expected_minutes) > ESTIMATED_MINUTES_TOLERANCE * expected_minutes:
        add("warning", "estimated_minutes", rel, f"estimated_minutes={estimated} but {total_words} words read in about {expected_minutes:.1f} minutes")
    if lesson.get("outline_approved") is not True:
        add("warning", "outline_not_approved", rel, "outline_approved is not true — full pages written before the outline was approved")

    metrics = {
        "lesson_id": gid, "title": lesson.get("title"), "words": total_words, "page_count": len(pages),
        "estimated_minutes": estimated, "estimated_minutes_expected": round(expected_minutes, 1),
        "errors": sum(1 for f in findings if f.severity == "error"),
        "warnings": sum(1 for f in findings if f.severity == "warning"),
        "pages": page_metrics,
    }
    return findings, metrics


def _check_lesson(ctx: Context, gid: str, outline: bool) -> tuple[list[Finding], dict | None]:
    """All gates for one lesson; metrics only for a full check with every page written."""
    path = ctx.input_dir / LESSONS_DIR / item_id_to_filename(gid)
    rel = f"{LESSONS_DIR}/{path.name}"
    if not path.exists():
        return [Finding(f"{STAGE}.missing", "error", rel, f"no lesson for group {gid}")], None
    before = len(ctx.findings)
    lesson = ctx.lesson(gid)
    findings = ctx.findings[before:]
    if lesson is None:
        if not findings:
            findings.append(Finding(f"{STAGE}.schema", "error", rel, "lesson must be a JSON object"))
        return findings, None
    scope = _scope(ctx, gid)
    findings.extend(_check_outline(ctx, scope, lesson, rel))
    if outline:
        return findings, None
    pages = _pages_of(lesson)
    unwritten = [p.get("index") for p in pages if not isinstance(p.get("markdown"), str) or not p["markdown"].strip()]
    if unwritten or not pages:
        findings.append(Finding(f"{STAGE}.markdown_missing", "error", rel,
                                f"pages {unwritten} have no markdown — an outline; run `check --outline` or write the pages"))
        return findings, None
    if any(f.severity == "error" and f.check_id.startswith(f"{STAGE}.schema") for f in findings):
        return findings, None
    page_findings, metrics = _check_pages(ctx, scope, lesson, rel)
    findings.extend(page_findings)
    metrics["errors"] = sum(1 for f in findings if f.severity == "error")
    metrics["warnings"] = sum(1 for f in findings if f.severity == "warning")
    return findings, metrics


def _title_duplicates(ctx: Context, selected: list[str]) -> list[Finding]:
    """Lesson titles must be unique curriculum-wide and must not shadow an area or module."""
    findings: list[Finding] = []
    titles: dict[str, list[str]] = {}
    for path in ctx.lesson_files():
        lid = filename_to_item_id(path.name)
        lesson = ctx.lesson(lid) if lid in ctx.groups else None
        if lesson is None:
            try:
                lesson = load_json(path)
            except (OSError, json.JSONDecodeError):
                continue
        title = lesson.get("title") if isinstance(lesson, dict) else None
        if isinstance(title, str) and title.strip():
            titles.setdefault(_norm_text(title), []).append(lid)
    structural = {}
    for a in ctx.areas:
        if isinstance(a.get("title"), str):
            structural.setdefault(_norm_text(a["title"]), f"area {a.get('id')}")
        for m in a.get("modules") or []:
            if isinstance(m, dict) and isinstance(m.get("title"), str):
                structural.setdefault(_norm_text(m["title"]), f"module {m.get('id')}")
    for key, ids in titles.items():
        for lid in ids:
            if lid not in selected:
                continue
            rel = f"{LESSONS_DIR}/{item_id_to_filename(lid)}"
            others = [o for o in ids if o != lid]
            if others:
                findings.append(Finding(f"{STAGE}.title_duplicate", "error", rel, f"title also used by {', '.join(others)} — lesson titles must be unique curriculum-wide"))
            if key in structural:
                findings.append(Finding(f"{STAGE}.title_duplicate", "error", rel, f"title collides with {structural[key]} — the uploader keys every topic by title"))
    return findings


def _write_metrics(input_dir: Path, metrics: dict[str, dict], partial: bool) -> None:
    path = input_dir / LESSON_METRICS_FILE
    existing: dict = {}
    if partial and path.exists():
        try:
            data = load_json(path)
            existing = data.get("lessons") if isinstance(data, dict) and isinstance(data.get("lessons"), dict) else {}
        except (OSError, json.JSONDecodeError):
            existing = {}
    existing.update(metrics)
    dump_json(path, {"contract_version": CONTRACT_VERSION, "lessons": existing})


def check(input_dir: Path, lesson_id: str | None = None, outline: bool = False, **_opts) -> list[Finding]:
    """Validate every lesson in lessons/ (or one with lesson_id). A full check writes
    lesson_metrics.json for the lessons whose pages are complete."""
    input_dir = Path(input_dir)
    ctx = load_context(input_dir)
    findings = list(ctx.findings)
    if ctx.fatal:
        return findings
    selected, select_findings = _select(ctx, [lesson_id] if lesson_id else None)
    findings.extend(select_findings)

    metrics: dict[str, dict] = {}
    for gid in selected:
        lesson_findings, entry = _check_lesson(ctx, gid, outline)
        findings.extend(lesson_findings)
        if entry is not None:
            metrics[gid] = entry
    findings.extend(_title_duplicates(ctx, selected))

    if lesson_id is None:
        for path in ctx.lesson_files():
            lid = filename_to_item_id(path.name)
            if lid not in ctx.groups:
                findings.append(Finding(f"{STAGE}.unknown_lesson", "error", f"{LESSONS_DIR}/{path.name}", f"lesson for {lid!r}, which is not a group in groups.json"))
    if not outline and (metrics or lesson_id is None):
        _write_metrics(input_dir, metrics, partial=lesson_id is not None)
    return findings


def check_item(input_dir: Path, item_id: str, outline: bool = False, **_opts) -> list[Finding]:
    """Validate the lesson of one group."""
    return check(input_dir, lesson_id=item_id, outline=outline)


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------


def _area_level(area: dict, groups: list[dict]) -> int | None:
    level = area.get("suggested_level")
    if level in (1, 2, 3):
        return level
    levels = Counter(g.get("suggested_level") for g in groups if g.get("suggested_level") in (1, 2, 3))
    return levels.most_common(1)[0][0] if levels else None


def _leaf(ctx: Context, gid: str, lesson: dict, titles: dict[str, str], depth: int, sort_order: int, findings: list[Finding]) -> dict:
    prerequisites = []
    for p in ctx.build_prerequisites(ctx.groups[gid]):
        title = titles.get(p["group_id"])
        if title is None:
            findings.append(Finding(f"{STAGE}.build.prerequisite_unbuilt", "warning", gid, f"prerequisite {p['group_id']} has no lesson title — edge dropped from structure.json"))
            continue
        prerequisites.append({"topic": title, "strength": p.get("strength") or "recommended"})
    return {
        "title": lesson["title"],
        "depth": depth,
        "sort_order": sort_order,
        "lesson_id": gid,
        "description": lesson.get("description") or "",
        "learning_objectives": [
            {"text": lo.get("text"), "bloom_level": lo.get("bloom_level")}
            for lo in (lesson.get("objectives") or []) if isinstance(lo, dict)
        ],
        "prerequisites": prerequisites,
        "source_sections": [],
        "children": [],
    }


def build_structure(ctx: Context, lessons: dict[str, dict], findings: list[Finding]) -> list[dict] | None:
    """areas (depth 0) > modules (depth 1, when present) > lessons (leaves)."""
    titles = {gid: lesson["title"] for gid, lesson in lessons.items()}
    by_area: dict[str, list[str]] = {}
    for gid in ctx.group_order:
        if gid in lessons:
            by_area.setdefault(ctx.groups[gid].get("area_id"), []).append(gid)
    area_ids = {a.get("id") for a in ctx.areas}
    unknown = sorted(str(a) for a in by_area if a not in area_ids)
    if unknown:
        findings.append(Finding(f"{STAGE}.build.area_unknown", "error", GROUPS_FILE, f"groups reference area(s) not in groups.json areas: {', '.join(unknown)}"))
        return None

    structure = []
    for a_pos, area in enumerate(sorted(ctx.areas, key=lambda a: (_order(a), str(a.get("id"))))):
        gids = by_area.get(area.get("id"), [])
        if not gids:
            continue
        node = {
            "title": area.get("title") or area.get("id"), "depth": 0, "sort_order": len(structure),
            "description": area.get("description") or "", "suggested_level": _area_level(area, [ctx.groups[g] for g in gids]),
            "learning_objectives": [], "children": [],
        }
        modules = {m.get("id"): m for m in (area.get("modules") or []) if isinstance(m, dict) and m.get("id")}
        module_nodes: dict[str, dict] = {}
        for gid in gids:
            mid = ctx.groups[gid].get("module_id")
            if mid in modules:
                parent = module_nodes.get(mid)
                if parent is None:
                    parent = module_nodes[mid] = {
                        "title": modules[mid].get("title") or mid, "depth": 1, "sort_order": 0,
                        "description": modules[mid].get("description") or "", "learning_objectives": [], "children": [],
                    }
                parent["children"].append(_leaf(ctx, gid, lessons[gid], titles, 2, len(parent["children"]), findings))
            else:
                node["children"].append(_leaf(ctx, gid, lessons[gid], titles, 1, 0, findings))
        ordered_modules = sorted(module_nodes.items(), key=lambda kv: (_order(modules[kv[0]]), kv[0]))
        node["children"].extend(m for _, m in ordered_modules)
        for i, child in enumerate(node["children"]):
            child["sort_order"] = i
        structure.append(node)
    return structure


def build_chunks(ctx: Context, structure: list[dict], lessons: dict[str, dict]) -> list[dict]:
    """One chunk per page, chunk_index = page index, topic_path from the structure."""
    paths: dict[str, list[str]] = {}

    def walk(nodes: list[dict], prefix: list[str]) -> None:
        for node in nodes:
            path = prefix + [node["title"]]
            if node.get("lesson_id") and not node.get("children"):
                paths[node["lesson_id"]] = path
            walk(node.get("children") or [], path)

    walk(structure, [])
    chunks = []
    for gid in ctx.group_order:
        lesson = lessons.get(gid)
        if lesson is None or gid not in paths:
            continue
        images = ctx.group_images(ctx.groups[gid])
        for pos, page in enumerate(_pages_of(lesson)):
            content = (page.get("markdown") or "").strip()
            used: dict[str, dict] = {}
            for m in IMAGE_PLACEHOLDER_RE.finditer(content):
                img = images.get(m.group("id"))
                if img and m.group("id") not in used:
                    used[m.group("id")] = {"id": img["id"], "local_path": img["local_path"],
                                           "alt_text": m.group("alt") or img["alt_text"], "mime_type": img["mime_type"]}
            chunks.append({
                "topic_path": paths[gid],
                "chunk_index": page.get("index") if isinstance(page.get("index"), int) else pos,
                "content": content,
                "token_count": count_tokens(content),
                "has_code": "```" in content,
                "page_number": None,
                "images": list(used.values()),
                "metadata": {
                    "lesson_id": gid,
                    "page_role": page.get("role"),
                    "lo_ids": [lo for lo in (page.get("lo_ids") or []) if isinstance(lo, str)],
                    "source_units": [u for u in (page.get("unit_ids") or []) if isinstance(u, str)],
                    "key_terms": [t for t in (lesson.get("key_terms") or []) if isinstance(t, str)],
                    "contract_version": CONTRACT_VERSION,
                },
            })
    return chunks


def build(input_dir: Path, allow_failing: bool = False) -> tuple[dict | None, list[Finding]]:
    """Write structure.json + chunks.json from the full lessons. Returns (result, findings);
    result is None when the build was blocked. result["rows"] feeds the summary table."""
    input_dir = Path(input_dir)
    ctx = load_context(input_dir)
    findings = list(ctx.findings)
    if ctx.fatal:
        return None, findings

    lessons: dict[str, dict] = {}
    for gid in ctx.group_order:
        rel = f"{LESSONS_DIR}/{item_id_to_filename(gid)}"
        lesson = ctx.lesson(gid)
        if lesson is None:
            findings.append(Finding(f"{STAGE}.build.lesson_missing", "error", rel, f"no lesson file for group {gid}"))
            continue
        pages = _pages_of(lesson)
        if not pages or any(not isinstance(p.get("markdown"), str) or not p["markdown"].strip() for p in pages):
            findings.append(Finding(f"{STAGE}.build.outline_only", "error", rel, "pages have no markdown — write the full pages before building"))
            continue
        if not isinstance(lesson.get("title"), str) or not lesson["title"].strip():
            findings.append(Finding(f"{STAGE}.build.title_missing", "error", rel, "lesson has no title"))
            continue
        lessons[gid] = lesson
    if any(f.severity == "error" for f in findings):
        return None, findings

    check_findings = check(input_dir)
    findings.extend(check_findings)
    errors = sum(1 for f in check_findings if f.severity == "error")
    if errors and not allow_failing:
        findings.append(Finding(f"{STAGE}.build.blocked", "error", LESSONS_DIR, f"{errors} check error(s) — fix them or pass --allow-failing"))
        return None, findings
    if errors:
        findings.append(Finding(f"{STAGE}.build.allow_failing", "warning", LESSONS_DIR, f"building despite {errors} check error(s)"))

    structure = build_structure(ctx, lessons, findings)
    if structure is None:
        return None, findings
    chunks = build_chunks(ctx, structure, lessons)
    dump_json(input_dir / "structure.json", structure)
    dump_json(input_dir / "chunks.json", chunks)

    rows = []
    for gid in ctx.group_order:
        if gid not in lessons:
            continue
        own = [c for c in chunks if c["metadata"]["lesson_id"] == gid]
        rows.append({
            "lesson_id": gid, "title": lessons[gid]["title"], "pages": len(own),
            "words": sum(word_count(_prose(c["content"])) for c in own),
            "tokens": sum(c["token_count"] for c in own),
            "prerequisites": len(ctx.build_prerequisites(ctx.groups[gid])),
            "errors": sum(1 for f in check_findings if f.severity == "error" and item_id_to_filename(gid) in f.location),
            "warnings": sum(1 for f in check_findings if f.severity == "warning" and item_id_to_filename(gid) in f.location),
        })
    findings.append(Finding(f"{STAGE}.build.written", "info", "structure.json, chunks.json",
                            f"{len(lessons)} lesson(s), {len(chunks)} chunk(s), {len(structure)} area(s)"))
    return {"structure": structure, "chunks": chunks, "rows": rows}, findings


def format_summary(rows: list[dict]) -> str:
    """Plain-text summary table for `build`."""
    columns = [("lesson_id", "Lesson"), ("title", "Title"), ("pages", "Pages"), ("words", "Words"),
               ("tokens", "Tokens"), ("prerequisites", "Prereqs"), ("errors", "Err"), ("warnings", "Warn")]
    widths = {key: max(len(label), *(len(str(r[key])) for r in rows)) if rows else len(label) for key, label in columns}
    line = "  ".join(label.ljust(widths[key]) for key, label in columns)
    out = [line, "  ".join("-" * widths[key] for key, _ in columns)]
    for r in rows:
        out.append("  ".join(str(r[key]).ljust(widths[key]) for key, _ in columns))
    out.append(f"{len(rows)} lesson(s), {sum(r['pages'] for r in rows)} chunk(s), {sum(r['words'] for r in rows)} words")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

_LABEL = {"error": "ERROR", "warning": "WARN", "info": "INFO"}


def print_findings(findings: list[Finding]) -> tuple[int, int]:
    order = {"error": 0, "warning": 1, "info": 2}
    for f in sorted(findings, key=lambda f: (order.get(f.severity, 3), f.location, f.check_id)):
        print(f"[{_LABEL.get(f.severity, f.severity.upper())}] {f.check_id} @ {f.location}: {f.message}")
    n_err = sum(1 for f in findings if f.severity == "error")
    n_warn = sum(1 for f in findings if f.severity == "warning")
    print(f"{n_err} errors, {n_warn} warnings")
    return n_err, n_warn


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stage 5: design lessons (prepare work files / check lessons / build structure + chunks)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_prep = sub.add_parser("prepare", help=f"write {LESSONS_DIR}/{WORK_SUBDIR}/<item>.json per group")
    p_prep.add_argument("input_dir", type=Path, help="output/<name>/ directory")
    p_prep.add_argument("--lesson", action="append", metavar="g:ID", help="only this lesson (repeatable)")
    p_prep.add_argument("--force", action="store_true", help="rewrite existing work files")
    p_prep.add_argument("--pass", dest="pass_", choices=PASSES, help="authoring pass (default: full when the lesson file exists, else outline)")

    p_check = sub.add_parser("check", help=f"validate {LESSONS_DIR}/<item>.json")
    p_check.add_argument("input_dir", type=Path, help="output/<name>/ directory")
    p_check.add_argument("--lesson", metavar="g:ID", help="only this lesson")
    p_check.add_argument("--outline", action="store_true", help="validate the outline (pages with markdown null) only")
    p_check.add_argument("--strict", action="store_true", help="warnings fail too")

    p_build = sub.add_parser("build", help="write structure.json + chunks.json from the full lessons")
    p_build.add_argument("input_dir", type=Path, help="output/<name>/ directory")
    p_build.add_argument("--allow-failing", action="store_true", help="build even when check reports errors")

    args = parser.parse_args(argv)
    if not args.input_dir.is_dir():
        print(f"error: {args.input_dir} is not a directory", file=sys.stderr)
        return 2

    if args.command == "prepare":
        n_err, _ = print_findings(prepare(args.input_dir, lesson_ids=args.lesson, force=args.force, pass_=args.pass_))
        return 1 if n_err else 0
    if args.command == "check":
        n_err, n_warn = print_findings(check(args.input_dir, lesson_id=args.lesson, outline=args.outline))
        return 1 if n_err or (args.strict and n_warn) else 0
    result, findings = build(args.input_dir, allow_failing=args.allow_failing)
    n_err, _ = print_findings(findings)
    if result is not None:
        print(format_summary(result["rows"]))
        return 0
    return 1 if n_err else 0


if __name__ == "__main__":
    sys.exit(main())
