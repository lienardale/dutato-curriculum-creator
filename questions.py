"""
Stage 6 of the lesson pipeline — DESIGN QUESTIONS.

PURE DATA UTILITY — no AI calls. The operating agent (or its sub-agents) writes
``questions/<item>.json``; this script prepares the inputs it needs and checks
the result against docs/artifacts.md (Stage 6) with deterministic gates.

Subcommands:

  prepare  Writes one work file per lesson to ``questions/work/<item>.json``:
           the lesson pages, the group's objectives / knowledge points / watch
           items (misconceptions double as distractor seeds), the generalization,
           the budget, concept labels and aliases, the brief items and unit texts
           questions may cite (``source_quote`` must be verbatim), the prerequisite
           lessons' titles and recap pages (for the ``recap_of`` item), the
           embedded-slot table, the authoring rubric (rubrics/questions.md) under
           ``instructions``, the previous revision (if any) and lessons_learned.
  check    Validates ``questions/<item>.json``: ids, enums, lo/concept/unit
           resolution, provenance quotes, embedded placement, prompt hygiene,
           answer leaks, MCQ item-writing flaws, coverage, budgets, variants and
           exercises. Findings print as ``[ERROR] <check_id> @ <location>: <msg>``
           followed by ``<n> errors, <m> warnings``.

Usage:
    python questions.py prepare output/<name>/ [--lesson g:ID ...] [--force] [--allow-edge-questions]
    python questions.py check   output/<name>/ [--lesson g:ID ...] [--allow-edge-questions] [--strict]

Module API (used by validate.py / critique.py):
    check(input_dir, allow_edge_questions=False) -> list[Finding]
    check_item(input_dir, item_id, allow_edge_questions=False) -> list[Finding]
        item_id is a lesson id (g:slug) or a sub-item id (g:slug#q3) — the
        latter filters the lesson's findings to that item.
    compute_slots(n_pages, allow_edge) -> {"before": [...], "after": [...]}
    is_answer_on_page(page_markdown, answer) -> bool
    answer_leaks(text, answer) -> bool
    mcq_flaws(prompt, options, correct_index, true_false=False) -> list[str]
    variant_too_similar(original, variant) -> bool

Exit codes: 0 clean, 1 errors (or warnings with --strict), 2 usage error.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

import _compat  # noqa: F401

from chunk import count_tokens
from contracts import (
    ABSOLUTE_TERMS_RE,
    ACCEPTANCE_MAX_CHARS,
    ALL_NONE_OF_ABOVE_RE,
    BLOOM_LEVELS,
    BLOOM_ORDER,
    BLOOM_TO_MASTERY,
    CAUSAL_CUE_RE,
    CONCEPTS_FILE,
    CONTRACT_VERSION,
    EMBEDDED_DEFAULT_POSITION,
    EMBEDDED_KINDS,
    EMBEDDED_PER_PAGE_MAX,
    EXERCISE_FADING,
    EXERCISE_HINTS,
    GROUPS_FILE,
    LESSONS_DIR,
    LITERAL_RECALL_PATTERNS,
    MCQ_FLAWS_MAX,
    MCQ_LONGEST_OPTION_RATIO,
    MCQ_OPTIONS,
    NEGATIVE_STEM_RE,
    QUESTION_POSITIONS,
    QUESTION_PROMPT_WORDS,
    QUESTIONS_DIR,
    QUIZ_TYPES,
    QUOTE_COPY_CONSECUTIVE_WORDS,
    STOPWORDS,
    SUBITEM_ID_RE,
    SYNTHESES_DIR,
    UNITS_DIR,
    VARIANT_SIMILARITY_MAX,
    WORK_SUBDIR,
    YES_NO_STEM_RE,
    Finding,
    budget_for,
    content_words,
    dump_json,
    item_id_to_filename,
    filename_to_item_id,
    list_item_files,
    load_json,
    normalize_ws,
    quote_in_unit,
    word_count,
)

STAGE = "questions"
RUBRIC_PATH = Path(__file__).resolve().parent / "rubrics" / "questions.md"
# Stands in for the rubric in work files when rubrics/questions.md is missing.
RUBRIC_MISSING_NOTE = (
    "rubrics/questions.md not found — follow docs/artifacts.md Stage 6: every item carries a two-layer key "
    "(acceptance {prediction, causal_reason} stated as a relationship, plus expected_answer / rationale / "
    "key_points), embedded questions use the allowed_slots table, and `python questions.py check` must be clean."
)

# Imperative openers that make a prompt a task even without a question mark.
IMPERATIVE_STARTS = ("explain", "describe", "predict", "compare", "identify", "write", "order", "classify")
# A copied answer run this long inside the stem/page counts as a leak.
ANSWER_LEAK_NGRAM = 4
# Options this similar count as duplicate/near-identical (MCQ item-writing flaw).
MCQ_DUPLICATE_OPTION_RATIO = 0.90
DIFFICULTY_RANGE = (1, 3)
TRUE_FALSE_OPTIONS = ["True", "False"]

_WORD_RE = re.compile(r"[a-z0-9][a-z0-9'-]*")
_MARKDOWN_NOISE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)|```[a-zA-Z0-9_-]*|[#*_`>|]")


# ---------------------------------------------------------------------------
# Text helpers (duplicated in cards.py on purpose: no new shared module)
# ---------------------------------------------------------------------------


def normalize_text(text: str | None) -> str:
    """Lowercase, punctuation stripped to spaces, whitespace collapsed."""
    return " ".join(_WORD_RE.findall((text or "").lower()))


def words(text: str | None) -> list[str]:
    """Lowercase word tokens in order."""
    return _WORD_RE.findall((text or "").lower())


def content_sequence(text: str | None) -> list[str]:
    """Ordered content words (same filter as contracts.content_words, order kept)."""
    return [w for w in words(text) if w not in STOPWORDS and len(w) > 1]


def strip_markdown(markdown: str | None) -> str:
    """Drop image placeholders, fences and inline markup so word runs match prose."""
    return _MARKDOWN_NOISE_RE.sub(" ", markdown or "")


def longest_common_run(a: list[str], b: list[str]) -> int:
    """Length of the longest run of consecutive tokens shared by a and b."""
    if not a or not b:
        return 0
    best = 0
    prev = [0] * (len(b) + 1)
    for x in a:
        cur = [0] * (len(b) + 1)
        for j, y in enumerate(b, start=1):
            if x == y:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best:
                    best = cur[j]
        prev = cur
    return best


def contains_run(haystack: list[str], needle: list[str]) -> bool:
    """True when needle appears as a consecutive run inside haystack."""
    n = len(needle)
    if n == 0 or n > len(haystack):
        return False
    return any(haystack[i:i + n] == needle for i in range(len(haystack) - n + 1))


def answer_leaks(text: str | None, answer: str | None) -> bool:
    """Does the keyed answer appear verbatim (content-word normalized) in text?

    Answers with >= ANSWER_LEAK_NGRAM content words leak when any
    ANSWER_LEAK_NGRAM-gram of theirs appears in the text; shorter answers leak
    only when the whole normalized answer appears.
    """
    seq = content_sequence(answer)
    if not seq:
        return False
    hay = content_sequence(text)
    if len(seq) >= ANSWER_LEAK_NGRAM:
        return any(contains_run(hay, seq[i:i + ANSWER_LEAK_NGRAM]) for i in range(len(seq) - ANSWER_LEAK_NGRAM + 1))
    return contains_run(hay, seq)


def is_answer_on_page(page_markdown: str | None, answer: str | None) -> bool:
    """Critic/validator hook: keyed answer present verbatim in the page prose."""
    return answer_leaks(strip_markdown(page_markdown), answer)


def similarity(a: str | None, b: str | None) -> float:
    return SequenceMatcher(None, normalize_text(a), normalize_text(b)).ratio()


def is_question(prompt: str) -> bool:
    """Ends with '?' (closing quotes/parens allowed) or opens with an imperative verb."""
    stripped = (prompt or "").strip().rstrip("\"'”’)]* ")
    if stripped.endswith("?"):
        return True
    first = words(prompt)[:1]
    return bool(first) and first[0] in IMPERATIVE_STARTS


def mentions_any(text: str, labels: list[str]) -> bool:
    """Case-insensitive, word-bounded match of any label/alias in text."""
    norm = f" {normalize_text(text)} "
    return any(f" {normalize_text(label)} " in norm for label in labels if normalize_text(label))


def compute_slots(n_pages: int, allow_edge: bool = False) -> dict[str, list[int]]:
    """Reachable embedded-question slots for an N-page lesson.

    Today's viewer renders a `before` question only when a previous page exists
    and an `after` question only when a next page exists, hence the default
    [1..N-1] / [0..N-2]; ``allow_edge`` widens both to [0..N-1].
    """
    if n_pages <= 0:
        return {"before": [], "after": []}
    if allow_edge:
        full = list(range(n_pages))
        return {"before": list(full), "after": list(full)}
    return {"before": list(range(1, n_pages)), "after": list(range(0, n_pages - 1))}


def mcq_flaws(prompt: str, options: list[str], correct_index: int, true_false: bool = False) -> list[str]:
    """Item-writing flaws of an MCQ item, one entry per flaw category triggered.

    Categories: longest_option, all_none_of_above, absolute_terms, negative_stem,
    duplicate_options, word_repeat (stem↔key content word no distractor shares).
    True/false items are only checked for a negative stem.
    """
    flaws: list[str] = []
    if NEGATIVE_STEM_RE.search(prompt or ""):
        flaws.append(f"negative_stem: stem contains {NEGATIVE_STEM_RE.search(prompt).group(0)!r}")
    if true_false:
        return flaws
    key = options[correct_index]
    distractors = [o for i, o in enumerate(options) if i != correct_index]
    lengths = sorted((len(normalize_ws(o)) for o in options), reverse=True)
    key_len = len(normalize_ws(key))
    if len(lengths) > 1 and key_len == lengths[0] and lengths[1] < MCQ_LONGEST_OPTION_RATIO * key_len:
        flaws.append(f"longest_option: key is the longest option ({key_len} chars vs {lengths[1]})")
    hits = [o for o in options if ALL_NONE_OF_ABOVE_RE.search(o)]
    if hits:
        flaws.append(f"all_none_of_above: {hits[0]!r}")
    hits = [o for o in options if ABSOLUTE_TERMS_RE.search(o)]
    if hits:
        flaws.append(f"absolute_terms: {', '.join(repr(ABSOLUTE_TERMS_RE.search(o).group(0)) for o in hits)}")
    dup = _near_duplicate_pair(options)
    if dup:
        flaws.append(f"duplicate_options: options {dup[0]} and {dup[1]} are near-identical")
    shared = content_words(prompt) & content_words(key)
    in_distractors: set[str] = set()
    for d in distractors:
        in_distractors |= content_words(d)
    leaked = sorted(shared - in_distractors)
    if leaked:
        flaws.append(f"word_repeat: stem and key share {leaked} and no distractor does")
    return flaws


def _near_duplicate_pair(options: list[str]) -> tuple[int, int] | None:
    for i in range(len(options)):
        for j in range(i + 1, len(options)):
            if similarity(options[i], options[j]) >= MCQ_DUPLICATE_OPTION_RATIO:
                return i, j
    return None


def _skeleton(text: str) -> str:
    """Digits and capitalized tokens replaced by placeholders (surface-variant detector)."""
    t = re.sub(r"\d+", "#", text or "")
    t = re.sub(r"\b[A-Z][A-Za-z]*\b", "@", t)
    return normalize_text(t)


def variant_too_similar(original: str | None, variant: str | None) -> bool:
    """Variant rejected when its normalized ratio to the original reaches
    VARIANT_SIMILARITY_MAX or it differs only in digits / capitalized nouns."""
    if not normalize_text(variant):
        return True
    if similarity(original, variant) >= VARIANT_SIMILARITY_MAX:
        return True
    return _skeleton(original or "") == _skeleton(variant or "")


# ---------------------------------------------------------------------------
# Pipeline context (groups, concepts, units, lessons, briefs)
# ---------------------------------------------------------------------------


def _load_json_safe(path: Path, check_id: str, location: str) -> tuple[object, Finding | None]:
    try:
        return load_json(path), None
    except json.JSONDecodeError as e:
        return None, Finding(check_id, "error", location, f"invalid JSON: {e}")
    except OSError as e:
        return None, Finding(check_id, "error", location, f"cannot read: {e}")


@dataclass
class Context:
    """Everything a check needs that is not the lesson's own authored file."""

    input_dir: Path
    groups: dict[str, dict] = field(default_factory=dict)
    group_order: list[str] = field(default_factory=list)
    concepts: dict[str, dict] = field(default_factory=dict)
    units: dict[str, dict] = field(default_factory=dict)
    has_statuses: bool = False
    findings: list[Finding] = field(default_factory=list)

    def load_lesson(self, lesson_id: str) -> dict | None:
        path = self.input_dir / LESSONS_DIR / item_id_to_filename(lesson_id)
        if not path.exists():
            return None
        data, err = _load_json_safe(path, f"{STAGE}.lesson_missing", f"{LESSONS_DIR}/{path.name}")
        if err is not None:
            self.findings.append(err)
            return None
        return data if isinstance(data, dict) else None

    def load_synthesis(self, lesson_id: str) -> dict | None:
        path = self.input_dir / SYNTHESES_DIR / item_id_to_filename(lesson_id)
        if not path.exists():
            return None
        data, _ = _load_json_safe(path, f"{STAGE}.synthesis_invalid", f"{SYNTHESES_DIR}/{path.name}")
        return data if isinstance(data, dict) else None

    def prerequisites(self, group: dict) -> list[dict]:
        """Accepted prerequisite edges: committed ones, or every proposed edge when
        no critique statuses exist yet anywhere in groups.json."""
        out = []
        for p in group.get("prerequisites") or []:
            status = p.get("status") or "proposed"
            if status == "committed" or (not self.has_statuses and status == "proposed"):
                out.append(p)
        return out

    def budget(self, group: dict) -> dict:
        explicit = group.get("budget")
        if isinstance(explicit, dict) and explicit:
            return explicit
        return budget_for(
            len(group.get("knowledge_points") or []),
            len(group.get("objectives") or []),
            group.get("learning_step") or "building_block",
            bool(self.prerequisites(group)),
        )

    def concept_labels(self, concept_id: str) -> list[str]:
        c = self.concepts.get(concept_id) or {}
        return [c.get("label") or "", *(c.get("aliases") or [])]

    def lesson_ids(self) -> list[str]:
        """Lesson ids that have an authored lessons/<item>.json, in group order."""
        present = {filename_to_item_id(p.name) for p in list_item_files(self.input_dir / LESSONS_DIR)}
        ordered = [g for g in self.group_order if g in present]
        return ordered + sorted(present - set(ordered))


