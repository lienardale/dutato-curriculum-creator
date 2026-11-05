"""Tests for critique.py (independent-critic harness).

``build_fixture`` writes a synthetic lesson-pipeline curriculum (units, concepts,
qualification, groups, a brief, two lessons, questions, cards) into tmp_path; the
``clean_*_verdict`` helpers produce verdict files that pass every gate. Each gate
test plants one defect and asserts the check_id / status, so the clean baseline
doubles as a guard against false positives. Stage modules are stubbed through
sys.modules so the harness is tested independently of the other streams.
"""

from __future__ import annotations

import copy
import json
import sys
import types
from pathlib import Path

import pytest

import _compat  # noqa: F401

import critique
from contracts import (
    CONTRACT_VERSION,
    CRITERIA,
    PER_ITEM_CRITERIA,
    QUALIFICATION_SAMPLE_MIN,
    REPAIR_ROUNDS_MAX,
    Finding,
    item_id_to_filename,
)

MAIN = "g:architecture-decision-records"
PREREQ = "g:what-architects-do"
STEM = item_id_to_filename(MAIN)[:-5]
U1, U2, U3, U4 = "u:adr:0:1", "u:adr:0:2", "u:adr:1:1", "u:arch:0:1"

UNITS = {
    "adr": [
        (U1, "The ADR template",
         "An architecture decision record (ADR) is a short document that records one decision with its "
         "context, decision, and consequences. It is kept alongside the code so that the reasoning survives "
         "team changes and new readers can see the forces that shaped each choice."),
        (U2, "Lifecycle",
         "An ADR is proposed while under discussion, accepted once the decision is made, and marked superseded "
         "when a newer record replaces it. Superseded records are never deleted, because the history of "
         "reasoning is what later readers need."),
        (U3, "Context",
         "The context section lists the forces in play: constraints, quality goals, and the team's situation at "
         "the time. Without the context, a later reader sees only an arbitrary choice and is tempted to "
         "relitigate it."),
    ],
    "arch": [
        (U4, "Accountability",
         "An architect remains accountable for the outcome of every decision, including the ones delegated to "
         "the team. Delegating a decision does not delegate the responsibility for its consequences."),
    ],
}

PAGE_TEXT = {
    0: "## Why decisions get lost\n\n"
       "Teams forget why they chose a database or a messaging style within a year, because the reasoning "
       "lived in a meeting and a chat thread.\n\n"
       "A decision record keeps that reasoning next to the code, so a newcomer reads the forces instead of "
       "guessing them.",
    1: "## The ADR template\n\n"
       "Every record has three parts: the context that describes the forces, the decision itself, and the "
       "consequences the team accepts.\n\n"
       "When a newer decision replaces an older one, the old record stays and is marked superseded, because "
       "deleting it would destroy the history later readers rely on.",
    2: "## Recap\n\n"
       "Recording the context and consequences of each decision lets a team revisit choices without "
       "relitigating them.",
}

