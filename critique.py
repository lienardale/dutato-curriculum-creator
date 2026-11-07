"""
critique.py — independent-critic harness for the lesson pipeline.

PURE DATA UTILITY — no AI calls. The operating agent runs `prepare` to write
work files that a fresh critic sub-agent reads; the critic writes a verdict
file; `check` turns verdicts into pass / fail / FAILED by rule
(rubrics/critique_protocol.md, docs/artifacts.md "Critique"). The critic never
states the result — the script computes it, and it defaults to failure.

Usage:
    python critique.py prepare <stage> output/<name>/ [--item ID ...] [--force] [--sample-seed N]
    python critique.py check   <stage> output/<name>/ [--item ID] [--strict]
    python critique.py status  output/<name>/

Stages (contracts.CRITIQUED_STAGES): qualification, groups, syntheses,
lessons, questions, cards. Each stage's deterministic module (qualify,
regroup, synthesize, lessons, questions, cards) is imported lazily for its
`check_item(input_dir, item_id)`; a missing module degrades to a warning
finding `critique.stage_module_missing`.

Files, relative to output/<name>/ (item = item_id_to_filename stem):
    critique/<stage>/work/<item>.json               critic work file          (prepare)
    critique/<stage>/work/<item>.student.json       test-student work file    (prepare, questions)
    critique/<stage>/work/<item>.grader.json        blind-grader work file    (prepare, questions)
    critique/<stage>/work/<item>.grader.key.json    hidden candidate mapping  (prepare, questions)
    critique/<stage>/work/<item>.snapshot.json      artifact copy per revision, for repair diffs
    critique/<stage>/work/<item>.verdict.r<N>.json  archived verdict of round N (check)
    critique/<stage>/<item>.json                    critic verdict            (authored)
    critique/<stage>/<item>.student.json            test-student answers      (authored, questions)
    critique/<stage>/<item>.grader.json             blind grades              (authored, questions)
    critique/summary.json                           per-stage status table    (check)

Module API (used by validate.py):
    prepare(input_dir, stage, items=None, force=False, sample_seed=0) -> list[Finding]
    check(input_dir, stage=None, item_id=None, write_summary=True) -> list[Finding]
    check_item(input_dir, item_id, stage=None, write_summary=True) -> list[Finding]
    compute_item_status(verdict, stage, deterministic_errors, extra) -> dict
    status(input_dir) -> dict            (critique/summary.json, or an empty summary)
    main(argv) -> int

Item status (critique/summary.json): pass / fail / FAILED (still failing at
round >= REPAIR_ROUNDS_MAX) / pending (no verdict yet, or the verdict's round is
behind the artifact's revision). A repaired artifact (revision > 0) is diffed
against the snapshot of the previous revision: repair.oversized (error) when
more than REPAIR_DIFF_MAX_RATIO of its prose changed, repair.out_of_scope
(warning) for changed paragraphs the previous round's actionable_items did not
name.

Exit codes: 0 = no errors (and, for check, no fail/FAILED item); 1 otherwise;
2 = the input directory does not exist.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import importlib
import json
import math
import random
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import _compat  # noqa: F401

from chunk import count_tokens
from contracts import (
    ACTIONABLE_ITEMS_MAX,
    BLOOM_LEVELS,
    CARDS_DIR,
    CONCEPTS_FILE,
    CONTRACT_VERSION,
    CRITERIA,
    CRITIQUE_DIR,
    CRITIQUE_SCORE_MIN,
    CRITIQUE_STATUSES,
    CRITIQUE_SUMMARY,
    CRITIQUED_STAGES,
    FIDELITY_PRECISION_MIN,
    FIDELITY_RECALL_MIN,
    GROUP_ID_RE,
    GROUPS_FILE,
    LESSONS_DIR,
    PER_ITEM_CRITERIA,
    PREREQ_VALIDATOR_VERDICTS,
    QUALIFICATION_DIR,
    QUALIFICATION_DISAGREEMENT_MAX,
    QUALIFICATION_DISAGREEMENT_TOLERANCE,
    UNIT_TYPE_FAMILIES,
    QUALIFICATION_SAMPLE_MIN,
    QUALIFICATION_SAMPLE_SHARE,
    QUESTION_VERDICTS,
    QUESTIONS_DIR,
    REPAIR_DIFF_MAX_RATIO,
    REPAIR_ROUNDS_MAX,
    SYNTHESES_DIR,
    TEST_STUDENT_CORRECT_MIN,
    UNIT_TYPES,
    UNITS_DIR,
    WORK_SUBDIR,
    ANCHOR_NOTE,
    Finding,
    anchor_artifact,
    dump_json,
    file_lock,
    filename_to_item_id,
    item_id_to_filename,
    list_item_files,
    load_json,
    normalize_ws,
    source_key,
)

# ---------------------------------------------------------------------------
# Stage tables
# ---------------------------------------------------------------------------

STAGE_MODULES = {
    "qualification": "qualify",
    "groups": "regroup",
    "syntheses": "synthesize",
    "lessons": "lessons",
    "questions": "questions",
    "cards": "cards",
}
STAGE_LEVEL_STAGES = ("groups", "syntheses", "lessons")
PER_ITEM_STAGES = ("qualification", "questions", "cards")
STAGE_DIRS = {
    "qualification": QUALIFICATION_DIR,
    "syntheses": SYNTHESES_DIR,
    "lessons": LESSONS_DIR,
    "questions": QUESTIONS_DIR,
    "cards": CARDS_DIR,
}

# Claim verdicts of the factored fidelity check (docs/artifacts.md "Critique").
# Not in contracts.py yet — see the stream report's contract_changes_needed.
CLAIM_VERDICTS = ("supported", "not_supported", "irrelevant")
KEEP_DROP_VERDICTS = ("keep", "drop")
GRADE_VALUES = ("pass", "fail")
PROBE_CRITERION = "q.probe_matrix_discriminating"
BLOOM_CRITERION = {"lessons": "bloom.objectives_relabel_match", "questions": "q.bloom_relabel_match"}
ROLE_CORRECT, ROLE_WRONG, ROLE_STUDENT = "correct_probe", "wrong_probe", "student"
CANDIDATE_LABELS = ("A", "B", "C")

PROTOCOL_PATH = Path(__file__).resolve().parent / "rubrics" / "critique_protocol.md"
PROTOCOL_GENERAL_SECTIONS = ("## 2.", "## 3.")

SNAPSHOT_SUFFIX = ".snapshot.json"
STUDENT_SUFFIX = ".student.json"
GRADER_SUFFIX = ".grader.json"
GRADER_KEY_SUFFIX = ".grader.key.json"
CLAIM_REF_RE = re.compile(r"^page:(?P<page>\d+):para:(?P<para>\d+)$")
SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}

# Keys whose values are identifiers, enums or script-owned metadata rather than
# authored prose; excluded from the repair diff so an id tweak is not "a change".
_NON_PROSE_KEYS = frozenset({
    "id", "item_id", "lesson_id", "group_id", "area_id", "module_id", "lo_id", "lo_ids",
    "concept_id", "concept_ids", "unit_id", "unit_ids", "evidence_unit_ids", "related_unit_ids",
    "knowledge_point_id", "knowledge_point_ids", "brief_item_ids", "revision", "contract_version",
    "lessons_learned", "outline_approved", "budget", "status", "kind", "type", "role", "card_type",
    "bloom_level", "mastery_level", "position", "page_index", "index", "sort_order", "confidence",
    "learning_step", "strength", "lens", "source_reference", "language", "quote", "source_quote",
    "recap_of", "correct_index", "correct_order", "difficulty", "fading", "critic", "stage", "round",
    "source", "block_type", "images", "local_path", "mime_type", "suggested_level", "estimated_minutes",
})

# Author fields the questions critic must never see (protocol §4 questions).
QUESTION_WITHHELD = ("rationale", "source_quote", "probes", "key_points", "bloom_level",
                     "distractors", "explanation", "feedback", "variants")


# ---------------------------------------------------------------------------
# Paths and small helpers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Paths:
    """Every file one critiqued item owns under critique/<stage>/."""

    input_dir: Path
    stage: str
    item_id: str

    @property
    def stem(self) -> str:
        return item_id_to_filename(self.item_id)[:-5]

    @property
    def critique_dir(self) -> Path:
        return self.input_dir / CRITIQUE_DIR / self.stage

    @property
    def work_dir(self) -> Path:
        return self.critique_dir / WORK_SUBDIR

    def work(self, suffix: str = ".json") -> Path:
        return self.work_dir / f"{self.stem}{suffix}"

    def verdict(self, suffix: str = ".json") -> Path:
        return self.critique_dir / f"{self.stem}{suffix}"

    def archive(self, round_: int) -> Path:
        return self.work_dir / f"{self.stem}.verdict.r{round_}.json"

    def rel(self, path: Path) -> str:
        return path.relative_to(self.input_dir).as_posix()


def _item_id_from_filename(stage: str, name: str) -> str:
    """qualification items are source keys (may contain '_'); others use the shared mapping."""
    return source_key(name) if stage == "qualification" else filename_to_item_id(name)


def _artifact_location(stage: str, item_id: str) -> str:
    """Where the author artifact of one item lives, for finding locations."""
    if stage == "groups":
        return f"{GROUPS_FILE} groups[{item_id}]"
    return f"{STAGE_DIRS[stage]}/{item_id_to_filename(item_id)}"


def _verdict_files(critique_dir: Path) -> list[Path]:
    return [p for p in list_item_files(critique_dir) if not p.name.endswith((STUDENT_SUFFIX, GRADER_SUFFIX))]


def _round_of(artifact: dict) -> int:
    rev = artifact.get("revision", 0)
    return rev if isinstance(rev, int) and rev >= 0 else 0


def _norm_lower(text: Any) -> str:
    return normalize_ws(str(text or "")).lower()


def _paragraphs(text: Any) -> list[str]:
    if not isinstance(text, str):
        return []
    return [normalize_ws(p) for p in re.split(r"\n\s*\n", text) if normalize_ws(p)]


def _preview(text: str, n: int = 60) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def _criteria_dicts(stage: str, exclude: Iterable[str] = ()) -> list[dict]:
    catalogue = CRITERIA.get(stage) or PER_ITEM_CRITERIA.get(stage) or []
    skip = set(exclude)
    return [
        {"id": cid, "weight": weight, "critical": critical, "description": description}
        for cid, weight, critical, description in catalogue
        if cid not in skip
    ]


def _protocol_section(stage: str) -> str:
    """§2, §3 and the stage's §4 subsection of rubrics/critique_protocol.md ('' when absent)."""
    try:
        text = PROTOCOL_PATH.read_text(encoding="utf-8")
    except OSError:
        return ""
    chunks: list[str] = []
    for part in re.split(r"(?m)^(?=## )", text):
        if part.startswith(PROTOCOL_GENERAL_SECTIONS):
            chunks.append(part.strip())
    for part in re.split(r"(?m)^(?=### )", text):
        if re.match(rf"### {re.escape(stage)}\b", part):
            chunks.append(part.split("\n## ")[0].strip())
    return "\n\n".join(chunks)


def _criteria_template(criteria: list[dict]) -> list[dict]:
    return [
        {
            "criterion_id": c["id"],
            "passed": "<bool — false when uncertain>",
            "evidence_span": "<verbatim span copied from the artifact>",
            "failure_reason": "<falsifiable statement, or null when passed>",
            "correction_instruction": "<one-place edit naming the unit to ground it in, or null>",
        }
        for c in criteria
    ]