def load_context(input_dir: Path) -> Context:
    ctx = Context(input_dir=Path(input_dir))
    groups_path = ctx.input_dir / GROUPS_FILE
    if not groups_path.exists():
        ctx.findings.append(Finding(f"{STAGE}.groups_missing", "error", GROUPS_FILE, "groups.json not found"))
    else:
        data, err = _load_json_safe(groups_path, f"{STAGE}.groups_missing", GROUPS_FILE)
        if err is not None:
            ctx.findings.append(err)
        elif isinstance(data, dict):
            for g in data.get("groups") or []:
                gid = g.get("id")
                if gid:
                    ctx.groups[gid] = g
                    ctx.group_order.append(gid)
            ctx.has_statuses = any(
                (p.get("status") or "proposed") != "proposed"
                for g in ctx.groups.values() for p in (g.get("prerequisites") or [])
            )
    concepts_path = ctx.input_dir / CONCEPTS_FILE
    if concepts_path.exists():
        data, err = _load_json_safe(concepts_path, f"{STAGE}.concepts_invalid", CONCEPTS_FILE)
        if err is not None:
            ctx.findings.append(err)
        elif isinstance(data, dict):
            ctx.concepts = {c["id"]: c for c in data.get("concepts") or [] if c.get("id")}
    else:
        ctx.findings.append(Finding(f"{STAGE}.concepts_missing", "warning", CONCEPTS_FILE,
                                    "concepts.json not found — concept labels cannot be checked"))
    units_dir = ctx.input_dir / UNITS_DIR
    for path in sorted(units_dir.glob("*.json")) if units_dir.is_dir() else []:
        data, err = _load_json_safe(path, f"{STAGE}.units_invalid", f"{UNITS_DIR}/{path.name}")
        if err is not None:
            ctx.findings.append(err)
            continue
        if isinstance(data, dict) and isinstance(data.get("units"), list):
            for u in data["units"]:
                if u.get("id"):
                    ctx.units[u["id"]] = u
    if not ctx.units:
        ctx.findings.append(Finding(f"{STAGE}.units_missing", "warning", f"{UNITS_DIR}/",
                                    "no units files found — source_quote checks are skipped"))
    return ctx