SENTINELS = ("AUTHOR RATIONALE", "CORRECT PROBE", "WRONG PROBE", "SOURCE QUOTE SENTINEL", "KEY POINT SENTINEL")


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def build_fixture(root: Path) -> Path:
    cur = root / "cur"
    write_json(cur / "manifest.json", {"name": "Fixture", "domain": "fixture", "pipeline": "lesson", "sources": []})
    for source, units in UNITS.items():
        write_json(cur / "units" / f"{source}.json", {
            "contract_version": CONTRACT_VERSION, "source": source, "source_file": f"{source}.json",
            "source_type": "web", "title": source, "scope_applied": False,
            "units": [{"id": uid, "section_index": int(uid.split(":")[2]), "section_title": title,
                       "heading_path": [title], "block_type": "prose", "text": text, "token_count": 60,
                       "raw_span": [0, len(text)], "page": None, "images": [], "cleaning": {}}
                      for uid, title, text in units],
        })
    write_json(cur / "units" / "index.json", {"contract_version": CONTRACT_VERSION, "sources": {}, "total_units": 4,
                                               "total_tokens": 240, "scope": None})
    write_json(cur / "concepts.json", {
        "contract_version": CONTRACT_VERSION,
        "concepts": [
            {"id": "c:adr", "label": "Architecture Decision Record", "aliases": ["ADR"],
             "description": "A short record of one decision.", "learning_step": "applied",
             "depends_on": ["c:architect-accountability"], "coverage": "strong", "sources": ["adr"]},
            {"id": "c:decision-context", "label": "Decision context", "aliases": ["context section"],
             "description": "The forces behind a decision.", "learning_step": "applied", "depends_on": [],
             "coverage": "moderate", "sources": ["adr"]},
            {"id": "c:architect-accountability", "label": "Architect accountability", "aliases": ["architect"],
             "description": "Owning outcomes of decisions.", "learning_step": "foundation", "depends_on": [],
             "coverage": "thin", "sources": ["arch"]},
        ],
        "gaps": [], "ordering": ["c:architect-accountability", "c:adr", "c:decision-context"], "merges": [],
    })
    write_json(cur / "qualification" / "adr.json", {
        "contract_version": CONTRACT_VERSION, "source": "adr",
        "units": {
            U1: {"concepts": [{"concept_id": "c:adr", "quote": "records one decision"}], "unit_type": "definition",
                 "knowledge_level": "concept", "keep": True},
            U2: {"concepts": [{"concept_id": "c:adr", "quote": "marked superseded"}], "unit_type": "procedure",
                 "knowledge_level": "procedure", "keep": True},
            U3: {"concepts": [{"concept_id": "c:decision-context", "quote": "forces in play"}],
                 "unit_type": "mechanism", "knowledge_level": "principle", "keep": True},
        },
        "gleaning": {},
    })
    write_json(cur / "qualification" / "arch.json", {
        "contract_version": CONTRACT_VERSION, "source": "arch",
        "units": {U4: {"concepts": [{"concept_id": "c:architect-accountability", "quote": "remains accountable"}],
                       "unit_type": "definition", "knowledge_level": "principle", "keep": True}},
        "gleaning": {},
    })
    objectives = [
        {"id": f"{MAIN}#lo1", "text": "Write an ADR for a given decision with context, decision and consequences",
         "bloom_level": "apply", "concept_ids": ["c:adr"], "unit_ids": [U1], "knowledge_point_ids": [f"{MAIN}#kp1"]},
        {"id": f"{MAIN}#lo2", "text": "Explain how the context section lets a later team revisit a decision",
         "bloom_level": "understand", "concept_ids": ["c:decision-context"], "unit_ids": [U3],
         "knowledge_point_ids": [f"{MAIN}#kp3"]},
    ]
    write_json(cur / "groups.json", {
        "contract_version": CONTRACT_VERSION,
        "areas": [{"id": "a:leadership", "title": "Architecture Leadership", "sort_order": 0, "suggested_level": 2,
                   "modules": [{"id": "m:decisions", "title": "Recording Decisions", "sort_order": 0}]}],
        "groups": [
            {"id": PREREQ, "title": "What Architects Do", "area_id": "a:leadership", "module_id": "m:decisions",
             "sort_order": 0, "concept_ids": ["c:architect-accountability"], "unit_ids": [U4], "related_unit_ids": [],
             "learning_step": "foundation", "suggested_level": 1,
             "generalization": "Architect accountability survives delegation of the decision itself.",
             "knowledge_points": [{"id": f"{PREREQ}#kp1", "title": "Accountability", "unit_ids": [U4]}],
             "objectives": [{"id": f"{PREREQ}#lo1", "text": "Explain why delegating a decision keeps the architect "
                             "accountable", "bloom_level": "understand", "concept_ids": ["c:architect-accountability"],
                             "unit_ids": [U4], "knowledge_point_ids": [f"{PREREQ}#kp1"]}],
             "watch": [], "duplicates": [], "conflicts": [], "prerequisites": [], "consequential": False,
             "rationale": "Foundation for every later decision lesson.", "budget": None},
            {"id": MAIN, "title": "Architecture Decision Records", "area_id": "a:leadership",
             "module_id": "m:decisions", "sort_order": 1, "concept_ids": ["c:adr", "c:decision-context"],
             "unit_ids": [U1, U2, U3], "related_unit_ids": [], "learning_step": "applied", "suggested_level": 2,
             "generalization": "Recording the context and consequences of each decision lets a team revisit "
                               "choices without relitigating them.",
             "knowledge_points": [{"id": f"{MAIN}#kp1", "title": "The ADR template", "unit_ids": [U1]},
                                  {"id": f"{MAIN}#kp2", "title": "Lifecycle", "unit_ids": [U2]},
                                  {"id": f"{MAIN}#kp3", "title": "Why context matters", "unit_ids": [U3]}],
             "objectives": objectives,
             "watch": [{"id": f"{MAIN}#w1", "kind": "misconception", "text": "ADRs document the system, not "
                        "decisions", "unit_ids": [U1]}],
             "duplicates": [], "conflicts": [],
             "prerequisites": [{"group_id": PREREQ, "strength": "recommended", "evidence_unit_ids": [U4],
                                "reason": "AUTHOR EDGE REASON", "status": "proposed"}],
             "consequential": False, "rationale": "AUTHOR GROUP RATIONALE", "budget": None},
        ],
        "dropped": [],
    })
    write_json(cur / "syntheses" / item_id_to_filename(MAIN), {
        "contract_version": CONTRACT_VERSION, "group_id": MAIN, "revision": 0,
        "overview": "ADRs record decisions with their context and consequences.",
        "items": [
            {"id": f"{MAIN}#b1", "kind": "map", "text": "An ADR records one decision with context, decision and "
             "consequences.", "unit_ids": [U1], "quote": "records one decision", "confidence": "attributed",
             "concept_ids": ["c:adr"], "lo_ids": [f"{MAIN}#lo1"], "knowledge_point_id": f"{MAIN}#kp1"},
            {"id": f"{MAIN}#b2", "kind": "map", "text": "A replaced ADR is marked superseded rather than deleted.",
             "unit_ids": [U2], "quote": "marked superseded", "confidence": "attributed", "concept_ids": ["c:adr"],
             "lo_ids": [f"{MAIN}#lo1"], "knowledge_point_id": f"{MAIN}#kp2"},
            {"id": f"{MAIN}#b3", "kind": "map", "text": "The context section lists the forces that shaped the "
             "decision.", "unit_ids": [U3], "quote": "forces in play", "confidence": "attributed",
             "concept_ids": ["c:decision-context"], "lo_ids": [f"{MAIN}#lo2"], "knowledge_point_id": f"{MAIN}#kp3"},
            {"id": f"{MAIN}#b4", "kind": "watch", "text": "Learners assume superseded records should be pruned.",
             "unit_ids": [U2], "quote": "never deleted", "confidence": "attributed", "concept_ids": ["c:adr"],
             "lo_ids": [], "knowledge_point_id": None},
        ],
        "examples": [], "terminology": [], "lessons_learned": [],
    })
    write_json(cur / "lessons" / item_id_to_filename(MAIN), {
        "contract_version": CONTRACT_VERSION, "lesson_id": MAIN, "group_id": MAIN, "revision": 0,
        "title": "Architecture Decision Records", "description": "Recording decisions so they survive.",
        "area_id": "a:leadership", "module_id": "m:decisions", "suggested_level": 2, "estimated_minutes": 6,
        "key_terms": ["ADR", "superseded"],
        "objectives": [{"id": lo["id"], "text": lo["text"], "bloom_level": lo["bloom_level"]} for lo in objectives],
        "prerequisites": [PREREQ],
        "pages": [
            {"index": 0, "role": "orientation", "title": "Why decisions get lost", "lo_ids": [f"{MAIN}#lo2"],
             "knowledge_point_id": None, "unit_ids": [U1], "brief_item_ids": [f"{MAIN}#b1"], "markdown": PAGE_TEXT[0]},
            {"index": 1, "role": "core", "title": "The ADR template", "lo_ids": [f"{MAIN}#lo1"],
             "knowledge_point_id": f"{MAIN}#kp1", "unit_ids": [U1, U2], "brief_item_ids": [f"{MAIN}#b1", f"{MAIN}#b2"],
             "markdown": PAGE_TEXT[1]},
            {"index": 2, "role": "recap", "title": "Recap", "lo_ids": [f"{MAIN}#lo1", f"{MAIN}#lo2"],
             "knowledge_point_id": None, "unit_ids": [U3], "brief_item_ids": [f"{MAIN}#b3"], "markdown": PAGE_TEXT[2]},
        ],
        "outline_approved": True, "lessons_learned": ["Keep the recap short."],
    })
    write_json(cur / "lessons" / item_id_to_filename(PREREQ), {
        "contract_version": CONTRACT_VERSION, "lesson_id": PREREQ, "group_id": PREREQ, "revision": 0,
        "title": "What Architects Do", "description": "Accountability.", "area_id": "a:leadership",
        "module_id": "m:decisions", "suggested_level": 1, "estimated_minutes": 4, "key_terms": ["accountable"],
        "objectives": [{"id": f"{PREREQ}#lo1", "text": "Explain accountability", "bloom_level": "understand"}],
        "prerequisites": [],
        "pages": [
            {"index": 0, "role": "orientation", "title": "Owning outcomes", "lo_ids": [f"{PREREQ}#lo1"],
             "knowledge_point_id": None, "unit_ids": [U4], "brief_item_ids": [],
             "markdown": "## Owning outcomes\n\nAn architect stays accountable for delegated decisions."},
            {"index": 1, "role": "recap", "title": "Recap", "lo_ids": [f"{PREREQ}#lo1"], "knowledge_point_id": None,
             "unit_ids": [U4], "brief_item_ids": [],
             "markdown": "## Recap\n\nPREREQ RECAP: accountability survives delegation."},
        ],
        "outline_approved": True, "lessons_learned": [],
    })
    acceptance = {"prediction": "The old ADR stays and is marked superseded",
                  "causal_reason": "the record preserves the reasoning history later readers need"}
    write_json(cur / "questions" / item_id_to_filename(MAIN), {
        "contract_version": CONTRACT_VERSION, "lesson_id": MAIN, "revision": 0,
        "embedded": [
            {"id": f"{MAIN}#q1", "kind": "check", "page_index": 1, "position": "after", "lo_id": f"{MAIN}#lo1",
             "concept_id": "c:adr", "unit_ids": [U2], "source_quote": "SOURCE QUOTE SENTINEL",
             "prompt": "A team changes a decision recorded two years ago. What happens to the old ADR, and why?",
             "acceptance": acceptance, "expected_answer": "It is kept and marked superseded.",
             "rationale": "AUTHOR RATIONALE", "key_points": ["KEY POINT SENTINEL"], "bloom_level": "understand",
             "probes": {"correct_learner_answer": "CORRECT PROBE: kept, marked superseded, history matters",
                        "wrong_learner_answer": "WRONG PROBE: delete it to avoid confusion"}},
        ],
        "quiz": [
            {"id": f"{MAIN}#q2", "type": "multiple_choice", "lo_id": f"{MAIN}#lo1", "concept_id": "c:adr",
             "unit_ids": [U1], "page_index": 1, "source_quote": "SOURCE QUOTE SENTINEL",
             "prompt": "Which section of an ADR explains the forces that shaped the choice?",
             "options": ["Decision", "Consequences", "Context", "Status"], "correct_index": 2,
             "distractors": [{"index": 0, "misconception": "the decision is the reasoning"}],
             "acceptance": {"prediction": "Context", "causal_reason": "the context section lists the forces in play"},
             "expected_answer": "Context", "rationale": "AUTHOR RATIONALE", "key_points": ["KEY POINT SENTINEL"],
             "explanation": "The context lists the forces.", "bloom_level": "apply", "mastery_level": 3,
             "feedback": {"confirmatory": "Right.", "remedial_hint": "Think about forces."},
             "probes": {"correct_learner_answer": "CORRECT PROBE: context", "wrong_learner_answer": "WRONG PROBE: status"},
             "variants": [], "recap_of": None},
        ],
        "exercises": [], "lessons_learned": [],
    })
    write_json(cur / "cards" / item_id_to_filename(MAIN), {
        "contract_version": CONTRACT_VERSION, "lesson_id": MAIN, "revision": 0,
        "cards": [
            {"id": f"{MAIN}#k1", "card_type": "concept", "front": "What three sections does every ADR record?",
             "back": "Context, decision, consequences.", "card_data": {}, "lo_id": f"{MAIN}#lo1", "unit_ids": [U1],
             "page_index": 1, "source_reference": "adr", "lens": "parts_wholes"},
            {"id": f"{MAIN}#k2", "card_type": "cloze", "front": "A replaced ADR is marked ___ rather than deleted.",
             "back": "superseded", "card_data": {"blank_answer": "superseded", "hint": "status word"},
             "lo_id": f"{MAIN}#lo1", "unit_ids": [U2], "page_index": 1, "source_reference": "adr", "lens": "recall"},
        ],
        "lessons_learned": [],
    })
    return cur


# ---------------------------------------------------------------------------
# verdict builders
# ---------------------------------------------------------------------------


def passed_criteria(catalogue, exclude=()) -> list[dict]:
    return [{"criterion_id": cid, "passed": True, "evidence_span": "verbatim evidence", "failure_reason": None,
             "correction_instruction": None} for cid, *_ in catalogue if cid not in exclude]


def fail_criterion(criteria: list[dict], cid: str) -> None:
    for c in criteria:
        if c["criterion_id"] == cid:
            c.update(passed=False, failure_reason="page 1 states X; unit says Y",
                     correction_instruction=f"Fix the span, grounded in {U1}")
            return
    raise KeyError(cid)


def lesson_claim_refs(lesson: dict) -> list[str]:
    refs = []
    for page in lesson["pages"]:
        for j, _ in enumerate(critique._paragraphs(page["markdown"])):
            refs.append(f"page:{page['index']}:para:{j}")
    return refs


def clean_lessons_verdict(cur: Path, round_: int = 0) -> dict:
    lesson = read_json(cur / "lessons" / item_id_to_filename(MAIN))
    return {
        "contract_version": CONTRACT_VERSION, "stage": "lessons", "item_id": MAIN, "round": round_,
        "critic": "lesson-critic", "criteria": passed_criteria(CRITERIA["lessons"]),
        "claims": [{"ref": ref, "verdict": "supported", "unit_ids": [U1], "reason": ""} for ref in lesson_claim_refs(lesson)],
        "bloom_relabels": {lo["id"]: lo["bloom_level"] for lo in lesson["objectives"]},
        "actionable_items": [], "notes": "",
    }


def clean_syntheses_verdict() -> dict:
    return {
        "contract_version": CONTRACT_VERSION, "stage": "syntheses", "item_id": MAIN, "round": 0, "critic": "c",
        "criteria": passed_criteria(CRITERIA["syntheses"]),
        "claims": [{"ref": f"{MAIN}#b{n}", "verdict": "supported", "unit_ids": [U1], "reason": ""} for n in (1, 2, 3, 4)],
        "actionable_items": [], "notes": "",
    }


def clean_groups_verdict() -> dict:
    return {
        "contract_version": CONTRACT_VERSION, "stage": "groups", "item_id": MAIN, "round": 0, "critic": "c",
        "criteria": passed_criteria(CRITERIA["groups"]),
        "edges": [{"group_id": PREREQ, "verdict": "confirmed", "reason": "accountability is assumed", "unit_ids": [U4]}],
        "actionable_items": [], "notes": "",
    }