def _stage_module(stage: str) -> tuple[Any | None, Finding | None]:
    """Import the stage's deterministic module lazily; None + warning when it does not exist."""
    name = STAGE_MODULES[stage]
    try:
        module = importlib.import_module(name)
    except ImportError as e:
        return None, Finding(
            "critique.stage_module_missing", "warning", f"{name}.py",
            f"Stage module {name!r} is not importable ({e}); deterministic errors are not computed",
        )
    if not callable(getattr(module, "check_item", None)):
        return None, Finding(
            "critique.stage_module_missing", "warning", f"{name}.py",
            f"Stage module {name!r} has no check_item(); deterministic errors are not computed",
        )
    return module, None


def _deterministic_errors(module: Any | None, input_dir: Path, stage: str, item_id: str) -> list[Finding]:
    if module is None:
        return []
    try:
        found = module.check_item(input_dir, item_id)
    except Exception as e:  # a bug in another stream must not pass an item silently
        return [Finding(
            "critique.stage_module_error", "error", f"{STAGE_MODULES[stage]}.check_item({item_id})",
            f"{type(e).__name__}: {e}",
        )]
    return [f for f in (found or []) if getattr(f, "severity", None) == "error"]


# ---------------------------------------------------------------------------
# Input loading (lazy, cached, finding-reporting)
# ---------------------------------------------------------------------------


class _Inputs:
    """Reads pipeline artifacts under output/<name>/ once, reporting missing or broken files as findings."""

    def __init__(self, input_dir: Path, findings: list[Finding]):
        self.input_dir = Path(input_dir)
        self.findings = findings
        self._cache: dict[str, Any] = {}
        self._units: dict[str, dict] | None = None

    def json(self, rel: str, *, required: bool = False, quiet: bool = False) -> Any | None:
        if rel in self._cache:
            return self._cache[rel]
        path = self.input_dir / rel
        data: Any | None = None
        if not path.exists():
            if not quiet:
                self.findings.append(Finding(
                    "critique.input_missing", "error" if required else "warning", rel,
                    "Required input is missing" if required else "Optional input is missing; the work file degrades",
                ))
        else:
            try:
                data = load_json(path)
            except (OSError, json.JSONDecodeError) as e:
                self.findings.append(Finding("critique.input_invalid_json", "error", rel, str(e)))
        self._cache[rel] = data
        return data

    def units(self) -> dict[str, dict]:
        if self._units is not None:
            return self._units
        self._units = {}
        udir = self.input_dir / UNITS_DIR
        if not udir.is_dir():
            self.findings.append(Finding(
                "critique.input_missing", "warning", f"{UNITS_DIR}/", "No units directory; unit texts are unavailable",
            ))
            return self._units
        for path in sorted(udir.glob("*.json")):
            if path.name == "index.json":
                continue
            try:
                data = load_json(path)
            except (OSError, json.JSONDecodeError) as e:
                self.findings.append(Finding("critique.input_invalid_json", "error", f"{UNITS_DIR}/{path.name}", str(e)))
                continue
            for unit in (data.get("units") or []) if isinstance(data, dict) else []:
                if isinstance(unit, dict) and unit.get("id"):
                    self._units[unit["id"]] = unit
        return self._units

    def concepts(self) -> list[dict]:
        data = self.json(CONCEPTS_FILE)
        return [c for c in (data.get("concepts") or []) if isinstance(c, dict)] if isinstance(data, dict) else []

    def groups(self, *, quiet: bool = False) -> dict | None:
        data = self.json(GROUPS_FILE, quiet=quiet)
        return data if isinstance(data, dict) else None

    def group(self, group_id: str, *, quiet: bool = False) -> dict | None:
        groups = self.groups(quiet=quiet)
        if groups is None:
            return None
        for entry in groups.get("groups") or []:
            if isinstance(entry, dict) and entry.get("id") == group_id:
                if "revision" not in entry and isinstance(groups.get("revision"), int):
                    entry = {**entry, "revision": groups["revision"]}
                return entry
        return None

    def stage_file(self, stage: str, item_id: str, *, quiet: bool = False) -> dict | None:
        rel = f"{STAGE_DIRS[stage]}/{item_id_to_filename(item_id)}"
        data = self.json(rel, quiet=quiet)
        return data if isinstance(data, dict) else None

    def unit_views(self, ids: Iterable[str], location: str) -> list[dict]:
        """[{id, text, block_type, section_title}] for known ids; unknown ids become a warning."""
        units = self.units()
        out: list[dict] = []
        seen: set[str] = set()
        for uid in ids:
            if not isinstance(uid, str) or uid in seen:
                continue
            seen.add(uid)
            unit = units.get(uid)
            if unit is None:
                self.findings.append(Finding("critique.unit_unknown", "warning", location, f"Unit {uid} is not in units/"))
                continue
            out.append({
                "id": uid, "text": unit.get("text", ""), "block_type": unit.get("block_type"),
                "section_title": unit.get("section_title"),
            })
        return out


def _load_artifact(inputs: _Inputs, stage: str, item_id: str) -> dict | None:
    """The author artifact one critique item refers to; None when absent (callers report)."""
    if stage == "groups":
        return inputs.group(item_id, quiet=True)
    if stage == "qualification":
        data = inputs.json(f"{QUALIFICATION_DIR}/{item_id}.json", quiet=True)
        return data if isinstance(data, dict) else None
    return inputs.stage_file(stage, item_id, quiet=True)


def _list_item_ids(inputs: _Inputs, stage: str) -> list[str]:
    """Item ids of a stage: group ids, source keys (qualification), or the ``g:<slug>`` ids
    the per-item files map back to (helper files such as ``lessons/anchor.json`` are skipped)."""
    if stage == "groups":
        groups = inputs.groups(quiet=True)
        return [g["id"] for g in (groups or {}).get("groups") or [] if isinstance(g, dict) and g.get("id")]
    stage_dir = inputs.input_dir / STAGE_DIRS[stage]
    ids = [_item_id_from_filename(stage, p.name) for p in list_item_files(stage_dir)]
    return ids if stage == "qualification" else [i for i in ids if GROUP_ID_RE.match(i)]


def _question_items(artifact: dict) -> list[tuple[str, dict]]:
    """(section, question) for embedded + quiz items (exercises are not critiqued here)."""
    out: list[tuple[str, dict]] = []
    for section in ("embedded", "quiz"):
        for q in artifact.get(section) or []:
            if isinstance(q, dict) and q.get("id"):
                out.append((section, q))
    return out


def _pages_by_index(lesson: dict | None) -> dict[int, dict]:
    pages: dict[int, dict] = {}
    for i, page in enumerate((lesson or {}).get("pages") or []):
        if isinstance(page, dict):
            idx = page.get("index")
            pages[idx if isinstance(idx, int) else i] = page
    return pages


# ---------------------------------------------------------------------------
# Snapshots and verdict archive
# ---------------------------------------------------------------------------


def _snapshots(paths: _Paths) -> dict[str, Any]:
    """{revision: artifact} stored so far for this item ({} when absent or unreadable)."""
    path = paths.work(SNAPSHOT_SUFFIX)
    if not path.exists():
        return {}
    try:
        data = load_json(path)
    except (OSError, json.JSONDecodeError):
        return {}
    revisions = data.get("revisions") if isinstance(data, dict) else None
    return revisions if isinstance(revisions, dict) else {}


def _store_snapshot(paths: _Paths, revision: int, artifact: dict, overwrite: bool = False) -> bool:
    """Record the artifact under its revision for later repair diffs.

    Returns False (and leaves the file alone) when a *different* snapshot of the
    same revision already exists and `overwrite` is False: the author edited the
    artifact without bumping `revision`.
    """
    revisions = _snapshots(paths)
    existing = revisions.get(str(revision))
    if existing is not None and not overwrite:
        return existing == artifact
    revisions[str(revision)] = artifact
    dump_json(paths.work(SNAPSHOT_SUFFIX), {
        "contract_version": CONTRACT_VERSION, "stage": paths.stage, "item_id": paths.item_id, "revisions": revisions,
    })
    return True


def _load_snapshot(paths: _Paths, revision: int) -> dict | None:
    snap = _snapshots(paths).get(str(revision))
    return snap if isinstance(snap, dict) else None


def _archive_verdict(paths: _Paths, round_: int, verdict: dict) -> None:
    dump_json(paths.archive(round_), verdict)


def _archived_verdict(paths: _Paths, round_: int) -> dict | None:
    path = paths.archive(round_)
    if not path.exists():
        return None
    try:
        data = load_json(path)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _work_is_current(path: Path, revision: int) -> bool:
    if not path.exists():
        return False
    try:
        data = load_json(path)
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(data, dict) and data.get("round") == revision


# ---------------------------------------------------------------------------
# prepare — work files per stage
# ---------------------------------------------------------------------------


def _previous_round(input_dir: Path | None, stage: str, item_id: str, round_: int) -> dict | None:
    """Digest of the previous round's verdict (archived by `check`) so a round >= 1 critic
    judges whether the named repairs landed instead of restarting from scratch."""
    if input_dir is None or round_ < 1:
        return None
    stem = item_id_to_filename(item_id)[:-5]
    archive = Path(input_dir) / CRITIQUE_DIR / stage / WORK_SUBDIR / f"{stem}.verdict.r{round_ - 1}.json"
    if not archive.exists():
        return None
    try:
        prev = load_json(archive)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(prev, dict):
        return None
    failed = [c.get("criterion_id") for c in (prev.get("criteria") or []) if isinstance(c, dict) and c.get("passed") is False]
    failed_items = [
        {"item_id": it.get("item_id"), "verdict": it.get("verdict"),
         "failed": [c.get("criterion_id") for c in (it.get("criteria") or []) if isinstance(c, dict) and c.get("passed") is False]}
        for it in (prev.get("items") or []) if isinstance(it, dict) and it.get("verdict") != "keep"
    ]
    return {
        "round": round_ - 1,
        "failed_criteria": failed,
        "failed_items": failed_items,
        "actionable_items": list(prev.get("actionable_items") or []),
        "instruction": ("Judge first whether each previous actionable item was addressed. A criterion that passed "
                        "in the previous round fails again only if the repair introduced a new defect; do not "
                        "restart the review from scratch."),
    }


def _base_work(stage: str, item_id: str, round_: int, artifact: dict, criteria: list[dict], input_dir: Path | None = None) -> dict:
    return {
        "contract_version": CONTRACT_VERSION,
        "stage": stage,
        "item_id": item_id,
        "round": round_,
        "criteria": criteria,
        "protocol": _protocol_section(stage),
        "previous_round": _previous_round(input_dir, stage, item_id, round_),
        "lessons_learned": list(artifact.get("lessons_learned") or []),
        "verdict_file": f"{CRITIQUE_DIR}/{stage}/{item_id_to_filename(item_id)}",
    }


def _finish(data: dict) -> dict:
    data["token_estimate"] = count_tokens(json.dumps(data, ensure_ascii=False))
    return data


