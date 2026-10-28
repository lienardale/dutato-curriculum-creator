"""
Stage 7 of the lesson pipeline — DESIGN CARDS.

PURE DATA UTILITY — no AI calls. The operating agent (or its sub-agents) writes
``cards/<item>.json`` (shape in docs/artifacts.md "Stage 7"); this script
prepares the inputs it needs and checks the result with deterministic gates.

Subcommands:

  prepare  Writes one work file per lesson to ``cards/work/<item>.json``: the
           lesson pages, the group's objectives / knowledge points /
           generalization / watch items, the brief's terminology and map items,
           the card budget (cards_min, cards_max and the one-card-per-100-words
           ceiling), knowledge-level counts of the lesson's units with the card
           types CARD_TYPES_BY_KNOWLEDGE suggests for each, the lens checklist,
           the prompts of the lesson's questions (a card must not restate one),
           a do-not-duplicate list of fronts from neighbouring lessons, the
           previous revision (if any) with its lessons_learned, and the
           authoring rubric (rubrics/cards.md) under ``instructions``.
  check    Validates ``cards/<item>.json``: ids, enums, lo/unit/page
           resolution, count and type budgets, objective coverage, front/back
           caps and hygiene, overlap and answer leaks, shared answers,
           cloze/ordering/discrimination shapes, and near-duplicate fronts
           within the lesson and against neighbouring lessons. Findings print
           as ``[ERROR] <check_id> @ <location>: <msg>`` followed by
           ``<n> errors, <m> warnings``.

Neighbours (do-not-duplicate list and dedup gate) are the lessons joined to
this one by an accepted prerequisite edge in either direction plus the lessons
of the same module. Code cards are exempt from the front word cap and the
compound-front heuristic; comparison cards are exempt from the latter because
naming two terms with "and" is their shape.

Usage:
    python cards.py prepare output/<name>/ [--lesson g:ID ...] [--force]
    python cards.py check   output/<name>/ [--lesson g:ID ...] [--strict]

Module API (used by validate.py / critique.py):
    check(input_dir, lesson_ids=None) -> list[Finding]
    check_item(input_dir, item_id) -> list[Finding]
        item_id is a lesson id (g:slug) or a card id (g:slug#k3) — the latter
        filters the lesson's findings to that card.
    card_ceiling(lesson_words, budget) -> int
    is_compound_front(front) -> bool
    front_back_overlap(front, back) -> float

Exit codes: 0 clean, 1 errors (or warnings with --strict), 2 usage error.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

import _compat  # noqa: F401

from chunk import count_tokens
from contracts import (
    CARD_BACK_MAX_WORDS,
    CARD_DEDUP_BAND,
    CARD_DEDUP_RATIO,
    CARD_FRONT_MAX_WORDS,
    CARD_LENSES,
    CARD_MIN_TYPES,
    CARD_OVERLAP_MAX,
    CARD_TYPES,
    CARD_TYPES_BY_KNOWLEDGE,
    CARDS_DIR,
    CARDS_PER_100_WORDS_MAX,
    CARDS_RANGE,
    CLOZE_HINT_MAX_WORDS,
    CLOZE_SENTENCE_MAX_WORDS,
    COMPOUND_FRONT_RE,
    CONCEPTS_FILE,
    CONTRACT_VERSION,
    GIVE_EXAMPLE_FRONT_RE,
    GROUP_ID_RE,
    GROUPS_FILE,
    KNOWLEDGE_LEVELS,
    LESSONS_DIR,
    OBSERVABLE_VERBS,
    ORDERING_ITEMS_MAX,
    PRONOUN_ONLY_FRONT_RE,
    QUALIFICATION_DIR,
    QUESTIONS_DIR,
    STOPWORDS,
    SUBITEM_ID_RE,
    SYNTHESES_DIR,
    UNITS_DIR,
    WORK_SUBDIR,
    YES_NO_STEM_RE,
    Finding,
    budget_for,
    content_words,
    dump_json,
    filename_to_item_id,
    item_id_to_filename,
    list_item_files,
    load_json,
    parse_unit_id,
    word_count,
)

STAGE = "cards"
RUBRIC_PATH = Path(__file__).resolve().parent / "rubrics" / "cards.md"
# Stands in for the rubric in work files when rubrics/cards.md is missing.
RUBRIC_MISSING_NOTE = (
    "rubrics/cards.md not found — follow docs/artifacts.md Stage 7: one proposition per card, >= 3 card types, "
    "every objective covered, fronts <= 25 words, backs within CARD_BACK_MAX_WORDS, two lenses on the "
    "generalization, and `python cards.py check` must be clean."
)

# Lens checklist handed to the author: the five lenses a generalization is viewed
# through, plus the two lenses that serve facts and procedures. Ids are CARD_LENSES.
LENS_CHECKLIST = (
    ("attributes", "generalization", "What defines it? Which defining attribute would a learner most likely drop?"),
    ("similarities_differences", "generalization", "What is it most easily confused with, and which single feature separates the two?"),
    ("parts_wholes", "generalization", "What are its parts, or what larger whole is it a part of?"),
    ("causes_effects", "generalization", "What brings it about, and what changes when it is present or missing?"),
    ("significance", "generalization", "Why does it matter — what breaks or becomes possible because of it?"),
    ("recall", "fact", "Which single fact must be retrievable on demand (term, value, name of a part)?"),
    ("procedure", "procedure", "Which step comes next, or what is the condition for taking a step?"),
)

CLOZE_BLANK_RE = re.compile(r"_{3,}")
COMPARISON_PAIR_RE = re.compile(r"\b(and|vs\.?|versus|between)\b", re.IGNORECASE)
HINT_PREFIX_LEN = 5            # hint and answer must not share a word prefix this long
ORDERING_ITEMS_MIN = 2
DISCRIMINATION_OPTIONS = (3, 4)

# Verb lexicon for the compound-front heuristic: both sides of an "and" must read
# as clauses (contain a verb) before the front counts as two propositions.
AUXILIARY_VERBS = {
    "is", "are", "was", "were", "be", "been", "being", "does", "do", "did", "has", "have", "had",
    "can", "could", "should", "would", "will", "must", "may", "might",
}
COMMON_CARD_VERBS = {
    "happens", "happen", "means", "mean", "becomes", "become", "matters", "matter", "belongs", "belong",
    "starts", "start", "ends", "end", "replaces", "replace", "keeps", "keep", "deletes", "delete", "records",
    "record", "stores", "store", "uses", "use", "causes", "cause", "prevents", "prevent", "requires", "require",
    "allows", "allow", "differs", "differ", "depends", "depend", "changes", "change", "fails", "fail", "works",
    "work", "returns", "return", "holds", "hold", "moves", "move", "goes", "go", "stays", "stay", "needs", "need",
    "makes", "make", "takes", "take", "gets", "get", "runs", "run", "calls", "call", "sends", "send", "reads",
    "read", "writes", "write", "lists", "names", "marks", "mark",
}
VERB_LEXICON = OBSERVABLE_VERBS | AUXILIARY_VERBS | COMMON_CARD_VERBS
SUBJECT_PRONOUNS = {"it", "this", "that", "they", "these", "those", "you", "we", "one", "each", "who", "which", "what"}
_INFLECTED_RE = re.compile(r"^[a-z]{3,}(s|ed|ing)$")
_WORD_RE = re.compile(r"[a-z0-9][a-z0-9'-]*")


# ---------------------------------------------------------------------------
# Text helpers (duplicated from questions.py on purpose: no new shared module)
# ---------------------------------------------------------------------------


def words(text: str | None) -> list[str]:
    """Lowercase word tokens in order (underscores and punctuation dropped)."""
    return _WORD_RE.findall((text or "").lower())


def normalize_text(text: str | None) -> str:
    """Lowercase, punctuation stripped to spaces, whitespace collapsed."""
    return " ".join(words(text))


def contains_run(haystack: list[str], needle: list[str]) -> bool:
    """True when needle appears as a consecutive run inside haystack."""
    n = len(needle)
    if n == 0 or n > len(haystack):
        return False
    return any(haystack[i:i + n] == needle for i in range(len(haystack) - n + 1))


def front_back_overlap(front: str | None, back: str | None) -> float:
    """Share of the back's content words that also appear on the front (0 when the back has none)."""
    back_words = content_words(back)
    if not back_words:
        return 0.0
    return len(content_words(front) & back_words) / len(back_words)