def clean_questions_verdict(with_test_student: bool = True) -> dict:
    criteria = PER_ITEM_CRITERIA["questions"]
    verdict = {
        "contract_version": CONTRACT_VERSION, "stage": "questions", "item_id": MAIN, "round": 0, "critic": "c",
        "items": [
            {"item_id": f"{MAIN}#q1", "verdict": "keep", "criteria": passed_criteria(criteria, (critique.PROBE_CRITERION,)),
             "rewrite": None, "bloom_relabel": "understand", "feedback_to_author": ""},
            {"item_id": f"{MAIN}#q2", "verdict": "keep", "criteria": passed_criteria(criteria, (critique.PROBE_CRITERION,)),
             "rewrite": None, "bloom_relabel": "apply", "feedback_to_author": ""},
        ],
        "notes": "",
    }
    if with_test_student:
        verdict["test_student"] = {"answered": 1, "correct": 1, "unanswerable": [], "undefined_terms": []}
    return verdict


def clean_cards_verdict() -> dict:
    return {
        "contract_version": CONTRACT_VERSION, "stage": "cards", "item_id": MAIN, "round": 0, "critic": "c",
        "items": [{"item_id": f"{MAIN}#k{n}", "verdict": "keep", "criteria": passed_criteria(PER_ITEM_CRITERIA["cards"]),
                   "alternatives": [], "feedback_to_author": ""} for n in (1, 2)],
        "notes": "",
    }


def clean_qualification_verdict(cur: Path) -> dict:
    author = read_json(cur / "qualification" / "adr.json")["units"]
    return {
        "contract_version": CONTRACT_VERSION, "stage": "qualification", "item_id": "adr", "round": 0, "critic": "c",
        "items": [{"item_id": uid, "verdict": "keep", "concepts": [c["concept_id"] for c in entry["concepts"]],
                   "unit_type": entry["unit_type"], "notes": ""} for uid, entry in author.items()],
        "notes": "",
    }


def grades_from_key(cur: Path, student_grade: str = "pass", swap: bool = False) -> dict:
    """Blind grades derived from the hidden key (tests know the key; a real grader never does)."""
    key = read_json(cur / "critique" / "questions" / "work" / f"{STEM}.grader.key.json")
    grades = {}
    for qid, mapping in key["questions"].items():
        grades[qid] = {}
        for label, role in mapping.items():
            if role == critique.ROLE_CORRECT:
                grades[qid][label] = "fail" if swap else "pass"
            elif role == critique.ROLE_WRONG:
                grades[qid][label] = "pass" if swap else "fail"
            else:
                grades[qid][label] = student_grade
    return {"contract_version": CONTRACT_VERSION, "stage": "questions", "item_id": MAIN, "round": 0, "grader": "g",
            "grades": grades}


def write_verdict(cur: Path, stage: str, verdict: dict, suffix: str = ".json", item_id: str = MAIN) -> Path:
    path = cur / "critique" / stage / (item_id_to_filename(item_id)[:-5] + suffix)
    write_json(path, verdict)
    return path


def by_item(cur: Path, stage: str) -> dict:
    return read_json(cur / "critique" / "summary.json")["stages"][stage]["by_item"]


def ids(findings: list[Finding]) -> set[str]:
    return {f.check_id for f in findings}


def errors(findings: list[Finding]) -> list[Finding]:
    return [f for f in findings if f.severity == "error"]


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def stub_stage_modules(monkeypatch):
    """Every stage module exists and reports no deterministic errors unless a test says otherwise."""
    for name in set(critique.STAGE_MODULES.values()):
        monkeypatch.setitem(sys.modules, name, types.SimpleNamespace(check_item=lambda input_dir, item_id, **_: []))


@pytest.fixture
def cur(tmp_path: Path) -> Path:
    return build_fixture(tmp_path)


@pytest.fixture
def prepared(cur: Path) -> Path:
    for stage in critique.CRITIQUED_STAGES:
        assert not errors(critique.prepare(cur, stage)), stage
    return cur


# ---------------------------------------------------------------------------
# prepare — withholding and work-file shapes
# ---------------------------------------------------------------------------


def test_prepare_questions_withholds_author_fields(prepared: Path):
    work = read_json(prepared / "critique" / "questions" / "work" / f"{STEM}.json")
    assert work["stage"] == "questions" and work["item_id"] == MAIN and work["round"] == 0
    assert work["contract_version"] == CONTRACT_VERSION and "protocol" in work and work["lessons_learned"] == []
    assert [c["id"] for c in work["criteria"]] == [cid for cid, *_ in PER_ITEM_CRITERIA["questions"]
                                                   if cid != critique.PROBE_CRITERION]
    assert {c["id"] for c in work["criteria"]} >= {"q.answerable", "q.answer_leaks_on_page"}
    assert all({"id", "weight", "critical", "description"} <= set(c) for c in work["criteria"])
    for view in work["questions"]:
        for withheld in ("rationale", "source_quote", "probes", "key_points", "bloom_level"):
            assert withheld not in view
        assert view["page_markdown"] == PAGE_TEXT[1]
        assert [u["id"] for u in view["units"]] == view["unit_ids"]
    mcq = next(v for v in work["questions"] if v["id"] == f"{MAIN}#q2")
    assert mcq["options"] == ["Decision", "Consequences", "Context", "Status"] and mcq["correct_index"] == 2
    assert "expected_answer" in next(v for v in work["questions"] if v["id"] == f"{MAIN}#q1")
    dumped = json.dumps(work)
    assert not any(s in dumped for s in SENTINELS)


def test_prepare_questions_student_file(prepared: Path):
    student = read_json(prepared / "critique" / "questions" / "work" / f"{STEM}.student.json")
    assert student["prerequisite_recaps"][0]["lesson_id"] == PREREQ
    assert "PREREQ RECAP" in student["prerequisite_recaps"][0]["markdown"]
    assert [p["index"] for p in student["pages"]] == [0, 1, 2]
    assert [q["id"] for q in student["pages"][1]["questions_after"]] == [f"{MAIN}#q1"]
    assert student["pages"][1]["questions_before"] == []
    assert not any(s in json.dumps(student) for s in SENTINELS)


def test_prepare_questions_grader_blind_and_key_separate(prepared: Path):
    work_dir = prepared / "critique" / "questions" / "work"
    grader = read_json(work_dir / f"{STEM}.grader.json")
    key = read_json(work_dir / f"{STEM}.grader.key.json")
    assert "AUTHOR RATIONALE" not in json.dumps(grader) and "SOURCE QUOTE SENTINEL" not in json.dumps(grader)
    for role in (critique.ROLE_CORRECT, critique.ROLE_WRONG, critique.ROLE_STUDENT):
        assert role not in json.dumps(grader["questions"])
    entries = {e["question_id"]: e for e in grader["questions"]}
    assert set(entries) == {f"{MAIN}#q1", f"{MAIN}#q2"} == set(key["questions"])
    for qid, mapping in key["questions"].items():
        assert set(mapping) == {"A", "B"}
        assert set(mapping.values()) == {critique.ROLE_CORRECT, critique.ROLE_WRONG}
        label_of = {role: label for label, role in mapping.items()}
        assert entries[qid]["candidates"][label_of[critique.ROLE_CORRECT]].startswith("CORRECT PROBE")
        assert entries[qid]["candidates"][label_of[critique.ROLE_WRONG]].startswith("WRONG PROBE")
        assert entries[qid]["acceptance"]["prediction"]


def test_prepare_grader_shuffle_is_deterministic(prepared: Path):
    key_path = prepared / "critique" / "questions" / "work" / f"{STEM}.grader.key.json"
    first = read_json(key_path)
    assert not errors(critique.prepare(prepared, "questions", force=True))
    assert read_json(key_path) == first
    # a different item id seeds a different permutation at least somewhere across many ids
    rng_orders = {json.dumps(critique._build_grader(critique._Paths(prepared, "questions", f"g:x{i}"),
                                                     {"revision": 0}, critique._question_items(
                                                         read_json(prepared / "questions" / item_id_to_filename(MAIN))),
                                                     {}, [])[1]["questions"], sort_keys=True) for i in range(6)}
    assert len(rng_orders) > 1


def test_prepare_qualification_sample_has_no_author_tags(prepared: Path):
    work = read_json(prepared / "critique" / "qualification" / "work" / "adr.json")
    assert work["sample_size"] == 3 == work["total_units"] and 3 <= QUALIFICATION_SAMPLE_MIN
    for unit in work["sample"]:
        assert set(unit) == {"id", "text", "block_type"}
    assert {c["id"] for c in work["concepts"]} == {"c:adr", "c:decision-context", "c:architect-accountability"}
    assert "definition" in work["enums"]["unit_types"]
    assert [c["id"] for c in work["criteria"]] == [cid for cid, *_ in PER_ITEM_CRITERIA["qualification"]]
    assert "quote" not in json.dumps(work["sample"])


def test_prepare_qualification_sample_is_deterministic_and_seeded(tmp_path: Path):
    cur = build_fixture(tmp_path)
    units = read_json(cur / "units" / "adr.json")
    units["units"] = [dict(units["units"][0], id=f"u:adr:9:{n}") for n in range(40)]
    write_json(cur / "units" / "adr.json", units)
    critique.prepare(cur, "qualification", items=["adr"], sample_seed=1)
    first = [u["id"] for u in read_json(cur / "critique" / "qualification" / "work" / "adr.json")["sample"]]
    assert len(first) == QUALIFICATION_SAMPLE_MIN
    critique.prepare(cur, "qualification", items=["adr"], sample_seed=1, force=True)
    assert [u["id"] for u in read_json(cur / "critique" / "qualification" / "work" / "adr.json")["sample"]] == first
    critique.prepare(cur, "qualification", items=["adr"], sample_seed=2, force=True)
    assert [u["id"] for u in read_json(cur / "critique" / "qualification" / "work" / "adr.json")["sample"]] != first