def _prepare_qualification(inputs: _Inputs, item_id: str, artifact: dict, seed: int) -> dict | None:
    units_file = inputs.json(f"{UNITS_DIR}/{item_id}.json", required=True)
    if not isinstance(units_file, dict):
        return None
    units = [u for u in units_file.get("units") or [] if isinstance(u, dict)]
    total = len(units)
    size = min(total, max(QUALIFICATION_SAMPLE_MIN, math.ceil(QUALIFICATION_SAMPLE_SHARE * total)))
    indices = sorted(random.Random(seed).sample(range(total), size)) if size < total else list(range(total))
    sample = [{"id": units[i].get("id"), "text": units[i].get("text", ""), "block_type": units[i].get("block_type")}
              for i in indices]
    registry = [{"id": c.get("id"), "label": c.get("label"), "description": c.get("description"),
                 "aliases": list(c.get("aliases") or [])} for c in inputs.concepts()]
    criteria = _criteria_dicts("qualification")
    work = _base_work("qualification", item_id, _round_of(artifact), artifact, criteria, input_dir=inputs.input_dir)
    work.update({
        "source": item_id,
        "sample_seed": seed,
        "sample_size": size,
        "total_units": total,
        "task": "Blind re-tag: for every sampled unit assign concepts (ids from `concepts`), unit_type "
                "(from `enums.unit_types`) and verdict keep|drop, without the author's tags. The script compares.",
        "sample": sample,
        "concepts": registry,
        "enums": {"unit_types": list(UNIT_TYPES)},
        "output_shape": {
            "contract_version": CONTRACT_VERSION, "stage": "qualification", "item_id": item_id,
            "round": work["round"], "critic": "<name>",
            "items": [{"item_id": "<unit id>", "verdict": "keep|drop", "concepts": ["c:<slug>"],
                       "unit_type": "<unit_type>", "notes": ""}],
            "notes": "",
        },
    })
    return _finish(work)


def _prepare_groups(inputs: _Inputs, item_id: str, artifact: dict) -> dict:
    loc = f"{GROUPS_FILE} groups[{item_id}]"
    group_view = {k: v for k, v in artifact.items() if k not in ("rationale", "prerequisites")}
    proposed = [
        {"group_id": p.get("group_id"), "strength": p.get("strength"),
         "evidence_unit_ids": list(p.get("evidence_unit_ids") or [])}
        for p in artifact.get("prerequisites") or []
        if isinstance(p, dict) and p.get("status") in (None, "proposed")
    ]
    unit_ids = list(artifact.get("unit_ids") or []) + list(artifact.get("related_unit_ids") or [])
    concept_ids = set(artifact.get("concept_ids") or [])
    criteria = _criteria_dicts("groups")
    work = _base_work("groups", item_id, _round_of(artifact), artifact, criteria, input_dir=inputs.input_dir)
    work.update({
        "group": group_view,
        "units": inputs.unit_views(unit_ids, loc),
        "concepts": [c for c in inputs.concepts() if c.get("id") in concept_ids],
        "proposed_prerequisites": proposed,
        "edge_verdicts": list(PREREQ_VALIDATOR_VERDICTS),
        "output_shape": {
            "contract_version": CONTRACT_VERSION, "stage": "groups", "item_id": item_id, "round": work["round"],
            "critic": "<name>", "criteria": _criteria_template(criteria),
            "edges": [{"group_id": p["group_id"], "verdict": "confirmed|either|uncertain", "reason": "<one sentence>",
                       "unit_ids": ["<units showing the dependency>"]} for p in proposed],
            "actionable_items": [{"span": "<verbatim span>", "instruction": "<edit>", "ground_in": ["u:…"]}],
            "notes": "",
        },
    })
    return _finish(work)


def _prepare_syntheses(inputs: _Inputs, item_id: str, artifact: dict) -> dict:
    loc = f"{SYNTHESES_DIR}/{item_id_to_filename(item_id)}"
    items = [it for it in artifact.get("items") or [] if isinstance(it, dict)]
    claims = [
        {"id": it.get("id"), "kind": it.get("kind"), "text": it.get("text", ""),
         "cited_units": [{"id": u["id"], "text": u["text"]} for u in inputs.unit_views(it.get("unit_ids") or [], loc)]}
        for it in items
    ]
    group = inputs.group(item_id)
    knowledge_points = [{"id": kp.get("id"), "title": kp.get("title")}
                        for kp in (group or {}).get("knowledge_points") or [] if isinstance(kp, dict)]
    # brief.terminology_defined is judged on the brief's own glossary: without it every critic fails the criterion.
    terminology = [{"term": t.get("term"), "definition": t.get("definition"), "unit_id": t.get("unit_id")}
                   for t in artifact.get("terminology") or [] if isinstance(t, dict)]
    claim_unit_ids = {u["id"] for c in claims for u in c["cited_units"]}
    term_units = inputs.unit_views([t["unit_id"] for t in terminology
                                    if isinstance(t["unit_id"], str) and t["unit_id"] not in claim_unit_ids], loc)
    criteria = _criteria_dicts("syntheses")
    work = _base_work("syntheses", item_id, _round_of(artifact), artifact, criteria, input_dir=inputs.input_dir)
    work.update({
        "terminology": terminology,
        "terminology_units": [{"id": u["id"], "text": u["text"]} for u in term_units],
        "terminology_instruction": "Judge brief.terminology_defined on `terminology`: every term a lesson on these "
                                   "knowledge points will need is listed, and each definition is supported by its "
                                   "unit (text in the claims' cited_units or in `terminology_units`).",
        "claims": claims,
        "claims_instruction": "Label every entry of `claims` supported | not_supported | irrelevant using ONLY its "
                              "cited_units. A claim no cited unit supports is not_supported even if true.",
        "coverage_task": {
            "knowledge_points": knowledge_points,
            "items": [{"id": it.get("id"), "kind": it.get("kind"), "text": it.get("text", "")} for it in items],
            "instruction": "List knowledge points that no map item covers, and watch items that silently merge "
                           "conflicting sources.",
        },
        "output_shape": {
            "contract_version": CONTRACT_VERSION, "stage": "syntheses", "item_id": item_id, "round": work["round"],
            "critic": "<name>", "criteria": _criteria_template(criteria),
            "claims": [{"ref": c["id"], "verdict": "supported|not_supported|irrelevant", "unit_ids": ["u:…"],
                        "reason": ""} for c in claims],
            "actionable_items": [{"span": "<verbatim span>", "instruction": "<edit>", "ground_in": ["u:…"]}],
            "notes": "",
        },
    })
    return _finish(work)


def _prepare_lessons(inputs: _Inputs, item_id: str, artifact: dict) -> dict:
    loc = f"{LESSONS_DIR}/{item_id_to_filename(item_id)}"
    pages_in = [p for p in artifact.get("pages") or [] if isinstance(p, dict)]
    pages, paragraphs = [], []
    brief_ids: list[str] = []
    unit_ids: list[str] = []
    for i, page in enumerate(pages_in):
        idx = page.get("index") if isinstance(page.get("index"), int) else i
        pages.append({k: page.get(k) for k in
                      ("index", "role", "title", "markdown", "lo_ids", "knowledge_point_id", "brief_item_ids")})
        for j, para in enumerate(_paragraphs(page.get("markdown"))):
            paragraphs.append({"ref": f"page:{idx}:para:{j}", "page_index": idx, "text": para})
        brief_ids += [b for b in page.get("brief_item_ids") or [] if isinstance(b, str)]
        unit_ids += [u for u in page.get("unit_ids") or [] if isinstance(u, str)]
    brief = inputs.stage_file("syntheses", item_id)
    brief_items = [
        {"id": it.get("id"), "kind": it.get("kind"), "text": it.get("text", ""), "unit_ids": list(it.get("unit_ids") or [])}
        for it in (brief or {}).get("items") or []
        if isinstance(it, dict) and it.get("id") in set(brief_ids)
    ]
    for it in brief_items:
        unit_ids += [u for u in it["unit_ids"] if isinstance(u, str)]
    group = inputs.group(item_id)
    criteria = _criteria_dicts("lessons")
    work = _base_work("lessons", item_id, _round_of(artifact), artifact, criteria, input_dir=inputs.input_dir)
    work.update({
        "title": artifact.get("title"),
        "pages": pages,
        "paragraphs": paragraphs,
        "brief_items": brief_items,
        "units": inputs.unit_views(unit_ids, loc),
        "objectives": [{"id": lo.get("id"), "text": lo.get("text")} for lo in artifact.get("objectives") or []
                       if isinstance(lo, dict)],
        "generalization": (group or {}).get("generalization"),
        "claims_instruction": "Output one `claims` entry per paragraph listed in `paragraphs`, using the same `ref` "
                              "(page:<page index>:para:<paragraph index>), verdict supported | not_supported | "
                              "irrelevant against brief_items + units. Re-label each objective's Bloom level in "
                              "`bloom_relabels` BEFORE judging anything else.",
        "output_shape": {
            "contract_version": CONTRACT_VERSION, "stage": "lessons", "item_id": item_id, "round": work["round"],
            "critic": "<name>", "criteria": _criteria_template(criteria),
            "claims": [{"ref": p["ref"], "verdict": "supported|not_supported|irrelevant", "unit_ids": ["u:…"],
                        "reason": ""} for p in paragraphs],
            "bloom_relabels": {lo["id"]: "<bloom level>" for lo in _objectives(artifact)},
            "actionable_items": [{"span": "<verbatim span>", "instruction": "<edit>", "ground_in": ["u:…"]}],
            "notes": "",
        },
    })
    found = anchor_artifact(inputs.input_dir, LESSONS_DIR, item_id)
    if found is not None:
        anchor_id, anchor = found
        work["anchor"] = {"lesson_id": anchor_id, "note": ANCHOR_NOTE,
                          "pages": [{k: p.get(k) for k in ("index", "role", "title", "markdown")}
                                    for p in anchor.get("pages") or [] if isinstance(p, dict)]}
    return _finish(work)


def _objectives(artifact: dict) -> list[dict]:
    return [lo for lo in artifact.get("objectives") or [] if isinstance(lo, dict) and lo.get("id")]


def _student_answers(student: dict | None) -> dict[str, str]:
    answers = (student or {}).get("answers")
    out: dict[str, str] = {}
    if isinstance(answers, dict):
        for qid, text in answers.items():
            if isinstance(text, str) and text.strip():
                out[str(qid)] = text
    elif isinstance(answers, list):
        for entry in answers:
            if isinstance(entry, dict) and entry.get("question_id") and isinstance(entry.get("answer"), str) \
                    and entry["answer"].strip():
                out[str(entry["question_id"])] = entry["answer"]
    return out


def _grader_has_students(key_path: Path) -> bool:
    if not key_path.exists():
        return False
    try:
        key = load_json(key_path)
    except (OSError, json.JSONDecodeError):
        return False
    questions = key.get("questions") if isinstance(key, dict) else None
    return any(ROLE_STUDENT in mapping.values() for mapping in (questions or {}).values() if isinstance(mapping, dict))


def _answers_digest(answers: dict[str, str]) -> str:
    """Fingerprint of the student answers folded into a grader key (empty answers -> fixed digest)."""
    return hashlib.sha256(json.dumps(sorted(answers.items()), ensure_ascii=False).encode("utf-8")).hexdigest()


def _key_answers_digest(key_path: Path) -> str | None:
    """The digest stored in a grader key; None when the key is missing, unreadable or predates digests."""
    try:
        key = load_json(key_path)
    except (OSError, json.JSONDecodeError):
        return None
    digest = key.get("student_answers_digest") if isinstance(key, dict) else None
    return digest if isinstance(digest, str) else None


def _current_student_answers(student: dict | None, revision: int) -> dict[str, str]:
    """Answers of a test-student verdict written for this revision; a verdict from an earlier round is stale
    (its questions may have changed) and contributes nothing. A verdict without a round counts as current."""
    if not isinstance(student, dict):
        return {}
    round_ = student.get("round")
    if isinstance(round_, int) and not isinstance(round_, bool) and round_ != revision:
        return {}
    return _student_answers(student)