def _has_verb(clause: str) -> bool:
    toks = words(clause)
    for i, tok in enumerate(toks):
        if tok in VERB_LEXICON:
            return True
        if i and toks[i - 1] in SUBJECT_PRONOUNS and tok not in STOPWORDS and _INFLECTED_RE.match(tok):
            return True
    return False


def is_compound_front(front: str | None) -> bool:
    """A front joins two verb phrases with "and" / "as well as" — two propositions on one card.

    "Which belongs in context and consequences?" names two terms (no verb on the
    right) and is not compound; "What is an ADR and why is it kept?" is.
    """
    text = front or ""
    for m in COMPOUND_FRONT_RE.finditer(text):
        if _has_verb(text[:m.start()]) and _has_verb(text[m.end():]):
            return True
    return False


def card_ceiling(lesson_words: int, budget: dict) -> int:
    """Max cards for a lesson: cards_max capped at CARDS_PER_100_WORDS_MAX per 100 words, never below cards_min."""
    cards_min = budget.get("cards_min") or CARDS_RANGE[0]
    cards_max = budget.get("cards_max") or CARDS_RANGE[1]
    by_words = math.floor(lesson_words / 100 * CARDS_PER_100_WORDS_MAX)
    return max(cards_min, min(cards_max, by_words))