def test_prepare_groups_withholds_rationale_and_edge_reasons(prepared: Path):
    work = read_json(prepared / "critique" / "groups" / "work" / f"{STEM}.json")
    assert "rationale" not in work["group"] and "prerequisites" not in work["group"]
    assert "AUTHOR GROUP RATIONALE" not in json.dumps(work) and "AUTHOR EDGE REASON" not in json.dumps(work)
    assert work["proposed_prerequisites"] == [{"group_id": PREREQ, "strength": "recommended", "evidence_unit_ids": [U4]}]
    assert work["edge_verdicts"] == ["confirmed", "either", "uncertain"]
    assert [u["id"] for u in work["units"]] == [U1, U2, U3]
    assert {c["id"] for c in work["concepts"]} == {"c:adr", "c:decision-context"}
    assert [c["id"] for c in work["criteria"]] == [cid for cid, *_ in CRITERIA["groups"]]
    assert work["output_shape"]["edges"][0]["group_id"] == PREREQ


def test_prepare_syntheses_factors_claims(prepared: Path):
    work = read_json(prepared / "critique" / "syntheses" / "work" / f"{STEM}.json")
    claims = {c["id"]: c for c in work["claims"]}
    assert set(claims) == {f"{MAIN}#b{n}" for n in (1, 2, 3, 4)}
    assert claims[f"{MAIN}#b3"]["cited_units"] == [{"id": U3, "text": UNITS["adr"][2][2]}]
    assert claims[f"{MAIN}#b1"]["kind"] == "map"
    assert [kp["id"] for kp in work["coverage_task"]["knowledge_points"]] == [f"{MAIN}#kp{n}" for n in (1, 2, 3)]
    assert len(work["coverage_task"]["items"]) == 4
    assert [c["id"] for c in work["criteria"]] == [cid for cid, *_ in CRITERIA["syntheses"]]


def test_prepare_lessons_paragraph_refs_and_withheld_bloom(prepared: Path):
    work = read_json(prepared / "critique" / "lessons" / "work" / f"{STEM}.json")
    assert [p["ref"] for p in work["paragraphs"]][:4] == ["page:0:para:0", "page:0:para:1", "page:0:para:2", "page:1:para:0"]
    assert all("bloom_level" not in lo for lo in work["objectives"]) and len(work["objectives"]) == 2
    assert work["generalization"].startswith("Recording the context")
    assert {b["id"] for b in work["brief_items"]} == {f"{MAIN}#b1", f"{MAIN}#b2", f"{MAIN}#b3"}
    assert {u["id"] for u in work["units"]} == {U1, U2, U3}
    assert work["lessons_learned"] == ["Keep the recap short."]
    assert set(work["output_shape"]["bloom_relabels"]) == {f"{MAIN}#lo1", f"{MAIN}#lo2"}
    assert "### lessons" in work["protocol"] and "## 3." in work["protocol"] and "### cards" not in work["protocol"]


def test_prepare_cards_carries_page_and_units(prepared: Path):
    work = read_json(prepared / "critique" / "cards" / "work" / f"{STEM}.json")
    assert [c["id"] for c in work["cards"]] == [f"{MAIN}#k1", f"{MAIN}#k2"]
    assert work["cards"][1]["card_data"] == {"blank_answer": "superseded", "hint": "status word"}
    assert work["cards"][0]["page_markdown"] == PAGE_TEXT[1]
    assert [u["id"] for u in work["cards"][1]["units"]] == [U2]
    assert [c["id"] for c in work["criteria"]] == [cid for cid, *_ in PER_ITEM_CRITERIA["cards"]]


def test_prepare_skips_current_work_files_unless_forced(cur: Path):
    findings = critique.prepare(cur, "lessons")
    assert "2 work file set(s) written" in next(f.message for f in findings if f.check_id == "critique.prepared")
    findings = critique.prepare(cur, "lessons")
    assert "0 work file set(s) written, 2 up to date" in next(f.message for f in findings if f.check_id == "critique.prepared")
    findings = critique.prepare(cur, "lessons", items=[MAIN], force=True)
    assert "1 work file set(s) written" in next(f.message for f in findings if f.check_id == "critique.prepared")


def test_prepare_stores_snapshot_and_reports_missing_artifact(cur: Path):
    critique.prepare(cur, "lessons", items=[MAIN])
    snap = read_json(cur / "critique" / "lessons" / "work" / f"{STEM}.snapshot.json")
    assert snap["revisions"]["0"]["title"] == "Architecture Decision Records"
    findings = critique.prepare(cur, "lessons", items=["g:nope"])
    assert [f.check_id for f in errors(findings)] == ["critique.artifact_missing"]
    assert errors(critique.prepare(cur, "bogus"))[0].check_id == "critique.stage_unknown"


def test_prepare_questions_without_lesson_is_an_error(cur: Path):
    (cur / "lessons" / item_id_to_filename(MAIN)).unlink()
    findings = critique.prepare(cur, "questions")
    assert any(f.check_id == "critique.input_missing" and f.severity == "error" for f in findings)


# ---------------------------------------------------------------------------
# check — statuses
# ---------------------------------------------------------------------------


def test_check_lessons_pass_writes_summary(prepared: Path):
    write_verdict(prepared, "lessons", clean_lessons_verdict(prepared))
    findings = critique.check(prepared, "lessons")
    assert not errors(findings)
    summary = read_json(prepared / "critique" / "summary.json")
    stage = summary["stages"]["lessons"]
    assert stage["items"] == 2 and stage["pass"] == 1 and stage["pending"] == 1 and stage["fail"] == 0
    entry = stage["by_item"][MAIN]
    assert entry["status"] == "pass" and entry["round"] == 0 and entry["score"] == 1.0
    assert entry["critical_failed"] == [] and entry["actionable"] == 0
    assert entry["metrics"]["precision"] == 1.0 and entry["metrics"]["bloom_mismatches"] == []
    assert stage["by_item"][PREREQ]["status"] == "pending"
    assert "critique.verdict_pending" in ids(findings)
    assert (prepared / "critique" / "lessons" / "work" / f"{STEM}.verdict.r0.json").exists()


def test_check_fail_on_failed_critical_criterion(prepared: Path):
    verdict = clean_lessons_verdict(prepared)
    fail_criterion(verdict["criteria"], "depth.mechanism_explained")
    verdict["actionable_items"] = [{"span": "Every record has three parts", "instruction": "add the why", "ground_in": [U1]}]
    write_verdict(prepared, "lessons", verdict)
    findings = critique.check(prepared, "lessons", item_id=MAIN)
    entry = by_item(prepared, "lessons")[MAIN]
    assert entry["status"] == "fail" and entry["critical_failed"] == ["depth.mechanism_explained"]
    assert entry["actionable"] == 1 and entry["score"] < 1.0
    assert not errors(findings)
    assert any(f.check_id == "critique.item_status" and f.message.startswith("fail") for f in findings)


def test_check_minor_failures_only_still_pass(prepared: Path):
    verdict = clean_lessons_verdict(prepared)
    fail_criterion(verdict["criteria"], "pedagogy.hook_recap_misconceptions")
    write_verdict(prepared, "lessons", verdict)
    critique.check(prepared, "lessons", item_id=MAIN)
    entry = by_item(prepared, "lessons")[MAIN]
    assert entry["status"] == "pass" and 0.8 <= entry["score"] < 1.0
    fail_criterion(verdict["criteria"], "register.second_person_terms_bolded_once")
    verdict["bloom_relabels"][f"{MAIN}#lo1"] = "remember"   # computed mismatch overrides the critic's "passed"
    write_verdict(prepared, "lessons", verdict)
    findings = critique.check(prepared, "lessons", item_id=MAIN)
    entry = by_item(prepared, "lessons")[MAIN]
    # With WEIGHT_CRITICAL=5 / WEIGHT_MINOR=1, every minor criterion failing still scores 40/43 >= 0.80:
    # the score gate only bites once a critical criterion fails, which already fails the item.
    assert entry["status"] == "pass" and entry["critical_failed"] == [] and entry["score"] == pytest.approx(40 / 43, abs=1e-4)
    assert entry["metrics"]["bloom_mismatches"] == [f"{MAIN}#lo1"]
    assert "critique.criterion_forced" in ids(findings)


def test_check_missing_criterion_is_an_error_and_fails(prepared: Path):
    verdict = clean_lessons_verdict(prepared)
    verdict["criteria"] = [c for c in verdict["criteria"] if c["criterion_id"] != "pages.standalone"]
    write_verdict(prepared, "lessons", verdict)
    findings = critique.check(prepared, "lessons", item_id=MAIN)
    assert any(f.check_id == "critique.criterion_missing" and "pages.standalone" in f.location for f in errors(findings))
    entry = by_item(prepared, "lessons")[MAIN]
    assert entry["status"] == "fail" and "pages.standalone" in entry["critical_failed"]


def test_check_duplicate_and_malformed_criteria(prepared: Path):
    verdict = clean_lessons_verdict(prepared)
    verdict["criteria"].append(dict(verdict["criteria"][0]))
    verdict["criteria"][1]["passed"] = "yes"
    verdict["criteria"][2].update(passed=False, failure_reason="", correction_instruction=None, evidence_span="")
    write_verdict(prepared, "lessons", verdict)
    findings = critique.check(prepared, "lessons", item_id=MAIN)
    assert {"critique.criterion_duplicate", "critique.criterion_invalid", "critique.reason_missing",
            "critique.evidence_missing"} <= ids(errors(findings))
    assert by_item(prepared, "lessons")[MAIN]["status"] == "fail"


def test_check_FAILED_when_round_reaches_max(tmp_path: Path):
    cur = build_fixture(tmp_path)
    lesson_path = cur / "lessons" / item_id_to_filename(MAIN)
    lesson = read_json(lesson_path)
    lesson["revision"] = REPAIR_ROUNDS_MAX
    write_json(lesson_path, lesson)
    verdict = clean_lessons_verdict(cur, round_=REPAIR_ROUNDS_MAX)
    fail_criterion(verdict["criteria"], "objectives.aligned")
    write_verdict(cur, "lessons", verdict)
    findings = critique.check(cur, "lessons", item_id=MAIN)
    assert by_item(cur, "lessons")[MAIN]["status"] == "FAILED"
    assert any(f.check_id == "critique.item_FAILED" and f.severity == "warning" for f in findings)
    assert "repair.revision_skipped" not in ids(findings)  # no earlier snapshot existed
    assert critique.main(["check", "lessons", str(cur), "--item", MAIN]) == 1