def _build_grader(paths: _Paths, artifact: dict, questions: list[tuple[str, dict]], student_answers: dict[str, str],
                  findings: list[Finding]) -> tuple[dict, dict]:
    """Blind grader work file + hidden key. Candidate order is shuffled by a Random seeded with the item id."""
    rng = random.Random(paths.item_id)
    criteria = [c for c in _criteria_dicts("questions") if c["id"] == PROBE_CRITERION]
    work = _base_work("questions", paths.item_id, _round_of(artifact), artifact, criteria, input_dir=paths.input_dir)
    key = {"contract_version": CONTRACT_VERSION, "stage": "questions", "item_id": paths.item_id,
           "round": work["round"], "student_answers_digest": _answers_digest(student_answers), "questions": {}}
    entries = []
    for _section, q in questions:
        probes = q.get("probes") if isinstance(q.get("probes"), dict) else {}
        correct, wrong = probes.get("correct_learner_answer"), probes.get("wrong_learner_answer")
        if not correct or not wrong:
            findings.append(Finding(
                "critique.probes_missing", "warning", f"{QUESTIONS_DIR}/{paths.stem}.json {q['id']}",
                "Question has no probes; the probe matrix cannot be graded",
            ))
            continue
        candidates = [(ROLE_CORRECT, correct), (ROLE_WRONG, wrong)]
        if q["id"] in student_answers:
            candidates.append((ROLE_STUDENT, student_answers[q["id"]]))
        rng.shuffle(candidates)
        labels = CANDIDATE_LABELS[: len(candidates)]
        entries.append({
            "question_id": q["id"],
            "acceptance": q.get("acceptance"),
            "candidates": {label: text for label, (_role, text) in zip(labels, candidates)},
        })
        key["questions"][q["id"]] = {label: role for label, (role, _text) in zip(labels, candidates)}
    work.update({
        "verdict_file": f"{CRITIQUE_DIR}/questions/{paths.stem}{GRADER_SUFFIX}",
        "task": "Grade every candidate answer ONLY against `acceptance` (prediction + causal_reason), in any "
                "wording: pass when it states the prediction for the stated causal reason, fail otherwise. "
                "You do not know which candidate is which.",
        "questions": entries,
        "output_shape": {"contract_version": CONTRACT_VERSION, "stage": "questions", "item_id": paths.item_id,
                         "round": work["round"], "grader": "<name>",
                         "grades": {"<question_id>": {label: "pass|fail" for label in CANDIDATE_LABELS}}},
    })
    return _finish(work), key


def _prepare_questions(inputs: _Inputs, paths: _Paths, artifact: dict, force: bool, findings: list[Finding]) -> bool:
    """Write the critic, test-student and grader work files; returns True when anything was written."""
    item_id = paths.item_id
    loc = f"{QUESTIONS_DIR}/{paths.stem}.json"
    lesson = inputs.stage_file("lessons", item_id)
    if lesson is None:
        findings.append(Finding("critique.input_missing", "error", f"{LESSONS_DIR}/{paths.stem}.json",
                                "Questions critique needs the lesson pages"))
        return False
    revision = _round_of(artifact)
    pages = _pages_by_index(lesson)
    questions = _question_items(artifact)
    written = False

    critic_path = paths.work()
    if force or not _work_is_current(critic_path, revision):
        criteria = _criteria_dicts("questions", exclude=(PROBE_CRITERION,))
        work = _base_work("questions", item_id, revision, artifact, criteria, input_dir=inputs.input_dir)
        views = []
        for section, q in questions:
            page = pages.get(q.get("page_index"))
            is_mcq = q.get("type") == "multiple_choice" or (section == "quiz" and "options" in q)
            view = {
                "id": q["id"], "section": section, "kind": q.get("kind") or q.get("type"),
                "page_index": q.get("page_index"), "position": q.get("position"), "lo_id": q.get("lo_id"),
                "concept_id": q.get("concept_id"), "unit_ids": list(q.get("unit_ids") or []),
                "page_markdown": (page or {}).get("markdown"),
                "units": inputs.unit_views(q.get("unit_ids") or [], f"{loc} {q['id']}"),
                "prompt": q.get("prompt"), "acceptance": q.get("acceptance"),
            }
            if is_mcq:
                view["options"] = list(q.get("options") or [])
                view["correct_index"] = q.get("correct_index")
            else:
                view["expected_answer"] = q.get("expected_answer")
            views.append(view)
        work.update({
            "lesson_title": lesson.get("title"),
            "withheld": list(QUESTION_WITHHELD),
            "questions": views,
            "output_shape": {
                "contract_version": CONTRACT_VERSION, "stage": "questions", "item_id": item_id, "round": revision,
                "critic": "<name>",
                "items": [{"item_id": v["id"], "verdict": "keep|rewrite|drop", "criteria": _criteria_template(criteria),
                           "rewrite": None, "bloom_relabel": "<bloom level>", "feedback_to_author": ""} for v in views],
                "notes": "",
            },
        })
        dump_json(critic_path, _finish(work))
        written = True

    student_path = paths.work(STUDENT_SUFFIX)
    if force or not _work_is_current(student_path, revision):
        recaps = []
        for prereq_id in lesson.get("prerequisites") or []:
            if not isinstance(prereq_id, str):
                continue
            prereq = inputs.stage_file("lessons", prereq_id)
            if prereq is None:
                continue
            for page in prereq.get("pages") or []:
                if isinstance(page, dict) and page.get("role") == "recap":
                    recaps.append({"lesson_id": prereq_id, "title": prereq.get("title"), "markdown": page.get("markdown")})
        embedded = [q for section, q in questions if section == "embedded"]
        student_pages = []
        for idx in sorted(pages):
            page = pages[idx]
            slot = lambda pos: [{"id": q["id"], "kind": q.get("kind"), "prompt": q.get("prompt")}  # noqa: E731
                                for q in embedded if q.get("page_index") == idx and q.get("position") == pos]
            student_pages.append({"index": idx, "title": page.get("title"), "role": page.get("role"),
                                  "questions_before": slot("before"), "markdown": page.get("markdown"),
                                  "questions_after": slot("after")})
        work = _base_work("questions", item_id, revision, artifact, [], input_dir=inputs.input_dir)
        work.update({
            "verdict_file": f"{CRITIQUE_DIR}/questions/{paths.stem}{STUDENT_SUFFIX}",
            "task": "You are a learner. Read `prerequisite_recaps`, then the pages in order. Answer every "
                    "question in `questions_before` before reading the page and every question in "
                    "`questions_after` before reading further. List every term you could not define from "
                    "what you had read so far.",
            "prerequisite_recaps": recaps,
            "pages": student_pages,
            "output_shape": {"contract_version": CONTRACT_VERSION, "stage": "questions", "item_id": item_id,
                             "round": revision, "student": "<name>",
                             "answers": [{"question_id": q["id"], "answer": "<your answer>"} for q in embedded],
                             "unanswerable": ["<question ids you could not answer from the text>"],
                             "undefined_terms": ["<terms>"]},
        })
        dump_json(student_path, _finish(work))
        written = True

    student_verdict_path = paths.verdict(STUDENT_SUFFIX)
    student_answers: dict[str, str] = {}
    if student_verdict_path.exists():
        try:
            student_answers = _current_student_answers(load_json(student_verdict_path), revision)
        except (OSError, json.JSONDecodeError) as e:
            findings.append(Finding("critique.verdict_invalid_json", "error", paths.rel(student_verdict_path), str(e)))
    grader_path, key_path = paths.work(GRADER_SUFFIX), paths.work(GRADER_KEY_SUFFIX)
    # Refresh whenever the folded answers differ from the current ones: on a repair round the first prepare
    # runs before the new test student answers, the second must pick those answers up.
    refresh = force or not _work_is_current(grader_path, revision) or not key_path.exists() \
        or _key_answers_digest(key_path) != _answers_digest(student_answers)
    if refresh:
        grader, key = _build_grader(paths, artifact, questions, student_answers, findings)
        dump_json(grader_path, grader)
        dump_json(key_path, key)
        written = True
    return written


def _prepare_cards(inputs: _Inputs, item_id: str, artifact: dict) -> dict:
    loc = f"{CARDS_DIR}/{item_id_to_filename(item_id)}"
    pages = _pages_by_index(inputs.stage_file("lessons", item_id))
    cards = []
    for card in artifact.get("cards") or []:
        if not isinstance(card, dict):
            continue
        page = pages.get(card.get("page_index"))
        cards.append({
            "id": card.get("id"), "card_type": card.get("card_type"), "front": card.get("front"),
            "back": card.get("back"), "card_data": card.get("card_data") or {}, "lo_id": card.get("lo_id"),
            "page_index": card.get("page_index"), "page_markdown": (page or {}).get("markdown"),
            "units": inputs.unit_views(card.get("unit_ids") or [], f"{loc} {card.get('id')}"),
        })
    criteria = _criteria_dicts("cards")
    work = _base_work("cards", item_id, _round_of(artifact), artifact, criteria, input_dir=inputs.input_dir)
    work.update({
        "cards": cards,
        "output_shape": {
            "contract_version": CONTRACT_VERSION, "stage": "cards", "item_id": item_id, "round": work["round"],
            "critic": "<name>",
            "items": [{"item_id": c["id"], "verdict": "keep|drop", "criteria": _criteria_template(criteria),
                       "alternatives": ["<plausible alternative answers for cloze/discrimination>"],
                       "feedback_to_author": ""} for c in cards],
            "notes": "",
        },
    })
    return _finish(work)


def prepare(input_dir: Path, stage: str, items: Iterable[str] | None = None, force: bool = False,
            sample_seed: int = 0) -> list[Finding]:
    """Write critique/<stage>/work/ files for every item (or `items`). Returns findings."""
    input_dir = Path(input_dir)
    findings: list[Finding] = []
    if stage not in CRITIQUED_STAGES:
        return [Finding("critique.stage_unknown", "error", stage, f"Stage must be one of {CRITIQUED_STAGES}")]
    inputs = _Inputs(input_dir, findings)
    item_ids = list(items) if items else _list_item_ids(inputs, stage)
    if not item_ids:
        findings.append(Finding("critique.no_items", "warning", stage, "No author artifacts found for this stage"))
    written = skipped = 0
    for item_id in item_ids:
        paths = _Paths(input_dir, stage, item_id)
        artifact = _load_artifact(inputs, stage, item_id)
        if artifact is None:
            findings.append(Finding("critique.artifact_missing", "error", f"{stage}:{item_id}",
                                    "No author artifact for this item"))
            continue
        revision = _round_of(artifact)
        if not _store_snapshot(paths, revision, artifact, overwrite=force):
            findings.append(Finding("repair.revision_not_bumped", "warning", _artifact_location(stage, item_id),
                                    f"Artifact differs from the stored snapshot of revision {revision}; bump `revision` "
                                    "so the repair can be validated (or --force to reset the snapshot)"))
        if stage == "questions":
            if _prepare_questions(inputs, paths, artifact, force, findings):
                written += 1
            else:
                skipped += 1
            continue
        if not force and _work_is_current(paths.work(), revision):
            skipped += 1
            continue
        if stage == "qualification":
            work = _prepare_qualification(inputs, item_id, artifact, sample_seed)
        elif stage == "groups":
            work = _prepare_groups(inputs, item_id, artifact)
        elif stage == "syntheses":
            work = _prepare_syntheses(inputs, item_id, artifact)
        elif stage == "lessons":
            work = _prepare_lessons(inputs, item_id, artifact)
        else:
            work = _prepare_cards(inputs, item_id, artifact)
        if work is None:
            continue
        dump_json(paths.work(), work)
        written += 1
    findings.append(Finding("critique.prepared", "info", f"{CRITIQUE_DIR}/{stage}/{WORK_SUBDIR}/",
                            f"{written} work file set(s) written, {skipped} up to date (use --force to rewrite)"))
    return findings


# ---------------------------------------------------------------------------
# check — criteria evaluation
# ---------------------------------------------------------------------------