def _pages_of(lesson: dict) -> list[dict]:
    pages = [p for p in (lesson.get("pages") or []) if isinstance(p, dict)]
    return sorted(pages, key=lambda p: p.get("index", 0))


def _rubric_text() -> str | None:
    try:
        return RUBRIC_PATH.read_text(encoding="utf-8")
    except OSError:
        return None


# ---------------------------------------------------------------------------
# prepare
# ---------------------------------------------------------------------------


def _prepare_lesson(ctx: Context, lesson_id: str, allow_edge: bool) -> tuple[dict | None, list[Finding]]:
    group = ctx.groups.get(lesson_id)
    if group is None:
        return None, [Finding(f"{STAGE}.unknown_lesson", "error", lesson_id, "no group with this id in groups.json")]
    lesson = ctx.load_lesson(lesson_id)
    if lesson is None:
        return None, [Finding(f"{STAGE}.lesson_missing", "error", lesson_id, "lessons/<item>.json not found or invalid")]
    pages = _pages_of(lesson)
    if not pages or any(p.get("markdown") is None for p in pages):
        return None, [Finding(f"{STAGE}.lesson_outline_only", "error", lesson_id,
                              "lesson pages have no markdown yet — finish lessons.py before designing questions")]
    brief = ctx.load_synthesis(lesson_id) or {}
    rubric = _rubric_text()
    findings: list[Finding] = []
    if rubric is None:
        findings.append(Finding(f"{STAGE}.rubric_missing", "warning", str(RUBRIC_PATH),
                                "rubric not found — work file carries a fallback note under instructions"))

    unit_scope = list(dict.fromkeys([*(group.get("unit_ids") or []), *(group.get("related_unit_ids") or [])]))
    units = [{"id": uid, "text": (ctx.units.get(uid) or {}).get("text", ""),
              "section_title": (ctx.units.get(uid) or {}).get("section_title", "")}
             for uid in unit_scope if uid in ctx.units]
    prereqs = []
    for p in ctx.prerequisites(group):
        pg = ctx.groups.get(p.get("group_id"), {})
        pl = ctx.load_lesson(p.get("group_id", "")) or {}
        recap = next((pp.get("markdown") for pp in _pages_of(pl) if pp.get("role") == "recap"), None)
        prereqs.append({"group_id": p.get("group_id"), "title": pl.get("title") or pg.get("title"),
                        "strength": p.get("strength"), "status": p.get("status") or "proposed",
                        "recap_markdown": recap})
    existing_path = ctx.input_dir / QUESTIONS_DIR / item_id_to_filename(lesson_id)
    existing = None
    if existing_path.exists():
        existing, err = _load_json_safe(existing_path, f"{STAGE}.invalid_json", f"{QUESTIONS_DIR}/{existing_path.name}")
        if err is not None:
            findings.append(err)
    lesson_markdown = "\n\n".join(p.get("markdown") or "" for p in pages)
    existing = existing if isinstance(existing, dict) else None
    work = {
        "contract_version": CONTRACT_VERSION,
        "stage": STAGE,
        "lesson_id": lesson_id,
        "group_id": group.get("id"),
        "title": lesson.get("title") or group.get("title"),
        "revision": _next_revision(existing),
        "pages": [{"index": p.get("index"), "role": p.get("role"), "title": p.get("title"),
                   "markdown": p.get("markdown"), "lo_ids": p.get("lo_ids") or [],
                   "knowledge_point_id": p.get("knowledge_point_id")} for p in pages],
        "lesson_words": word_count(lesson_markdown),
        "lesson_tokens": count_tokens(lesson_markdown),
        "objectives": group.get("objectives") or [],
        "knowledge_points": group.get("knowledge_points") or [],
        "watch": group.get("watch") or [],
        "distractor_seeds": [w.get("text") for w in (group.get("watch") or []) if w.get("kind") == "misconception"],
        "generalization": group.get("generalization"),
        "budget": ctx.budget(group),
        "concepts": [{"id": cid, "label": (ctx.concepts.get(cid) or {}).get("label"),
                      "aliases": (ctx.concepts.get(cid) or {}).get("aliases") or [],
                      "description": (ctx.concepts.get(cid) or {}).get("description")}
                     for cid in group.get("concept_ids") or []],
        "brief_items": [{"id": b.get("id"), "kind": b.get("kind"), "text": b.get("text"),
                         "unit_ids": b.get("unit_ids") or [], "quote": b.get("quote"),
                         "lo_ids": b.get("lo_ids") or [], "knowledge_point_id": b.get("knowledge_point_id")}
                        for b in brief.get("items") or []],
        "units": units,
        "prerequisites": prereqs,
        "allowed_slots": compute_slots(len(pages), allow_edge),
        "allow_edge_questions": allow_edge,
        "default_position": dict(EMBEDDED_DEFAULT_POSITION),
        "instructions": rubric if rubric is not None else RUBRIC_MISSING_NOTE,
        "existing": existing,
        "lessons_learned": (existing or {}).get("lessons_learned") or [],
    }
    return work, findings