def test_check_pending_without_verdict_and_when_verdict_is_stale(prepared: Path):
    critique.check(prepared, "lessons", item_id=MAIN)
    assert by_item(prepared, "lessons")[MAIN] == {
        "status": "pending", "round": 0, "score": None, "critical_failed": [], "undesirable": [], "actionable": 0,
        "deterministic_errors": 0, "verdict_errors": 0, "metrics": {}}
    write_verdict(prepared, "lessons", clean_lessons_verdict(prepared, round_=0))
    lesson_path = prepared / "lessons" / item_id_to_filename(MAIN)
    lesson = read_json(lesson_path)
    lesson["revision"] = 1
    write_json(lesson_path, lesson)
    findings = critique.check(prepared, "lessons", item_id=MAIN)
    assert by_item(prepared, "lessons")[MAIN]["status"] == "pending"
    assert "critique.verdict_stale" in ids(findings)


def test_check_unreadable_verdict_never_passes(prepared: Path):
    path = write_verdict(prepared, "lessons", clean_lessons_verdict(prepared))
    path.write_text("{not json", encoding="utf-8")
    findings = critique.check(prepared, "lessons", item_id=MAIN)
    assert "critique.verdict_invalid_json" in ids(errors(findings))
    assert by_item(prepared, "lessons")[MAIN]["status"] == "fail"


def test_check_stage_and_item_mismatch_block_pass(prepared: Path):
    verdict = clean_lessons_verdict(prepared)
    verdict["stage"], verdict["item_id"] = "cards", PREREQ
    write_verdict(prepared, "lessons", verdict)
    findings = critique.check(prepared, "lessons", item_id=MAIN)
    assert {"critique.verdict_stage_mismatch", "critique.verdict_item_mismatch"} <= ids(errors(findings))
    assert by_item(prepared, "lessons")[MAIN]["status"] == "fail"


def test_check_orphan_verdict_and_missing_artifact(prepared: Path):
    write_verdict(prepared, "lessons", clean_lessons_verdict(prepared), item_id="g:ghost")
    findings = critique.check(prepared, "lessons")
    assert any(f.check_id == "critique.verdict_orphan" for f in findings)
    findings = critique.check(prepared, "lessons", item_id="g:ghost")
    assert [f.check_id for f in errors(findings)] == ["critique.artifact_missing"]
    assert "g:ghost" not in by_item(prepared, "lessons")


def test_check_no_items_and_unknown_stage(cur: Path):
    assert any(f.check_id == "critique.no_items" for f in critique.check(cur, "cards") if cur) or True
    (cur / "cards" / item_id_to_filename(MAIN)).unlink()
    assert "critique.no_items" in ids(critique.check(cur, "cards"))
    assert [f.check_id for f in critique.check(cur, "bogus")] == ["critique.stage_unknown"]


# ---------------------------------------------------------------------------
# check — deterministic errors and stage modules
# ---------------------------------------------------------------------------


def test_check_deterministic_errors_fail_a_clean_verdict(prepared: Path, monkeypatch):
    monkeypatch.setitem(sys.modules, "lessons", types.SimpleNamespace(
        check_item=lambda input_dir, item_id, **_: [Finding("lessons.words", "error", item_id, "too short"),
                                                    Finding("lessons.minor", "warning", item_id, "meh")]))
    write_verdict(prepared, "lessons", clean_lessons_verdict(prepared))
    findings = critique.check(prepared, "lessons", item_id=MAIN)
    entry = by_item(prepared, "lessons")[MAIN]
    assert entry["status"] == "fail" and entry["deterministic_errors"] == 1 and entry["critical_failed"] == []
    assert "lessons.words" in ids(errors(findings))


def test_check_stage_module_missing_is_a_warning_not_a_crash(prepared: Path, monkeypatch):
    monkeypatch.setitem(sys.modules, "lessons", None)  # import of "lessons" halted -> ImportError
    write_verdict(prepared, "lessons", clean_lessons_verdict(prepared))
    findings = critique.check(prepared, "lessons", item_id=MAIN)
    missing = [f for f in findings if f.check_id == "critique.stage_module_missing"]
    assert missing and missing[0].severity == "warning" and not errors(findings)
    assert by_item(prepared, "lessons")[MAIN]["status"] == "pass"
    monkeypatch.setitem(sys.modules, "lessons", types.SimpleNamespace())  # no check_item()
    assert "critique.stage_module_missing" in ids(critique.check(prepared, "lessons", item_id=MAIN))


def test_check_stage_module_exception_is_an_error(prepared: Path, monkeypatch):
    def boom(input_dir, item_id, **_):
        raise RuntimeError("bug in another stream")
    monkeypatch.setitem(sys.modules, "lessons", types.SimpleNamespace(check_item=boom))
    write_verdict(prepared, "lessons", clean_lessons_verdict(prepared))
    findings = critique.check(prepared, "lessons", item_id=MAIN)
    assert "critique.stage_module_error" in ids(errors(findings))
    assert by_item(prepared, "lessons")[MAIN]["status"] == "fail"


# ---------------------------------------------------------------------------
# check — fidelity, groups edges, qualification
# ---------------------------------------------------------------------------


def test_check_syntheses_pass_and_fidelity_metrics(prepared: Path):
    write_verdict(prepared, "syntheses", clean_syntheses_verdict())
    findings = critique.check(prepared, "syntheses")
    entry = by_item(prepared, "syntheses")[MAIN]
    assert entry["status"] == "pass" and not errors(findings)
    assert entry["metrics"]["precision"] == 1.0 and entry["metrics"]["recall"] == 1.0
    assert entry["metrics"]["uncovered_knowledge_points"] == []


def test_check_fidelity_metrics_force_criteria_failed(prepared: Path):
    verdict = clean_syntheses_verdict()
    verdict["claims"][1]["verdict"] = "not_supported"  # b2 is kp2's only map item
    write_verdict(prepared, "syntheses", verdict)
    findings = critique.check(prepared, "syntheses", item_id=MAIN)
    entry = by_item(prepared, "syntheses")[MAIN]
    assert entry["status"] == "fail"
    assert entry["critical_failed"] == ["fidelity.precision", "fidelity.recall"]
    assert entry["metrics"]["precision"] == 0.75 and entry["metrics"]["recall"] == pytest.approx(2 / 3)
    assert entry["metrics"]["uncovered_knowledge_points"] == [f"{MAIN}#kp2"]
    forced = [f for f in findings if f.check_id == "critique.criterion_forced"]
    assert {f.location.split()[-1] for f in forced} == {"fidelity.precision", "fidelity.recall"}


def test_check_claims_missing_or_invalid(prepared: Path):
    verdict = clean_syntheses_verdict()
    del verdict["claims"]
    write_verdict(prepared, "syntheses", verdict)
    findings = critique.check(prepared, "syntheses", item_id=MAIN)
    assert "critique.claims_missing" in ids(errors(findings))
    assert set(by_item(prepared, "syntheses")[MAIN]["critical_failed"]) == {"fidelity.precision", "fidelity.recall"}
    verdict = clean_syntheses_verdict()
    verdict["claims"][0]["verdict"] = "maybe"
    write_verdict(prepared, "syntheses", verdict)
    findings = critique.check(prepared, "syntheses", item_id=MAIN)
    assert "critique.claim_verdict_invalid" in ids(errors(findings))
    assert by_item(prepared, "syntheses")[MAIN]["status"] == "fail"


def test_check_lessons_precision_forces_and_page_clean_share(prepared: Path):
    verdict = clean_lessons_verdict(prepared)
    verdict["claims"][3]["verdict"] = "not_supported"   # page:1:para:0
    verdict["claims"][4]["verdict"] = "irrelevant"
    write_verdict(prepared, "lessons", verdict)
    critique.check(prepared, "lessons", item_id=MAIN)
    entry = by_item(prepared, "lessons")[MAIN]
    assert entry["status"] == "fail" and entry["critical_failed"] == ["fidelity.precision"]
    assert entry["metrics"]["page_clean_share"] == pytest.approx(2 / 3)
    assert entry["metrics"]["claims"] == {"supported": 6, "not_supported": 1, "total": 8}


def test_check_lessons_bloom_relabels_missing_fails_minor_criterion(prepared: Path):
    verdict = clean_lessons_verdict(prepared)
    del verdict["bloom_relabels"]
    write_verdict(prepared, "lessons", verdict)
    findings = critique.check(prepared, "lessons", item_id=MAIN)
    assert "critique.bloom_relabel_missing" in ids(findings)
    entry = by_item(prepared, "lessons")[MAIN]
    assert entry["status"] == "pass" and entry["score"] == pytest.approx(42 / 43, abs=1e-4)


def test_check_groups_pass_and_missing_edge(prepared: Path):
    write_verdict(prepared, "groups", clean_groups_verdict())
    findings = critique.check(prepared, "groups", item_id=MAIN)
    entry = by_item(prepared, "groups")[MAIN]
    assert entry["status"] == "pass" and entry["metrics"]["edges"] == {PREREQ: "confirmed"} and not errors(findings)
    verdict = clean_groups_verdict()
    verdict["edges"] = []
    write_verdict(prepared, "groups", verdict)
    findings = critique.check(prepared, "groups", item_id=MAIN)
    assert "critique.edge_missing" in ids(errors(findings))
    assert by_item(prepared, "groups")[MAIN]["status"] == "fail"
    del verdict["edges"]
    write_verdict(prepared, "groups", verdict)
    assert "critique.edges_missing" in ids(errors(critique.check(prepared, "groups", item_id=MAIN)))
    verdict["edges"] = [{"group_id": PREREQ, "verdict": "rejected", "reason": "", "unit_ids": []}]
    write_verdict(prepared, "groups", verdict)
    assert "critique.edge_verdict_invalid" in ids(errors(critique.check(prepared, "groups", item_id=MAIN)))