@dataclass
class _Eval:
    """Aggregated verdict evaluation for one item (or one sub-item)."""

    critical_all_passed: bool = True
    score: float = 0.0
    critical_failed: list[str] = field(default_factory=list)
    undesirable: list[str] = field(default_factory=list)
    items_ok: bool = True
    results: dict[str, bool] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)


def _eval_criteria(entries: Any, catalogue: list, loc: str, overrides: dict[str, bool],
                   findings: list[Finding]) -> _Eval:
    """Validate a `criteria` list against the stage catalogue and score it.

    Missing, duplicated or malformed criteria count as failed. `overrides` holds
    script-computed results (fidelity metrics, probe matrix, Bloom relabels, blind tags):
    they replace the critic's flag and silence the `criterion_missing` finding.
    """
    cat = {cid: (weight, critical) for cid, weight, critical, _ in catalogue}
    seen: dict[str, bool] = {}
    if entries is None:
        entries = []
    if not isinstance(entries, list):
        findings.append(Finding("critique.criteria_invalid", "error", loc, "`criteria` must be a list"))
        entries = []
    for i, entry in enumerate(entries):
        if not isinstance(entry, dict):
            findings.append(Finding("critique.criterion_invalid", "error", f"{loc} criteria[{i}]", "Entry is not an object"))
            continue
        cid = entry.get("criterion_id")
        if cid not in cat:
            findings.append(Finding("critique.criterion_unknown", "warning", f"{loc} criteria[{i}]",
                                    f"Unknown criterion_id {cid!r} for this stage"))
            continue
        if cid in seen:
            findings.append(Finding("critique.criterion_duplicate", "error", f"{loc} {cid}",
                                    "Criterion appears more than once; the first entry is used"))
            continue
        passed = entry.get("passed")
        if not isinstance(passed, bool):
            findings.append(Finding("critique.criterion_invalid", "error", f"{loc} {cid}",
                                    f"`passed` must be a bool, got {passed!r}; treated as failed"))
            passed = False
        evidence = str(entry.get("evidence_span") or "").strip()
        if not passed:
            if not evidence:
                findings.append(Finding("critique.evidence_missing", "error", f"{loc} {cid}",
                                        "Failed criterion has no evidence_span"))
            if not str(entry.get("failure_reason") or "").strip() or \
                    not str(entry.get("correction_instruction") or "").strip():
                findings.append(Finding("critique.reason_missing", "error", f"{loc} {cid}",
                                        "Failed criterion needs failure_reason and correction_instruction"))
        elif cat[cid][1] and not evidence:
            findings.append(Finding("critique.evidence_missing", "warning", f"{loc} {cid}",
                                    "Passed critical criterion has no evidence_span"))
        seen[cid] = passed
    for cid in cat:
        if cid not in seen and cid not in overrides:
            findings.append(Finding("critique.criterion_missing", "error", f"{loc} {cid}",
                                    "Criterion absent from the verdict; treated as failed"))
            seen[cid] = False
    for cid, forced in overrides.items():
        if cid not in cat:
            continue
        if forced is False and seen.get(cid) is True:
            findings.append(Finding("critique.criterion_forced", "info", f"{loc} {cid}",
                                    "Critic marked it passed; the computed metric forces it to failed"))
        seen[cid] = bool(forced)
    pos_total = sum(w for w, _ in cat.values() if w > 0)
    pos_passed = sum(w for cid, (w, _) in cat.items() if w > 0 and seen.get(cid))
    return _Eval(
        critical_all_passed=all(seen.get(cid) for cid, (_, critical) in cat.items() if critical),
        score=(pos_passed / pos_total) if pos_total else 1.0,
        critical_failed=[cid for cid, (_, critical) in cat.items() if critical and not seen.get(cid)],
        undesirable=[cid for cid, (w, _) in cat.items() if w < 0 and seen.get(cid) is False],
        results=dict(seen),
    )


def _relabel_map(raw: Any) -> dict[str, str] | None:
    """Accept {id: level} or [{id|lo_id|item_id, bloom_level}]; None when absent."""
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    if isinstance(raw, list):
        out = {}
        for entry in raw:
            if isinstance(entry, dict):
                key = entry.get("id") or entry.get("lo_id") or entry.get("item_id")
                if key:
                    out[str(key)] = str(entry.get("bloom_level"))
        return out
    return None


def _eval_stage_level(verdict: dict, stage: str, extra: dict, loc: str, findings: list[Finding]) -> _Eval:
    overrides: dict[str, bool] = {}
    metrics: dict[str, Any] = {}

    if stage in ("syntheses", "lessons"):
        claims = verdict.get("claims")
        if not isinstance(claims, list) or not claims:
            findings.append(Finding("critique.claims_missing", "error", loc,
                                    "`claims` is required (one per brief item / page paragraph)"))
            claims = []
            overrides["fidelity.precision"] = False
            if stage == "syntheses":
                overrides["fidelity.recall"] = False
        supported = not_supported = 0
        supported_refs: set[str] = set()
        page_clean: dict[int, bool] = {}
        for i, claim in enumerate(claims):
            if not isinstance(claim, dict):
                findings.append(Finding("critique.claim_invalid", "error", f"{loc} claims[{i}]", "Claim is not an object"))
                continue
            verdict_value = claim.get("verdict")
            if verdict_value not in CLAIM_VERDICTS:
                findings.append(Finding("critique.claim_verdict_invalid", "error", f"{loc} claims[{i}]",
                                        f"verdict {verdict_value!r} not in {CLAIM_VERDICTS}; counted as not_supported"))
                verdict_value = "not_supported"
            ref = str(claim.get("ref") or claim.get("id") or "")
            if verdict_value == "supported":
                supported += 1
                supported_refs.add(ref)
            elif verdict_value == "not_supported":
                not_supported += 1
            match = CLAIM_REF_RE.match(ref)
            if match:
                page = int(match.group("page"))
                page_clean[page] = page_clean.get(page, True) and verdict_value != "not_supported"
        denom = supported + not_supported
        precision = (supported / denom) if denom else None
        metrics["precision"] = precision
        metrics["claims"] = {"supported": supported, "not_supported": not_supported, "total": len(claims)}
        if claims and (precision is None or precision < FIDELITY_PRECISION_MIN):
            overrides["fidelity.precision"] = False
        if stage == "syntheses":
            kps = extra.get("knowledge_point_ids")
            claim_kp = extra.get("claim_kp") or {}
            if kps:
                covered = {claim_kp.get(ref) for ref in supported_refs}
                recall = len(set(kps) & covered) / len(kps)
                metrics["recall"] = recall
                metrics["uncovered_knowledge_points"] = sorted(set(kps) - covered)
                if recall < FIDELITY_RECALL_MIN:
                    overrides["fidelity.recall"] = False
            else:
                metrics["recall"] = None
        else:
            page_count = extra.get("page_count")
            if isinstance(page_count, int) and page_count > 0:
                clean = sum(1 for p in range(page_count) if page_clean.get(p, True))
                metrics["page_clean_share"] = clean / page_count
            relabels = _relabel_map(verdict.get("bloom_relabels"))
            author = extra.get("author_bloom") or {}
            if author and relabels is None:
                findings.append(Finding("critique.bloom_relabel_missing", "warning", f"{loc} bloom_relabels",
                                        "No independent Bloom relabels; bloom.objectives_relabel_match fails"))
                overrides[BLOOM_CRITERION["lessons"]] = False
            elif author:
                bad = {k: v for k, v in relabels.items() if v not in BLOOM_LEVELS}
                for lo_id, level in bad.items():
                    findings.append(Finding("critique.bloom_invalid", "error", f"{loc} bloom_relabels[{lo_id}]",
                                            f"{level!r} is not a Bloom level"))
                mismatches = sorted(k for k, v in author.items() if relabels.get(k) != v)
                metrics["bloom_mismatches"] = mismatches
                overrides[BLOOM_CRITERION["lessons"]] = not mismatches and not bad

    if stage == "groups":
        proposed = [g for g in extra.get("proposed_edges") or [] if g]
        edges = verdict.get("edges")
        if not isinstance(edges, list):
            if proposed:
                findings.append(Finding("critique.edges_missing", "error", loc,
                                        "`edges` is required: one 3-way verdict per proposed prerequisite"))
            edges = []
        covered: dict[str, str] = {}
        for i, edge in enumerate(edges):
            if not isinstance(edge, dict):
                findings.append(Finding("critique.edge_invalid", "error", f"{loc} edges[{i}]", "Edge is not an object"))
                continue
            value = edge.get("verdict")
            if value not in PREREQ_VALIDATOR_VERDICTS:
                findings.append(Finding("critique.edge_verdict_invalid", "error", f"{loc} edges[{i}]",
                                        f"verdict {value!r} not in {PREREQ_VALIDATOR_VERDICTS}"))
                continue
            covered[str(edge.get("group_id"))] = value
        for gid in proposed:
            if gid not in covered:
                findings.append(Finding("critique.edge_missing", "error", f"{loc} edges",
                                        f"Proposed prerequisite {gid} has no verdict"))
        metrics["edges"] = covered

    ev = _eval_criteria(verdict.get("criteria"), CRITERIA[stage], loc, overrides, findings)
    ev.metrics = metrics
    return ev


def _test_student_gate(verdict: dict, extra: dict, loc: str, findings: list[Finding], metrics: dict) -> bool:
    ts = extra.get("test_student")
    if ts is None and isinstance(verdict.get("test_student"), dict):
        ts = verdict["test_student"]
    if not isinstance(ts, dict):
        findings.append(Finding("critique.test_student_missing", "warning", loc,
                                "No graded test-student run; the lesson cannot pass without one"))
        return False
    answered = ts.get("answered") if isinstance(ts.get("answered"), int) else 0
    correct = ts.get("correct") if isinstance(ts.get("correct"), int) else 0
    unanswerable = list(ts.get("unanswerable") or [])
    share = (correct / answered) if answered else 0.0
    metrics["test_student"] = {"answered": answered, "correct": correct, "share": share,
                               "unanswerable": unanswerable, "undefined_terms": list(ts.get("undefined_terms") or [])}
    ok = True
    if share < TEST_STUDENT_CORRECT_MIN:
        findings.append(Finding("critique.test_student_below_min", "info", loc,
                                f"Test student correct share {share:.2f} < {TEST_STUDENT_CORRECT_MIN}"))
        ok = False
    if unanswerable:
        findings.append(Finding("critique.test_student_unanswerable", "warning", loc,
                                f"Test student could not answer {unanswerable} from the text"))
        ok = False
    return ok


