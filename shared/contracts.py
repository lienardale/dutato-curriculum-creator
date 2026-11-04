"""
Shared contract for the lesson pipeline: artifact names, id formats, enums,
thresholds and the critique criteria catalogue.

PURE DATA — no AI calls, no I/O beyond tiny JSON helpers. Every pipeline
script (qualify.py, regroup.py, synthesize.py, lessons.py, questions.py,
cards.py, critique.py, validate.py, upload.py) imports its constants from here
so a threshold is changed in exactly one place. docs/artifacts.md is the prose
version of this module; keep the two in sync.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

CONTRACT_VERSION = 1

# ---------------------------------------------------------------------------
# Pipeline modes and stages
# ---------------------------------------------------------------------------

PIPELINE_LESSON = "lesson"
PIPELINE_RAW = "raw"
PIPELINES = {PIPELINE_LESSON, PIPELINE_RAW}

# Lesson-mode stages in order: (stage, artifact path relative to output dir,
# description, optional). Directory artifacts end with "/".
LESSON_STAGES = [
    ("manifest", "manifest.json", "Manifest created", False),
    ("extract", "extracted/", "Sources extracted", False),
    ("normalize", "extracted/", "Titles normalized (in place)", True),
    ("analyze_images", "image_analysis.json", "Images analyzed (optional)", True),
    ("qualify", "units/", "Units segmented (qualify prepare)", False),
    ("qualification", "qualification/", "Units qualified by the agent", False),
    ("concepts", "concepts.json", "Concept registry written", False),
    ("regroup", "groups.json", "Lesson candidates grouped", False),
    ("synthesize", "syntheses/", "Knowledge briefs written", False),
    ("lessons", "lessons/", "Lessons designed", False),
    ("build", "chunks.json", "structure.json + chunks.json built", False),
    ("questions", "questions/", "Questions designed", False),
    ("cards", "cards/", "Cards designed", False),
    ("validate", "validation_report.json", "Validation report (optional)", True),
    ("review", "review.json", "Quality reviewed", False),
    ("upload", "upload_result.json", "Uploaded to Supabase", False),
    ("condense", "condensation_plan.json", "Condensed variants planned (optional)", True),
]

# Stages that have an author artifact + an independent critique directory.
CRITIQUED_STAGES = ("qualification", "groups", "syntheses", "lessons", "questions", "cards")

# ---------------------------------------------------------------------------
# Directory / file layout (relative to output/<name>/)
# ---------------------------------------------------------------------------

UNITS_DIR = "units"
UNITS_INDEX = "units/index.json"
QUALIFICATION_DIR = "qualification"
CONCEPTS_FILE = "concepts.json"
GROUPS_DRAFT_FILE = "groups.draft.json"
GROUPS_FILE = "groups.json"
SYNTHESES_DIR = "syntheses"
LESSONS_DIR = "lessons"
LESSON_METRICS_FILE = "lesson_metrics.json"
QUESTIONS_DIR = "questions"
CARDS_DIR = "cards"
CRITIQUE_DIR = "critique"
CRITIQUE_SUMMARY = "critique/summary.json"
SCOPE_FILE = "scope.json"
WORK_SUBDIR = "work"  # <stage dir>/work/<id>.json holds prepare() work files

# ---------------------------------------------------------------------------
# Identifiers
# ---------------------------------------------------------------------------

UNIT_ID_RE = re.compile(r"^u:(?P<source>[A-Za-z0-9._-]+):(?P<section>\d+):(?P<n>\d+)$")
CONCEPT_ID_RE = re.compile(r"^c:[a-z0-9]+(?:-[a-z0-9]+)*$")
GROUP_ID_RE = re.compile(r"^g:[a-z0-9]+(?:-[a-z0-9]+)*$")
AREA_ID_RE = re.compile(r"^a:[a-z0-9]+(?:-[a-z0-9]+)*$")
MODULE_ID_RE = re.compile(r"^m:[a-z0-9]+(?:-[a-z0-9]+)*$")
# Sub-item ids hang off a group/lesson id: g:<slug>#lo1, #kp2, #w3, #b4, #q5, #k6, #e7, #ex8
SUBITEM_ID_RE = re.compile(r"^g:[a-z0-9]+(?:-[a-z0-9]+)*#(?P<kind>lo|kp|w|b|q|k|e|ex)(?P<n>\d+)(?:v(?P<variant>\d+))?$")

SLUG_MAX_LEN = 48


def slugify(text: str, max_len: int = SLUG_MAX_LEN) -> str:
    """Lowercase ASCII slug: letters/digits separated by single hyphens."""
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    s = re.sub(r"-{2,}", "-", s)
    return s[:max_len].rstrip("-") or "x"


def source_key(filename: str) -> str:
    """Extracted filename -> source key used inside unit ids (strip .json)."""
    name = Path(filename).name
    return name[:-5] if name.endswith(".json") else name


def unit_id(source: str, section_index: int, n: int) -> str:
    return f"u:{source}:{section_index}:{n}"


def parse_unit_id(uid: str) -> tuple[str, int, int] | None:
    m = UNIT_ID_RE.match(uid or "")
    if not m:
        return None
    return m.group("source"), int(m.group("section")), int(m.group("n"))


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

BLOOM_LEVELS = ("remember", "understand", "apply", "analyze", "evaluate", "create")
BLOOM_ORDER = {level: i for i, level in enumerate(BLOOM_LEVELS)}

# Bloom level -> DuTaTo mastery level (1 awareness, 2 understanding, 3 application, 4 evaluation)
BLOOM_TO_MASTERY = {
    "remember": 1, "understand": 2, "apply": 3, "analyze": 3, "evaluate": 4, "create": 4,
}
MASTERY_LEVELS = (1, 2, 3, 4)

BLOCK_TYPES = ("prose", "code", "table", "list", "figure_caption")

UNIT_TYPES = (
    "definition", "mechanism", "procedure", "example", "code", "pitfall",
    "trade_off", "comparison", "context", "reference", "noise",
)
KNOWLEDGE_LEVELS = ("fact", "concept", "procedure", "principle")
LEARNING_STEPS = ("foundation", "building_block", "applied", "advanced")
DROP_REASONS = ("meta", "duplicate", "off_topic", "noise")  # duplicate is written as "duplicate_of:<unit_id>"

# Default Bloom ceiling by knowledge level (agent may override with a reason).
BLOOM_CEILING_DEFAULT = {
    "fact": "understand", "concept": "analyze", "procedure": "apply", "principle": "evaluate",
}

COVERAGE_LEVELS = ("strong", "moderate", "thin", "gap")
GAP_SEVERITIES = ("critical", "recommended", "nice-to-have")
GAP_RESOLUTION_RE = re.compile(r"^(user_accepted|descoped|source_added: .+)$")

PREREQ_STRENGTHS = ("required", "recommended")
PREREQ_STATUSES = ("proposed", "committed", "uncertain", "rejected")
PREREQ_VALIDATOR_VERDICTS = ("confirmed", "either", "uncertain")

WATCH_KINDS = ("misconception", "boundary", "contrast", "failure_mode", "example_seed")
BRIEF_ITEM_KINDS = ("map", "watch", "inference")
CONFIDENCE_LEVELS = ("stated", "attributed")

PAGE_ROLES = ("orientation", "core", "worked_example", "pitfalls", "recap")
# Required page order: orientation first, recap last, core pages before worked_example, pitfalls after core.
PAGE_ROLE_ORDER = {"orientation": 0, "core": 1, "worked_example": 2, "pitfalls": 3, "recap": 4}

EMBEDDED_KINDS = ("predict", "pre_question", "check")  # == reading_questions.question_type
QUESTION_POSITIONS = ("before", "after")
EMBEDDED_DEFAULT_POSITION = {"predict": "before", "pre_question": "before", "check": "after"}
QUIZ_TYPES = ("multiple_choice", "true_false", "short_answer", "code_output")
EXERCISE_FADING = ("worked", "completion", "conventional")

CARD_TYPES = ("concept", "application", "comparison", "code", "cloze", "ordering", "discrimination")
CARD_LENSES = (
    "attributes", "similarities_differences", "parts_wholes", "causes_effects",
    "significance", "recall", "procedure",
)
# knowledge level -> preferred card types (first = primary)
CARD_TYPES_BY_KNOWLEDGE = {
    "fact": ("concept", "cloze"),
    "concept": ("discrimination", "comparison", "concept"),
    "procedure": ("application", "code", "ordering"),
    "principle": ("concept", "application"),
}

CRITIQUE_STATUSES = ("pending", "pass", "fail", "FAILED")
QUESTION_VERDICTS = ("keep", "rewrite", "drop")
KEEP_DROP_VERDICTS = ("keep", "drop")
CLAIM_VERDICTS = ("supported", "not_supported", "irrelevant")
GRADE_VALUES = ("pass", "fail")
CANDIDATE_LABELS = ("A", "B", "C")
# Fields withheld from the questions critic work file (re-derived or graded blind instead)
QUESTION_WITHHELD = ("rationale", "source_quote", "probes", "key_points", "bloom_level", "distractors", "explanation", "feedback", "variants")

# ---------------------------------------------------------------------------
# Thresholds (the single place to tune gates)
# ---------------------------------------------------------------------------

# Units (qualify prepare)
UNIT_MAX_TOKENS = 400          # prose grouped up to this size
UNIT_MIN_TOKENS = 40           # smaller trailing prose merges into the previous unit
UNIT_DEDUP_RATIO = 0.85        # SequenceMatcher ratio => alternate_of
UNIT_DEDUP_BAND = 0.75         # [band, ratio) => flagged for the agent

# Concepts
CONCEPT_LABEL_WORDS = (1, 5)
CONCEPT_MERGE_RATIO = 0.85     # label similarity => merge candidate
CONCEPT_BROAD_SHARE = 0.40     # attached to > this share of units => too broad
CONCEPT_THIN_UNITS = 2         # < this many units => thin
NEW_CONCEPTS_PER_GROUP_MAX = 5

# Qualification critique
# Blind re-tag agreement compares unit types by family (exact enum equality over 11 fuzzy
# values measured 13-40% disagreement on real sources even when the tagging was sound).
UNIT_TYPE_FAMILIES = {
    "definition": "explain", "mechanism": "explain", "context": "explain",
    "example": "example", "code": "example",
    "pitfall": "judge", "trade_off": "judge", "comparison": "judge",
    "procedure": "procedure",
    "reference": "reference", "noise": "reference",
}
QUALIFICATION_SAMPLE_SHARE = 0.10
QUALIFICATION_SAMPLE_MIN = 15
QUALIFICATION_DISAGREEMENT_MAX = 0.20
QUALIFICATION_DISAGREEMENT_TOLERANCE = 1   # a single disagreement never fails a source (small samples)

# Groups
GROUP_TOKENS = (300, 8000)
GROUP_KNOWLEDGE_POINTS = (3, 5)
GROUP_OBJECTIVES = (2, 4)
GROUP_WATCH_MIN = 2
GROUPS_PER_AREA = (2, 10)
AREAS_PER_CURRICULUM = (1, 15)
PREREQ_FANIN_WARN = 4
PREREQ_FANIN_FAIL = 6
PREREQ_ROOTS_MIN = 3           # when lessons >= PREREQ_ROOTS_MIN_LESSONS
PREREQ_ROOTS_MIN_LESSONS = 10
PREREQ_BOTTLENECK_WARN = 0.85  # one lesson gating > this share of downstream lessons

# Budgets (deterministic; ceilings not quotas)
# A lesson's fixed roles are orientation + pitfalls + recap (3 pages), plus one core page per
# knowledge point, plus one worked_example page when the group has an example unit:
# pages = knowledge_points + 3 (+ 1 with an example), clamped to PAGES_RANGE.
PAGES_RANGE = (4, 8)
PAGES_FIXED = 3                # orientation + pitfalls + recap
EMBEDDED_PER_PAGE_MAX = 1      # per position (before/after) per page
QUIZ_ITEMS_PER_LO = 2
QUIZ_MIN_BLOOM_LEVELS = 3
CARDS_RANGE = (4, 7)
EXERCISES_MAX = 3
CARDS_PER_100_WORDS_MAX = 1.0

# Units that justify a worked_example page (code is embedded, never paraphrased). The same rule
# decides the budget (regroup.py budget) and the page template (lessons.py prepare).
EXAMPLE_UNIT_TYPES = ("example", "code")
EXAMPLE_BLOCK_TYPES = ("code",)


def is_example_unit(unit: dict | None, qualification: dict | None) -> bool:
    """A unit is an example when its block_type is code or its qualified unit_type is example/code."""
    block = (unit or {}).get("block_type")
    unit_type = (qualification or {}).get("unit_type")
    return block in EXAMPLE_BLOCK_TYPES or unit_type in EXAMPLE_UNIT_TYPES


def group_has_example(group: dict, units: dict, qualification: dict) -> bool:
    """True when any primary or related unit of the group is an example unit.

    ``units`` and ``qualification`` are unit id -> unit / qualification entry maps; a unit missing
    from either map contributes only what the other map knows about it.
    """
    unit_ids = [u for u in (group.get("unit_ids") or []) if isinstance(u, str)]
    unit_ids += [u for u in (group.get("related_unit_ids") or []) if isinstance(u, str) and u not in unit_ids]
    return any(is_example_unit((units or {}).get(u), (qualification or {}).get(u)) for u in unit_ids)


def pages_for(knowledge_points: int, has_example: bool = False) -> int:
    """Page count of a lesson: one core page per knowledge point on top of the fixed roles,
    plus a worked_example page when the group has an example unit, clamped to PAGES_RANGE."""
    pages = knowledge_points + PAGES_FIXED + (1 if has_example else 0)
    return max(PAGES_RANGE[0], min(PAGES_RANGE[1], pages))


def budget_for(knowledge_points: int, objectives: int, learning_step: str, has_prereq: bool,
               has_example: bool = False) -> dict:
    pages = pages_for(knowledge_points, has_example)
    quiz = max(QUIZ_ITEMS_PER_LO * objectives, QUIZ_MIN_BLOOM_LEVELS)
    cards_max = min(CARDS_RANGE[1], 2 + knowledge_points)
    exercises = EXERCISES_MAX if learning_step in ("applied", "advanced") else 1
    return {
        "pages": pages,
        "has_example": bool(has_example),           # input recorded so lessons.py can detect a stale budget
        "embedded_questions_max": 2 * (pages - 1),  # conservative reachable slots (see questions)
        "embedded_questions_min": 1,
        "quiz_items": quiz,
        "quiz_min_bloom_levels": QUIZ_MIN_BLOOM_LEVELS,
        "recap_item": bool(has_prereq),
        "cards_min": CARDS_RANGE[0],
        "cards_max": max(CARDS_RANGE[0], cards_max),
        "exercises_max": exercises,
    }


# Briefs (synthesize)
BRIEF_ITEMS_MAX = 30
BRIEF_CITATIONS_PER_ITEM_MAX = 3
BRIEF_INFERENCE_SHARE_MAX = 0.10
BRIEF_COVERAGE_WARN = 0.70     # share of the group's kept units cited by >= 1 item
BRIEF_ITEM_WORDS = (4, 40)

# Verbatim gates (shared/verbatim.py) — fragments are measured in word tokens
VERBATIM_FRAGMENT_MAX = 8      # any non-whitelisted copied run >= this many words fails
VERBATIM_JACCARD5_MAX = {"brief": 0.03, "page": 0.06}
VERBATIM_DENSITY_MAX = {"brief": 1.5, "page": 2.0}
VERBATIM_COVERAGE_UNGROUNDED = 0.02  # coverage below this => "ungrounded" warning

# Fidelity (critic verdicts aggregated by script)
FIDELITY_PRECISION_MIN = 0.95
FIDELITY_RECALL_MIN = 0.90

# Lessons
LESSON_WORDS = (900, 2800)          # error band (8 pages x ~300 words at the top)
LESSON_WORDS_TARGET = (900, 2400)   # above the target max lessons.py warns (lessons.words_over_target)
PAGE_WORDS = {
    "orientation": (150, 250),
    "core": (250, 450),
    "worked_example": (250, 450),
    "pitfalls": (150, 450),
    "recap": (150, 250),
}
PAGE_WORDS_TOLERANCE = 0.15    # +/- share before a warning becomes an error
NEW_TERMS_PER_PAGE_MAX = 4
TERM_DENSITY_RANGE = (0.08, 0.15)   # core pages; registry-term tokens introduced on the page / tokens
UNDEFINED_TERM_RATIO_MAX = 0.02
DEFINITION_WINDOW_CHARS = 150
SENTENCE_MEAN_WORDS_MAX = 22
SENTENCE_FLAG_WORDS = 45
PARAGRAPH_MAX_SENTENCES = 4
EXAMPLE_REQUIRED_OVER_WORDS = 200
PITFALLS_RANGE = (2, 4)
RECAP_TAKEAWAYS = (3, 5)
COHERENCE_FLAGGED_PARAGRAPHS_PER_PAGE_MAX = 1
CHUNK_TOKENS_SOFT_LESSON = (150, 900)
CHUNK_TOKENS_HARD = (50, 2500)
IMAGE_PLACEHOLDER_RE = re.compile(r"!\[(?P<alt>[^\]]*)\]\(image:(?P<id>[A-Za-z0-9._:-]+)\)")

# Questions
QUESTION_PROMPT_WORDS = (5, 150)
QUOTE_COPY_CONSECUTIVE_WORDS = 6   # stem shares >= this many consecutive words with its quote => quote-copy
ACCEPTANCE_MAX_CHARS = 600
MCQ_OPTIONS = 4
MCQ_FLAWS_MAX = 1              # 0-1 item-writing flaws pass, >= 2 reject
MCQ_LONGEST_OPTION_RATIO = 0.80  # second-longest < this share of the key length => "longest option" flaw
VARIANT_SIMILARITY_MAX = 0.70  # normalized SequenceMatcher ratio to the original; >= rejects the variant
TEST_STUDENT_CORRECT_MIN = 0.80
EXERCISE_HINTS = 3

# Cards
CARD_FRONT_MAX_WORDS = 25      # code cards exempt
CARD_BACK_MAX_WORDS = {
    "concept": 15, "cloze": 15, "application": 25, "comparison": 25,
    "discrimination": 25, "code": 40, "ordering": 40,
}
CARD_OVERLAP_MAX = 0.50        # front/back content-word overlap
CLOZE_SENTENCE_MAX_WORDS = 25
CLOZE_HINT_MAX_WORDS = 6
ORDERING_ITEMS_MAX = 5
CARD_DEDUP_RATIO = 0.85
CARD_DEDUP_BAND = 0.75
CARD_MIN_TYPES = 3

# Critique
REPAIR_ROUNDS_MAX = 2
ACTIONABLE_ITEMS_MAX = 3
REPAIR_DIFF_MAX_RATIO = 0.5
CRITIQUE_SCORE_MIN = 0.80      # weighted positive score / max positive score
WEIGHT_CRITICAL = 5
WEIGHT_MINOR = 1
WEIGHT_UNDESIRABLE = -5

# ---------------------------------------------------------------------------
# Critique criteria catalogue: stage -> [(criterion_id, weight, critical, description)]
# "critical" criteria must all pass; weighted score over positive weights must reach CRITIQUE_SCORE_MIN.
# Per-sub-item stages (qualification, questions, cards) use PER_ITEM_CRITERIA.
# ---------------------------------------------------------------------------

Criterion = tuple[str, int, bool, str]

CRITERIA: dict[str, list[Criterion]] = {
    "groups": [
        ("group.one_generalization", WEIGHT_CRITICAL, True,
         "The group teaches exactly one generalization; its knowledge points are facets of it, not separate topics."),
        ("group.knowledge_points_distinct", WEIGHT_CRITICAL, True,
         "Each knowledge point adds one distinct twist; none duplicates another or spans two concepts."),
        ("group.objectives_observable", WEIGHT_CRITICAL, True,
         "Every objective names an observable action at a Bloom level the units can support."),
        ("group.title_clear", WEIGHT_MINOR, False,
         "Title is a noun phrase or question, no source branding, no chapter numbering."),
        ("group.watch_real", WEIGHT_MINOR, False,
         "Watch items are genuine misconceptions/boundaries a learner would plausibly hold, grounded in units."),
        ("group.ordering_sound", WEIGHT_MINOR, False,
         "Prerequisites and sort order follow what builds on what, not the source table of contents."),
    ],
    "syntheses": [
        ("fidelity.precision", WEIGHT_CRITICAL, True,
         f"Share of claims Supported by their cited units >= {FIDELITY_PRECISION_MIN} (computed from `claims`)."),
        ("fidelity.recall", WEIGHT_CRITICAL, True,
         f"Share of the group's knowledge points covered by >= 1 map item >= {FIDELITY_RECALL_MIN}."),
        ("brief.no_inference_as_fact", WEIGHT_CRITICAL, True,
         "Every item that is not literally supported by a unit is tagged kind=inference."),
        ("brief.conflicts_preserved", WEIGHT_MINOR, False,
         "Where sources disagree, the disagreement is a watch item, not a silent merge."),
        ("brief.terminology_defined", WEIGHT_MINOR, False,
         "Every term the lesson will need is in `terminology` with a unit-grounded definition."),
    ],
    "lessons": [
        ("fidelity.precision", WEIGHT_CRITICAL, True,
         f"Share of page claims Supported by brief + units >= {FIDELITY_PRECISION_MIN}."),
        ("fidelity.recall", WEIGHT_CRITICAL, True,
         f"Share of each page's assigned brief items actually taught >= {FIDELITY_RECALL_MIN}."),
        ("depth.mechanism_explained", WEIGHT_CRITICAL, True,
         "Core pages explain how/why (mechanism, cause, consequence), not only what something is called."),
        ("structure.one_knowledge_point_per_page", WEIGHT_CRITICAL, True,
         "No core page teaches two knowledge points; no knowledge point is split across pages without a reason."),
        ("examples.present_where_required", WEIGHT_CRITICAL, True,
         "Kinds-of concepts have an example and a non-example; procedures have a worked example; mechanisms have a concrete case."),
        ("pages.standalone", WEIGHT_CRITICAL, True,
         "Each page can be read alone: starts with a full sentence, restates what it depends on, no dangling references."),
        ("objectives.aligned", WEIGHT_CRITICAL, True,
         "Every objective is taught by >= 1 page and every page serves >= 1 objective; nothing is promised that is not taught."),
        ("coherence.paragraph_errors", WEIGHT_CRITICAL, True,
         f"<= {COHERENCE_FLAGGED_PARAGRAPHS_PER_PAGE_MAX} paragraph per page flagged for omission, discontinuity, duplication, inconsistency or language."),
        ("pedagogy.hook_recap_misconceptions", WEIGHT_MINOR, False,
         "Orientation has a hook and why-it-matters; pitfalls has 2-4 specific misconceptions with remediation; recap restates the generalization."),
        ("register.second_person_terms_bolded_once", WEIGHT_MINOR, False,
         "Second-person explanatory register; each key term bolded exactly once at its definition."),
        ("bloom.objectives_relabel_match", WEIGHT_MINOR, False,
         "Critic's independent Bloom label for each objective matches the author's."),
    ],
}

PER_ITEM_CRITERIA: dict[str, list[Criterion]] = {
    "qualification": [
        ("tags.concepts_agree", WEIGHT_CRITICAL, True, "Critic's blind concept tags overlap the author's (>= 1 shared concept)."),
        ("tags.unit_type_agrees", WEIGHT_CRITICAL, True, "Critic's blind unit_type equals the author's."),
        ("tags.keep_agrees", WEIGHT_MINOR, False, "Critic agrees with keep/drop."),
    ],
    "questions": [
        ("q.answerable", WEIGHT_CRITICAL, True,
         "Re-deriving the answer from the page/units alone (before reading the key) yields the keyed answer."),
        ("q.central", WEIGHT_CRITICAL, True, "Tests the objective's knowledge point, not a detail or a literal."),
        ("q.acceptance_is_relationship", WEIGHT_CRITICAL, True,
         "acceptance.causal_reason names a relationship expressible in several wordings, not a specific phrase."),
        ("q.not_leading", WEIGHT_CRITICAL, True, "The stem does not reveal the outcome it asks about."),
        ("q.probe_matrix_discriminating", WEIGHT_CRITICAL, True,
         "Blind-grading the correct probe against acceptance passes and the wrong probe fails."),
        ("q.understandable_clear", WEIGHT_MINOR, False, "Grammatical, unambiguous, one interrogative."),
        ("q.bloom_relabel_match", WEIGHT_MINOR, False, "Critic's independent Bloom label matches the author's."),
        ("q.answer_leaks_on_page", WEIGHT_UNDESIRABLE, False,
         "UNDESIRABLE: the keyed answer appears verbatim on the page before a check question (passed=false when it does)."),
    ],
    "cards": [
        ("card.correct", WEIGHT_CRITICAL, True, "Every claim on front and back is true per the cited units."),
        ("card.answerable_from_front_alone", WEIGHT_CRITICAL, True,
         "A learner who read the lesson can produce the single intended answer from the front alone."),
        ("card.single_filler", WEIGHT_CRITICAL, True, "Cloze/discrimination has exactly one plausible answer (critic lists alternatives otherwise)."),
        ("card.tests_takeaway", WEIGHT_MINOR, False, "Tests a takeaway or relationship, not trivia or wording."),
    ],
}

# ---------------------------------------------------------------------------
# Lexicons used by deterministic gates
# ---------------------------------------------------------------------------

OBSERVABLE_VERBS = {
    "list", "define", "name", "identify", "recall", "state", "label", "recognize",
    "explain", "compare", "describe", "summarize", "classify", "contrast", "predict", "distinguish", "interpret", "illustrate",
    "implement", "use", "solve", "demonstrate", "apply", "compute", "derive", "calculate", "execute", "write", "build", "configure",
    "analyze", "debug", "differentiate", "examine", "trace", "diagnose", "decompose", "test", "break down",
    "justify", "choose", "critique", "assess", "evaluate", "judge", "argue", "defend", "select", "recommend", "weigh",
    "design", "construct", "plan", "produce", "create", "compose", "propose", "formulate", "refactor",
}
WEAK_OBJECTIVE_VERBS = {"understand", "know", "appreciate", "learn", "be familiar", "be aware", "grasp", "realize"}
WEAK_GENERALIZATION_VERBS = {"is", "are", "has", "have", "affects", "affect", "impacts", "impact", "influences", "influence"}

TOO_BROAD_CONCEPT_LABELS = {
    "software", "software engineering", "computer science", "programming", "architecture", "system", "systems",
    "design", "development", "technology", "engineering", "best practices", "introduction", "overview", "basics",
}

# Page furniture / extraction artifacts that must never appear in a lesson page
PAGE_FURNITURE_PATTERNS = [
    re.compile(r"^\s*\d{1,4}\s*$", re.MULTILINE),                       # bare page number line
    re.compile(r"From the Library of", re.IGNORECASE),
    re.compile(r"^\s*\d+\s*\|\s*Chapter\b.*$", re.MULTILINE | re.IGNORECASE),
    re.compile(r"^\s*CHAPTER\s+\d+\s*$", re.MULTILINE),
    re.compile(r"[ﬀ-ﬆ]"),                                      # ligature glyphs
    re.compile(r"Click here to view code image", re.IGNORECASE),
    re.compile(r"Want to learn more about this pattern\?", re.IGNORECASE),
]
DANGLING_REFERENCE_PATTERNS = [
    re.compile(r"\b(this|the previous|the next|the following|the preceding) (chapter|book|section)\b", re.IGNORECASE),
    re.compile(r"\b(as (we )?(saw|discussed|mentioned) (above|earlier|before|previously))\b", re.IGNORECASE),
    re.compile(r"\b(later in this (chapter|book))\b", re.IGNORECASE),
    re.compile(r"\bFigure\s+\d+[-.]\d+\b"),                             # figure reference without an attached image
]

LITERAL_RECALL_PATTERNS = [
    re.compile(r"\bwhat (is|was) the (exact )?(value|number|name|date|year|version|port|default value) of\b", re.IGNORECASE),
    re.compile(r"\bwhich (file|line|page|chapter|section|method name|function name)\b", re.IGNORECASE),
    re.compile(r"\bhow many (pages|chapters|sections|lines)\b", re.IGNORECASE),
]
CAUSAL_CUE_RE = re.compile(
    r"\b(why|because|explain|reason|what (would )?(change|happen|break)s?|how does|how would|compare|instead|even if|"
    r"despite|what if|under what condition|trade-?off|consequence|effect)\b",
    re.IGNORECASE,
)
YES_NO_STEM_RE = re.compile(r"^\s*(is|are|does|do|can|could|should|will|would|has|have|did|was|were)\b", re.IGNORECASE)
ABSOLUTE_TERMS_RE = re.compile(r"\b(always|never|all|none|every|completely|entirely|only ever)\b", re.IGNORECASE)
ALL_NONE_OF_ABOVE_RE = re.compile(r"\b(all|none) of the above\b", re.IGNORECASE)
NEGATIVE_STEM_RE = re.compile(r"\b(not|except|incorrect|false|least|never)\b", re.IGNORECASE)
PRONOUN_ONLY_FRONT_RE = re.compile(r"^\s*(what|why|how|when|where|which)\b[^A-Za-z]*\b(it|this|that|they|these|those|the former|the latter)\b", re.IGNORECASE)
COMPOUND_FRONT_RE = re.compile(r"\b(and|as well as)\b", re.IGNORECASE)
GIVE_EXAMPLE_FRONT_RE = re.compile(r"\b(give|name|list) (an|one|some|three|two) example", re.IGNORECASE)
STOPWORDS = {
    "the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "is", "are", "was", "were", "be", "been", "it", "its",
    "that", "this", "these", "those", "with", "as", "by", "at", "from", "into", "than", "then", "so", "if", "but", "not",
    "no", "do", "does", "did", "can", "could", "should", "would", "will", "may", "might", "which", "who", "whom", "what",
    "when", "where", "why", "how", "you", "your", "we", "our", "they", "their", "them", "he", "she", "his", "her", "i",
}

# ---------------------------------------------------------------------------
# Finding + tiny I/O helpers shared by check() functions
# ---------------------------------------------------------------------------


@dataclass
class Finding:
    check_id: str
    severity: str  # "error" | "warning" | "info"
    location: str
    message: str

    def as_dict(self) -> dict:
        return {"check_id": self.check_id, "severity": self.severity, "location": self.location, "message": self.message}


def load_json(path: Path):
    with Path(path).open(encoding="utf-8") as f:
        return json.load(f)


def dump_json(path: Path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def pipeline_mode(input_dir: Path) -> str:
    """Read manifest.pipeline; absent => raw (back-compat with old outputs)."""
    manifest_path = Path(input_dir) / "manifest.json"
    try:
        manifest = load_json(manifest_path)
    except (OSError, json.JSONDecodeError):
        return PIPELINE_RAW
    mode = manifest.get("pipeline") or PIPELINE_RAW
    return mode if mode in PIPELINES else PIPELINE_RAW


def normalize_ws(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()


def word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", text or ""))


def content_words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9][a-z0-9'-]*", (text or "").lower()) if w not in STOPWORDS and len(w) > 1}


def quote_in_unit(quote: str, unit_text: str) -> bool:
    """Whitespace- and case-insensitive substring check used for every provenance quote."""
    q = normalize_ws(quote).lower()
    return bool(q) and q in normalize_ws(unit_text).lower()


def list_item_files(stage_dir: Path) -> list[Path]:
    """Authored per-item files in a stage directory (excludes work/ and *.work.json)."""
    stage_dir = Path(stage_dir)
    if not stage_dir.is_dir():
        return []
    return sorted(p for p in stage_dir.glob("*.json") if not p.name.endswith(".work.json"))


def item_id_to_filename(item_id: str) -> str:
    """g:event-sourcing -> g_event-sourcing.json (colons are awkward in paths)."""
    return item_id.replace(":", "_").replace("#", "__") + ".json"


def filename_to_item_id(filename: str) -> str:
    stem = Path(filename).name
    if stem.endswith(".json"):
        stem = stem[:-5]
    return stem.replace("__", "#").replace("_", ":", 1)