# ---------------------------------------------------------------------------
# Pipeline context (groups, concepts, units, qualification, per-lesson files)
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
    """Everything a check or prepare needs that is not the lesson's own authored file."""

    input_dir: Path
    groups: dict[str, dict] = field(default_factory=dict)
    group_order: list[str] = field(default_factory=list)
    concepts: dict[str, dict] = field(default_factory=dict)
    units: dict[str, dict] = field(default_factory=dict)
    qualification: dict[str, dict] = field(default_factory=dict)  # unit id -> qualification entry
    has_statuses: bool = False
    findings: list[Finding] = field(default_factory=list)

    def read_item(self, stage_dir: str, lesson_id: str, check_id: str, severity: str = "error") -> tuple[dict | None, Finding | None]:
        """Load <stage_dir>/<item>.json; (None, None) when absent, (None, finding) when unreadable."""
        path = self.input_dir / stage_dir / item_id_to_filename(lesson_id)
        if not path.exists():
            return None, None
        rel = f"{stage_dir}/{path.name}"
        data, err = _load_json_safe(path, check_id, rel)
        if err is not None:
            err.severity = severity
            return None, err
        if not isinstance(data, dict):
            return None, Finding(check_id, severity, rel, "top level must be an object")
        return data, None

    def load_lesson(self, lesson_id: str) -> tuple[dict | None, Finding | None]:
        return self.read_item(LESSONS_DIR, lesson_id, f"{STAGE}.lesson_missing")

    def prerequisites(self, group: dict) -> list[dict]:
        """Accepted prerequisite edges: committed ones, or every proposed edge when
        no critique statuses exist yet anywhere in groups.json."""
        out = []
        for p in group.get("prerequisites") or []:
            if not isinstance(p, dict):
                continue
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

    def neighbours(self, lesson_id: str) -> list[str]:
        """Lessons joined by an accepted prerequisite edge (either direction) or sharing the module."""
        group = self.groups.get(lesson_id) or {}
        out = [p.get("group_id") for p in self.prerequisites(group)]
        module = group.get("module_id")
        for gid, g in self.groups.items():
            if gid == lesson_id:
                continue
            if any(p.get("group_id") == lesson_id for p in self.prerequisites(g)):
                out.append(gid)
            if module and g.get("module_id") == module:
                out.append(gid)
        return [g for g in dict.fromkeys(out) if g in self.groups and g != lesson_id]

    def lesson_ids(self) -> list[str]:
        """Lesson ids that have an authored lessons/<item>.json, in group order.

        Only files whose name maps back to a ``g:<slug>`` id count: a helper file in
        the directory (``lessons/anchor.json``) is not a lesson."""
        present = {lid for lid in (filename_to_item_id(p.name) for p in list_item_files(self.input_dir / LESSONS_DIR))
                   if GROUP_ID_RE.match(lid)}
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
                if isinstance(g, dict) and g.get("id"):
                    ctx.groups[g["id"]] = g
                    ctx.group_order.append(g["id"])
            ctx.has_statuses = any(
                isinstance(p, dict) and (p.get("status") or "proposed") != "proposed"
                for g in ctx.groups.values() for p in (g.get("prerequisites") or [])
            )
    concepts_path = ctx.input_dir / CONCEPTS_FILE
    if concepts_path.exists():
        data, err = _load_json_safe(concepts_path, f"{STAGE}.concepts_invalid", CONCEPTS_FILE)
        if err is not None:
            ctx.findings.append(err)
        elif isinstance(data, dict):
            ctx.concepts = {c["id"]: c for c in data.get("concepts") or [] if isinstance(c, dict) and c.get("id")}
    units_dir = ctx.input_dir / UNITS_DIR
    for path in sorted(units_dir.glob("*.json")) if units_dir.is_dir() else []:
        data, err = _load_json_safe(path, f"{STAGE}.units_invalid", f"{UNITS_DIR}/{path.name}")
        if err is not None:
            ctx.findings.append(err)
            continue
        if isinstance(data, dict) and isinstance(data.get("units"), list):
            for u in data["units"]:
                if isinstance(u, dict) and u.get("id"):
                    ctx.units[u["id"]] = u
    if not ctx.units:
        ctx.findings.append(Finding(f"{STAGE}.units_missing", "warning", f"{UNITS_DIR}/",
                                    "no units files found — unit_ids are checked against groups.json only"))
    qual_dir = ctx.input_dir / QUALIFICATION_DIR
    for path in sorted(qual_dir.glob("*.json")) if qual_dir.is_dir() else []:
        data, err = _load_json_safe(path, f"{STAGE}.qualification_invalid", f"{QUALIFICATION_DIR}/{path.name}")
        if err is not None:
            ctx.findings.append(err)
            continue
        if isinstance(data, dict) and isinstance(data.get("units"), dict):
            ctx.qualification.update({uid: q for uid, q in data["units"].items() if isinstance(q, dict)})
    return ctx


def _pages_of(lesson: dict) -> list[dict]:
    pages = [p for p in (lesson.get("pages") or []) if isinstance(p, dict)]
    return sorted(pages, key=lambda p: p.get("index", 0))


def _lesson_markdown(pages: list[dict]) -> str:
    return "\n\n".join(p.get("markdown") or "" for p in pages)


def _rubric_text() -> str | None:
    try:
        return RUBRIC_PATH.read_text(encoding="utf-8")
    except OSError:
        return None