def _eval_per_item(verdict: dict, stage: str, extra: dict, loc: str, findings: list[Finding]) -> _Eval:
    catalogue = PER_ITEM_CRITERIA[stage]
    allowed = QUESTION_VERDICTS if stage == "questions" else KEEP_DROP_VERDICTS
    items = verdict.get("items")
    if not isinstance(items, list):
        findings.append(Finding("critique.items_missing", "error", loc, "`items` list is required"))
        items = []
    expected = extra.get("sub_item_ids")
    probe_results = extra.get("probe_results")
    author_bloom = extra.get("author_bloom") or {}
    seen: set[str] = set()
    sub: dict[str, dict] = {}
    critical_failed: list[str] = []
    undesirable: list[str] = []
    scores: list[float] = []
    items_ok = True
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            findings.append(Finding("critique.item_invalid", "error", f"{loc} items[{i}]", "Item is not an object"))
            items_ok = False
            continue
        sid = str(item.get("item_id") or "")
        iloc = f"{loc} items[{sid or i}]"
        if sid in seen:
            findings.append(Finding("critique.item_duplicate", "error", iloc, "Sub-item judged twice"))
            continue
        seen.add(sid)
        if expected is not None and sid not in expected:
            findings.append(Finding("critique.item_unknown", "warning", iloc, "Sub-item is not in the artifact"))
            continue
        value = item.get("verdict")
        if value not in allowed:
            findings.append(Finding("critique.item_verdict_invalid", "error", iloc,
                                    f"verdict {value!r} not in {allowed}"))
            value = None
        overrides: dict[str, bool] = {}
        if stage == "questions":
            if probe_results is None:
                overrides[PROBE_CRITERION] = False
            else:
                result = probe_results.get(sid)
                if result is None:
                    findings.append(Finding("critique.probe_grades_missing", "warning", iloc,
                                            "No blind grades for this question's probes; probe matrix fails"))
                overrides[PROBE_CRITERION] = bool(result)
            relabel = item.get("bloom_relabel")
            if sid in author_bloom:
                if relabel is None:
                    findings.append(Finding("critique.bloom_relabel_missing", "warning", iloc,
                                            "No independent Bloom relabel; q.bloom_relabel_match fails"))
                elif relabel not in BLOOM_LEVELS:
                    findings.append(Finding("critique.bloom_invalid", "error", iloc, f"{relabel!r} is not a Bloom level"))
                overrides[BLOOM_CRITERION["questions"]] = relabel == author_bloom[sid]
        ev = _eval_criteria(item.get("criteria"), catalogue, iloc, overrides, findings)
        accepted = value == "keep"  # rewrite/drop are suggestions to the author; the lesson fails until repaired
        if not accepted:
            items_ok = False
        sub[sid] = {"verdict": value, "accepted": accepted, "score": round(ev.score, 4),
                    "critical_failed": ev.critical_failed, "undesirable": ev.undesirable}
        critical_failed += [f"{sid}:{cid}" for cid in ev.critical_failed]
        undesirable += [f"{sid}:{cid}" for cid in ev.undesirable]
        scores.append(ev.score)
    if expected is not None:
        for sid in expected:
            if sid not in seen:
                findings.append(Finding("critique.item_missing", "error", f"{loc} items[{sid}]",
                                        "Sub-item has no verdict; treated as not kept"))
                items_ok = False
    metrics: dict[str, Any] = {"items": sub}
    if stage == "questions" and not _test_student_gate(verdict, extra, loc, findings, metrics):
        items_ok = False
    return _Eval(
        critical_all_passed=not critical_failed,
        score=(sum(scores) / len(scores)) if scores else 0.0,
        critical_failed=critical_failed,
        undesirable=undesirable,
        items_ok=items_ok,
        metrics=metrics,
    )


def _eval_qualification(verdict: dict, extra: dict, loc: str, findings: list[Finding]) -> _Eval:
    """Blind re-tag comparison: the script derives the tags.* criteria from the critic's tags."""
    catalogue = PER_ITEM_CRITERIA["qualification"]
    author = extra.get("author_units") or {}
    expected = extra.get("sub_item_ids")
    items = verdict.get("items")
    if not isinstance(items, list):
        findings.append(Finding("critique.items_missing", "error", loc, "`items` list is required"))
        items = []
    seen: set[str] = set()
    disagree = {"tags.concepts_agree": 0, "tags.unit_type_agrees": 0, "tags.keep_agrees": 0}
    scores: list[float] = []
    items_ok = True
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            findings.append(Finding("critique.item_invalid", "error", f"{loc} items[{i}]", "Item is not an object"))
            continue
        sid = str(item.get("item_id") or "")
        iloc = f"{loc} items[{sid or i}]"
        if sid in seen:
            findings.append(Finding("critique.item_duplicate", "error", iloc, "Unit judged twice"))
            continue
        seen.add(sid)
        tags = author.get(sid)
        if tags is None or (expected is not None and sid not in expected):
            findings.append(Finding("critique.item_unknown", "warning", iloc, "Unit is not in the critic's sample"))
            continue
        value = item.get("verdict")
        if value not in KEEP_DROP_VERDICTS:
            findings.append(Finding("critique.item_verdict_invalid", "error", iloc,
                                    f"verdict {value!r} not in {KEEP_DROP_VERDICTS}"))
            value = None
        overrides: dict[str, bool] = {}
        both_keep = (value == "keep") and bool(tags["keep"])
        both_drop = (value == "drop") and not tags["keep"]
        if isinstance(item.get("concepts"), list):
            # Concept tags only matter on units both sides keep; a dropped unit carries no
            # teaching claim, so differing tags there are not a disagreement.
            overrides["tags.concepts_agree"] = (not both_keep) or bool(set(item["concepts"]) & set(tags["concepts"]))
        if item.get("unit_type") is not None:
            same_family = UNIT_TYPE_FAMILIES.get(item["unit_type"]) == UNIT_TYPE_FAMILIES.get(tags["unit_type"])
            overrides["tags.unit_type_agrees"] = both_drop or same_family
        if value is not None:
            overrides["tags.keep_agrees"] = (value == "keep") == bool(tags["keep"])
        ev = _eval_criteria(item.get("criteria"), catalogue, iloc, overrides, findings)
        for cid in disagree:
            if not ev.results.get(cid):
                disagree[cid] += 1
        scores.append(ev.score)
    if expected is not None:
        for sid in expected:
            if sid not in seen:
                findings.append(Finding("critique.item_missing", "error", f"{loc} items[{sid}]",
                                        "Sampled unit has no blind tags"))
                items_ok = False
    n = len(scores)
    rates = {cid: (count / n if n else 1.0) for cid, count in disagree.items()}
    critical_failed = [cid for cid in ("tags.concepts_agree", "tags.unit_type_agrees")
                       if rates[cid] > QUALIFICATION_DISAGREEMENT_MAX
                       and disagree[cid] > QUALIFICATION_DISAGREEMENT_TOLERANCE]
    return _Eval(
        critical_all_passed=not critical_failed,
        score=(sum(scores) / n) if n else 0.0,
        critical_failed=critical_failed,
        items_ok=items_ok,
        metrics={"sampled": n, "concept_disagreement": rates["tags.concepts_agree"],
                 "unit_type_disagreement": rates["tags.unit_type_agrees"],
                 "keep_disagreement": rates["tags.keep_agrees"]},
    )


def compute_item_status(verdict: dict, stage: str, deterministic_errors: list, extra: dict | None = None) -> dict:
    """Turn one verdict file into {status, round, score, critical_failed, undesirable, actionable, metrics, findings}.

    pass iff every critical criterion passed, no undesirable criterion triggered, weighted
    positive score >= CRITIQUE_SCORE_MIN, no deterministic error for the item, no structural
    error in the verdict (missing claims/edges/items, duplicate criteria...) and (per-item
    stages) every sub-item is kept; else fail, or FAILED when round >= REPAIR_ROUNDS_MAX.
    `extra` carries stage context computed by `check` (knowledge points, proposed edges,
    author Bloom labels, probe grades, test-student run...).
    """
    if stage not in CRITIQUED_STAGES:
        raise ValueError(f"unknown critique stage {stage!r}")
    extra = dict(extra or {})
    findings: list[Finding] = []
    loc = extra.get("location") or f"{CRITIQUE_DIR}/{stage}/{item_id_to_filename(str(verdict.get('item_id') or '?'))}"
    round_ = verdict.get("round")
    if not isinstance(round_, int) or round_ < 0:
        round_ = int(extra.get("revision") or 0)
    det_errors = [f for f in deterministic_errors or [] if getattr(f, "severity", None) == "error"]
    if stage in STAGE_LEVEL_STAGES:
        ev = _eval_stage_level(verdict, stage, extra, loc, findings)
    elif stage == "qualification":
        ev = _eval_qualification(verdict, extra, loc, findings)
    else:
        ev = _eval_per_item(verdict, stage, extra, loc, findings)
    actionable = verdict.get("actionable_items")
    if actionable is None:
        actionable = []
    if not isinstance(actionable, list):
        findings.append(Finding("critique.actionable_invalid", "error", loc, "`actionable_items` must be a list"))
        actionable = []
    if len(actionable) > ACTIONABLE_ITEMS_MAX:
        findings.append(Finding("critique.actionable_items_excess", "warning", loc,
                                f"{len(actionable)} actionable items > {ACTIONABLE_ITEMS_MAX}; the author repairs only "
                                f"the first {ACTIONABLE_ITEMS_MAX}"))
    verdict_errors = sum(1 for f in findings if f.severity == "error")
    ok = ev.critical_all_passed and not ev.undesirable and ev.score >= CRITIQUE_SCORE_MIN \
        and not det_errors and ev.items_ok and not verdict_errors
    return {
        "status": _status_for(ok, round_),
        "round": round_,
        "score": round(ev.score, 4),
        "critical_failed": ev.critical_failed,
        "undesirable": ev.undesirable,
        "actionable": len(actionable),
        "deterministic_errors": len(det_errors),
        "verdict_errors": verdict_errors,
        "metrics": ev.metrics,
        "findings": findings,
    }


def _status_for(ok: bool, round_: int) -> str:
    return "pass" if ok else ("FAILED" if round_ >= REPAIR_ROUNDS_MAX else "fail")


def _entry(status: str, round_: int, score: float | None) -> dict:
    """Summary entry for an item that could not be evaluated (pending or unreadable verdict)."""
    return {
        "status": status, "round": round_, "score": score, "critical_failed": [], "undesirable": [],
        "actionable": 0, "deterministic_errors": 0, "verdict_errors": 0, "metrics": {},
    }


# ---------------------------------------------------------------------------
# check — stage context, blind-grader resolution, repair validation
# ---------------------------------------------------------------------------


def _read_json_object(path: Path, rel: str, findings: list[Finding],
                      check_id: str = "critique.verdict_invalid_json") -> tuple[dict | None, bool]:
    """(object, exists). A file that exists but is unreadable or not an object reports an error -> (None, True)."""
    if not path.exists():
        return None, False
    try:
        data = load_json(path)
    except (OSError, json.JSONDecodeError) as e:
        findings.append(Finding(check_id, "error", rel, str(e)))
        return None, True
    if not isinstance(data, dict):
        findings.append(Finding(check_id, "error", rel, "Expected a JSON object"))
        return None, True
    return data, True


def _grade(q_grades: dict, label: str | None, loc: str, findings: list[Finding]) -> str | None:
    """One blind grade (pass|fail) or None when absent/invalid (reported; counts as not passing)."""
    if label is None:
        return None
    value = q_grades.get(label)
    if value is None:
        findings.append(Finding("critique.grade_missing", "warning", f"{loc}[{label}]", "Candidate has no grade"))
        return None
    if value not in GRADE_VALUES:
        findings.append(Finding("critique.grade_invalid", "error", f"{loc}[{label}]",
                                f"grade {value!r} not in {GRADE_VALUES}; treated as fail"))
        return None
    return value