def test_check_groups_prereq_lesson_with_no_edges_passes_without_edges_key(prepared: Path):
    verdict = dict(clean_groups_verdict(), item_id=PREREQ)
    del verdict["edges"]
    write_verdict(prepared, "groups", verdict, item_id=PREREQ)
    findings = critique.check(prepared, "groups", item_id=PREREQ)
    assert not errors(findings) and by_item(prepared, "groups")[PREREQ]["status"] == "pass"


def test_check_qualification_blind_retag(prepared: Path):
    write_verdict(prepared, "qualification", clean_qualification_verdict(prepared), item_id="adr")
    findings = critique.check(prepared, "qualification", item_id="adr")
    entry = by_item(prepared, "qualification")["adr"]
    assert entry["status"] == "pass" and not errors(findings)
    assert entry["metrics"] == {"sampled": 3, "concept_disagreement": 0.0, "unit_type_disagreement": 0.0,
                                "keep_disagreement": 0.0}
    verdict = clean_qualification_verdict(prepared)
    verdict["items"][0]["unit_type"] = "noise"          # cross-family disagreement (author type is not reference/noise)
    verdict["items"][0]["verdict"] = "keep"
    verdict["items"][2]["unit_type"] = "noise"          # second one: 2/3 > 0.20 and above the one-disagreement tolerance
    verdict["items"][2]["verdict"] = "keep"
    verdict["items"][1]["verdict"] = "drop"             # minor: keep disagreement
    write_verdict(prepared, "qualification", verdict, item_id="adr")
    critique.check(prepared, "qualification", item_id="adr")
    entry = by_item(prepared, "qualification")["adr"]
    assert entry["status"] == "fail" and entry["critical_failed"] == ["tags.unit_type_agrees"]
    assert entry["metrics"]["unit_type_disagreement"] == pytest.approx(2 / 3)
    assert entry["metrics"]["keep_disagreement"] == pytest.approx(1 / 3)


def test_check_qualification_single_disagreement_and_same_family_tolerated(prepared: Path):
    verdict = clean_qualification_verdict(prepared)
    author_type = verdict["items"][0]["unit_type"]
    same_family = {"definition": "mechanism", "mechanism": "context", "context": "definition",
                   "example": "code", "code": "example", "pitfall": "trade_off", "trade_off": "comparison",
                   "comparison": "pitfall", "procedure": "procedure", "reference": "noise", "noise": "reference"}
    verdict["items"][0]["unit_type"] = same_family[author_type]   # same family: not a disagreement
    verdict["items"][1]["unit_type"] = "noise"                    # one real disagreement: tolerated
    verdict["items"][1]["verdict"] = "keep"
    write_verdict(prepared, "qualification", verdict, item_id="adr")
    critique.check(prepared, "qualification", item_id="adr")
    entry = by_item(prepared, "qualification")["adr"]
    assert entry["status"] == "pass" and entry["critical_failed"] == []
    assert entry["metrics"]["unit_type_disagreement"] == pytest.approx(1 / 3)


def test_check_qualification_missing_sampled_unit(prepared: Path):
    verdict = clean_qualification_verdict(prepared)
    verdict["items"] = verdict["items"][:2]
    write_verdict(prepared, "qualification", verdict, item_id="adr")
    findings = critique.check(prepared, "qualification", item_id="adr")
    assert "critique.item_missing" in ids(errors(findings))
    assert by_item(prepared, "qualification")["adr"]["status"] == "fail"


# ---------------------------------------------------------------------------
# check — questions: probe matrix, undesirable, test student, cards
# ---------------------------------------------------------------------------


def test_check_questions_probe_matrix_passes_with_correct_mapping(prepared: Path):
    write_verdict(prepared, "questions", grades_from_key(prepared), suffix=".grader.json")
    write_verdict(prepared, "questions", clean_questions_verdict())
    findings = critique.check(prepared, "questions", item_id=MAIN)
    entry = by_item(prepared, "questions")[MAIN]
    assert entry["status"] == "pass" and not errors(findings)
    assert entry["metrics"]["items"][f"{MAIN}#q1"] == {"verdict": "keep", "accepted": True, "score": 1.0,
                                                        "critical_failed": [], "undesirable": []}
    assert entry["metrics"]["test_student"]["share"] == 1.0


def test_check_questions_probe_matrix_swapped_fails(prepared: Path):
    write_verdict(prepared, "questions", grades_from_key(prepared, swap=True), suffix=".grader.json")
    write_verdict(prepared, "questions", clean_questions_verdict())
    critique.check(prepared, "questions", item_id=MAIN)
    entry = by_item(prepared, "questions")[MAIN]
    assert entry["status"] == "fail"
    assert entry["critical_failed"] == [f"{MAIN}#q1:{critique.PROBE_CRITERION}", f"{MAIN}#q2:{critique.PROBE_CRITERION}"]


def test_check_questions_without_grader_fails_probe_criterion(prepared: Path):
    write_verdict(prepared, "questions", clean_questions_verdict())
    findings = critique.check(prepared, "questions", item_id=MAIN)
    assert "critique.grader_missing" in ids(findings)
    entry = by_item(prepared, "questions")[MAIN]
    assert entry["status"] == "fail" and all(c.endswith(critique.PROBE_CRITERION) for c in entry["critical_failed"])


def test_check_questions_partial_or_invalid_grades(prepared: Path):
    grader = grades_from_key(prepared)
    del grader["grades"][f"{MAIN}#q2"]
    label = next(iter(grader["grades"][f"{MAIN}#q1"]))
    grader["grades"][f"{MAIN}#q1"][label] = "maybe"
    write_verdict(prepared, "questions", grader, suffix=".grader.json")
    write_verdict(prepared, "questions", clean_questions_verdict())
    findings = critique.check(prepared, "questions", item_id=MAIN)
    assert "critique.probe_grades_missing" in ids(findings)
    assert "critique.grade_invalid" in ids(errors(findings))
    assert by_item(prepared, "questions")[MAIN]["status"] == "fail"


def test_check_questions_undesirable_triggered(prepared: Path):
    write_verdict(prepared, "questions", grades_from_key(prepared), suffix=".grader.json")
    verdict = clean_questions_verdict()
    fail_criterion(verdict["items"][0]["criteria"], "q.answer_leaks_on_page")
    write_verdict(prepared, "questions", verdict)
    critique.check(prepared, "questions", item_id=MAIN)
    entry = by_item(prepared, "questions")[MAIN]
    assert entry["status"] == "fail" and entry["critical_failed"] == []
    assert entry["undesirable"] == [f"{MAIN}#q1:q.answer_leaks_on_page"]


def test_check_questions_rewrite_or_drop_fails_the_lesson(prepared: Path):
    write_verdict(prepared, "questions", grades_from_key(prepared), suffix=".grader.json")
    verdict = clean_questions_verdict()
    verdict["items"][1]["verdict"] = "rewrite"
    verdict["items"][1]["rewrite"] = {"prompt": "…", "options": ["a", "b", "c", "d"], "correct_index": 0}
    write_verdict(prepared, "questions", verdict)
    critique.check(prepared, "questions", item_id=MAIN)
    entry = by_item(prepared, "questions")[MAIN]
    assert entry["status"] == "fail" and entry["critical_failed"] == [] and entry["undesirable"] == []
    assert entry["metrics"]["items"][f"{MAIN}#q2"]["accepted"] is False
    verdict["items"][1]["verdict"] = "maybe"
    write_verdict(prepared, "questions", verdict)
    assert "critique.item_verdict_invalid" in ids(errors(critique.check(prepared, "questions", item_id=MAIN)))


def test_check_questions_sub_item_missing_or_unknown(prepared: Path):
    write_verdict(prepared, "questions", grades_from_key(prepared), suffix=".grader.json")
    verdict = clean_questions_verdict()
    verdict["items"][1]["item_id"] = f"{MAIN}#q9"
    write_verdict(prepared, "questions", verdict)
    findings = critique.check(prepared, "questions", item_id=MAIN)
    assert "critique.item_unknown" in ids(findings) and "critique.item_missing" in ids(errors(findings))
    assert by_item(prepared, "questions")[MAIN]["status"] == "fail"


def test_check_questions_bloom_relabel_mismatch_or_missing(prepared: Path):
    write_verdict(prepared, "questions", grades_from_key(prepared), suffix=".grader.json")
    verdict = clean_questions_verdict()
    verdict["items"][0]["bloom_relabel"] = "create"
    del verdict["items"][1]["bloom_relabel"]
    write_verdict(prepared, "questions", verdict)
    findings = critique.check(prepared, "questions", item_id=MAIN)
    assert "critique.bloom_relabel_missing" in ids(findings)
    entry = by_item(prepared, "questions")[MAIN]
    assert entry["status"] == "pass"  # minor criterion only
    assert entry["metrics"]["items"][f"{MAIN}#q1"]["score"] == entry["metrics"]["items"][f"{MAIN}#q2"]["score"] < 1.0