def _next_revision(existing: dict | None) -> int:
    """Round number for the next authoring pass: previous revision + 1, else 0."""
    prev = (existing or {}).get("revision")
    return prev + 1 if isinstance(prev, int) and not isinstance(prev, bool) else 0


def prepare(input_dir: Path, lesson_ids: list[str] | None = None, force: bool = False,
            allow_edge_questions: bool = False) -> tuple[list[Path], list[Finding]]:
    """Write questions/work/<item>.json per lesson. Returns (written paths, findings)."""
    ctx = load_context(Path(input_dir))
    findings = list(ctx.findings)
    if not ctx.groups:
        return [], findings
    written: list[Path] = []
    for lesson_id in lesson_ids or ctx.lesson_ids():
        target = ctx.input_dir / QUESTIONS_DIR / WORK_SUBDIR / item_id_to_filename(lesson_id)
        if target.exists() and not force:
            findings.append(Finding(f"{STAGE}.work_exists", "info", lesson_id, f"{target.name} exists — use --force to overwrite"))
            continue
        work, lesson_findings = _prepare_lesson(ctx, lesson_id, allow_edge_questions)
        findings.extend(lesson_findings)
        if work is not None:
            dump_json(target, work)
            written.append(target)
    return written, findings


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------


@dataclass
class _Scope:
    """Resolution scope for one lesson's items."""

    lesson_id: str
    objectives: dict[str, dict]
    concept_ids: set[str]
    unit_ids: set[str]
    n_pages: int
    page_text: dict[int, str]
    prereq_ids: list[str]