def _grade_probes(paths: _Paths, revision: int, embedded_ids: list[str],
                  findings: list[Finding]) -> tuple[dict[str, bool | None] | None, dict | None]:
    """Resolve the blind grades through the hidden key.

    Returns (probe_results, test_student): probe_results maps question id -> True when the
    correct probe was graded pass AND the wrong probe fail (None when that question has no
    grades); it is None altogether when the grader verdict or the key is missing, which
    fails q.probe_matrix_discriminating everywhere. test_student is computed from the
    student's verdict file when it exists: `correct` = student candidates graded pass,
    `answered` = embedded questions (an unanswered question counts as wrong).
    """
    key_path, grader_path, student_path = paths.work(GRADER_KEY_SUFFIX), paths.verdict(GRADER_SUFFIX), \
        paths.verdict(STUDENT_SUFFIX)
    key, _ = _read_json_object(key_path, paths.rel(key_path), findings)
    grader, _ = _read_json_object(grader_path, paths.rel(grader_path), findings)
    student, _ = _read_json_object(student_path, paths.rel(student_path), findings)
    probe_results: dict[str, bool | None] | None = None
    student_pass = 0
    key_has_student = False
    if key is None or grader is None:
        findings.append(Finding("critique.grader_missing", "warning", paths.rel(grader_path),
                                "No blind grader verdict (or hidden key): every q.probe_matrix_discriminating fails — "
                                "run `critique.py prepare questions` and the grader sub-agent"))
    else:
        if grader.get("round") != revision:
            findings.append(Finding("critique.grader_stale", "warning", paths.rel(grader_path),
                                    f"Grader round {grader.get('round')!r} != artifact revision {revision}"))
        grades = grader.get("grades")
        if not isinstance(grades, dict):
            findings.append(Finding("critique.grades_invalid", "error", paths.rel(grader_path),
                                    "`grades` must map question id -> {label: pass|fail}"))
            grades = {}
        probe_results = {}
        mapping_all = key.get("questions") if isinstance(key.get("questions"), dict) else {}
        for qid, mapping in mapping_all.items():
            if not isinstance(mapping, dict):
                continue
            labels = {role: label for label, role in mapping.items()}
            key_has_student = key_has_student or ROLE_STUDENT in labels
            q_grades = grades.get(qid)
            if not isinstance(q_grades, dict):
                probe_results[qid] = None
                continue
            loc = f"{paths.rel(grader_path)} grades[{qid}]"
            correct = _grade(q_grades, labels.get(ROLE_CORRECT), loc, findings)
            wrong = _grade(q_grades, labels.get(ROLE_WRONG), loc, findings)
            probe_results[qid] = correct == "pass" and wrong == "fail"
            if ROLE_STUDENT in labels and _grade(q_grades, labels[ROLE_STUDENT], loc, findings) == "pass":
                student_pass += 1
    test_student = None
    if student is not None:
        answers = _student_answers(student)
        key_digest = _key_answers_digest(key_path)
        folded_stale = key_digest is not None and key_digest != _answers_digest(_current_student_answers(student, revision))
        if (answers and not key_has_student) or folded_stale:
            findings.append(Finding("critique.grader_stale", "warning", paths.rel(key_path),
                                    "The student's answers are not among the grader candidates; re-run "
                                    "`critique.py prepare questions` and the grader"))
        test_student = {
            "answered": len(embedded_ids), "correct": student_pass,
            "unanswerable": [q for q in (student.get("unanswerable") or []) if isinstance(q, str)],
            "undefined_terms": [t for t in (student.get("undefined_terms") or []) if isinstance(t, str)],
        }
    return probe_results, test_student


def _stage_extra(inputs: _Inputs, paths: _Paths, stage: str, artifact: dict, findings: list[Finding]) -> dict:
    """Stage context compute_item_status needs beyond the verdict: knowledge points, proposed edges,
    author Bloom labels, sub-item ids, blind grades, the qualification sample and author tags."""
    extra: dict[str, Any] = {"location": paths.rel(paths.verdict()), "revision": _round_of(artifact)}
    item_id = paths.item_id
    if stage == "groups":
        extra["proposed_edges"] = [
            p.get("group_id") for p in artifact.get("prerequisites") or []
            if isinstance(p, dict) and p.get("group_id") and p.get("status") in (None, "proposed")
        ]
    elif stage == "syntheses":
        group = inputs.group(item_id)
        if group is None:
            findings.append(Finding("critique.input_missing", "warning", f"{GROUPS_FILE} groups[{item_id}]",
                                    "No group entry; fidelity.recall cannot be computed"))
        else:
            extra["knowledge_point_ids"] = [kp["id"] for kp in group.get("knowledge_points") or []
                                            if isinstance(kp, dict) and kp.get("id")]
        extra["claim_kp"] = {it["id"]: it.get("knowledge_point_id") for it in artifact.get("items") or []
                             if isinstance(it, dict) and it.get("id") and it.get("kind") == "map"}
    elif stage == "lessons":
        extra["page_count"] = sum(1 for p in artifact.get("pages") or [] if isinstance(p, dict))
        extra["author_bloom"] = {lo["id"]: lo.get("bloom_level") for lo in _objectives(artifact)}
    elif stage == "questions":
        questions = _question_items(artifact)
        extra["sub_item_ids"] = [q["id"] for _section, q in questions]
        extra["author_bloom"] = {q["id"]: q["bloom_level"] for _section, q in questions if q.get("bloom_level")}
        embedded = [q["id"] for section, q in questions if section == "embedded"]
        extra["probe_results"], extra["test_student"] = _grade_probes(paths, extra["revision"], embedded, findings)
    elif stage == "cards":
        extra["sub_item_ids"] = [c["id"] for c in artifact.get("cards") or [] if isinstance(c, dict) and c.get("id")]
    else:  # qualification: the author's tags for the blind comparison + the sampled ids
        units = artifact.get("units") if isinstance(artifact.get("units"), dict) else {}
        extra["author_units"] = {
            uid: {"concepts": [c.get("concept_id") for c in entry.get("concepts") or [] if isinstance(c, dict)],
                  "unit_type": entry.get("unit_type"), "keep": entry.get("keep", True)}
            for uid, entry in units.items() if isinstance(entry, dict)
        }
        work, _ = _read_json_object(paths.work(), paths.rel(paths.work()), findings, "critique.work_invalid_json")
        if work is not None:
            extra["sub_item_ids"] = [u["id"] for u in work.get("sample") or [] if isinstance(u, dict) and u.get("id")]
    return extra


def _prose_paragraphs(node: Any) -> list[str]:
    """Authored prose of an artifact as paragraphs, in document order (ids/enums/metadata skipped)."""
    out: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key not in _NON_PROSE_KEYS:
                out += _prose_paragraphs(value)
    elif isinstance(node, list):
        for value in node:
            out += _prose_paragraphs(value)
    elif isinstance(node, str):
        out += _paragraphs(node)
    return out


def _words(text: str) -> list[str]:
    return re.findall(r"[\w'-]+", text.lower())


def _diff_prose(old: list[str], new: list[str]) -> tuple[float, list[dict]]:
    """Changed share of the prose and the changed paragraph blocks.

    share = max(changed old words / old words, changed new words / new words); inside a
    replaced block words are matched so a one-sentence edit does not count the whole
    paragraph. Each block: {"old": [...], "new": [...], "before": str|None, "after": str|None}.
    """
    old_words, new_words = [_words(p) for p in old], [_words(p) for p in new]
    changed_old = changed_new = 0
    blocks: list[dict] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        o = [w for ws in old_words[i1:i2] for w in ws]
        n = [w for ws in new_words[j1:j2] for w in ws]
        matched = 0
        if tag == "replace":
            matched = sum(b.size for b in difflib.SequenceMatcher(None, o, n, autojunk=False).get_matching_blocks())
        changed_old += len(o) - matched
        changed_new += len(n) - matched
        blocks.append({"old": old[i1:i2], "new": new[j1:j2],
                       "before": old[i1 - 1] if i1 > 0 else None, "after": old[i1] if i1 < len(old) else None})
    total_old, total_new = sum(map(len, old_words)), sum(map(len, new_words))
    share = max(changed_old / total_old if total_old else 0.0, changed_new / total_new if total_new else 0.0)
    return share, blocks


def _block_covered(block: dict, spans: list[str]) -> bool:
    """A changed block is in scope when a named span lies in one of its old paragraphs (or, for a pure
    insertion, in a neighbouring paragraph: 'add one paragraph after X' is a legitimate instruction)."""
    def covers(span: str, para: str) -> bool:
        p = _norm_lower(para)
        return bool(p) and (span in p or p in span)

    targets = block["old"] or [p for p in (block["before"], block["after"]) if p]
    return any(covers(span, para) for span in spans for para in targets)


def _validate_repair(paths: _Paths, artifact: dict, revision: int, live_verdict: dict | None,
                     findings: list[Finding]) -> None:
    """Gate a repaired artifact (revision > 0) against the snapshot of revision - 1 and the
    actionable_items of that round's verdict."""
    loc = _artifact_location(paths.stage, paths.item_id)
    previous = _load_snapshot(paths, revision - 1)
    if previous is None:
        if any(r.isdigit() and int(r) < revision for r in _snapshots(paths)):
            findings.append(Finding("repair.revision_skipped", "warning", loc,
                                    f"No snapshot of revision {revision - 1}; a repair advances `revision` by exactly 1"))
        return
    share, blocks = _diff_prose(_prose_paragraphs(previous), _prose_paragraphs(artifact))
    if share > REPAIR_DIFF_MAX_RATIO:
        findings.append(Finding("repair.oversized", "error", loc,
                                f"{share:.0%} of the prose changed from revision {revision - 1} to {revision} "
                                f"(> {REPAIR_DIFF_MAX_RATIO:.0%}); a repair edits only the spans named in actionable_items"))
    prev_verdict = _archived_verdict(paths, revision - 1)
    if prev_verdict is None and isinstance(live_verdict, dict) and live_verdict.get("round") == revision - 1:
        prev_verdict = live_verdict
    if prev_verdict is None:
        findings.append(Finding("repair.previous_verdict_missing", "info", loc,
                                f"No verdict of round {revision - 1} on file; the repair's scope cannot be checked"))
        return
    spans = [_norm_lower(a.get("span")) for a in prev_verdict.get("actionable_items") or []
             if isinstance(a, dict) and _norm_lower(a.get("span"))]
    for block in blocks:
        if not _block_covered(block, spans):
            sample = (block["old"] or block["new"])[0]
            findings.append(Finding("repair.out_of_scope", "warning", loc,
                                    f"Changed paragraph not named by round {revision - 1} actionable_items: "
                                    f"\"{_preview(sample)}\""))


# ---------------------------------------------------------------------------
# check — per item, per stage, summary
# ---------------------------------------------------------------------------


def _check_one(inputs: _Inputs, module: Any | None, stage: str, item_id: str, findings: list[Finding]) -> dict | None:
    """Evaluate one item's verdict; returns its summary entry (None when the artifact is missing)."""
    paths = _Paths(inputs.input_dir, stage, item_id)
    rel = paths.rel(paths.verdict())
    artifact = _load_artifact(inputs, stage, item_id)
    verdict, exists = _read_json_object(paths.verdict(), rel, findings)
    if artifact is None:
        findings.append(Finding("critique.artifact_missing", "error", _artifact_location(stage, item_id),
                                "No author artifact for this item" + (" (a verdict file exists)" if exists else "")))
        return None
    revision = _round_of(artifact)
    if not _store_snapshot(paths, revision, artifact):
        findings.append(Finding("repair.revision_not_bumped", "warning", _artifact_location(stage, item_id),
                                f"Artifact differs from the stored snapshot of revision {revision}; bump `revision` "
                                "so the repair can be validated"))
    if revision > 0:
        _validate_repair(paths, artifact, revision, verdict, findings)
    if not exists:
        findings.append(Finding("critique.verdict_pending", "info", rel, "No verdict file yet"))
        return _entry("pending", revision, None)
    if verdict is None:  # unreadable: never passed
        return _entry(_status_for(False, revision), revision, 0.0)
    round_ = verdict.get("round")
    if isinstance(round_, int) and round_ < revision:
        findings.append(Finding("critique.verdict_stale", "info", rel,
                                f"Verdict round {round_} is behind artifact revision {revision}; awaiting a new critique"))
        return _entry("pending", revision, None)
    if isinstance(round_, int) and round_ > revision:
        findings.append(Finding("critique.round_ahead", "warning", rel,
                                f"Verdict round {round_} is ahead of artifact revision {revision}"))
    elif not isinstance(round_, int):
        findings.append(Finding("critique.round_missing", "warning", rel,
                                f"`round` missing or not an int; using artifact revision {revision}"))
    if verdict.get("stage") != stage:
        findings.append(Finding("critique.verdict_stage_mismatch", "error", rel,
                                f"stage {verdict.get('stage')!r} != {stage!r}"))
    if verdict.get("item_id") != item_id:
        findings.append(Finding("critique.verdict_item_mismatch", "error", rel,
                                f"item_id {verdict.get('item_id')!r} != {item_id!r}"))
    if verdict.get("contract_version") != CONTRACT_VERSION:
        findings.append(Finding("critique.contract_version", "warning", rel,
                                f"contract_version={verdict.get('contract_version')!r}, expected {CONTRACT_VERSION}"))
    pre_errors = [f for f in findings if f.severity == "error" and f.location == rel]
    extra = _stage_extra(inputs, paths, stage, artifact, findings)
    det_errors = _deterministic_errors(module, inputs.input_dir, stage, item_id)
    findings.extend(det_errors)
    result = compute_item_status(verdict, stage, det_errors, extra)
    findings.extend(result.pop("findings"))
    if pre_errors and result["status"] == "pass":  # stage/item mismatch is a structural error too
        result["status"] = _status_for(False, result["round"])
        result["verdict_errors"] += len(pre_errors)
    _archive_verdict(paths, result["round"], verdict)
    return result