def _neighbour_fronts(ctx: Context, lesson_id: str) -> tuple[list[dict], list[Finding]]:
    """Fronts of the neighbouring lessons' authored cards: [{lesson_id, id, front}]."""
    out: list[dict] = []
    findings: list[Finding] = []
    for nid in ctx.neighbours(lesson_id):
        data, err = ctx.read_item(CARDS_DIR, nid, f"{STAGE}.neighbour_invalid", "warning")
        if err is not None:
            findings.append(err)
        for i, card in enumerate((data or {}).get("cards") or []):
            if not isinstance(card, dict) or not isinstance(card.get("front"), str) or not card["front"].strip():
                continue
            cid = card.get("id") if isinstance(card.get("id"), str) and card.get("id") else f"{nid} cards[{i}]"
            out.append({"lesson_id": nid, "id": cid, "front": card["front"]})
    return out, findings


# ---------------------------------------------------------------------------
# prepare
# ---------------------------------------------------------------------------


def _knowledge_levels(ctx: Context, unit_ids: list[str]) -> dict:
    counts = {level: 0 for level in KNOWLEDGE_LEVELS}
    for uid in unit_ids:
        level = (ctx.qualification.get(uid) or {}).get("knowledge_level")
        if level in counts:
            counts[level] += 1
    return {
        "counts": counts,
        "suggested_card_types": {level: list(CARD_TYPES_BY_KNOWLEDGE[level]) for level, n in counts.items() if n},
    }


def _question_prompts(questions: dict | None) -> list[dict]:
    out = []
    for section in ("embedded", "quiz"):
        for q in (questions or {}).get(section) or []:
            if isinstance(q, dict) and isinstance(q.get("prompt"), str) and q["prompt"].strip():
                out.append({"id": q.get("id"), "section": section, "prompt": q["prompt"]})
    return out


def _next_revision(existing: dict | None) -> int:
    """Round number for the next authoring pass: previous revision + 1, else 0."""
    prev = (existing or {}).get("revision")
    return prev + 1 if isinstance(prev, int) and not isinstance(prev, bool) else 0


def _prepare_lesson(ctx: Context, lesson_id: str) -> tuple[dict | None, list[Finding]]:
    group = ctx.groups.get(lesson_id)
    if group is None:
        return None, [Finding(f"{STAGE}.unknown_lesson", "error", lesson_id, "no group with this id in groups.json")]
    lesson, err = ctx.load_lesson(lesson_id)
    if lesson is None:
        return None, [err or Finding(f"{STAGE}.lesson_missing", "error", lesson_id, "lessons/<item>.json not found")]
    pages = _pages_of(lesson)
    if not pages or any(p.get("markdown") is None for p in pages):
        return None, [Finding(f"{STAGE}.lesson_outline_only", "error", lesson_id,
                              "lesson pages have no markdown yet — finish lessons.py before designing cards")]
    findings: list[Finding] = []
    brief, err = ctx.read_item(SYNTHESES_DIR, lesson_id, f"{STAGE}.synthesis_invalid", "warning")
    if err is not None:
        findings.append(err)
    questions, err = ctx.read_item(QUESTIONS_DIR, lesson_id, f"{STAGE}.questions_invalid", "warning")
    if err is not None:
        findings.append(err)
    elif questions is None:
        findings.append(Finding(f"{STAGE}.questions_missing", "warning", lesson_id,
                                f"no {QUESTIONS_DIR}/{item_id_to_filename(lesson_id)} — question_prompts is empty; "
                                "design questions first so cards do not restate them"))
    existing, err = ctx.read_item(CARDS_DIR, lesson_id, f"{STAGE}.invalid_json")
    if err is not None:
        findings.append(err)
    rubric = _rubric_text()
    if rubric is None:
        findings.append(Finding(f"{STAGE}.rubric_missing", "warning", str(RUBRIC_PATH),
                                "rubric not found — work file carries a fallback note under instructions"))
    if not ctx.qualification:
        findings.append(Finding(f"{STAGE}.qualification_missing", "warning", f"{QUALIFICATION_DIR}/",
                                "no qualification files — knowledge-level counts are all zero"))
    do_not_duplicate, nb_findings = _neighbour_fronts(ctx, lesson_id)
    findings.extend(nb_findings)

    lesson_markdown = _lesson_markdown(pages)
    lesson_words = word_count(lesson_markdown)
    budget = ctx.budget(group)
    unit_scope = list(dict.fromkeys([*(group.get("unit_ids") or []), *(group.get("related_unit_ids") or [])]))
    brief = brief or {}
    work = {
        "contract_version": CONTRACT_VERSION,
        "stage": STAGE,
        "lesson_id": lesson_id,
        "group_id": group.get("id"),
        "title": lesson.get("title") or group.get("title"),
        "revision": _next_revision(existing),
        "pages": [{"index": p.get("index"), "role": p.get("role"), "title": p.get("title"), "markdown": p.get("markdown"),
                   "lo_ids": p.get("lo_ids") or [], "knowledge_point_id": p.get("knowledge_point_id"),
                   "unit_ids": p.get("unit_ids") or []} for p in pages],
        "lesson_words": lesson_words,
        "lesson_tokens": count_tokens(lesson_markdown),
        "objectives": group.get("objectives") or [],
        "knowledge_points": group.get("knowledge_points") or [],
        "generalization": group.get("generalization"),
        "watch": group.get("watch") or [],
        "concepts": [{"id": cid, "label": (ctx.concepts.get(cid) or {}).get("label"),
                      "aliases": (ctx.concepts.get(cid) or {}).get("aliases") or [],
                      "description": (ctx.concepts.get(cid) or {}).get("description")}
                     for cid in group.get("concept_ids") or []],
        "terminology": brief.get("terminology") or [],
        "map_items": [{"id": b.get("id"), "text": b.get("text"), "unit_ids": b.get("unit_ids") or [],
                       "lo_ids": b.get("lo_ids") or [], "knowledge_point_id": b.get("knowledge_point_id")}
                      for b in brief.get("items") or [] if isinstance(b, dict) and b.get("kind") == "map"],
        "units": [{"id": uid, "source": (parse_unit_id(uid) or (None,))[0],
                   "section_title": (ctx.units.get(uid) or {}).get("section_title", "")}
                  for uid in unit_scope],
        "budget": {
            "cards_min": budget.get("cards_min") or CARDS_RANGE[0],
            "cards_max": budget.get("cards_max") or CARDS_RANGE[1],
            "cards_ceiling": card_ceiling(lesson_words, budget),
            "lesson_words": lesson_words,
            "cards_per_100_words_max": CARDS_PER_100_WORDS_MAX,
        },
        "knowledge_levels": _knowledge_levels(ctx, unit_scope),
        "lens_checklist": [{"lens": lens, "applies_to": applies, "ask": ask} for lens, applies, ask in LENS_CHECKLIST],
        "question_prompts": _question_prompts(questions),
        "do_not_duplicate": do_not_duplicate,
        "existing": existing,
        "lessons_learned": (existing or {}).get("lessons_learned") or [],
        "instructions": rubric if rubric is not None else RUBRIC_MISSING_NOTE,
    }
    return work, findings


