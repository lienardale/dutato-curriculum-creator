"""
Pipeline artifact validator — machine-checks curriculum checkpoint files.

This is a PURE DATA UTILITY — no AI calls. It verifies the mechanical
quality gates that used to live only in rubrics/review_checklist.md:
schema-shape of each checkpoint, stale source_sections references,
extracted-section coverage, chunk token ranges, prerequisite DAG cycles,
Bloom's level enums, breadth/balance bounds, and condensation plan
integrity. Judgment gates (readability, naming, solution correctness)
remain the operating agent's job.

Two pipelines share this entry point; ``manifest.pipeline`` (read through
``contracts.pipeline_mode``, absent => ``raw``) selects the stage list:

* **raw** (legacy ``structure.json`` with ``source_sections`` -> chunk_bridge):
  manifest, extracted, exploration, structure, chunks, exercises, review,
  condensation — every gate lives in this file.
* **lesson** (docs/artifacts.md): manifest, extracted, units, qualification,
  concepts, groups, syntheses, lessons, chunks, structure, questions, cards,
  critique, review. Each family delegates to its stage module's
  ``check(input_dir)`` (qualify / regroup / synthesize / lessons / questions /
  cards) and converts the module's findings 1:1 — no gate is duplicated here.
  ``qualify.check`` covers three families; its findings are split by check_id
  prefix (``units.`` / ``qualification.`` / ``concepts.``). ``structure`` and
  ``chunks`` keep this file's integrity checks but skip the raw-only
  extracted-section coverage and breadth heuristics (``source_sections`` is
  empty by contract; regroup owns breadth) and use
  ``contracts.CHUNK_TOKENS_SOFT_LESSON`` for the chunk token band. The
  ``critique`` family reads ``critique/summary.json``: items in status
  ``fail`` / ``FAILED`` are errors at ``--stage upload`` and warnings
  otherwise, and every critiqued stage whose author artifacts exist but has
  no (or a pending) verdict yields a ``critique.pending`` warning. The raw-only
  families (exploration, exercises, condensation) still run in lesson mode
  when their file exists. ``--stage upload`` in lesson mode requires manifest,
  structure, chunks, lessons, questions, cards and the critique summary (with
  no failing item), escalates ``questions.missing`` / ``cards.missing`` to
  errors, and needs ``review.json`` with ``approved: true``.

Usage:
    python validate.py output/<name>/                  # validate every stage whose checkpoint exists
    python validate.py output/<name>/ --stage structure
    python validate.py output/<name>/ --stage lessons  # lesson mode: any lesson-pipeline family
    python validate.py output/<name>/ --stage upload   # pre-upload gate (requires review approval)
    python validate.py output/<name>/ --json           # also write validation_report.json
    python validate.py output/<name>/ --strict         # warnings fail too

Exit codes: 0 = clean, 1 = errors found (or warnings with --strict).

Findings are {check_id, severity, location, message}; severity is "error",
"warning" or (stage-module informational findings in lesson mode) "info".
The check_id values are referenced by rubrics/review_checklist.md and
embedded into review.json under the "validation" key.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import _compat  # noqa: F401

from chunk import count_tokens
from contracts import (
    CARDS_DIR,
    CHUNK_TOKENS_HARD,
    CHUNK_TOKENS_SOFT_LESSON,
    CRITIQUE_SUMMARY,
    CRITIQUED_STAGES,
    GROUP_ID_RE,
    GROUPS_FILE,
    LESSONS_DIR,
    PIPELINE_LESSON,
    PIPELINES,
    QUALIFICATION_DIR,
    QUESTIONS_DIR,
    SYNTHESES_DIR,
    UNITS_DIR,
    filename_to_item_id,
    item_id_to_filename,
    list_item_files,
    pipeline_mode,
)
from normalize_titles import PREFIX_PATTERNS, is_book_meta

BLOOM_LEVELS = {"remember", "understand", "apply", "analyze", "evaluate", "create"}
VARIANTS = {"extensive", "detailed", "classic", "core"}
CONDENSE_TIERS = ("detailed", "classic", "core")
TIER_TARGET_RATIO = {"detailed": 0.10, "classic": 0.01, "core": 0.001}
GAP_SEVERITIES = {"critical", "recommended", "nice-to-have"}
PREREQ_STRENGTHS = {"required", "recommended"}

# Token bounds: warn outside the soft range, error outside the hard range.
# Condensed variants legitimately run shorter chunks (AGENT.md: 200-2000).
SOFT_RANGE_EXTENSIVE = (500, 1500)
SOFT_RANGE_VARIANT = (200, 2000)
HARD_RANGE = (50, 2500)

LEVEL_TARGET_PCT = {1: 30, 2: 40, 3: 30}
LEVEL_TOLERANCE_PCT = 15

# Raw-pipeline stage families (STAGE_CHECKS below maps each to its checkpoint + check).
STAGES = [
    "manifest", "extracted", "exploration", "structure",
    "chunks", "exercises", "review", "condensation",
]

# Lesson-pipeline stage families in pipeline order (docs/artifacts.md).
LESSON_STAGES = [
    "manifest", "extracted", "units", "qualification", "concepts", "groups",
    "syntheses", "lessons", "chunks", "structure", "questions", "cards",
    "critique", "review",
]
# Checkpoint per lesson-mode family (directories hold authored per-item files).
LESSON_ARTIFACTS = {
    "manifest": "manifest.json",
    "extracted": "extracted",
    "units": UNITS_DIR,
    "qualification": QUALIFICATION_DIR,
    "concepts": "concepts.json",
    "groups": GROUPS_FILE,
    "syntheses": SYNTHESES_DIR,
    "lessons": LESSONS_DIR,
    "chunks": "chunks.json",
    "structure": "structure.json",
    "questions": QUESTIONS_DIR,
    "cards": CARDS_DIR,
    "critique": CRITIQUE_SUMMARY,
    "review": "review.json",
}
# Raw-only families that still run in lesson mode when their checkpoint exists.
LESSON_OPTIONAL_RAW_STAGES = ["exploration", "exercises", "condensation"]
LESSON_ONLY_STAGES = [s for s in LESSON_STAGES if s not in STAGES]
LESSON_UPLOAD_REQUIRED = {"manifest", "structure", "chunks", "lessons", "questions", "cards", "critique"}
# Stage-module warnings that become errors at --stage upload (a lesson without practice).
LESSON_UPLOAD_ESCALATED = {"questions.missing", "cards.missing"}
# Per-family check_id prefixes of qualify.check (everything else lands in "units").
_QUALIFY_FAMILY_PREFIX = {"qualification": "qualification.", "concepts": "concepts."}
# Author artifact location per critiqued stage (for the critique.pending warning).
_CRITIQUED_ARTIFACT_DIRS = {
    "qualification": QUALIFICATION_DIR, "syntheses": SYNTHESES_DIR, "lessons": LESSONS_DIR,
    "questions": QUESTIONS_DIR, "cards": CARDS_DIR,
}
_SEVERITY_RANK = {"error": 0, "warning": 1, "info": 2}


@dataclass
class Finding:
    check_id: str
    severity: str  # "error" | "warning" | "info"
    location: str
    message: str


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _norm(text: str) -> str:
    """Whitespace normalization identical to chunk_bridge._normalize."""
    return (text or "").replace("\xa0", " ").strip()


def _key(text: str) -> str:
    """Matching key: chunk_bridge indexes both exact-norm and lowercase-norm,
    so case-insensitive normalized comparison reflects what will match."""
    return _norm(text).lower()


def _load_json(path: Path):
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _topics_of(structure) -> list[dict]:
    return structure if isinstance(structure, list) else structure.get("topics", [])


def _walk(topics: list[dict], path: tuple[str, ...] = (), depth: int = 0):
    """Yield (topic, path_including_self, nesting_depth) for every node."""
    for topic in topics:
        title = topic.get("title", "")
        node_path = path + (title,)
        yield topic, node_path, depth
        yield from _walk(topic.get("children", []), node_path, depth + 1)


def _extracted_titles(extracted_dir: Path) -> dict[str, list[str]]:
    """Map extracted filename -> list of section titles."""
    out: dict[str, list[str]] = {}
    for path in sorted(extracted_dir.glob("*.json")):
        try:
            data = _load_json(path)
        except (json.JSONDecodeError, OSError):
            continue
        out[path.name] = [
            (s.get("title") or "") for s in data.get("sections", [])
        ]
    return out


def _has_unstripped_prefix(title: str) -> bool:
    t = _norm(title)
    return any(pat.sub("", t) != t for pat in PREFIX_PATTERNS)


# ---------------------------------------------------------------------------
# Stage checks
# ---------------------------------------------------------------------------

def check_manifest(input_dir: Path) -> list[Finding]:
    findings: list[Finding] = []
    path = input_dir / "manifest.json"
    try:
        manifest = _load_json(path)
    except json.JSONDecodeError as e:
        return [Finding("manifest.invalid_json", "error", path.name, str(e))]

    for k in ("name", "domain", "sources"):
        if not manifest.get(k):
            findings.append(Finding(
                "manifest.missing_key", "error", path.name,
                f"Required key {k!r} is missing or empty",
            ))
    for k in ("domain_family", "variant", "description"):
        if not manifest.get(k):
            findings.append(Finding(
                "manifest.missing_key", "warning", path.name,
                f"Key {k!r} is missing (upload.py back-fills family/variant, "
                f"but the manifest should be explicit)",
            ))

    variant = manifest.get("variant")
    if variant and variant not in VARIANTS:
        findings.append(Finding(
            "manifest.invalid_variant", "error", path.name,
            f"variant={variant!r} not in {sorted(VARIANTS)}",
        ))

    pipeline = manifest.get("pipeline")
    if pipeline is None:
        findings.append(Finding(
            "manifest.missing_key", "warning", path.name,
            f"Key 'pipeline' is missing (defaults to 'raw'; set one of {sorted(PIPELINES)} explicitly)",
        ))
    elif pipeline not in PIPELINES:
        findings.append(Finding(
            "manifest.invalid_pipeline", "error", path.name,
            f"pipeline={pipeline!r} not in {sorted(PIPELINES)}",
        ))

    for i, src in enumerate(manifest.get("sources") or []):
        if not isinstance(src, dict) or not src.get("type") or not src.get("path"):
            findings.append(Finding(
                "manifest.source_fields", "error", f"{path.name} sources[{i}]",
                "Each source needs non-empty 'type' and 'path'",
            ))

    return findings


def check_extracted(input_dir: Path) -> list[Finding]:
    findings: list[Finding] = []
    extracted_dir = input_dir / "extracted"
    by_file = _extracted_titles(extracted_dir)
    if not by_file:
        return [Finding(
            "extracted.no_files", "error", "extracted/",
            "No extracted JSON files found",
        )]

    seen: dict[str, str] = {}  # normalized title -> "file::title"
    for fname, titles in by_file.items():
        data = _load_json(extracted_dir / fname)
        sections = data.get("sections", [])
        if not sections:
            findings.append(Finding(
                "extracted.empty_sections", "error", f"extracted/{fname}",
                "File has no sections",
            ))
            continue
        for i, section in enumerate(sections):
            title = (section.get("title") or "").strip()
            loc = f"extracted/{fname} sections[{i}]"
            if not title:
                findings.append(Finding(
                    "extracted.untitled_section", "warning", loc,
                    "Section has no title — it cannot be referenced by source_sections",
                ))
                continue
            if not (section.get("content") or "").strip():
                findings.append(Finding(
                    "extracted.empty_content", "warning", loc,
                    f"Section {title!r} has empty content",
                ))
            if is_book_meta(title) or _has_unstripped_prefix(title):
                findings.append(Finding(
                    "extracted.unnormalized_title", "warning", loc,
                    f"Title {title!r} looks like book-meta or carries a chapter "
                    f"prefix — run normalize_titles.py",
                ))
            k = _key(title)
            prev = seen.get(k)
            if prev and not prev.startswith(f"{fname}::"):
                findings.append(Finding(
                    "extracted.duplicate_title", "warning", loc,
                    f"Title {title!r} also appears in {prev.split('::')[0]} — "
                    f"title-keyed matching (chunker, condense) is ambiguous",
                ))
            seen.setdefault(k, f"{fname}::{title}")

    return findings


def check_exploration(input_dir: Path) -> list[Finding]:
    findings: list[Finding] = []
    path = input_dir / "exploration.json"
    try:
        data = _load_json(path)
    except json.JSONDecodeError as e:
        return [Finding("exploration.invalid_json", "error", path.name, str(e))]

    for k in ("concepts", "per_source_analysis"):
        if k not in data:
            findings.append(Finding(
                "exploration.missing_key", "error", path.name,
                f"Required key {k!r} is missing",
            ))
    for k in ("gaps", "concept_order", "coverage_summary"):
        if k not in data:
            findings.append(Finding(
                "exploration.missing_key", "warning", path.name,
                f"Key {k!r} is missing",
            ))

    extracted_files = set(_extracted_titles(input_dir / "extracted"))
    analyzed = set((data.get("per_source_analysis") or {}))
    for fname in analyzed - extracted_files:
        findings.append(Finding(
            "exploration.unknown_source", "warning", path.name,
            f"per_source_analysis references {fname!r}, which is not in extracted/",
        ))
    for fname in extracted_files - analyzed:
        findings.append(Finding(
            "exploration.missing_source", "warning", path.name,
            f"Extracted source {fname!r} has no per_source_analysis entry",
        ))

    for i, gap in enumerate(data.get("gaps") or []):
        severity = gap.get("severity")
        loc = f"{path.name} gaps[{i}]"
        if severity not in GAP_SEVERITIES:
            findings.append(Finding(
                "exploration.invalid_gap_severity", "warning", loc,
                f"severity={severity!r} not in {sorted(GAP_SEVERITIES)}",
            ))
        if severity == "critical" and not gap.get("resolution"):
            findings.append(Finding(
                "exploration.unresolved_critical_gap", "warning", loc,
                f"Critical gap {gap.get('concept', '?')!r} has no 'resolution' "
                f"(expected 'user_accepted', 'source_added: <name>', or 'descoped')",
            ))

    return findings


def check_structure(input_dir: Path, lesson_mode: bool | None = None) -> list[Finding]:
    """Integrity of structure.json. In lesson mode (``lessons.py build`` output; auto-detected
    from the manifest when ``lesson_mode`` is None) the extracted-section coverage checks and
    the raw breadth / children-count / level-distribution heuristics are skipped: leaves carry
    ``source_sections: []`` by contract and regroup.check owns the breadth rules."""
    findings: list[Finding] = []
    path = input_dir / "structure.json"
    try:
        topics = _topics_of(_load_json(path))
    except json.JSONDecodeError as e:
        return [Finding("structure.invalid_json", "error", path.name, str(e))]

    if not topics:
        return [Finding("structure.empty", "error", path.name, "No topics defined")]

    if lesson_mode is None:
        lesson_mode = pipeline_mode(input_dir) == PIPELINE_LESSON

    # Extracted titles (absent for condensed variants — section-level checks
    # are skipped there because their source_sections reference original
    # topic titles, not extracted sections — and in lesson mode, where the
    # structure is built from lessons, not from source sections).
    by_file = {} if lesson_mode else _extracted_titles(input_dir / "extracted")
    extracted_keys = {_key(t) for titles in by_file.values() for t in titles if t}
    has_extracted = bool(by_file)

    referenced_keys: set[str] = set()
    leaf_title_keys: set[str] = set()
    title_counts: dict[str, list[str]] = {}
    doc_order: dict[str, int] = {}
    prereq_edges: dict[str, set[str]] = {}  # topic key -> set of prereq keys
    depth0_levels: list = []

    for pos, (topic, node_path, depth) in enumerate(_walk(topics)):
        loc = " > ".join(node_path)
        title = topic.get("title", "")
        if not title:
            findings.append(Finding(
                "structure.missing_field", "error", loc or f"topics[{pos}]",
                "Topic has no title",
            ))
            continue

        title_counts.setdefault(_key(title), []).append(loc)
        doc_order.setdefault(_key(title), pos)

        declared_depth = topic.get("depth")
        if declared_depth is None:
            findings.append(Finding(
                "structure.missing_field", "warning", loc, "Topic has no 'depth' field",
            ))
        elif declared_depth != depth or depth > 2:
            findings.append(Finding(
                "structure.invalid_depth", "error", loc,
                f"depth={declared_depth} but nesting position is {depth}"
                + (" (max depth is 2)" if depth > 2 else ""),
            ))

        children = topic.get("children", [])
        is_leaf = not children

        if depth == 0:
            level = topic.get("suggested_level")
            depth0_levels.append(level)
            if level is None:
                findings.append(Finding(
                    "structure.missing_level", "warning", loc,
                    "Depth-0 topic has no suggested_level",
                ))
            elif level not in (1, 2, 3):
                findings.append(Finding(
                    "structure.invalid_level", "error", loc,
                    f"suggested_level={level!r} must be 1, 2, or 3",
                ))

        if children and not lesson_mode and not (2 <= len(children) <= 8):
            findings.append(Finding(
                "structure.children_count", "warning", loc,
                f"Parent has {len(children)} children (recommended 2-8)",
            ))

        if is_leaf:
            leaf_title_keys.add(_key(title))
            source_sections = topic.get("source_sections") or []
            if len(source_sections) > 10:
                findings.append(Finding(
                    "structure.giant_topic", "warning", loc,
                    f"Leaf maps {len(source_sections)} source sections (max 10 recommended)",
                ))
            if has_extracted:
                if not source_sections:
                    if _key(title) in extracted_keys:
                        findings.append(Finding(
                            "structure.leaf_title_fallback", "warning", loc,
                            "Leaf has no source_sections — the chunker will fall "
                            "back to matching the topic title; make the mapping explicit",
                        ))
                    else:
                        findings.append(Finding(
                            "structure.empty_leaf", "error", loc,
                            "Leaf has no source_sections and its title matches no "
                            "extracted section — it will produce zero content",
                        ))
                for ref in source_sections:
                    if _key(ref) in extracted_keys:
                        referenced_keys.add(_key(ref))
                    else:
                        findings.append(Finding(
                            "structure.stale_ref", "error", loc,
                            f"source_sections entry {ref!r} matches no extracted "
                            f"section title — the chunker will pull nothing for it",
                        ))

        # Objectives
        objectives = topic.get("learning_objectives") or []
        if is_leaf and not (2 <= len(objectives) <= 5):
            findings.append(Finding(
                "structure.objective_count", "warning", loc,
                f"Leaf has {len(objectives)} learning objectives (expected 2-5)",
            ))
        for i, obj in enumerate(objectives):
            if not (obj.get("text") or "").strip():
                findings.append(Finding(
                    "structure.empty_objective", "error", f"{loc} objectives[{i}]",
                    "Learning objective has empty text",
                ))
            bloom = obj.get("bloom_level")
            if bloom not in BLOOM_LEVELS:
                findings.append(Finding(
                    "structure.invalid_bloom", "error", f"{loc} objectives[{i}]",
                    f"bloom_level={bloom!r} not in {sorted(BLOOM_LEVELS)}",
                ))

        # Prerequisites (validated globally after the walk)
        for i, prereq in enumerate(topic.get("prerequisites") or []):
            strength = prereq.get("strength")
            if strength and strength not in PREREQ_STRENGTHS:
                findings.append(Finding(
                    "structure.prereq_strength", "warning", f"{loc} prerequisites[{i}]",
                    f"strength={strength!r} not in {sorted(PREREQ_STRENGTHS)}",
                ))
            prereq_edges.setdefault(_key(title), set()).add(_key(prereq.get("topic", "")))

    # Duplicate titles (collide in title-keyed lookups: upload path_to_id,
    # condense build_chunk_index)
    for key, locs in title_counts.items():
        if len(locs) > 1:
            findings.append(Finding(
                "structure.duplicate_title", "warning", "; ".join(locs),
                f"Title appears {len(locs)} times — title-keyed lookups "
                f"(upload, condense) cannot distinguish them",
            ))

    # Breadth (raw only — regroup.check gates areas / groups per area in lesson mode)
    if not lesson_mode and not (5 <= len(topics) <= 15):
        findings.append(Finding(
            "structure.breadth", "warning", path.name,
            f"{len(topics)} depth-0 topics (recommended 5-15)",
        ))

    # Level distribution (raw only)
    valid_levels = [] if lesson_mode else [lv for lv in depth0_levels if lv in (1, 2, 3)]
    if len(valid_levels) >= 5:
        for level, target in LEVEL_TARGET_PCT.items():
            pct = 100 * valid_levels.count(level) / len(valid_levels)
            if abs(pct - target) > LEVEL_TOLERANCE_PCT:
                findings.append(Finding(
                    "structure.level_distribution", "warning", path.name,
                    f"Level {level} covers {pct:.0f}% of depth-0 topics "
                    f"(target ~{target}% ±{LEVEL_TOLERANCE_PCT})",
                ))

    # Coverage: extracted sections referenced by no topic (title fallback counts)
    if has_extracted:
        covered = referenced_keys | leaf_title_keys
        for fname, titles in by_file.items():
            orphans = [t for t in titles if t and _key(t) not in covered]
            for t in orphans:
                findings.append(Finding(
                    "structure.orphan_section", "warning", f"extracted/{fname}",
                    f"Section {t!r} is referenced by no topic — its content "
                    f"will be silently dropped at chunking",
                ))

    # Prerequisite graph checks
    all_title_keys = set(title_counts)
    for topic_key, prereqs in prereq_edges.items():
        for p in prereqs:
            if p == topic_key:
                findings.append(Finding(
                    "structure.prereq_self", "error", topic_key,
                    "Topic lists itself as a prerequisite",
                ))
            elif p not in all_title_keys:
                findings.append(Finding(
                    "structure.prereq_unknown_topic", "error", topic_key,
                    f"Prerequisite {p!r} matches no topic title in this structure",
                ))
            elif doc_order.get(p, 0) > doc_order.get(topic_key, 0):
                findings.append(Finding(
                    "structure.prereq_order", "warning", topic_key,
                    f"Prerequisite {p!r} appears after its dependent in the "
                    f"structure ordering",
                ))

    findings.extend(_check_prereq_cycles(prereq_edges))
    findings.extend(_check_prereq_transitive(prereq_edges))
    return findings


def _check_prereq_cycles(edges: dict[str, set[str]]) -> list[Finding]:
    """DFS cycle detection over topic -> prerequisite edges."""
    findings: list[Finding] = []
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {}
    reported: set[frozenset] = set()

    def dfs(node: str, stack: list[str]) -> None:
        color[node] = GRAY
        stack.append(node)
        for nxt in edges.get(node, set()):
            if nxt not in edges and nxt not in color:
                continue  # unknown-topic refs are reported separately
            state = color.get(nxt, WHITE)
            if state == GRAY:
                cycle = stack[stack.index(nxt):] + [nxt]
                key = frozenset(cycle)
                if key not in reported:
                    reported.add(key)
                    findings.append(Finding(
                        "structure.prereq_cycle", "error", " -> ".join(cycle),
                        "Prerequisite graph has a cycle — upload.py would skip "
                        "ALL prerequisite insertion",
                    ))
            elif state == WHITE:
                dfs(nxt, stack)
        stack.pop()
        color[node] = BLACK

    for node in edges:
        if color.get(node, WHITE) == WHITE:
            dfs(node, [])
    return findings


def _check_prereq_transitive(edges: dict[str, set[str]]) -> list[Finding]:
    """Warn when A->C exists alongside A->B->...->C."""
    findings: list[Finding] = []

    def reachable(start: str, seen: set[str]) -> set[str]:
        out: set[str] = set()
        for nxt in edges.get(start, set()):
            if nxt in seen:
                continue
            seen.add(nxt)
            out.add(nxt)
            out |= reachable(nxt, seen)
        return out

    for topic, prereqs in edges.items():
        for direct in prereqs:
            others = prereqs - {direct}
            indirect = set()
            for other in others:
                indirect |= reachable(other, {topic, other})
            if direct in indirect:
                findings.append(Finding(
                    "structure.prereq_transitive", "warning", topic,
                    f"Prerequisite {direct!r} is already implied transitively — "
                    f"drop the direct link",
                ))
    return findings


def check_chunks(input_dir: Path, lesson_mode: bool | None = None) -> list[Finding]:
    """Integrity of chunks.json. In lesson mode (one chunk per lesson page; auto-detected
    from the manifest when ``lesson_mode`` is None) the token band is
    ``contracts.CHUNK_TOKENS_SOFT_LESSON`` / ``CHUNK_TOKENS_HARD``."""
    findings: list[Finding] = []
    path = input_dir / "chunks.json"
    try:
        chunks = _load_json(path)
    except json.JSONDecodeError as e:
        return [Finding("chunks.invalid_json", "error", path.name, str(e))]

    if not chunks:
        return [Finding("chunks.empty", "error", path.name, "No chunks produced")]

    if lesson_mode is None:
        lesson_mode = pipeline_mode(input_dir) == PIPELINE_LESSON

    structure_path = input_dir / "structure.json"
    node_paths: set[tuple[str, ...]] = set()
    leaf_paths: set[tuple[str, ...]] = set()
    if structure_path.exists():
        topics = _topics_of(_load_json(structure_path))
        for topic, node_path, _ in _walk(topics):
            node_paths.add(node_path)
            if not topic.get("children"):
                leaf_paths.add(node_path)
    else:
        findings.append(Finding(
            "chunks.missing_structure", "warning", path.name,
            "structure.json not found — skipping topic-path cross-checks",
        ))

    # Variants use a wider acceptable token range (AGENT.md: 200-2000);
    # lesson pages are shorter by contract (docs/artifacts.md Stage 5).
    soft_lo, soft_hi = CHUNK_TOKENS_SOFT_LESSON if lesson_mode else SOFT_RANGE_EXTENSIVE
    hard_lo, hard_hi = CHUNK_TOKENS_HARD if lesson_mode else HARD_RANGE
    manifest_path = input_dir / "manifest.json"
    if not lesson_mode and manifest_path.exists():
        try:
            if _load_json(manifest_path).get("variant") in CONDENSE_TIERS:
                soft_lo, soft_hi = SOFT_RANGE_VARIANT
        except json.JSONDecodeError:
            pass

    by_path: dict[tuple[str, ...], list[int]] = {}
    for i, chunk in enumerate(chunks):
        content = chunk.get("content", "")
        topic_path = tuple(chunk.get("topic_path") or ())
        loc = f"{path.name}[{i}] ({' > '.join(topic_path) or 'no topic_path'})"

        if not content.strip():
            findings.append(Finding(
                "chunks.empty_content", "error", loc, "Chunk content is empty",
            ))
            continue

        tokens = count_tokens(content)
        if tokens < hard_lo or tokens > hard_hi:
            findings.append(Finding(
                "chunks.token_range", "error", loc,
                f"{tokens} tokens — outside hard bounds [{hard_lo}, {hard_hi}]",
            ))
        elif tokens < soft_lo or tokens > soft_hi:
            findings.append(Finding(
                "chunks.token_range", "warning", loc,
                f"{tokens} tokens — outside target range [{soft_lo}, {soft_hi}]",
            ))

        if not topic_path:
            findings.append(Finding(
                "chunks.unknown_topic_path", "error", loc,
                "Chunk has no topic_path",
            ))
        elif node_paths and topic_path not in node_paths:
            findings.append(Finding(
                "chunks.unknown_topic_path", "error", loc,
                "topic_path does not match any topic in structure.json",
            ))
        by_path.setdefault(topic_path, []).append(chunk.get("chunk_index", 0))

    # Every leaf must have at least one chunk (the silent-drop symptom)
    for leaf in sorted(leaf_paths):
        if leaf not in by_path:
            findings.append(Finding(
                "chunks.empty_topic", "error", " > ".join(leaf),
                "Leaf topic has no chunks — source_sections did not match any "
                "extracted content",
            ))

    for topic_path, indices in by_path.items():
        if sorted(indices) != list(range(len(indices))):
            findings.append(Finding(
                "chunks.index_gap", "warning", " > ".join(topic_path),
                f"chunk_index values {sorted(indices)} are not contiguous from 0",
            ))

    return findings


def check_exercises(input_dir: Path) -> list[Finding]:
    findings: list[Finding] = []
    path = input_dir / "exercises.json"
    try:
        entries = _load_json(path)
    except json.JSONDecodeError as e:
        return [Finding("exercises.invalid_json", "error", path.name, str(e))]

    title_keys: set[str] = set()
    structure_path = input_dir / "structure.json"
    if structure_path.exists():
        for topic, _, _ in _walk(_topics_of(_load_json(structure_path))):
            if topic.get("title"):
                title_keys.add(_key(topic["title"]))

    for e_i, entry in enumerate(entries):
        topic_path = entry.get("topic_path") or []
        loc_base = f"{path.name}[{e_i}] ({' > '.join(topic_path) or 'no topic_path'})"
        # upload.py resolves by scanning the path right-to-left for any known title
        if title_keys and not any(_key(t) in title_keys for t in topic_path):
            findings.append(Finding(
                "exercises.unknown_topic_path", "error", loc_base,
                "No element of topic_path matches a topic title — exercises "
                "would be silently skipped at upload",
            ))
        for x_i, ex in enumerate(entry.get("exercises") or []):
            loc = f"{loc_base} exercises[{x_i}]"
            if not (ex.get("problem_statement") or "").strip():
                findings.append(Finding(
                    "exercises.empty_problem", "error", loc,
                    "Exercise has empty problem_statement",
                ))
            bloom = ex.get("bloom_level")
            if bloom not in BLOOM_LEVELS:
                findings.append(Finding(
                    "exercises.invalid_bloom", "error", loc,
                    f"bloom_level={bloom!r} not in {sorted(BLOOM_LEVELS)}",
                ))
            hints = ex.get("hints") or []
            if len(hints) < 3:
                findings.append(Finding(
                    "exercises.few_hints", "warning", loc,
                    f"{len(hints)} hints (rubric expects 3 progressive hints)",
                ))
            if not (ex.get("expected_solution") or "").strip():
                findings.append(Finding(
                    "exercises.missing_solution", "warning", loc,
                    "Exercise has no expected_solution",
                ))
            difficulty = ex.get("difficulty")
            if not isinstance(difficulty, int):
                findings.append(Finding(
                    "exercises.invalid_difficulty", "warning", loc,
                    f"difficulty={difficulty!r} should be an integer",
                ))

    return findings


def check_review(input_dir: Path) -> list[Finding]:
    findings: list[Finding] = []
    path = input_dir / "review.json"
    try:
        review = _load_json(path)
    except json.JSONDecodeError as e:
        return [Finding("review.invalid_json", "error", path.name, str(e))]

    if "approved" not in review:
        findings.append(Finding(
            "review.missing_key", "error", path.name, "Missing 'approved' key",
        ))
    elif not isinstance(review["approved"], bool):
        findings.append(Finding(
            "review.missing_key", "error", path.name,
            f"'approved' must be a boolean, got {type(review['approved']).__name__}",
        ))
    if "validation" not in review:
        findings.append(Finding(
            "review.no_validation", "warning", path.name,
            "review.json has no 'validation' key — run "
            "`python validate.py <dir> --json` and embed the summary",
        ))
    return findings


def check_condensation(input_dir: Path) -> list[Finding]:
    findings: list[Finding] = []
    path = input_dir / "condensation_plan.json"
    try:
        plan = _load_json(path)
    except json.JSONDecodeError as e:
        return [Finding("condensation.invalid_json", "error", path.name, str(e))]

    # Original curriculum context
    title_keys: set[str] = set()
    structure_path = input_dir / "structure.json"
    if structure_path.exists():
        for topic, _, _ in _walk(_topics_of(_load_json(structure_path))):
            if topic.get("title"):
                title_keys.add(_key(topic["title"]))

    chunk_tokens_by_leaf: dict[str, int] = {}
    total_tokens = 0
    chunks_path = input_dir / "chunks.json"
    if chunks_path.exists():
        for chunk in _load_json(chunks_path):
            tokens = chunk.get("token_count") or count_tokens(chunk.get("content", ""))
            total_tokens += tokens
            tp = chunk.get("topic_path") or []
            if tp:
                k = _key(tp[-1])
                chunk_tokens_by_leaf[k] = chunk_tokens_by_leaf.get(k, 0) + tokens

    for tier in plan:
        if tier not in CONDENSE_TIERS:
            findings.append(Finding(
                "condensation.unknown_tier", "warning", path.name,
                f"Tier {tier!r} not in {list(CONDENSE_TIERS)}",
            ))

    def check_leaf(leaf: dict, loc: str) -> int:
        """Validate one plan leaf; return its estimated token contribution."""
        strategy = leaf.get("condensation_strategy", "keep")
        content = leaf.get("content")
        source_keys = leaf.get(
            "source_children", leaf.get("source_topics", [leaf.get("title", "")])
        )

        if strategy in ("merge", "synthesize") and content is None:
            findings.append(Finding(
                "condensation.missing_content", "error", loc,
                f"Strategy {strategy!r} requires an inline 'content' field — "
                f"condense.py would degrade to raw concatenation",
            ))

        if title_keys:
            for key in source_keys:
                if _key(key) not in title_keys:
                    findings.append(Finding(
                        "condensation.unknown_source", "error", loc,
                        f"Source reference {key!r} matches no topic title in the "
                        f"original structure",
                    ))

        if content is not None:
            return count_tokens(content)
        kept = sum(chunk_tokens_by_leaf.get(_key(k), 0) for k in source_keys)
        if chunk_tokens_by_leaf and strategy == "keep" and kept == 0:
            findings.append(Finding(
                "condensation.source_no_chunks", "warning", loc,
                "Strategy 'keep' but the referenced sources have no chunks "
                "(references must name LEAF topic titles)",
            ))
        return kept

    for tier, topics in plan.items():
        if tier not in CONDENSE_TIERS or not isinstance(topics, list):
            continue
        tier_tokens = 0
        for topic in topics:
            loc = f"{path.name} {tier} > {topic.get('title', '?')}"
            children = topic.get("children") or []
            if not children:
                tier_tokens += check_leaf(topic, loc)
            else:
                for child in children:
                    tier_tokens += check_leaf(
                        child, f"{loc} > {child.get('title', '?')}"
                    )

        if total_tokens:
            target = total_tokens * TIER_TARGET_RATIO[tier]
            if tier_tokens and not (target / 3 <= tier_tokens <= target * 3):
                findings.append(Finding(
                    "condensation.budget", "warning", f"{path.name} {tier}",
                    f"~{tier_tokens:,} tokens vs target ~{target:,.0f} "
                    f"({TIER_TARGET_RATIO[tier]:.1%} of {total_tokens:,}) — "
                    f"outside 3x tolerance",
                ))

    return findings


def _item_files(stage_dir: Path) -> list[Path]:
    """Authored per-item files of a ``g:<slug>``-keyed directory: names that map back to a
    group id (``lessons/anchor.json`` and other helper files are not items)."""
    return [p for p in list_item_files(stage_dir) if GROUP_ID_RE.match(filename_to_item_id(p.name))]


def _critiqued_artifacts_present(input_dir: Path, stage: str) -> bool:
    """True when the author artifacts of a critiqued stage exist (so a critique is due)."""
    if stage == "groups":
        return (input_dir / GROUPS_FILE).exists()
    if stage == "qualification":  # keyed by source, not by g:<slug>
        return bool(list_item_files(input_dir / QUALIFICATION_DIR))
    return bool(_item_files(input_dir / _CRITIQUED_ARTIFACT_DIRS[stage]))


def check_critique(input_dir: Path, upload: bool = False) -> list[Finding]:
    """Read critique/summary.json (written by ``critique.py check``): failing items are
    errors at --stage upload and warnings otherwise; critiqued stages whose author
    artifacts exist but have no verdicts (or pending ones) warn ``critique.pending``."""
    findings: list[Finding] = []
    path = input_dir / CRITIQUE_SUMMARY
    try:
        summary = _load_json(path)
    except json.JSONDecodeError as e:
        return [Finding("critique.invalid_json", "error", CRITIQUE_SUMMARY, str(e))]
    stages = summary.get("stages") if isinstance(summary, dict) else None
    if not isinstance(stages, dict):
        return [Finding(
            "critique.shape", "error", CRITIQUE_SUMMARY,
            "summary must be an object with a 'stages' map — run `critique.py check <stage>`",
        )]

    failing_severity = "error" if upload else "warning"
    for stage in CRITIQUED_STAGES:
        data = stages.get(stage)
        by_item = data.get("by_item") if isinstance(data, dict) else None
        if not isinstance(by_item, dict):
            by_item = {}
        pending: list[str] = []
        for item_id, entry in sorted(by_item.items()):
            status = entry.get("status") if isinstance(entry, dict) else None
            loc = f"critique/{stage}/{item_id_to_filename(item_id)}"
            if status in ("fail", "FAILED"):
                detail = ", ".join(entry.get("critical_failed") or []) or "see the verdict"
                findings.append(Finding(
                    "critique.item_failed", failing_severity, loc,
                    f"{status} at round {entry.get('round')} ({detail})"
                    + (" — fix or drop the item before uploading" if upload else ""),
                ))
            elif status != "pass":  # "pending" (no or stale verdict) or an unknown status
                pending.append(item_id)
        if stage not in stages and _critiqued_artifacts_present(input_dir, stage):
            findings.append(Finding(
                "critique.pending", "warning", f"critique/{stage}/",
                f"{stage} artifacts exist but no critique verdict was checked — "
                f"run `critique.py prepare {stage}`, the critic, then `critique.py check {stage}`",
            ))
        elif pending:
            findings.append(Finding(
                "critique.pending", "warning", f"critique/{stage}/",
                f"{len(pending)} item(s) await a critique verdict: {', '.join(pending[:10])}"
                + (f" (+{len(pending) - 10} more)" if len(pending) > 10 else ""),
            ))
    return findings


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

STAGE_CHECKS = {
    "manifest": ("manifest.json", check_manifest),
    "extracted": ("extracted", check_extracted),
    "exploration": ("exploration.json", check_exploration),
    "structure": ("structure.json", check_structure),
    "chunks": ("chunks.json", check_chunks),
    "exercises": ("exercises.json", check_exercises),
    "review": ("review.json", check_review),
    "condensation": ("condensation_plan.json", check_condensation),
}


def _contract_findings(findings) -> list[Finding]:
    """contracts.Finding (stage modules) -> validate.Finding, 1:1."""
    return [Finding(str(f.check_id), str(f.severity), str(f.location), str(f.message)) for f in findings]


def _lesson_artifact_present(input_dir: Path, name: str) -> bool:
    """Checkpoint presence per lesson-mode family: per-item directories count once they
    hold an authored file (work/ files alone do not), units/ once a source file exists."""
    artifact = input_dir / LESSON_ARTIFACTS[name]
    if name == "units":
        return any(p.name not in ("index.json", "dedup.json") for p in list_item_files(artifact))
    if name == "qualification":
        return bool(list_item_files(artifact))
    if name in ("syntheses", "lessons", "questions", "cards"):
        return bool(_item_files(artifact))
    return artifact.exists()


class _LessonValidator:
    """Runs one lesson-mode family by delegating to the stage module (results memoized so
    the three qualify families share a single ``qualify.check`` run per validation)."""

    def __init__(self, input_dir: Path, stage: str):
        self.input_dir = input_dir
        self.stage = stage
        self._qualify: list[Finding] | None = None

    def _qualify_findings(self) -> list[Finding]:
        if self._qualify is None:
            import qualify
            self._qualify = _contract_findings(qualify.check(self.input_dir))
        return self._qualify

    def run(self, name: str) -> list[Finding]:
        if name in ("units", "qualification", "concepts"):
            prefix = _QUALIFY_FAMILY_PREFIX.get(name)
            others = tuple(p for n, p in _QUALIFY_FAMILY_PREFIX.items() if n != name)
            return [
                f for f in self._qualify_findings()
                if (f.check_id.startswith(prefix) if prefix else not f.check_id.startswith(others))
            ]
        if name == "groups":
            import regroup
            return _contract_findings(regroup.check(self.input_dir))
        if name == "syntheses":
            import synthesize
            return _contract_findings(synthesize.check(self.input_dir))
        if name == "lessons":
            import lessons
            return _contract_findings(lessons.check(self.input_dir))
        if name == "questions":
            import questions
            return _contract_findings(questions.check(self.input_dir))
        if name == "cards":
            import cards
            return _contract_findings(cards.check(self.input_dir))
        if name == "structure":
            return check_structure(self.input_dir, lesson_mode=True)
        if name == "chunks":
            return check_chunks(self.input_dir, lesson_mode=True)
        if name == "critique":
            return check_critique(self.input_dir, upload=self.stage == "upload")
        artifact, fn = STAGE_CHECKS[name]  # manifest, extracted, review
        return fn(self.input_dir)


def _run_lesson_validation(input_dir: Path, stage: str) -> list[Finding]:
    """Lesson-mode counterpart of run_validation (same stage keywords)."""
    runner = _LessonValidator(input_dir, stage)

    if stage in LESSON_ARTIFACTS:
        if not _lesson_artifact_present(input_dir, stage):
            return [Finding(
                f"{stage}.file_missing", "error", LESSON_ARTIFACTS[stage],
                f"Checkpoint {LESSON_ARTIFACTS[stage]} not found in {input_dir}",
            )]
        return runner.run(stage)
    if stage in STAGE_CHECKS:  # a raw-only family asked for explicitly
        artifact, fn = STAGE_CHECKS[stage]
        if not (input_dir / artifact).exists():
            return [Finding(
                f"{stage}.file_missing", "error", artifact,
                f"Checkpoint {artifact} not found in {input_dir}",
            )]
        return fn(input_dir)

    if stage == "enrich":
        required = {"structure"}
        families, extras = ["manifest", "structure"], ["exercises"]
    elif stage == "upload":
        required = set(LESSON_UPLOAD_REQUIRED)
        families, extras = list(LESSON_STAGES), list(LESSON_OPTIONAL_RAW_STAGES)
    elif stage == "all":
        required = set()
        families, extras = list(LESSON_STAGES), list(LESSON_OPTIONAL_RAW_STAGES)
    else:
        raise ValueError(f"Unknown stage: {stage!r}")

    findings: list[Finding] = []
    for name in families:
        if _lesson_artifact_present(input_dir, name):
            findings.extend(runner.run(name))
        elif name in required:
            findings.append(Finding(
                f"{name}.file_missing", "error", LESSON_ARTIFACTS[name],
                f"Checkpoint {LESSON_ARTIFACTS[name]} is required for {stage} but not found",
            ))
    for name in extras:
        artifact, fn = STAGE_CHECKS[name]
        if (input_dir / artifact).exists():
            findings.extend(fn(input_dir))

    if stage == "upload":
        for f in findings:
            if f.check_id in LESSON_UPLOAD_ESCALATED and f.severity == "warning":
                f.severity = "error"
                f.message += " — every lesson needs questions and cards before upload"
        findings.extend(_upload_approval(input_dir))
    return findings


def _upload_approval(input_dir: Path) -> list[Finding]:
    review_path = input_dir / "review.json"
    approved = False
    if review_path.exists():
        try:
            approved = _load_json(review_path).get("approved") is True
        except json.JSONDecodeError:
            pass
    if approved:
        return []
    return [Finding(
        "upload.not_approved", "error", "review.json",
        "review.json missing or approved is not true — get user "
        "approval before uploading",
    )]


def run_validation(input_dir: Path, stage: str = "all") -> list[Finding]:
    """Run validation checks. Returns the list of findings.

    The pipeline mode comes from manifest.pipeline (contracts.pipeline_mode):
    raw (default) uses STAGES, lesson uses LESSON_STAGES (see the module docstring).

    stage: one of STAGES / LESSON_STAGES, or
      - "all":    every stage whose checkpoint file exists (skip missing)
      - "upload": all of the above, but manifest/structure/chunks (raw) — or
                  manifest/structure/chunks/lessons/questions/cards/critique
                  (lesson) — are required and review.json must exist with
                  approved=true
      - "enrich": structure (+ exercises if present); structure required
    """
    findings: list[Finding] = []

    mode = pipeline_mode(input_dir)
    if mode == PIPELINE_LESSON:
        return _run_lesson_validation(input_dir, stage)
    if stage in LESSON_ONLY_STAGES:
        return [Finding(
            f"{stage}.not_applicable", "error", "manifest.json",
            f"stage {stage!r} belongs to the lesson pipeline but manifest.pipeline is {mode!r}",
        )]

    if stage in STAGE_CHECKS:
        artifact, fn = STAGE_CHECKS[stage]
        if not (input_dir / artifact).exists():
            return [Finding(
                f"{stage}.file_missing", "error", artifact,
                f"Checkpoint {artifact} not found in {input_dir}",
            )]
        return fn(input_dir)

    if stage == "enrich":
        required = {"structure"}
        stages = ["manifest", "structure", "exercises"]
    elif stage == "upload":
        required = {"manifest", "structure", "chunks"}
        stages = list(STAGE_CHECKS)
    elif stage == "all":
        required = set()
        stages = list(STAGE_CHECKS)
    else:
        raise ValueError(f"Unknown stage: {stage!r}")

    for name in stages:
        artifact, fn = STAGE_CHECKS[name]
        if (input_dir / artifact).exists():
            findings.extend(fn(input_dir))
        elif name in required:
            findings.append(Finding(
                f"{name}.file_missing", "error", artifact,
                f"Checkpoint {artifact} is required for {stage} but not found",
            ))

    if stage == "upload":
        findings.extend(_upload_approval(input_dir))

    return findings


def summarize(findings: list[Finding], stage: str) -> dict:
    by_check: dict[str, int] = {}
    for f in findings:
        by_check[f.check_id] = by_check.get(f.check_id, 0) + 1
    return {
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "stage": stage,
        "errors": sum(1 for f in findings if f.severity == "error"),
        "warnings": sum(1 for f in findings if f.severity == "warning"),
        "by_check": dict(sorted(by_check.items())),
    }


def write_report(input_dir: Path, findings: list[Finding], stage: str) -> Path:
    report = {
        "summary": summarize(findings, stage),
        "findings": [asdict(f) for f in findings],
    }
    path = input_dir / "validation_report.json"
    path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8",
    )
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate curriculum pipeline checkpoint files",
    )
    parser.add_argument("input_dir", type=Path, help="output/<name>/ directory")
    parser.add_argument(
        "--stage", default="all",
        choices=[*STAGES, *LESSON_ONLY_STAGES, "all", "upload", "enrich"],
        help="Stage to validate (default: all stages whose checkpoint exists); "
             "units/qualification/concepts/groups/syntheses/lessons/questions/cards/critique "
             "apply to manifest.pipeline = lesson",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Write validation_report.json into the input directory",
    )
    parser.add_argument(
        "--strict", action="store_true",
        help="Exit non-zero on warnings too",
    )
    args = parser.parse_args(argv)

    if not args.input_dir.is_dir():
        print(f"error: {args.input_dir} is not a directory", file=sys.stderr)
        return 2

    findings = run_validation(args.input_dir, args.stage)
    summary = summarize(findings, args.stage)

    from rich.console import Console
    from rich.table import Table

    console = Console()
    if findings:
        table = Table(title=f"Validation: {args.input_dir} (stage: {args.stage})")
        table.add_column("Severity")
        table.add_column("Check")
        table.add_column("Location", overflow="fold")
        table.add_column("Message", overflow="fold")
        for f in sorted(findings, key=lambda f: (_SEVERITY_RANK.get(f.severity, 3), f.check_id)):
            style = {"error": "red", "warning": "yellow"}.get(f.severity, "cyan")
            table.add_row(
                f"[{style}]{f.severity}[/]", f.check_id, f.location, f.message,
            )
        console.print(table)

    if args.json:
        report_path = write_report(args.input_dir, findings, args.stage)
        console.print(f"Report written to {report_path}")

    if summary["errors"]:
        console.print(
            f"[red]{summary['errors']} error(s)[/], "
            f"[yellow]{summary['warnings']} warning(s)[/]"
        )
        return 1
    if summary["warnings"]:
        console.print(f"[yellow]{summary['warnings']} warning(s)[/], no errors")
        return 1 if args.strict else 0
    console.print("[green]All checks passed[/]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