def _check_stage(inputs: _Inputs, stage: str, item_ids: list[str], findings: list[Finding]) -> dict[str, dict]:
    module, module_finding = _stage_module(stage)
    if module_finding is not None:
        findings.append(module_finding)
    by_item: dict[str, dict] = {}
    for item_id in item_ids:
        entry = _check_one(inputs, module, stage, item_id, findings)
        if entry is None:
            continue
        by_item[item_id] = entry
        rel = f"{CRITIQUE_DIR}/{stage}/{item_id_to_filename(item_id)}"
        if entry["status"] == "FAILED":
            findings.append(Finding("critique.item_FAILED", "warning", rel,
                                    f"Still failing at round {entry['round']} (>= {REPAIR_ROUNDS_MAX} repair rounds); "
                                    "list it in review.json for a human"))
        else:
            score = "-" if entry["score"] is None else f"{entry['score']:.2f}"
            findings.append(Finding("critique.item_status", "info", rel,
                                    f"{entry['status']} (round {entry['round']}, score {score})"))
    return by_item


def _load_summary(input_dir: Path) -> dict:
    path = Path(input_dir) / CRITIQUE_SUMMARY
    if path.exists():
        try:
            data = load_json(path)
        except (OSError, json.JSONDecodeError):
            data = None
        if isinstance(data, dict) and isinstance(data.get("stages"), dict):
            return data
    return {"contract_version": CONTRACT_VERSION, "stages": {}}


def _stage_summary(by_item: dict[str, dict]) -> dict:
    counts = {s: 0 for s in CRITIQUE_STATUSES}
    for entry in by_item.values():
        counts[entry["status"] if entry.get("status") in counts else "fail"] += 1
    return {"items": len(by_item), **counts, "by_item": {k: by_item[k] for k in sorted(by_item)}}


def _write_summary(input_dir: Path, stage: str, by_item: dict[str, dict], replace: bool) -> dict:
    """Merge one stage's results into critique/summary.json (replace=False keeps other items of the stage).

    Locked: parallel critics each run `check --item` against the same summary."""
    with file_lock(Path(input_dir) / CRITIQUE_SUMMARY):
        data = _load_summary(input_dir)
        previous = {} if replace else (data["stages"].get(stage) or {}).get("by_item")
        merged = {**(previous if isinstance(previous, dict) else {}), **by_item}
        data["stages"][stage] = _stage_summary(merged)
        dump_json(Path(input_dir) / CRITIQUE_SUMMARY, data)
    return data


def _run(input_dir: Path, stage: str | None, item_id: str | None,
         write_summary: bool) -> tuple[dict[str, dict[str, dict]], list[Finding]]:
    """Check every item of `stage` (all critiqued stages when None) -> ({stage: {item: entry}}, findings)."""
    input_dir = Path(input_dir)
    findings: list[Finding] = []
    results: dict[str, dict[str, dict]] = {}
    if stage is not None and stage not in CRITIQUED_STAGES:
        return results, [Finding("critique.stage_unknown", "error", stage, f"Stage must be one of {CRITIQUED_STAGES}")]
    inputs = _Inputs(input_dir, findings)
    for st in ([stage] if stage else CRITIQUED_STAGES):
        item_ids = [item_id] if item_id else _list_item_ids(inputs, st)
        if item_id is None:
            for path in _verdict_files(input_dir / CRITIQUE_DIR / st):
                if _item_id_from_filename(st, path.name) not in item_ids:
                    findings.append(Finding("critique.verdict_orphan", "warning", f"{CRITIQUE_DIR}/{st}/{path.name}",
                                            "Verdict file without an author artifact"))
        if not item_ids:
            if stage is not None:
                findings.append(Finding("critique.no_items", "warning", st, "No author artifacts found for this stage"))
            continue
        results[st] = _check_stage(inputs, st, item_ids, findings)
        if write_summary:
            _write_summary(input_dir, st, results[st], replace=item_id is None)
    return results, findings


def check(input_dir: Path, stage: str | None = None, item_id: str | None = None, write_summary: bool = True,
          **_opts) -> list[Finding]:
    """Turn verdict files into pass/fail/FAILED/pending per item and merge critique/summary.json.

    Item statuses are reported as `critique.item_status` (info) / `critique.item_FAILED`
    (warning) findings; structural problems in a verdict are errors and block a pass.
    """
    return _run(input_dir, stage, item_id, write_summary)[1]


def check_item(input_dir: Path, item_id: str, stage: str | None = None, write_summary: bool = True,
               **_opts) -> list[Finding]:
    """Check one item; without `stage`, every critiqued stage that holds an artifact or verdict for it."""
    input_dir = Path(input_dir)
    if stage is None:
        probe = _Inputs(input_dir, [])
        stages = [s for s in CRITIQUED_STAGES
                  if _Paths(input_dir, s, item_id).verdict().exists() or _load_artifact(probe, s, item_id) is not None]
        if not stages:
            return [Finding("critique.item_unknown", "error", item_id,
                            "No artifact or verdict for this item in any critiqued stage")]
    else:
        stages = [stage]
    findings: list[Finding] = []
    for st in stages:
        findings.extend(_run(input_dir, st, item_id, write_summary)[1])
    return findings


def status(input_dir: Path) -> dict:
    """critique/summary.json as written by check (an empty summary when absent)."""
    return _load_summary(Path(input_dir))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

_SEVERITY_TAG = {"error": "[ERROR]", "warning": "[WARN]", "info": "[INFO]"}


def print_findings(findings: list[Finding]) -> tuple[int, int]:
    """Print findings (errors first) and the summary line; returns (errors, warnings)."""
    for f in sorted(findings, key=lambda f: (SEVERITY_ORDER.get(f.severity, 3), f.location, f.check_id)):
        print(f"{_SEVERITY_TAG.get(f.severity, '[INFO]')} {f.check_id} @ {f.location}: {f.message}")
    n_err = sum(1 for f in findings if f.severity == "error")
    n_warn = sum(1 for f in findings if f.severity == "warning")
    print(f"{n_err} errors, {n_warn} warnings")
    return n_err, n_warn


def _table(rows: list[dict]) -> str:
    """Plain-text table (deterministic; no terminal-width wrapping)."""
    if not rows:
        return "(nothing to show)"
    headers = list(rows[0])
    widths = [max(len(h), *(len(str(r.get(h, ""))) for r in rows)) for h in headers]

    def line(values: Iterable[Any]) -> str:
        return "  ".join(str(v).ljust(w) for v, w in zip(values, widths)).rstrip()

    body = (line(r.get(h, "") for h in headers) for r in rows)
    return "\n".join([line(headers), line("-" * w for w in widths), *body])


def _stage_rows(summary: dict, stages: Iterable[str] | None = None) -> list[dict]:
    wanted = set(stages) if stages else None
    return [
        {"stage": st, "items": data.get("items", 0), **{s: data.get(s, 0) for s in CRITIQUE_STATUSES}}
        for st, data in summary.get("stages", {}).items() if wanted is None or st in wanted
    ]


def _item_rows(by_stage: dict[str, dict[str, dict]]) -> list[dict]:
    rows = []
    for st, by_item in by_stage.items():
        for item_id in sorted(by_item):
            e = by_item[item_id]
            rows.append({
                "stage": st, "item": item_id, "status": e.get("status"), "round": e.get("round"),
                "score": "-" if e.get("score") is None else f"{e['score']:.2f}",
                "critical_failed": ", ".join(e.get("critical_failed") or []) or "-",
                "actionable": e.get("actionable", 0),
            })
    return rows


def _cmd_prepare(args: argparse.Namespace) -> int:
    findings = prepare(args.input_dir, args.stage, items=args.item, force=args.force, sample_seed=args.sample_seed)
    n_err, _ = print_findings(findings)
    return 1 if n_err else 0


def _cmd_check(args: argparse.Namespace) -> int:
    results, findings = _run(args.input_dir, args.stage, args.item, write_summary=True)
    n_err, n_warn = print_findings(findings)
    print(_table(_item_rows(results)))
    failing = any(e["status"] in ("fail", "FAILED") for by_item in results.values() for e in by_item.values())
    return 1 if n_err or failing or (args.strict and n_warn) else 0


def _cmd_status(args: argparse.Namespace) -> int:
    summary = status(args.input_dir)
    if not summary.get("stages"):
        print(f"no {CRITIQUE_SUMMARY} yet — run `critique.py check <stage>` first")
        return 0
    print(_table(_stage_rows(summary)))
    print()
    print(_table(_item_rows({st: data.get("by_item") or {} for st, data in summary["stages"].items()})))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Independent-critic harness: prepare work files / check verdicts")
    sub = parser.add_subparsers(dest="command", required=True)

    p_prep = sub.add_parser("prepare", help=f"write {CRITIQUE_DIR}/<stage>/{WORK_SUBDIR}/ files for a critic sub-agent")
    p_prep.add_argument("stage", choices=CRITIQUED_STAGES)
    p_prep.add_argument("input_dir", type=Path, help="output/<name>/ directory")
    p_prep.add_argument("--item", action="append", metavar="ID", help="only this item id / source key (repeatable)")
    p_prep.add_argument("--force", action="store_true", help="rewrite up-to-date work files and reset the snapshot")
    p_prep.add_argument("--sample-seed", type=int, default=0, help="qualification sample seed (default 0)")
    p_prep.set_defaults(func=_cmd_prepare)

    p_check = sub.add_parser("check", help=f"compute pass/fail/FAILED per verdict and update {CRITIQUE_SUMMARY}")
    p_check.add_argument("stage", choices=CRITIQUED_STAGES)
    p_check.add_argument("input_dir", type=Path, help="output/<name>/ directory")
    p_check.add_argument("--item", metavar="ID", help="only this item id / source key")
    p_check.add_argument("--strict", action="store_true", help="warnings fail too")
    p_check.set_defaults(func=_cmd_check)

    p_status = sub.add_parser("status", help=f"table from {CRITIQUE_SUMMARY}")
    p_status.add_argument("input_dir", type=Path, help="output/<name>/ directory")
    p_status.set_defaults(func=_cmd_status)

    args = parser.parse_args(argv)
    if not args.input_dir.is_dir():
        print(f"error: {args.input_dir} is not a directory", file=sys.stderr)
        return 2
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