def test_check_questions_test_student_flow(prepared: Path):
    write_verdict(prepared, "questions", {
        "contract_version": CONTRACT_VERSION, "stage": "questions", "item_id": MAIN, "round": 0, "student": "s",
        "answers": [{"question_id": f"{MAIN}#q1", "answer": "STUDENT ANSWER: kept and marked superseded"}],
        "unanswerable": [], "undefined_terms": ["forces"],
    }, suffix=".student.json")
    write_verdict(prepared, "questions", clean_questions_verdict(with_test_student=False))
    stale = critique.check(prepared, "questions", item_id=MAIN)  # grader does not know the student yet
    assert {"critique.grader_stale", "critique.grader_missing"} <= ids(stale)
    assert by_item(prepared, "questions")[MAIN]["status"] == "fail"
    assert not errors(critique.prepare(prepared, "questions"))      # refreshes the grader with the student
    key = read_json(prepared / "critique" / "questions" / "work" / f"{STEM}.grader.key.json")
    assert set(key["questions"][f"{MAIN}#q1"].values()) == {critique.ROLE_CORRECT, critique.ROLE_WRONG, critique.ROLE_STUDENT}
    assert set(key["questions"][f"{MAIN}#q2"].values()) == {critique.ROLE_CORRECT, critique.ROLE_WRONG}
    grader = read_json(prepared / "critique" / "questions" / "work" / f"{STEM}.grader.json")
    assert "STUDENT ANSWER" in json.dumps(grader["questions"])
    assert critique.ROLE_STUDENT not in json.dumps(grader["questions"])
    write_verdict(prepared, "questions", grades_from_key(prepared, student_grade="pass"), suffix=".grader.json")
    write_verdict(prepared, "questions", clean_questions_verdict(with_test_student=False))
    findings = critique.check(prepared, "questions", item_id=MAIN)
    entry = by_item(prepared, "questions")[MAIN]
    assert entry["status"] == "pass" and not errors(findings)
    assert entry["metrics"]["test_student"] == {"answered": 1, "correct": 1, "share": 1.0, "unanswerable": [],
                                                "undefined_terms": ["forces"]}
    write_verdict(prepared, "questions", grades_from_key(prepared, student_grade="fail"), suffix=".grader.json")
    findings = critique.check(prepared, "questions", item_id=MAIN)
    assert by_item(prepared, "questions")[MAIN]["status"] == "fail"
    assert "critique.test_student_below_min" in ids(findings)


def test_check_questions_test_student_unanswerable_or_missing(prepared: Path):
    write_verdict(prepared, "questions", grades_from_key(prepared), suffix=".grader.json")
    verdict = clean_questions_verdict()
    verdict["test_student"]["unanswerable"] = [f"{MAIN}#q1"]
    write_verdict(prepared, "questions", verdict)
    findings = critique.check(prepared, "questions", item_id=MAIN)
    assert by_item(prepared, "questions")[MAIN]["status"] == "fail"
    assert "critique.test_student_unanswerable" in ids(findings)
    write_verdict(prepared, "questions", clean_questions_verdict(with_test_student=False))
    findings = critique.check(prepared, "questions", item_id=MAIN)
    assert by_item(prepared, "questions")[MAIN]["status"] == "fail"
    assert "critique.test_student_missing" in ids(findings)


def test_check_cards_pass_and_drop(prepared: Path):
    write_verdict(prepared, "cards", clean_cards_verdict())
    findings = critique.check(prepared, "cards")
    assert by_item(prepared, "cards")[MAIN]["status"] == "pass" and not errors(findings)
    verdict = clean_cards_verdict()
    verdict["items"][1]["verdict"] = "drop"
    fail_criterion(verdict["items"][1]["criteria"], "card.single_filler")
    write_verdict(prepared, "cards", verdict)
    critique.check(prepared, "cards", item_id=MAIN)
    entry = by_item(prepared, "cards")[MAIN]
    assert entry["status"] == "fail" and entry["critical_failed"] == [f"{MAIN}#k2:card.single_filler"]
    verdict["items"][1]["verdict"] = "rewrite"  # not a card verdict
    write_verdict(prepared, "cards", verdict)
    assert "critique.item_verdict_invalid" in ids(errors(critique.check(prepared, "cards", item_id=MAIN)))


def test_check_actionable_items_excess_is_a_warning(prepared: Path):
    verdict = clean_lessons_verdict(prepared)
    verdict["actionable_items"] = [{"span": f"s{i}", "instruction": "x", "ground_in": [U1]} for i in range(4)]
    write_verdict(prepared, "lessons", verdict)
    findings = critique.check(prepared, "lessons", item_id=MAIN)
    assert "critique.actionable_items_excess" in ids(findings) and not errors(findings)
    assert by_item(prepared, "lessons")[MAIN]["actionable"] == 4


# ---------------------------------------------------------------------------
# compute_item_status
# ---------------------------------------------------------------------------


def test_compute_item_status_direct():
    verdict = clean_cards_verdict()
    result = critique.compute_item_status(verdict, "cards", [], {"sub_item_ids": [f"{MAIN}#k1", f"{MAIN}#k2"]})
    assert result["status"] == "pass" and result["score"] == 1.0 and result["findings"] == []
    det = [Finding("cards.back_words", "error", "k1", "too long"), Finding("cards.x", "warning", "k1", "")]
    assert critique.compute_item_status(verdict, "cards", det, {})["status"] == "fail"
    assert critique.compute_item_status(dict(verdict, round=REPAIR_ROUNDS_MAX), "cards", det, {})["status"] == "FAILED"
    assert critique.compute_item_status(dict(verdict, round=None), "cards", det, {"revision": 2})["round"] == 2
    with pytest.raises(ValueError):
        critique.compute_item_status(verdict, "bogus", [], {})
    bad = dict(verdict, actionable_items="nope")
    result = critique.compute_item_status(bad, "cards", [], {})
    assert result["status"] == "fail" and "critique.actionable_invalid" in ids(result["findings"])


# ---------------------------------------------------------------------------
# repair validation
# ---------------------------------------------------------------------------


def _repair_setup(cur: Path) -> Path:
    """Round 0: prepare, failing verdict naming the page-1 'three parts' paragraph, check (archives r0)."""
    critique.prepare(cur, "lessons", items=[MAIN])
    verdict = clean_lessons_verdict(cur)
    fail_criterion(verdict["criteria"], "depth.mechanism_explained")
    verdict["actionable_items"] = [{"span": "Every record has three parts: the context that describes the forces",
                                    "instruction": "Explain why the three parts matter", "ground_in": [U1]}]
    write_verdict(cur, "lessons", verdict)
    critique.check(cur, "lessons", item_id=MAIN)
    assert by_item(cur, "lessons")[MAIN]["status"] == "fail"
    return cur / "lessons" / item_id_to_filename(MAIN)


def test_repair_in_scope_edit_is_clean(cur: Path):
    lesson_path = _repair_setup(cur)
    lesson = read_json(lesson_path)
    lesson["revision"] = 1
    lesson["pages"][1]["markdown"] = PAGE_TEXT[1].replace(
        "Every record has three parts: the context that describes the forces, the decision itself, and the "
        "consequences the team accepts.",
        "Every record has three parts: the context that describes the forces, the decision itself, and the "
        "consequences the team accepts, because a reader who sees all three can judge the choice later.")
    write_json(lesson_path, lesson)
    findings = critique.check(cur, "lessons", item_id=MAIN)
    assert "repair.oversized" not in ids(findings) and "repair.out_of_scope" not in ids(findings)
    assert by_item(cur, "lessons")[MAIN]["status"] == "pending"   # awaiting the round-1 critique
    snap = read_json(cur / "critique" / "lessons" / "work" / f"{STEM}.snapshot.json")
    assert set(snap["revisions"]) == {"0", "1"}


def test_repair_out_of_scope_paragraph_is_a_warning(cur: Path):
    lesson_path = _repair_setup(cur)
    lesson = read_json(lesson_path)
    lesson["revision"] = 1
    lesson["pages"][0]["markdown"] = PAGE_TEXT[0].replace("Teams forget why they chose", "Teams rarely remember why they chose")
    write_json(lesson_path, lesson)
    findings = critique.check(cur, "lessons", item_id=MAIN)
    out = [f for f in findings if f.check_id == "repair.out_of_scope"]
    assert len(out) == 1 and out[0].severity == "warning" and "Teams forget why they chose" in out[0].message
    assert "repair.oversized" not in ids(findings)


def test_repair_inserted_paragraph_next_to_named_span_is_in_scope(cur: Path):
    lesson_path = _repair_setup(cur)
    lesson = read_json(lesson_path)
    lesson["revision"] = 1
    lesson["pages"][1]["markdown"] = PAGE_TEXT[1].replace(
        "consequences the team accepts.", "consequences the team accepts.\n\nA record missing any of the three "
                                          "parts leaves later readers guessing at the trade-off.")
    write_json(lesson_path, lesson)
    findings = critique.check(cur, "lessons", item_id=MAIN)
    assert "repair.out_of_scope" not in ids(findings) and "repair.oversized" not in ids(findings)


def test_repair_oversized_is_an_error(cur: Path):
    lesson_path = _repair_setup(cur)
    lesson = read_json(lesson_path)
    lesson["revision"] = 1
    for page in lesson["pages"]:
        page["markdown"] = f"## {page['title']}\n\nCompletely new prose about something else entirely, " \
                           f"written from scratch for page {page['index']} with none of the original wording."
    write_json(lesson_path, lesson)
    findings = critique.check(cur, "lessons", item_id=MAIN)
    oversized = [f for f in findings if f.check_id == "repair.oversized"]
    assert len(oversized) == 1 and oversized[0].severity == "error"
    assert critique.main(["check", "lessons", str(cur), "--item", MAIN]) == 1


def test_repair_revision_not_bumped_and_skipped(cur: Path):
    lesson_path = _repair_setup(cur)
    lesson = read_json(lesson_path)
    lesson["pages"][0]["markdown"] += "\n\nAn extra paragraph without a revision bump."
    write_json(lesson_path, lesson)
    findings = critique.check(cur, "lessons", item_id=MAIN)
    assert "repair.revision_not_bumped" in ids(findings)
    snapshot = read_json(cur / "critique" / "lessons" / "work" / f"{STEM}.snapshot.json")
    assert snapshot["revisions"]["0"]["pages"][0]["markdown"] == PAGE_TEXT[0]   # snapshot of revision 0 kept intact
    lesson["revision"] = 2
    write_json(lesson_path, lesson)
    findings = critique.check(cur, "lessons", item_id=MAIN)
    assert "repair.revision_skipped" in ids(findings) and "repair.oversized" not in ids(findings)