def _valid_index(value: object, size: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value < size


def _err(check: str, loc: str, msg: str) -> Finding:
    return Finding(f"{STAGE}.{check}", "error", loc, msg)


def _warn(check: str, loc: str, msg: str) -> Finding:
    return Finding(f"{STAGE}.{check}", "warning", loc, msg)


def _info(check: str, loc: str, msg: str) -> Finding:
    return Finding(f"{STAGE}.{check}", "info", loc, msg)


def _check_id(qid: object, kind: str, lesson_id: str, seen: set[str], fallback: str) -> tuple[str, list[Finding]]:
    """Validate a sub-item id; returns (location label, findings)."""
    if not isinstance(qid, str) or not qid:
        return fallback, [_err("id_format", fallback, "missing id")]
    m = SUBITEM_ID_RE.match(qid)
    if not m or m.group("kind") != kind or m.group("variant") is not None or qid.split("#")[0] != lesson_id:
        return qid, [_err("id_format", qid, f"expected {lesson_id}#{kind}<n>")]
    if qid in seen:
        return qid, [_err("duplicate_id", qid, "id used more than once")]
    seen.add(qid)
    return qid, []


def _check_references(item: dict, loc: str, scope: _Scope, ctx: Context, *, extra_concepts: set[str] = frozenset(),
                      extra_units: set[str] = frozenset(), lo_optional: bool = False) -> list[Finding]:
    """lo_id / concept_id / unit_ids / page_index / source_quote resolution."""
    out: list[Finding] = []
    lo = item.get("lo_id")
    if not (lo is None and lo_optional) and lo not in scope.objectives:
        out.append(_err("unknown_lo", loc, f"lo_id {lo!r} is not one of the lesson's objectives"))
    cid = item.get("concept_id")
    if cid not in scope.concept_ids | set(extra_concepts):
        out.append(_err("unknown_concept", loc, f"concept_id {cid!r} is not in the lesson's group"))
    unit_ids = item.get("unit_ids")
    if not isinstance(unit_ids, list) or not unit_ids:
        out.append(_err("missing_field", loc, "unit_ids must be a non-empty list"))
        unit_ids = []
    allowed = scope.unit_ids | set(extra_units)
    for uid in unit_ids:
        if uid not in allowed:
            out.append(_err("unknown_unit", loc, f"unit {uid!r} is not in the group's units ∪ related units"))
        elif ctx.units and uid not in ctx.units:
            out.append(_err("unknown_unit", loc, f"unit {uid!r} is cited but no units file defines it"))
    pi = item.get("page_index")
    if not _valid_index(pi, scope.n_pages):
        out.append(_err("page_index", loc, f"page_index {pi!r} outside [0, {scope.n_pages - 1}]"))
    quote = item.get("source_quote")
    if not normalize_ws(quote or ""):
        out.append(_warn("quote_missing", loc, "source_quote is empty"))
    elif ctx.units:
        cited = [ctx.units[u].get("text", "") for u in unit_ids if u in ctx.units]
        if cited and not any(quote_in_unit(quote, t) for t in cited):
            out.append(_err("quote_mismatch", loc, "source_quote is not a verbatim substring of any cited unit"))
    return out


def _check_prompt(item: dict, loc: str, ctx: Context, *, key_answer: str | None, needs_causal: bool,
                  yes_no_applies: bool, question_required: bool) -> list[Finding]:
    """Prompt hygiene shared by embedded and quiz items."""
    out: list[Finding] = []
    prompt = item.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return [_err("missing_field", loc, "prompt is empty")]
    n = word_count(prompt)
    lo, hi = QUESTION_PROMPT_WORDS
    if not lo <= n <= hi:
        out.append(_err("prompt_length", loc, f"prompt has {n} words (allowed {lo}-{hi})"))
    if question_required and not is_question(prompt):
        out.append(_warn("not_a_question", loc, "prompt neither ends with '?' nor opens with an imperative"))
    if prompt.count("?") > 1:
        out.append(_warn("multiple_interrogatives", loc, f"prompt contains {prompt.count('?')} '?' — ask one bounded question"))
    if key_answer and answer_leaks(prompt, key_answer):
        out.append(_err("answer_in_stem", loc, "keyed answer appears verbatim in the prompt"))
    quote = item.get("source_quote") or ""
    run = longest_common_run(words(prompt), words(quote))
    if run >= QUOTE_COPY_CONSECUTIVE_WORDS:
        out.append(_err("quote_copy", loc, f"stem copies {run} consecutive words of source_quote"))
    cid = item.get("concept_id")
    if cid in ctx.concepts and not mentions_any(prompt, ctx.concept_labels(cid)):
        out.append(_warn("concept_missing", loc, f"neither the label nor an alias of {cid} appears in the prompt"))
    if needs_causal and not CAUSAL_CUE_RE.search(prompt):
        out.append(_err("no_causal_cue", loc, "predict / apply+ prompts need a causal cue (why, what happens, compare, …)"))
    for pat in LITERAL_RECALL_PATTERNS:
        if pat.search(prompt):
            out.append(_err("literal_recall", loc, f"literal-recall phrasing {pat.search(prompt).group(0)!r}"))
            break
    if yes_no_applies and YES_NO_STEM_RE.match(prompt):
        out.append(_err("yes_no_stem", loc, "yes/no stem — ask for the outcome and its reason instead"))
    return out


def _check_key(item: dict, loc: str) -> list[Finding]:
    """acceptance {prediction, causal_reason} and probes."""
    out: list[Finding] = []
    acc = item.get("acceptance")
    if not isinstance(acc, dict):
        out.append(_err("acceptance", loc, "acceptance must be an object with prediction and causal_reason"))
    else:
        for k in ("prediction", "causal_reason"):
            v = acc.get(k)
            if not isinstance(v, str) or not v.strip():
                out.append(_err("acceptance", loc, f"acceptance.{k} is empty"))
            elif len(v) > ACCEPTANCE_MAX_CHARS:
                out.append(_err("acceptance", loc, f"acceptance.{k} has {len(v)} chars (max {ACCEPTANCE_MAX_CHARS})"))
    probes = item.get("probes")
    if not isinstance(probes, dict) or not all(
        isinstance(probes.get(k), str) and probes[k].strip() for k in ("correct_learner_answer", "wrong_learner_answer")
    ):
        out.append(_err("probes", loc, "probes need non-empty correct_learner_answer and wrong_learner_answer"))
    return out


def _bloom(item: dict, loc: str) -> tuple[str | None, list[Finding]]:
    b = item.get("bloom_level")
    if b not in BLOOM_LEVELS:
        return None, [_err("enum", loc, f"bloom_level {b!r} not in {list(BLOOM_LEVELS)}")]
    return b, []


def _check_embedded(embedded: list, scope: _Scope, ctx: Context, budget: dict, allow_edge: bool,
                    seen: set[str], quotes: list[tuple[str, str]]) -> list[Finding]:
    out: list[Finding] = []
    slots = compute_slots(scope.n_pages, allow_edge)
    slot_use: dict[tuple[int, str], list[str]] = {}
    reachable = 0
    for i, q in enumerate(embedded):
        if not isinstance(q, dict):
            out.append(_err("missing_field", f"embedded[{i}]", "item is not an object"))
            continue
        loc, id_findings = _check_id(q.get("id"), "q", scope.lesson_id, seen, f"embedded[{i}]")
        out.extend(id_findings)
        kind = q.get("kind")
        if kind not in EMBEDDED_KINDS:
            out.append(_err("enum", loc, f"kind {kind!r} not in {list(EMBEDDED_KINDS)}"))
            kind = None
        position = q.get("position")
        if position not in QUESTION_POSITIONS:
            out.append(_err("enum", loc, f"position {position!r} not in {list(QUESTION_POSITIONS)}"))
            position = None
        elif kind and position != EMBEDDED_DEFAULT_POSITION[kind]:
            out.append(_err("position_kind", loc, f"{kind} questions go {EMBEDDED_DEFAULT_POSITION[kind]!r} the page, not {position!r}"))
        out.extend(_check_references(q, loc, scope, ctx))
        pi = q.get("page_index")
        page_ok = _valid_index(pi, scope.n_pages)
        if page_ok and position:
            if pi not in slots[position]:
                out.append(_err("unreachable", loc, f"position {position!r} on page {pi} is never shown by the viewer "
                                                     f"(allowed pages: {slots[position]})"))
            else:
                reachable += 1
                slot_use.setdefault((pi, position), []).append(loc)
        bloom, b_findings = _bloom(q, loc)
        out.extend(b_findings)
        needs_causal = kind == "predict" or (bloom is not None and BLOOM_ORDER[bloom] >= BLOOM_ORDER["apply"])
        expected = q.get("expected_answer")
        if not isinstance(expected, str) or not expected.strip():
            out.append(_err("missing_field", loc, "expected_answer is empty"))
            expected = None
        out.extend(_check_prompt(q, loc, ctx, key_answer=expected, needs_causal=needs_causal,
                                 yes_no_applies=kind in ("predict", "check"), question_required=True))
        out.extend(_check_key(q, loc))
        if kind == "check" and position == "after" and page_ok and expected and is_answer_on_page(scope.page_text.get(pi, ""), expected):
            out.append(_warn("answer_on_page", loc, "expected_answer appears verbatim on the page the learner just read — "
                                                   "this tests reading comprehension, not understanding"))
        if q.get("source_quote"):
            quotes.append((loc, q["source_quote"]))
    for (pi, position), locs in slot_use.items():
        if len(locs) > EMBEDDED_PER_PAGE_MAX:
            out.append(_err("slot_overflow", ", ".join(locs), f"{len(locs)} questions {position!r} page {pi} (max {EMBEDDED_PER_PAGE_MAX})"))
    cap = budget.get("embedded_questions_max")
    if isinstance(cap, int) and len(embedded) > cap:
        out.append(_err("embedded_budget", scope.lesson_id, f"{len(embedded)} embedded questions exceed budget {cap}"))
    minimum = max(1, budget.get("embedded_questions_min") or 1)
    if reachable < minimum:
        out.append(_err("none_reachable", scope.lesson_id, f"{reachable} reachable embedded question(s); every lesson needs >= {minimum}"))
    return out


def _check_mcq(q: dict, loc: str, qtype: str) -> tuple[str | None, list[Finding]]:
    """Options / correct_index / distractors / flaws. Returns (key text, findings)."""
    out: list[Finding] = []
    options = q.get("options")
    ci = q.get("correct_index")
    if qtype == "true_false":
        if options != TRUE_FALSE_OPTIONS:
            out.append(_err("mcq_options", loc, f"true_false options must be exactly {TRUE_FALSE_OPTIONS}"))
            options = None
    elif not isinstance(options, list) or len(options) != MCQ_OPTIONS or not all(isinstance(o, str) and o.strip() for o in options):
        out.append(_err("mcq_options", loc, f"multiple_choice needs exactly {MCQ_OPTIONS} non-empty options"))
        options = None
    if options is None:
        return None, out
    if not _valid_index(ci, len(options)):
        out.append(_err("correct_index", loc, f"correct_index {ci!r} outside [0, {len(options) - 1}]"))
        return None, out
    if qtype == "multiple_choice":
        covered = {d.get("index") for d in (q.get("distractors") or []) if isinstance(d, dict) and (d.get("misconception") or "").strip()}
        missing = [i for i in range(len(options)) if i != ci and i not in covered]
        if missing:
            out.append(_warn("distractors_incomplete", loc, f"options {missing} have no misconception recorded in distractors"))
    flaws = mcq_flaws(q.get("prompt") or "", options, ci, true_false=qtype == "true_false")
    if len(flaws) > MCQ_FLAWS_MAX:
        out.append(_err("mcq_flaws", loc, f"{len(flaws)} item-writing flaws (max {MCQ_FLAWS_MAX}): " + "; ".join(flaws)))
    elif flaws:
        out.append(_info("mcq_flaws", loc, "1 item-writing flaw: " + flaws[0]))
    return None if qtype == "true_false" else options[ci], out


def _check_variants(q: dict, loc: str, qtype: str) -> list[Finding]:
    out: list[Finding] = []
    for j, v in enumerate(q.get("variants") or []):
        if not isinstance(v, dict):
            out.append(_err("variant_id", f"{loc} variants[{j}]", "variant is not an object"))
            continue
        vid = v.get("id") if isinstance(v.get("id"), str) else f"{loc} variants[{j}]"
        if not re.fullmatch(re.escape(loc) + r"v\d+", vid):
            out.append(_err("variant_id", vid, f"variant id must be {loc}v<n>"))
        if variant_too_similar(q.get("prompt"), v.get("prompt")):
            out.append(_err("variant_too_similar", vid, f"variant prompt is a surface rewrite of the original "
                                                       f"(normalized ratio >= {VARIANT_SIMILARITY_MAX} or digits/names only)"))
        if qtype not in ("multiple_choice", "true_false"):
            continue
        options, ci = v.get("options"), v.get("correct_index")
        if qtype == "true_false":
            valid = options == TRUE_FALSE_OPTIONS
        else:
            valid = isinstance(options, list) and len(options) == MCQ_OPTIONS and all(isinstance(o, str) and o.strip() for o in options)
        if not valid:
            out.append(_err("mcq_options", vid, f"variant options must match the {qtype} shape"))
        elif not _valid_index(ci, len(options)):
            out.append(_err("correct_index", vid, f"correct_index {ci!r} outside [0, {len(options) - 1}]"))
        elif qtype == "multiple_choice" and answer_leaks(v.get("prompt"), options[ci]):
            out.append(_err("answer_in_stem", vid, "variant's keyed option appears verbatim in its prompt"))
    return out


def _check_quiz(quiz: list, scope: _Scope, ctx: Context, budget: dict, seen: set[str],
                quotes: list[tuple[str, str]]) -> list[Finding]:
    out: list[Finding] = []
    covered: set[str] = set()
    blooms: set[str] = set()
    for i, q in enumerate(quiz):
        if not isinstance(q, dict):
            out.append(_err("missing_field", f"quiz[{i}]", "item is not an object"))
            continue
        loc, id_findings = _check_id(q.get("id"), "q", scope.lesson_id, seen, f"quiz[{i}]")
        out.extend(id_findings)
        qtype = q.get("type")
        if qtype not in QUIZ_TYPES:
            out.append(_err("enum", loc, f"type {qtype!r} not in {list(QUIZ_TYPES)}"))
            qtype = None
        recap_of = q.get("recap_of")
        extra_concepts: set[str] = set()
        extra_units: set[str] = set()
        if recap_of is not None:
            if recap_of not in scope.prereq_ids:
                out.append(_err("recap_of", loc, f"recap_of {recap_of!r} is not a committed prerequisite of this lesson "
                                                 f"(accepted: {scope.prereq_ids})"))
            pg = ctx.groups.get(recap_of) or {}
            extra_concepts = set(pg.get("concept_ids") or [])
            extra_units = set(pg.get("unit_ids") or []) | set(pg.get("related_unit_ids") or [])
        out.extend(_check_references(q, loc, scope, ctx, extra_concepts=extra_concepts, extra_units=extra_units,
                                     lo_optional=recap_of is not None))
        if recap_of is None and q.get("lo_id") in scope.objectives:
            covered.add(q["lo_id"])
        bloom, b_findings = _bloom(q, loc)
        out.extend(b_findings)
        if bloom:
            blooms.add(bloom)
            if q.get("mastery_level") != BLOOM_TO_MASTERY[bloom]:
                out.append(_err("mastery_level", loc, f"mastery_level {q.get('mastery_level')!r} != BLOOM_TO_MASTERY[{bloom}] = {BLOOM_TO_MASTERY[bloom]}"))
        key: str | None = None
        if qtype in ("multiple_choice", "true_false"):
            key, mcq_findings = _check_mcq(q, loc, qtype)
            out.extend(mcq_findings)
        elif qtype is not None:
            if "options" in q or "correct_index" in q:
                out.append(_warn("extraneous_field", loc, f"{qtype} items omit options/correct_index"))
            key = q.get("expected_answer") if isinstance(q.get("expected_answer"), str) else None
            if not key or not key.strip():
                out.append(_err("missing_field", loc, "expected_answer is empty"))
                key = None
        needs_causal = bloom is not None and BLOOM_ORDER[bloom] >= BLOOM_ORDER["apply"]
        out.extend(_check_prompt(q, loc, ctx, key_answer=key, needs_causal=needs_causal, yes_no_applies=False,
                                 question_required=qtype != "true_false"))
        out.extend(_check_key(q, loc))
        out.extend(_check_variants(q, loc, qtype or ""))
        if q.get("source_quote"):
            quotes.append((loc, q["source_quote"]))
    for lo_id in scope.objectives:
        if lo_id not in covered:
            out.append(_err("objective_uncovered", lo_id, "no quiz item targets this objective"))
    need = budget.get("quiz_min_bloom_levels") or 0
    if len(blooms) < need:
        out.append(_err("bloom_span", scope.lesson_id, f"quiz spans {len(blooms)} Bloom level(s) {sorted(blooms)}; needs >= {need}"))
    cap = budget.get("quiz_items")
    if isinstance(cap, int) and len(quiz) > cap + 1:
        out.append(_warn("quiz_budget", scope.lesson_id, f"{len(quiz)} quiz items exceed budget {cap} (+1 recap)"))
    return out


def _check_exercises(exercises: list, scope: _Scope, ctx: Context, budget: dict, seen: set[str]) -> list[Finding]:
    out: list[Finding] = []
    last_fading = -1
    for i, e in enumerate(exercises):
        if not isinstance(e, dict):
            out.append(_err("missing_field", f"exercises[{i}]", "item is not an object"))
            continue
        loc, id_findings = _check_id(e.get("id"), "e", scope.lesson_id, seen, f"exercises[{i}]")
        out.extend(id_findings)
        for k in ("title", "problem_statement", "expected_solution"):
            if not isinstance(e.get(k), str) or not e[k].strip():
                out.append(_err("missing_field", loc, f"{k} is empty"))
        hints = e.get("hints")
        if not isinstance(hints, list) or len(hints) != EXERCISE_HINTS or not all(isinstance(h, str) and h.strip() for h in hints):
            out.append(_err("exercise_hints", loc, f"exactly {EXERCISE_HINTS} progressive hints required"))
        fading = e.get("fading")
        if fading not in EXERCISE_FADING:
            out.append(_err("enum", loc, f"fading {fading!r} not in {list(EXERCISE_FADING)}"))
        else:
            idx = EXERCISE_FADING.index(fading)
            if idx < last_fading:
                out.append(_err("fading_order", loc, f"fading {fading!r} after a later stage — order is {' → '.join(EXERCISE_FADING)}"))
            last_fading = max(last_fading, idx)
        out.extend(_bloom(e, loc)[1])
        d = e.get("difficulty")
        if not isinstance(d, int) or isinstance(d, bool) or not DIFFICULTY_RANGE[0] <= d <= DIFFICULTY_RANGE[1]:
            out.append(_warn("difficulty", loc, f"difficulty {d!r} should be an int in {DIFFICULTY_RANGE}"))
        if e.get("lo_id") not in scope.objectives:
            out.append(_err("unknown_lo", loc, f"lo_id {e.get('lo_id')!r} is not one of the lesson's objectives"))
        unit_ids = e.get("unit_ids")
        if not isinstance(unit_ids, list) or not unit_ids:
            out.append(_err("missing_field", loc, "unit_ids must be a non-empty list"))
        else:
            for uid in unit_ids:
                if uid not in scope.unit_ids:
                    out.append(_err("unknown_unit", loc, f"unit {uid!r} is not in the group's units ∪ related units"))
    cap = budget.get("exercises_max")
    if isinstance(cap, int) and len(exercises) > cap:
        out.append(_err("exercise_budget", scope.lesson_id, f"{len(exercises)} exercises exceed budget {cap}"))
    return out


def _check_quote_overlap(quotes: list[tuple[str, str]]) -> list[Finding]:
    out: list[Finding] = []
    toks = [(loc, words(q)) for loc, q in quotes]
    for i in range(len(toks)):
        for j in range(i + 1, len(toks)):
            run = longest_common_run(toks[i][1], toks[j][1])
            if run >= QUOTE_COPY_CONSECUTIVE_WORDS:
                out.append(_warn("quote_overlap", f"{toks[i][0]}, {toks[j][0]}",
                                 f"source_quotes share {run} consecutive words — cite distinct evidence"))
    return out


def _check_lesson(ctx: Context, lesson_id: str, allow_edge: bool) -> list[Finding]:
    path = ctx.input_dir / QUESTIONS_DIR / item_id_to_filename(lesson_id)
    rel = f"{QUESTIONS_DIR}/{path.name}"
    if not path.exists():
        return [_err("file_missing", lesson_id, f"{rel} not found")]
    data, err = _load_json_safe(path, f"{STAGE}.invalid_json", rel)
    if err is not None:
        return [err]
    if not isinstance(data, dict):
        return [_err("invalid_json", rel, "top level must be an object")]
    out: list[Finding] = []
    group = ctx.groups.get(lesson_id)
    if group is None:
        return [_err("unknown_lesson", lesson_id, "no group with this id in groups.json")]
    if data.get("contract_version") != CONTRACT_VERSION:
        out.append(_warn("contract_version", rel, f"contract_version {data.get('contract_version')!r} != {CONTRACT_VERSION}"))
    if data.get("lesson_id") != lesson_id:
        out.append(_err("lesson_mismatch", rel, f"lesson_id {data.get('lesson_id')!r} does not match the file name"))
    lesson = ctx.load_lesson(lesson_id)
    if lesson is None:
        out.append(_err("lesson_missing", lesson_id, "lessons/<item>.json not found or invalid — cannot resolve pages"))
        return out
    pages = _pages_of(lesson)
    if any(p.get("markdown") is None for p in pages):
        out.append(_warn("lesson_outline_only", lesson_id, "some pages have no markdown — answer-on-page checks are partial"))
    scope = _Scope(
        lesson_id=lesson_id,
        objectives={o["id"]: o for o in (group.get("objectives") or []) if o.get("id")},
        concept_ids=set(group.get("concept_ids") or []),
        unit_ids=set(group.get("unit_ids") or []) | set(group.get("related_unit_ids") or []),
        n_pages=len(pages),
        page_text={p.get("index", i): p.get("markdown") or "" for i, p in enumerate(pages)},
        prereq_ids=[p.get("group_id") for p in ctx.prerequisites(group)],
    )
    budget = ctx.budget(group)
    seen: set[str] = set()
    quotes: list[tuple[str, str]] = []
    for key in ("embedded", "quiz", "exercises"):
        if not isinstance(data.get(key), list):
            out.append(_err("missing_field", rel, f"{key} must be a list"))
    out.extend(_check_embedded(data.get("embedded") if isinstance(data.get("embedded"), list) else [], scope, ctx, budget, allow_edge, seen, quotes))
    out.extend(_check_quiz(data.get("quiz") if isinstance(data.get("quiz"), list) else [], scope, ctx, budget, seen, quotes))
    out.extend(_check_exercises(data.get("exercises") if isinstance(data.get("exercises"), list) else [], scope, ctx, budget, seen))
    out.extend(_check_quote_overlap(quotes))
    return out


def check(input_dir: Path, allow_edge_questions: bool = False, lesson_ids: list[str] | None = None) -> list[Finding]:
    """Whole stage: every authored questions file, plus lessons that still lack one."""
    ctx = load_context(Path(input_dir))
    findings = list(ctx.findings)
    if not ctx.groups:
        return findings
    authored = {filename_to_item_id(p.name) for p in list_item_files(ctx.input_dir / QUESTIONS_DIR)}
    targets = lesson_ids or ctx.lesson_ids()
    for lesson_id in targets:
        if lesson_id in authored or lesson_ids:
            findings.extend(_check_lesson(ctx, lesson_id, allow_edge_questions))
        else:
            findings.append(_warn("missing", lesson_id, f"no {QUESTIONS_DIR}/{item_id_to_filename(lesson_id)} yet"))
    if not lesson_ids:
        for stray in sorted(authored - set(targets)):
            findings.extend(_check_lesson(ctx, stray, allow_edge_questions))
    return findings


def check_item(input_dir: Path, item_id: str, allow_edge_questions: bool = False) -> list[Finding]:
    """One lesson (g:slug) or one sub-item (g:slug#q3, findings filtered to it)."""
    lesson_id, _, sub = item_id.partition("#")
    ctx = load_context(Path(input_dir))
    findings = [f for f in ctx.findings if f.severity == "error"]
    if not ctx.groups:
        return findings
    findings.extend(_check_lesson(ctx, lesson_id, allow_edge_questions))
    if sub:
        findings = [f for f in findings if item_id in f.location.split(", ")]
    return findings


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

_SEVERITY_TAG = {"error": "[ERROR]", "warning": "[WARN]", "info": "[INFO]"}
_SEVERITY_RANK = {"error": 0, "warning": 1, "info": 2}


def print_findings(findings: list[Finding]) -> tuple[int, int]:
    for f in sorted(findings, key=lambda f: _SEVERITY_RANK.get(f.severity, 3)):
        print(f"{_SEVERITY_TAG.get(f.severity, '[INFO]')} {f.check_id} @ {f.location}: {f.message}")
    errors = sum(1 for f in findings if f.severity == "error")
    warnings = sum(1 for f in findings if f.severity == "warning")
    print(f"{errors} errors, {warnings} warnings")
    return errors, warnings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stage 6 — design questions: prepare work files / check authored files")
    sub = parser.add_subparsers(dest="command", required=True)
    p_prep = sub.add_parser("prepare", help="write questions/work/<item>.json per lesson")
    p_prep.add_argument("input_dir", type=Path)
    p_prep.add_argument("--lesson", action="append", default=None, metavar="g:ID", help="only this lesson (repeatable)")
    p_prep.add_argument("--force", action="store_true", help="overwrite existing work files")
    p_prep.add_argument("--allow-edge-questions", action="store_true", help="allow before/after slots on the first/last page")
    p_check = sub.add_parser("check", help="validate questions/<item>.json")
    p_check.add_argument("input_dir", type=Path)
    p_check.add_argument("--lesson", action="append", default=None, metavar="g:ID", help="only this lesson (repeatable)")
    p_check.add_argument("--allow-edge-questions", action="store_true")
    p_check.add_argument("--strict", action="store_true", help="warnings fail too")
    args = parser.parse_args(argv)

    if not args.input_dir.is_dir():
        print(f"error: {args.input_dir} is not a directory", file=sys.stderr)
        return 2
    if args.command == "prepare":
        written, findings = prepare(args.input_dir, args.lesson, args.force, args.allow_edge_questions)
        for path in written:
            print(f"wrote {path.relative_to(args.input_dir)}")
        errors, _ = print_findings(findings)
        return 1 if errors else 0
    findings = check(args.input_dir, args.allow_edge_questions, args.lesson)
    errors, warnings = print_findings(findings)
    if errors or (args.strict and warnings):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