def prepare(input_dir: Path, lesson_ids: list[str] | None = None, force: bool = False) -> tuple[list[Path], list[Finding]]:
    """Write cards/work/<item>.json per lesson. Returns (written paths, findings)."""
    ctx = load_context(Path(input_dir))
    findings = list(ctx.findings)
    if not ctx.groups:
        return [], findings
    written: list[Path] = []
    for lesson_id in lesson_ids or ctx.lesson_ids():
        target = ctx.input_dir / CARDS_DIR / WORK_SUBDIR / item_id_to_filename(lesson_id)
        if target.exists() and not force:
            findings.append(Finding(f"{STAGE}.work_exists", "info", lesson_id, f"{target.name} exists — use --force to overwrite"))
            continue
        work, lesson_findings = _prepare_lesson(ctx, lesson_id)
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
    """Resolution scope for one lesson's cards."""

    lesson_id: str
    objectives: dict[str, dict]
    unit_ids: set[str]
    n_pages: int
    cards_min: int
    cards_max: int
    ceiling: int
    lesson_words: int
    neighbour_fronts: list[tuple[str, str]]  # (card id, normalized front)


def _valid_index(value: object, size: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value < size


def _err(check: str, loc: str, msg: str) -> Finding:
    return Finding(f"{STAGE}.{check}", "error", loc, msg)


def _warn(check: str, loc: str, msg: str) -> Finding:
    return Finding(f"{STAGE}.{check}", "warning", loc, msg)


def _check_id(cid: object, lesson_id: str, seen: set[str], fallback: str) -> tuple[str, list[Finding]]:
    """Validate a card id (<lesson>#k<n>); returns (location label, findings)."""
    if not isinstance(cid, str) or not cid:
        return fallback, [_err("id_format", fallback, "missing id")]
    m = SUBITEM_ID_RE.match(cid)
    if not m or m.group("kind") != "k" or m.group("variant") is not None or cid.split("#")[0] != lesson_id:
        return cid, [_err("id_format", cid, f"expected {lesson_id}#k<n>")]
    if cid in seen:
        return cid, [_err("duplicate_id", cid, "id used more than once")]
    seen.add(cid)
    return cid, []


def _check_references(card: dict, loc: str, scope: _Scope, ctx: Context) -> list[Finding]:
    """lo_id / unit_ids / page_index resolution plus source_reference presence."""
    out: list[Finding] = []
    if card.get("lo_id") not in scope.objectives:
        out.append(_err("unknown_lo", loc, f"lo_id {card.get('lo_id')!r} is not one of the lesson's objectives"))
    unit_ids = card.get("unit_ids")
    if not isinstance(unit_ids, list) or not unit_ids:
        out.append(_err("missing_field", loc, "unit_ids must be a non-empty list"))
        unit_ids = []
    for uid in unit_ids:
        if uid not in scope.unit_ids:
            out.append(_err("unknown_unit", loc, f"unit {uid!r} is not in the group's units ∪ related units"))
        elif ctx.units and uid not in ctx.units:
            out.append(_err("unknown_unit", loc, f"unit {uid!r} is cited but no units file defines it"))
    pi = card.get("page_index")
    if not _valid_index(pi, scope.n_pages):
        out.append(_err("page_index", loc, f"page_index {pi!r} outside [0, {scope.n_pages - 1}]"))
    ref = card.get("source_reference")
    if not isinstance(ref, str) or not ref.strip():
        out.append(_warn("missing_field", loc, "source_reference is empty — the viewer shows it under the card"))
    return out


def _check_text(ctype: str | None, loc: str, front: str, back: str) -> list[Finding]:
    """Word caps and front hygiene shared by every card type."""
    out: list[Finding] = []
    if ctype != "code" and word_count(front) > CARD_FRONT_MAX_WORDS:
        out.append(_err("front_length", loc, f"front has {word_count(front)} words (max {CARD_FRONT_MAX_WORDS}; code cards exempt)"))
    cap = CARD_BACK_MAX_WORDS.get(ctype or "")
    if cap and word_count(back) > cap:
        out.append(_err("back_length", loc, f"back has {word_count(back)} words (max {cap} for {ctype})"))
    if ctype not in ("code", "comparison") and is_compound_front(front):
        out.append(_warn("compound_front", loc, "front joins two verb phrases with 'and' / 'as well as' — one proposition per card"))
    if PRONOUN_ONLY_FRONT_RE.match(front):
        out.append(_err("pronoun_front", loc, "front refers to 'it/this/that' without naming its subject"))
    if ctype != "cloze" and YES_NO_STEM_RE.match(front):
        out.append(_err("yes_no_front", loc, "yes/no front — ask for the attribute, outcome or distinction instead"))
    if GIVE_EXAMPLE_FRONT_RE.search(front):
        out.append(_err("give_example_front", loc, "'give/name an example' fronts have many right answers"))
    if contains_run(words(front), words(back)):
        out.append(_err("answer_on_front", loc, "the back appears verbatim on the front"))
    else:
        overlap = front_back_overlap(front, back)
        if overlap > CARD_OVERLAP_MAX:
            out.append(_err("overlap", loc, f"front shares {overlap:.0%} of the back's content words (max {CARD_OVERLAP_MAX:.0%})"))
    return out


def _check_cloze(loc: str, front: str, card_data: dict) -> list[Finding]:
    out: list[Finding] = []
    blanks = len(CLOZE_BLANK_RE.findall(front))
    if blanks != 1:
        out.append(_err("cloze_blank", loc, f"cloze front has {blanks} blanks ('___'); exactly one required"))
    n = word_count(CLOZE_BLANK_RE.sub(" ", front))
    if n >= CLOZE_SENTENCE_MAX_WORDS:
        out.append(_err("cloze_length", loc, f"cloze sentence has {n} words (must be under {CLOZE_SENTENCE_MAX_WORDS})"))
    answer = card_data.get("blank_answer")
    if not isinstance(answer, str) or not answer.strip():
        out.append(_err("cloze_answer", loc, "card_data.blank_answer is missing"))
        answer = None
    elif normalize_text(answer) in STOPWORDS:
        out.append(_err("cloze_answer", loc, f"blank_answer {answer!r} is a stopword — blank the concept, not a function word"))
    hint = card_data.get("hint")
    if hint is None:
        return out
    if not isinstance(hint, str):
        out.append(_err("cloze_hint", loc, "card_data.hint must be a string"))
    elif word_count(hint) > CLOZE_HINT_MAX_WORDS:
        out.append(_err("cloze_hint", loc, f"hint has {word_count(hint)} words (max {CLOZE_HINT_MAX_WORDS})"))
    elif answer:
        prefixes = {w[:HINT_PREFIX_LEN] for w in words(answer) if len(w) >= HINT_PREFIX_LEN}
        shared = sorted(w for w in words(hint) if len(w) >= HINT_PREFIX_LEN and w[:HINT_PREFIX_LEN] in prefixes)
        if shared:
            out.append(_err("cloze_hint", loc, f"hint word(s) {shared} share a {HINT_PREFIX_LEN}-letter stem with the answer"))
    return out


def _check_ordering(loc: str, card_data: dict) -> list[Finding]:
    items = card_data.get("items")
    items_ok = (isinstance(items, list) and ORDERING_ITEMS_MIN <= len(items) <= ORDERING_ITEMS_MAX
                and all(isinstance(it, str) and it.strip() for it in items))
    if not items_ok:
        return [_err("ordering_items", loc, f"card_data.items must hold {ORDERING_ITEMS_MIN}-{ORDERING_ITEMS_MAX} non-empty strings "
                                            "(split longer sequences into overlapping windows)")]
    order = card_data.get("correct_order")
    valid_ints = isinstance(order, list) and all(isinstance(i, int) and not isinstance(i, bool) for i in order)
    if not valid_ints or sorted(order) != list(range(len(items))):
        return [_err("ordering_permutation", loc, f"card_data.correct_order {order!r} is not a permutation of 0..{len(items) - 1}")]
    if order == list(range(len(items))):
        return [_warn("ordering_unshuffled", loc, "items are already listed in the correct order — shuffle them")]
    return []


def _check_discrimination(loc: str, card_data: dict) -> tuple[str | None, list[Finding]]:
    """Options/correct_index shape. Returns (keyed option text, findings)."""
    options = card_data.get("options")
    lo, hi = DISCRIMINATION_OPTIONS
    if not isinstance(options, list) or not lo <= len(options) <= hi or not all(isinstance(o, str) and o.strip() for o in options):
        return None, [_err("discrimination_options", loc, f"card_data.options must hold {lo}-{hi} non-empty strings")]
    ci = card_data.get("correct_index")
    if not _valid_index(ci, len(options)):
        return None, [_err("correct_index", loc, f"card_data.correct_index {ci!r} outside [0, {len(options) - 1}]")]
    return options[ci], []


def _dedup_pair(a: tuple[str, str], b: tuple[str, str]) -> Finding | None:
    if not a[1] or not b[1]:
        return None
    ratio = SequenceMatcher(None, a[1], b[1]).ratio()
    loc = f"{a[0]}, {b[0]}"
    if ratio >= CARD_DEDUP_RATIO:
        return _err("duplicate", loc, f"fronts are near-identical (ratio {ratio:.2f} >= {CARD_DEDUP_RATIO})")
    if ratio >= CARD_DEDUP_BAND:
        return _warn("near_duplicate", loc, f"fronts are similar (ratio {ratio:.2f}) — reword or merge to avoid interference")
    return None


def _check_dedup(fronts: list[tuple[str, str]], neighbour_fronts: list[tuple[str, str]]) -> list[Finding]:
    out: list[Finding] = []
    for i, a in enumerate(fronts):
        for b in [*fronts[i + 1:], *neighbour_fronts]:
            finding = _dedup_pair(a, b)
            if finding is not None:
                out.append(finding)
    return out


def _check_cards(cards: list, scope: _Scope, ctx: Context) -> list[Finding]:
    out: list[Finding] = []
    seen: set[str] = set()
    types: set[str] = set()
    lenses: set[str] = set()
    covered: set[str] = set()
    answers: dict[str, str] = {}          # normalized back / answer -> first card holding it
    fronts: list[tuple[str, str]] = []
    for i, card in enumerate(cards):
        if not isinstance(card, dict):
            out.append(_err("missing_field", f"cards[{i}]", "card is not an object"))
            continue
        loc, id_findings = _check_id(card.get("id"), scope.lesson_id, seen, f"cards[{i}]")
        out.extend(id_findings)
        ctype = card.get("card_type")
        if ctype not in CARD_TYPES:
            out.append(_err("enum", loc, f"card_type {ctype!r} not in {list(CARD_TYPES)}"))
            ctype = None
        else:
            types.add(ctype)
        lens = card.get("lens")
        if lens not in CARD_LENSES:
            out.append(_err("enum", loc, f"lens {lens!r} not in {list(CARD_LENSES)}"))
        else:
            lenses.add(lens)
        out.extend(_check_references(card, loc, scope, ctx))
        if card.get("lo_id") in scope.objectives:
            covered.add(card["lo_id"])
        card_data = card.get("card_data")
        if not isinstance(card_data, dict):
            out.append(_err("missing_field", loc, "card_data must be an object ({} when the type needs none)"))
            card_data = {}
        front, back = card.get("front"), card.get("back")
        texts_ok = True
        for name, value in (("front", front), ("back", back)):
            if not isinstance(value, str) or not value.strip():
                out.append(_err("missing_field", loc, f"{name} is empty"))
                texts_ok = False
        if not texts_ok:
            continue
        out.extend(_check_text(ctype, loc, front, back))
        keys = {normalize_text(back)}
        if ctype == "cloze":
            out.extend(_check_cloze(loc, front, card_data))
            if isinstance(card_data.get("blank_answer"), str):
                keys.add(normalize_text(card_data["blank_answer"]))
        elif ctype == "ordering":
            out.extend(_check_ordering(loc, card_data))
        elif ctype == "discrimination":
            keyed, d_findings = _check_discrimination(loc, card_data)
            out.extend(d_findings)
            if keyed:
                keys.add(normalize_text(keyed))
        elif ctype == "comparison" and not COMPARISON_PAIR_RE.search(front):
            out.append(_warn("comparison_terms", loc, "comparison front should name both terms ('X and Y', 'X vs Y', 'between X and Y')"))
        for key in sorted(k for k in keys if k):
            if key in answers and answers[key] != loc:
                out.append(_err("shared_answer", f"{answers[key]}, {loc}", f"both cards resolve to the answer {key!r}"))
            else:
                answers.setdefault(key, loc)
        fronts.append((loc, normalize_text(front)))

    n = len(cards)
    if not scope.cards_min <= n <= scope.ceiling:
        out.append(_err("count", scope.lesson_id, f"{n} cards; budget allows {scope.cards_min}..{scope.ceiling} "
                                                   f"({scope.lesson_words} lesson words at <= {CARDS_PER_100_WORDS_MAX:g} per 100, cards_max {scope.cards_max})"))
    if len(types) < CARD_MIN_TYPES:
        out.append(_err("types", scope.lesson_id, f"{len(types)} card type(s) {sorted(types)}; needs >= {CARD_MIN_TYPES}"))
    if len(lenses) < 2:
        out.append(_warn("generalization_angles", scope.lesson_id,
                         f"all cards use the lens {sorted(lenses)} — view the generalization through >= 2 lenses"))
    for lo_id in scope.objectives:
        if lo_id not in covered:
            out.append(_err("objective_uncovered", lo_id, "no card targets this objective"))
    out.extend(_check_dedup(fronts, scope.neighbour_fronts))
    return out


def _check_lesson(ctx: Context, lesson_id: str) -> list[Finding]:
    path = ctx.input_dir / CARDS_DIR / item_id_to_filename(lesson_id)
    rel = f"{CARDS_DIR}/{path.name}"
    if not path.exists():
        return [_err("file_missing", lesson_id, f"{rel} not found")]
    data, err = _load_json_safe(path, f"{STAGE}.invalid_json", rel)
    if err is not None:
        return [err]
    if not isinstance(data, dict):
        return [_err("invalid_json", rel, "top level must be an object")]
    group = ctx.groups.get(lesson_id)
    if group is None:
        return [_err("unknown_lesson", lesson_id, "no group with this id in groups.json")]
    out: list[Finding] = []
    if data.get("contract_version") != CONTRACT_VERSION:
        out.append(_warn("contract_version", rel, f"contract_version {data.get('contract_version')!r} != {CONTRACT_VERSION}"))
    if data.get("lesson_id") != lesson_id:
        out.append(_err("lesson_mismatch", rel, f"lesson_id {data.get('lesson_id')!r} does not match the file name"))
    lesson, err = ctx.load_lesson(lesson_id)
    if lesson is None:
        out.append(err or _err("lesson_missing", lesson_id, "lessons/<item>.json not found — cannot resolve pages"))
        return out
    pages = _pages_of(lesson)
    budget = ctx.budget(group)
    lesson_words = word_count(_lesson_markdown(pages))
    neighbours, nb_findings = _neighbour_fronts(ctx, lesson_id)
    out.extend(nb_findings)
    scope = _Scope(
        lesson_id=lesson_id,
        objectives={o["id"]: o for o in (group.get("objectives") or []) if isinstance(o, dict) and o.get("id")},
        unit_ids=set(group.get("unit_ids") or []) | set(group.get("related_unit_ids") or []),
        n_pages=len(pages),
        cards_min=budget.get("cards_min") or CARDS_RANGE[0],
        cards_max=budget.get("cards_max") or CARDS_RANGE[1],
        ceiling=card_ceiling(lesson_words, budget),
        lesson_words=lesson_words,
        neighbour_fronts=[(nb["id"], normalize_text(nb["front"])) for nb in neighbours],
    )
    cards = data.get("cards")
    if not isinstance(cards, list):
        out.append(_err("missing_field", rel, "cards must be a list"))
        cards = []
    out.extend(_check_cards(cards, scope, ctx))
    return out


def check(input_dir: Path, lesson_ids: list[str] | None = None, **_opts) -> list[Finding]:
    """Whole stage: every authored cards file, plus lessons that still lack one."""
    ctx = load_context(Path(input_dir))
    findings = list(ctx.findings)
    if not ctx.groups:
        return findings
    authored = {lid for lid in (filename_to_item_id(p.name) for p in list_item_files(ctx.input_dir / CARDS_DIR))
                if GROUP_ID_RE.match(lid)}
    targets = lesson_ids or ctx.lesson_ids()
    for lesson_id in targets:
        if lesson_id in authored or lesson_ids:
            findings.extend(_check_lesson(ctx, lesson_id))
        else:
            findings.append(_warn("missing", lesson_id, f"no {CARDS_DIR}/{item_id_to_filename(lesson_id)} yet"))
    if not lesson_ids:
        for stray in sorted(authored - set(targets)):
            findings.extend(_check_lesson(ctx, stray))
    return findings


def check_item(input_dir: Path, item_id: str, **_opts) -> list[Finding]:
    """One lesson (g:slug) or one card (g:slug#k3, findings filtered to it)."""
    lesson_id, _, sub = item_id.partition("#")
    ctx = load_context(Path(input_dir))
    findings = [f for f in ctx.findings if f.severity == "error"]
    if not ctx.groups:
        return findings
    findings.extend(_check_lesson(ctx, lesson_id))
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
    parser = argparse.ArgumentParser(description="Stage 7 — design cards: prepare work files / check authored files")
    sub = parser.add_subparsers(dest="command", required=True)
    p_prep = sub.add_parser("prepare", help="write cards/work/<item>.json per lesson")
    p_prep.add_argument("input_dir", type=Path)
    p_prep.add_argument("--lesson", action="append", default=None, metavar="g:ID", help="only this lesson (repeatable)")
    p_prep.add_argument("--force", action="store_true", help="overwrite existing work files")
    p_check = sub.add_parser("check", help="validate cards/<item>.json")
    p_check.add_argument("input_dir", type=Path)
    p_check.add_argument("--lesson", action="append", default=None, metavar="g:ID", help="only this lesson (repeatable)")
    p_check.add_argument("--strict", action="store_true", help="warnings fail too")
    args = parser.parse_args(argv)

    if not args.input_dir.is_dir():
        print(f"error: {args.input_dir} is not a directory", file=sys.stderr)
        return 2
    if args.command == "prepare":
        written, findings = prepare(args.input_dir, args.lesson, args.force)
        for path in written:
            print(f"wrote {path.relative_to(args.input_dir)}")
        errors, _ = print_findings(findings)
        return 1 if errors else 0
    findings = check(args.input_dir, args.lesson)
    errors, warnings = print_findings(findings)
    if errors or (args.strict and warnings):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