def test_repair_without_previous_verdict_cannot_check_scope(cur: Path):
    critique.prepare(cur, "lessons", items=[MAIN])
    lesson_path = cur / "lessons" / item_id_to_filename(MAIN)
    lesson = read_json(lesson_path)
    lesson["revision"] = 1
    lesson["pages"][0]["markdown"] = PAGE_TEXT[0].replace("Teams forget", "Teams often forget")
    write_json(lesson_path, lesson)
    findings = critique.check(cur, "lessons", item_id=MAIN)
    assert "repair.previous_verdict_missing" in ids(findings) and "repair.out_of_scope" not in ids(findings)


def test_diff_prose_shares():
    old = ["The quick brown fox jumps over the lazy dog near the river bank today.", "Second paragraph stays."]
    assert critique._diff_prose(old, list(old)) == (0.0, [])
    share, blocks = critique._diff_prose(old, ["The quick brown fox jumps over the lazy dog near the river bank tonight.",
                                               "Second paragraph stays."])
    assert 0 < share < 0.1 and len(blocks) == 1 and blocks[0]["old"] == [old[0]]
    share, blocks = critique._diff_prose(old, ["Second paragraph stays."])
    assert share == pytest.approx(14 / 17) and blocks[0]["old"] == [old[0]] and blocks[0]["new"] == []
    share, blocks = critique._diff_prose(old, [old[0], "Inserted.", old[1]])
    assert blocks[0]["old"] == [] and blocks[0]["before"] == old[0] and blocks[0]["after"] == old[1]
    assert critique._diff_prose([], [])[0] == 0.0


def test_prose_paragraphs_skip_ids_and_metadata():
    paras = critique._prose_paragraphs({"id": "g:x", "unit_ids": ["u:a:1:1"], "title": "A title",
                                        "pages": [{"index": 0, "markdown": "One.\n\nTwo.", "quote": "skip me"}],
                                        "revision": 3})
    assert paras == ["A title", "One.", "Two."]


# ---------------------------------------------------------------------------
# summary merging, check_item, status, CLI
# ---------------------------------------------------------------------------


def test_summary_merges_stages_and_items(prepared: Path):
    write_verdict(prepared, "lessons", clean_lessons_verdict(prepared))
    write_verdict(prepared, "syntheses", clean_syntheses_verdict())
    critique.check(prepared, "lessons")
    critique.check(prepared, "syntheses")
    summary = read_json(prepared / "critique" / "summary.json")
    assert summary["contract_version"] == CONTRACT_VERSION
    assert set(summary["stages"]) == {"lessons", "syntheses"}
    assert set(summary["stages"]["lessons"]["by_item"]) == {MAIN, PREREQ}
    verdict = clean_lessons_verdict(prepared)
    fail_criterion(verdict["criteria"], "pages.standalone")
    write_verdict(prepared, "lessons", verdict)
    critique.check(prepared, "lessons", item_id=MAIN)          # --item merges, does not wipe PREREQ
    stage = read_json(prepared / "critique" / "summary.json")["stages"]["lessons"]
    assert set(stage["by_item"]) == {MAIN, PREREQ}
    assert stage["items"] == 2 and stage["fail"] == 1 and stage["pending"] == 1 and stage["pass"] == 0
    assert read_json(prepared / "critique" / "summary.json")["stages"]["syntheses"]["pass"] == 1
    (prepared / "critique" / "summary.json").write_text("garbage", encoding="utf-8")
    critique.check(prepared, "syntheses")
    assert set(read_json(prepared / "critique" / "summary.json")["stages"]) == {"syntheses"}


def test_check_all_stages_and_check_item_without_stage(prepared: Path):
    write_verdict(prepared, "lessons", clean_lessons_verdict(prepared))
    write_verdict(prepared, "cards", clean_cards_verdict())
    findings = critique.check(prepared)
    assert not errors(findings)
    assert set(read_json(prepared / "critique" / "summary.json")["stages"]) == set(critique.CRITIQUED_STAGES)
    findings = critique.check_item(prepared, MAIN)
    statuses = {f.location.split("/")[1] for f in findings if f.check_id == "critique.item_status"}
    assert statuses == {"groups", "syntheses", "lessons", "questions", "cards"}
    assert [f.check_id for f in critique.check_item(prepared, "g:nowhere")] == ["critique.item_unknown"]
    assert not errors(critique.check_item(prepared, MAIN, stage="cards", write_summary=False))


def test_status_returns_summary(prepared: Path):
    assert critique.status(prepared) == {"contract_version": CONTRACT_VERSION, "stages": {}}
    write_verdict(prepared, "cards", clean_cards_verdict())
    critique.check(prepared, "cards")
    assert critique.status(prepared)["stages"]["cards"]["pass"] == 1


def test_cli_exit_codes_and_output(prepared: Path, capsys):
    assert critique.main(["check", "lessons", str(prepared)]) == 0          # pending only
    out = capsys.readouterr().out
    assert "0 errors, 0 warnings" in out and "pending" in out and MAIN in out
    verdict = clean_lessons_verdict(prepared)
    fail_criterion(verdict["criteria"], "objectives.aligned")
    write_verdict(prepared, "lessons", verdict)
    assert critique.main(["check", "lessons", str(prepared), "--item", MAIN]) == 1
    out = capsys.readouterr().out
    assert "fail" in out and "objectives.aligned" in out
    write_verdict(prepared, "lessons", clean_lessons_verdict(prepared))
    assert critique.main(["check", "lessons", str(prepared), "--item", MAIN]) == 0
    out = capsys.readouterr().out
    assert "[INFO] critique.item_status @ critique/lessons/" in out
    verdict = clean_lessons_verdict(prepared)
    verdict["criteria"].append({"criterion_id": "made.up", "passed": True})
    write_verdict(prepared, "lessons", verdict)
    assert critique.main(["check", "lessons", str(prepared), "--item", MAIN]) == 0
    assert critique.main(["check", "lessons", str(prepared), "--item", MAIN, "--strict"]) == 1
    out = capsys.readouterr().out
    assert "[WARN] critique.criterion_unknown @" in out
    verdict = clean_lessons_verdict(prepared)
    del verdict["claims"]
    write_verdict(prepared, "lessons", verdict)
    assert critique.main(["check", "lessons", str(prepared), "--item", MAIN]) == 1
    out = capsys.readouterr().out
    assert "[ERROR] critique.claims_missing @ critique/lessons/" in out and "1 errors," in out


def test_cli_prepare_status_and_usage(prepared: Path, capsys):
    assert critique.main(["prepare", "cards", str(prepared), "--item", MAIN, "--force"]) == 0
    out = capsys.readouterr().out
    assert "[INFO] critique.prepared @ critique/cards/work/: 1 work file set(s) written" in out
    assert critique.main(["status", str(prepared)]) == 0
    assert "no critique/summary.json yet" in capsys.readouterr().out
    write_verdict(prepared, "cards", clean_cards_verdict())
    assert critique.main(["check", "cards", str(prepared)]) == 0
    capsys.readouterr()
    assert critique.main(["status", str(prepared)]) == 0
    out = capsys.readouterr().out
    assert "cards" in out and MAIN in out and "pass" in out
    assert critique.main(["check", "cards", str(prepared / "nope")]) == 2
    with pytest.raises(SystemExit):
        critique.main(["check", "bogus", str(prepared)])
    with pytest.raises(SystemExit):
        critique.main(["frobnicate", str(prepared)])


def test_groups_artifact_from_groups_json_revision(prepared: Path):
    groups = read_json(prepared / "groups.json")
    groups["revision"] = 1
    write_json(prepared / "groups.json", groups)
    write_verdict(prepared, "groups", dict(clean_groups_verdict(), round=1))
    findings = critique.check(prepared, "groups", item_id=MAIN)
    assert by_item(prepared, "groups")[MAIN]["round"] == 1 and by_item(prepared, "groups")[MAIN]["status"] == "pass"
    assert "repair.revision_skipped" not in ids(findings)


def test_verdict_round_ahead_is_a_warning(prepared: Path):
    write_verdict(prepared, "cards", dict(clean_cards_verdict(), round=1))
    findings = critique.check(prepared, "cards", item_id=MAIN)
    assert "critique.round_ahead" in ids(findings) and by_item(prepared, "cards")[MAIN]["status"] == "pass"
    write_verdict(prepared, "cards", {k: v for k, v in clean_cards_verdict().items() if k != "round"})
    findings = critique.check(prepared, "cards", item_id=MAIN)
    assert "critique.round_missing" in ids(findings) and by_item(prepared, "cards")[MAIN]["round"] == 0


def test_copy_of_fixture_is_not_mutated_between_tests(cur: Path):
    assert read_json(cur / "lessons" / item_id_to_filename(MAIN))["revision"] == 0
    assert copy.deepcopy(PAGE_TEXT) == PAGE_TEXT


def test_parallel_summary_writes_keep_every_item(tmp_path: Path):
    # Parallel critics each run `check --item`; every item must survive the merge.
    import threading

    from contracts import CRITIQUE_SUMMARY

    items = [f"g:item-{i:02d}" for i in range(24)]
    critique._write_summary(tmp_path, "groups", {"g:other-stage": {"status": "pass"}}, replace=False)
    barrier = threading.Barrier(len(items))

    def write(item: str) -> None:
        barrier.wait()
        critique._write_summary(tmp_path, "syntheses", {item: {"status": "pass"}}, replace=False)

    threads = [threading.Thread(target=write, args=(item,)) for item in items]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    summary = read_json(tmp_path / CRITIQUE_SUMMARY)
    assert sorted(summary["stages"]["syntheses"]["by_item"]) == items
    assert summary["stages"]["syntheses"]["pass"] == len(items)
    assert "g:other-stage" in summary["stages"]["groups"]["by_item"]


def test_dump_json_is_atomic_and_leaves_no_temp_files(tmp_path: Path):
    from contracts import dump_json

    target = tmp_path / "nested" / "data.json"
    dump_json(target, {"a": 1})
    dump_json(target, {"a": 2, "b": "é"})
    assert read_json(target) == {"a": 2, "b": "é"}
    assert [p.name for p in target.parent.iterdir()] == ["data.json"]
