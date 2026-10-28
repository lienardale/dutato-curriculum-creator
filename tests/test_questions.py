"""Tests for questions.py (Stage 6 — design questions).

A synthetic three-lesson curriculum (units, concepts, qualification, groups,
syntheses, lessons) is built in tmp_path by ``build_fixture``; ``clean_questions``
returns an authored file that passes every gate with zero findings. Each gate
test plants one defect and asserts the check_id, so the clean baseline doubles as
a regression guard against false positives. test_cards.py reuses the fixture.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

import _compat  # noqa: F401

import questions
from contracts import CONTRACT_VERSION, Finding, item_id_to_filename

MAIN = "g:architecture-decision-records"
PREREQ = "g:what-architects-do"
SIBLING = "g:decision-context-forces"

UNITS = {
    "adr": [
        ("u:adr:0:1", "The ADR template",
         "An architecture decision record (ADR) is a short document that records one decision with its "
         "context, decision, and consequences. It is kept alongside the code so that the reasoning survives "
         "team changes and new readers can see the forces that shaped each choice."),
        ("u:adr:0:2", "Lifecycle",
         "An ADR is proposed while under discussion, accepted once the decision is made, and marked superseded "
         "when a newer record replaces it. Superseded records are never deleted, because the history of "
         "reasoning is what later readers need."),
        ("u:adr:1:1", "Context",
         "The context section lists the forces in play: constraints, quality goals, and the team's situation at "
         "the time. Without the context, a later reader sees only an arbitrary choice and is tempted to "
         "relitigate it."),
    ],
    "arch": [
        ("u:arch:0:1", "Accountability",
         "An architect remains accountable for the outcome of every decision, including the ones delegated to "
         "the team. Delegating a decision does not delegate the responsibility for its consequences."),
    ],
}

FILLER = "The team revisits this point during its regular design review and notes what changed. "


def _pad(text: str, min_words: int) -> str:
    while len(text.split()) < min_words:
        text += " " + FILLER.strip()
    return text


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def build_fixture(root: Path) -> Path:
    """Lesson-pipeline curriculum: one prerequisite lesson, one main lesson, one sibling."""
    cur = root / "cur"
    write_json(cur / "manifest.json", {"name": "Fixture", "domain": "fixture", "pipeline": "lesson",
                                       "sources": [{"type": "web", "path": "adr"}]})
    for source, units in UNITS.items():
        write_json(cur / "units" / f"{source}.json", {
            "contract_version": CONTRACT_VERSION, "source": source, "source_file": f"{source}.json",
            "source_type": "web", "title": source, "scope_applied": False,
            "units": [{"id": uid, "section_index": int(uid.split(":")[2]), "section_title": title,
                       "heading_path": [title], "block_type": "prose", "text": text,
                       "token_count": 60, "raw_span": [0, len(text)], "page": None, "images": [],
                       "cleaning": {}} for uid, title, text in units],
        })
    write_json(cur / "units" / "index.json", {"contract_version": CONTRACT_VERSION, "sources": {}, "total_units": 4,
                                               "total_tokens": 240, "scope": None})
    write_json(cur / "concepts.json", {
        "contract_version": CONTRACT_VERSION,
        "concepts": [
            {"id": "c:adr", "label": "Architecture Decision Record", "aliases": ["ADR", "decision record"],
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
            "u:adr:0:1": {"concepts": [{"concept_id": "c:adr", "quote": "records one decision"}], "unit_type": "definition",
                          "knowledge_level": "concept", "keep": True},
            "u:adr:0:2": {"concepts": [{"concept_id": "c:adr", "quote": "marked superseded"}], "unit_type": "procedure",
                          "knowledge_level": "procedure", "keep": True},
            "u:adr:1:1": {"concepts": [{"concept_id": "c:decision-context", "quote": "forces in play"}],
                          "unit_type": "mechanism", "knowledge_level": "principle", "keep": True},
        },
        "gleaning": {},
    })
    write_json(cur / "qualification" / "arch.json", {
        "contract_version": CONTRACT_VERSION, "source": "arch",
        "units": {"u:arch:0:1": {"concepts": [{"concept_id": "c:architect-accountability", "quote": "remains accountable"}],
                                 "unit_type": "definition", "knowledge_level": "principle", "keep": True}},
        "gleaning": {},
    })

    main_objectives = [
        {"id": f"{MAIN}#lo1", "text": "Write an ADR for a given decision with context, decision and consequences",
         "bloom_level": "apply", "concept_ids": ["c:adr"], "unit_ids": ["u:adr:0:1"], "knowledge_point_ids": [f"{MAIN}#kp2"]},
        {"id": f"{MAIN}#lo2", "text": "Explain how the context section lets a later team revisit a decision without relitigating it",
         "bloom_level": "understand", "concept_ids": ["c:decision-context"], "unit_ids": ["u:adr:1:1"],
         "knowledge_point_ids": [f"{MAIN}#kp1"]},
    ]
    prereq_objectives = [
        {"id": f"{PREREQ}#lo1", "text": "Explain what an architect stays accountable for after delegating",
         "bloom_level": "understand", "concept_ids": ["c:architect-accountability"], "unit_ids": ["u:arch:0:1"],
         "knowledge_point_ids": [f"{PREREQ}#kp1"]},
    ]
    sibling_objectives = [
        {"id": f"{SIBLING}#lo1", "text": "Identify the forces a context section must list", "bloom_level": "understand",
         "concept_ids": ["c:decision-context"], "unit_ids": ["u:adr:1:1"], "knowledge_point_ids": [f"{SIBLING}#kp1"]},
    ]
    write_json(cur / "groups.json", {
        "contract_version": CONTRACT_VERSION,
        "areas": [{"id": "a:architecture-leadership", "title": "Architecture Leadership", "sort_order": 0, "suggested_level": 2,
                   "modules": [{"id": "m:role", "title": "The Architect's Role", "sort_order": 0},
                               {"id": "m:decision-records", "title": "Recording Decisions", "sort_order": 1}]}],
        "groups": [
            {"id": PREREQ, "title": "What Architects Do", "area_id": "a:architecture-leadership", "module_id": "m:role",
             "sort_order": 0, "concept_ids": ["c:architect-accountability"], "unit_ids": ["u:arch:0:1"], "related_unit_ids": [],
             "learning_step": "foundation", "suggested_level": 1,
             "generalization": "An architect owns the outcome of every decision, even delegated ones.",
             "knowledge_points": [{"id": f"{PREREQ}#kp1", "title": "Accountability survives delegation", "unit_ids": ["u:arch:0:1"]}],
             "objectives": prereq_objectives, "watch": [], "duplicates": [], "conflicts": [], "prerequisites": [],
             "consequential": False, "rationale": "", "budget": None},
            {"id": MAIN, "title": "Architecture Decision Records", "area_id": "a:architecture-leadership",
             "module_id": "m:decision-records", "sort_order": 1, "concept_ids": ["c:adr", "c:decision-context"],
             "unit_ids": ["u:adr:0:1", "u:adr:0:2"], "related_unit_ids": ["u:adr:1:1"],
             "learning_step": "applied", "suggested_level": 2,
             "generalization": "Recording the context and consequences of each decision lets a team revisit choices without relitigating them.",
             "knowledge_points": [
                 {"id": f"{MAIN}#kp1", "title": "Why decisions get lost", "unit_ids": ["u:adr:1:1"]},
                 {"id": f"{MAIN}#kp2", "title": "The ADR template", "unit_ids": ["u:adr:0:1"]},
                 {"id": f"{MAIN}#kp3", "title": "Lifecycle: proposed, accepted, superseded", "unit_ids": ["u:adr:0:2"]},
             ],
             "objectives": main_objectives,
             "watch": [
                 {"id": f"{MAIN}#w1", "kind": "misconception", "text": "A reversed decision's ADR should be deleted", "unit_ids": ["u:adr:0:2"]},
                 {"id": f"{MAIN}#w2", "kind": "boundary", "text": "An ADR is not a design document", "unit_ids": ["u:adr:0:1"]},
             ],
             "duplicates": [], "conflicts": [],
             "prerequisites": [{"group_id": PREREQ, "strength": "recommended", "evidence_unit_ids": ["u:arch:0:1"],
                                "reason": "assumes accountability", "status": "committed"}],
             "consequential": False, "rationale": "",
             "budget": {"pages": 4, "embedded_questions_min": 1, "embedded_questions_max": 6, "quiz_items": 4,
                        "quiz_min_bloom_levels": 3, "recap_item": True, "cards_min": 4, "cards_max": 5, "exercises_max": 3}},
            {"id": SIBLING, "title": "Decision Context Forces", "area_id": "a:architecture-leadership",
             "module_id": "m:decision-records", "sort_order": 2, "concept_ids": ["c:decision-context"],
             "unit_ids": ["u:adr:1:1"], "related_unit_ids": [], "learning_step": "applied", "suggested_level": 2,
             "generalization": "Naming the forces behind a decision makes the decision reviewable.",
             "knowledge_points": [{"id": f"{SIBLING}#kp1", "title": "Forces", "unit_ids": ["u:adr:1:1"]}],
             "objectives": sibling_objectives, "watch": [], "duplicates": [], "conflicts": [],
             "prerequisites": [{"group_id": MAIN, "strength": "required", "evidence_unit_ids": [], "reason": "", "status": "rejected"}],
             "consequential": False, "rationale": "", "budget": None},
        ],
        "dropped": [],
    })

    pages = [
        {"index": 0, "role": "orientation", "title": "Why decisions get lost", "lo_ids": [f"{MAIN}#lo2"],
         "knowledge_point_id": None, "unit_ids": ["u:adr:1:1"], "brief_item_ids": [f"{MAIN}#b3"],
         "markdown": _pad(
             "## Why decisions get lost\n\nEvery long-lived system carries hundreds of choices nobody remembers making. "
             "Imagine joining a team whose service talks to two databases: the reasons for the second one left with the "
             "engineer who added it. Without a written trace, the team either lives with a choice it cannot explain or "
             "argues it again from scratch. An **Architecture Decision Record** (ADR) is the lightweight answer: a short "
             "document, kept with the code, that captures one decision at the moment it is made.", 130)},
        {"index": 1, "role": "core", "title": "The ADR template", "lo_ids": [f"{MAIN}#lo1"],
         "knowledge_point_id": f"{MAIN}#kp2", "unit_ids": ["u:adr:0:1"], "brief_item_ids": [f"{MAIN}#b1"],
         "markdown": _pad(
             "## The ADR template\n\nAn ADR captures one decision with its **context**, the **decision** itself and its "
             "**consequences**. The context part lists the forces at play: constraints, quality goals, the team's situation. "
             "The decision part states what was chosen in one or two sentences. The consequences part is where honesty "
             "lives: it names what becomes easier, what becomes harder and which follow-up work the choice creates. For "
             "example, choosing a message queue over direct calls might note that retries become free while debugging a "
             "request now spans two services. Keep each ADR short enough to read in two minutes.", 130)},
        {"index": 2, "role": "pitfalls", "title": "Lifecycle and the deletion trap", "lo_ids": [f"{MAIN}#lo1", f"{MAIN}#lo2"],
         "knowledge_point_id": f"{MAIN}#kp3", "unit_ids": ["u:adr:0:2"], "brief_item_ids": [f"{MAIN}#b2"],
         "markdown": _pad(
             "## Lifecycle and the deletion trap\n\nAn ADR moves through a small set of states. It starts as **proposed** "
             "while the team discusses it, becomes **accepted** once the choice is taken, and is later marked **superseded** "
             "when a newer record replaces it. Consider a team that reverses a choice after two years: the common mistake "
             "is to delete or edit the earlier record. Doing so destroys the reasoning history that later readers need to "
             "understand why the system looks the way it does. Instead, the earlier record stays in place with a pointer "
             "to its successor, and the new one explains what changed.", 130)},
        {"index": 3, "role": "recap", "title": "Recap", "lo_ids": [f"{MAIN}#lo1", f"{MAIN}#lo2"],
         "knowledge_point_id": None, "unit_ids": ["u:adr:0:1", "u:adr:0:2"], "brief_item_ids": [f"{MAIN}#b1", f"{MAIN}#b2"],
         "markdown": _pad(
             "## Recap\n\nRecording the context and consequences of each decision lets a team revisit choices without "
             "relitigating them. Three takeaways: an ADR has three parts; records move from proposed to accepted to "
             "superseded; and a replaced record is pointed at, never removed.", 130)},
    ]
    write_json(cur / "lessons" / item_id_to_filename(MAIN), {
        "contract_version": CONTRACT_VERSION, "lesson_id": MAIN, "group_id": MAIN, "revision": 1,
        "title": "Architecture Decision Records", "description": "Recording decisions so they survive the team.",
        "area_id": "a:architecture-leadership", "module_id": "m:decision-records", "suggested_level": 2,
        "estimated_minutes": 8, "key_terms": ["ADR", "superseded"],
        "objectives": [{"id": o["id"], "text": o["text"], "bloom_level": o["bloom_level"]} for o in main_objectives],
        "prerequisites": [PREREQ], "pages": pages, "outline_approved": True, "lessons_learned": [],
    })
    write_json(cur / "lessons" / item_id_to_filename(PREREQ), {
        "contract_version": CONTRACT_VERSION, "lesson_id": PREREQ, "group_id": PREREQ, "revision": 0,
        "title": "What Architects Do", "description": "The architect's accountability.",
        "area_id": "a:architecture-leadership", "module_id": "m:role", "suggested_level": 1, "estimated_minutes": 5,
        "key_terms": ["accountability"],
        "objectives": [{"id": o["id"], "text": o["text"], "bloom_level": o["bloom_level"]} for o in prereq_objectives],
        "prerequisites": [],
        "pages": [
            {"index": 0, "role": "orientation", "title": "Owning outcomes", "lo_ids": [f"{PREREQ}#lo1"], "knowledge_point_id": None,
             "unit_ids": ["u:arch:0:1"], "brief_item_ids": [], "markdown": _pad("## Owning outcomes\n\nAn architect decides less than people think and answers for more.", 120)},
            {"index": 1, "role": "core", "title": "Delegation", "lo_ids": [f"{PREREQ}#lo1"], "knowledge_point_id": f"{PREREQ}#kp1",
             "unit_ids": ["u:arch:0:1"], "brief_item_ids": [], "markdown": _pad("## Delegation\n\nDelegating a decision never delegates its consequences.", 120)},
            {"index": 2, "role": "recap", "title": "Recap", "lo_ids": [f"{PREREQ}#lo1"], "knowledge_point_id": None,
             "unit_ids": ["u:arch:0:1"], "brief_item_ids": [], "markdown": _pad("## Recap\n\nAn architect owns the outcome of every decision, even delegated ones.", 120)},
        ],
        "outline_approved": True, "lessons_learned": [],
    })
    write_json(cur / "lessons" / item_id_to_filename(SIBLING), {
        "contract_version": CONTRACT_VERSION, "lesson_id": SIBLING, "group_id": SIBLING, "revision": 0,
        "title": "Decision Context Forces", "description": "Which forces to list.", "area_id": "a:architecture-leadership",
        "module_id": "m:decision-records", "suggested_level": 2, "estimated_minutes": 5, "key_terms": [],
        "objectives": [{"id": o["id"], "text": o["text"], "bloom_level": o["bloom_level"]} for o in sibling_objectives],
        "prerequisites": [],
        "pages": [{"index": 0, "role": "orientation", "title": "Forces", "lo_ids": [f"{SIBLING}#lo1"], "knowledge_point_id": None,
                   "unit_ids": ["u:adr:1:1"], "brief_item_ids": [], "markdown": _pad("## Forces\n\nA context section names constraints and goals.", 120)}],
        "outline_approved": True, "lessons_learned": [],
    })
    write_json(cur / "syntheses" / item_id_to_filename(MAIN), {
        "contract_version": CONTRACT_VERSION, "group_id": MAIN, "revision": 0,
        "overview": "ADRs record one decision each so reasoning survives the team.",
        "items": [
            {"id": f"{MAIN}#b1", "kind": "map", "text": "An ADR records one decision with its context, decision and consequences.",
             "unit_ids": ["u:adr:0:1"], "quote": "context, decision, and consequences", "confidence": "stated",
             "concept_ids": ["c:adr"], "lo_ids": [f"{MAIN}#lo1"], "knowledge_point_id": f"{MAIN}#kp2"},
            {"id": f"{MAIN}#b2", "kind": "map", "text": "A replaced ADR is marked superseded and kept.",
             "unit_ids": ["u:adr:0:2"], "quote": "marked superseded when a newer record replaces it", "confidence": "stated",
             "concept_ids": ["c:adr"], "lo_ids": [f"{MAIN}#lo1"], "knowledge_point_id": f"{MAIN}#kp3"},
            {"id": f"{MAIN}#b3", "kind": "watch", "text": "Without context a later reader sees only an arbitrary choice.",
             "unit_ids": ["u:adr:1:1"], "quote": "sees only an arbitrary choice", "confidence": "stated",
             "concept_ids": ["c:decision-context"], "lo_ids": [f"{MAIN}#lo2"], "knowledge_point_id": f"{MAIN}#kp1"},
        ],
        "examples": [], "terminology": [{"term": "superseded", "definition": "Replaced by a newer record.", "unit_id": "u:adr:0:2"}],
        "lessons_learned": [],
    })
    return cur


def clean_questions() -> dict:
    """Authored questions file for MAIN that passes every gate with no findings."""
    return {
        "contract_version": CONTRACT_VERSION, "lesson_id": MAIN, "revision": 0,
        "embedded": [
            {"id": f"{MAIN}#q1", "kind": "check", "page_index": 1, "position": "after", "lo_id": f"{MAIN}#lo1",
             "concept_id": "c:adr", "unit_ids": ["u:adr:0:2"], "source_quote": "Superseded records are never deleted",
             "prompt": "Suppose a team revisits a decision made two years ago. What should happen to the old ADR, and why?",
             "acceptance": {"prediction": "The old ADR is kept and marked superseded by the new one",
                            "causal_reason": "the record preserves the reasoning history later readers need, so removing it destroys that context"},
             "expected_answer": "The old ADR stays in the log and is marked superseded by the new record.",
             "rationale": "Tests the deletion misconception.", "key_points": ["kept", "superseded", "history"],
             "bloom_level": "understand",
             "probes": {"correct_learner_answer": "Keep it but mark it superseded so the history stays readable.",
                        "wrong_learner_answer": "Delete it, the decision no longer applies."}},
            {"id": f"{MAIN}#q2", "kind": "predict", "page_index": 2, "position": "before", "lo_id": f"{MAIN}#lo2",
             "concept_id": "c:decision-context", "unit_ids": ["u:adr:1:1"],
             "source_quote": "Without the context, a later reader sees only an arbitrary choice",
             "prompt": "A new engineer asks why the service uses two databases. Predict which part of that decision's ADR "
                       "answers her, and what would break if the context section were missing?",
             "acceptance": {"prediction": "The context part answers her; without it the choice looks arbitrary",
                            "causal_reason": "the context names the forces that made the decision necessary, so removing it removes the justification"},
             "expected_answer": "The context section explains the forces behind the choice; without it the decision looks arbitrary and gets relitigated.",
             "rationale": "Primes the lifecycle page.", "key_points": ["context", "forces", "arbitrary"],
             "bloom_level": "understand",
             "probes": {"correct_learner_answer": "The context, because it lists the constraints that forced the second database.",
                        "wrong_learner_answer": "The consequences, because they say what the second database costs."}},
        ],
        "quiz": [
            {"id": f"{MAIN}#q3", "type": "multiple_choice", "lo_id": f"{MAIN}#lo1", "concept_id": "c:adr",
             "unit_ids": ["u:adr:0:2"], "page_index": 2, "source_quote": "marked superseded when a newer record replaces it",
             "prompt": "A team wants to reverse a decision recorded in an ADR two years ago. What should happen to the old record, and why?",
             "options": ["Delete the old ADR so the repository shows current decisions",
                         "Keep the old ADR and mark it superseded by the new one",
                         "Edit the old ADR in place so it describes the new decision",
                         "Move the old ADR to an archive folder outside the repository"],
             "correct_index": 1,
             "distractors": [{"index": 0, "misconception": "ADRs document the current system, not decisions"},
                             {"index": 2, "misconception": "A record can be rewritten like documentation"},
                             {"index": 3, "misconception": "History belongs outside the code"}],
             "acceptance": {"prediction": "Keep and mark superseded", "causal_reason": "the reasoning history must stay readable next to the code"},
             "expected_answer": "Keep the old ADR and mark it superseded.", "rationale": "Lifecycle rule.",
             "key_points": ["superseded", "kept"], "explanation": "Superseded records preserve the reasoning history.",
             "bloom_level": "apply", "mastery_level": 3,
             "feedback": {"confirmatory": "Right: history stays.", "remedial_hint": "Think about who reads the record later."},
             "probes": {"correct_learner_answer": "Keep and mark superseded.", "wrong_learner_answer": "Delete it."},
             "variants": [{"id": f"{MAIN}#q3v1",
                           "prompt": "Six months after adopting a queue, the platform group picks a different broker. How should the earlier ADR be treated, and why?",
                           "options": ["Rewrite it to describe the broker now in use",
                                       "Mark it superseded and link the new record",
                                       "Remove it so the repository stays tidy",
                                       "Archive it outside the repository"],
                           "correct_index": 1}],
             "recap_of": None},
            {"id": f"{MAIN}#q4", "type": "true_false", "lo_id": f"{MAIN}#lo1", "concept_id": "c:adr",
             "unit_ids": ["u:adr:0:2"], "page_index": 2, "source_quote": "the history of reasoning is what later readers need",
             "prompt": "An ADR whose decision has been reversed should be deleted from the repository.",
             "options": ["True", "False"], "correct_index": 1, "distractors": [{"index": 0, "misconception": "Records describe the current state"}],
             "acceptance": {"prediction": "False", "causal_reason": "reversed decisions keep their record, marked superseded"},
             "expected_answer": "False", "rationale": "Deletion trap.", "key_points": ["superseded"],
             "explanation": "Superseded records are kept.", "bloom_level": "remember", "mastery_level": 1,
             "feedback": {"confirmatory": "Correct.", "remedial_hint": "What would a future reader lose?"},
             "probes": {"correct_learner_answer": "False", "wrong_learner_answer": "True"}, "variants": [], "recap_of": None},
            {"id": f"{MAIN}#q5", "type": "short_answer", "lo_id": f"{MAIN}#lo2", "concept_id": "c:decision-context",
             "unit_ids": ["u:adr:1:1"], "page_index": 0, "source_quote": "The context section lists the forces in play",
             "prompt": "Explain how the context section of an ADR changes what a later team can do with the decision it records.",
             "acceptance": {"prediction": "Later teams can judge whether the forces still hold",
                            "causal_reason": "the context exposes the constraints behind the choice, which is what a review needs"},
             "expected_answer": "Later teams can see the forces that drove the choice and judge whether they still hold instead of relitigating the decision.",
             "rationale": "Mechanism of context.", "key_points": ["forces visible", "judge whether they still hold"],
             "explanation": "Context makes a decision reviewable.", "bloom_level": "understand", "mastery_level": 2,
             "feedback": {"confirmatory": "Yes.", "remedial_hint": "What does a reader see without it?"},
             "probes": {"correct_learner_answer": "They can check whether the constraints still apply before changing it.",
                        "wrong_learner_answer": "They can see who made the decision."}, "variants": [], "recap_of": None},
            {"id": f"{MAIN}#q6", "type": "short_answer", "lo_id": None, "concept_id": "c:architect-accountability",
             "unit_ids": ["u:arch:0:1"], "page_index": 0, "source_quote": "Delegating a decision does not delegate the responsibility",
             "prompt": "Explain why an architect who delegates a decision to the team still answers for its consequences.",
             "acceptance": {"prediction": "The architect remains accountable", "causal_reason": "delegating the choice does not transfer ownership of the outcome"},
             "expected_answer": "Responsibility for outcomes stays with the architect even when the choice is made by others.",
             "rationale": "Recap of the prerequisite.", "key_points": ["accountable", "outcome"],
             "explanation": "Delegation moves the decision, not the responsibility.", "bloom_level": "understand", "mastery_level": 2,
             "feedback": {"confirmatory": "Yes.", "remedial_hint": "Who answers when the delegated choice fails?"},
             "probes": {"correct_learner_answer": "Because the outcome is still theirs to own.",
                        "wrong_learner_answer": "Because they wrote the ADR."}, "variants": [], "recap_of": PREREQ},
        ],
        "exercises": [
            {"id": f"{MAIN}#e1", "title": "Read a worked ADR", "problem_statement": "Study the ADR below and label its three parts.",
             "hints": ["Find the forces", "Find the choice", "Find what gets harder"], "expected_solution": "Context, decision, consequences labelled.",
             "common_mistakes": ["Labelling consequences as context"], "bloom_level": "apply", "difficulty": 1,
             "fading": "worked", "lo_id": f"{MAIN}#lo1", "unit_ids": ["u:adr:0:1"]},
            {"id": f"{MAIN}#e2", "title": "Complete an ADR", "problem_statement": "Fill in the consequences part of this partial ADR.",
             "hints": ["What becomes easier?", "What becomes harder?", "What follow-up work appears?"],
             "expected_solution": "A consequences part naming one gain, one cost and one follow-up.",
             "common_mistakes": ["Listing only benefits"], "bloom_level": "apply", "difficulty": 2,
             "fading": "completion", "lo_id": f"{MAIN}#lo1", "unit_ids": ["u:adr:0:1"]},
        ],
        "lessons_learned": [],
    }


def write_questions(cur: Path, data: dict, lesson_id: str = MAIN) -> Path:
    path = cur / "questions" / item_id_to_filename(lesson_id)
    write_json(path, data)
    return path


def ids(findings: list[Finding], severity: str | None = None) -> list[str]:
    return [f.check_id for f in findings if severity is None or f.severity == severity]


def errors(findings: list[Finding]) -> list[str]:
    return ids(findings, "error")


@pytest.fixture
def cur(tmp_path: Path) -> Path:
    return build_fixture(tmp_path)


@pytest.fixture
def clean(cur: Path) -> tuple[Path, dict]:
    data = clean_questions()
    write_questions(cur, data)
    return cur, data


def check_with(cur: Path, data: dict, **opts) -> list[Finding]:
    write_questions(cur, data)
    return questions.check_item(cur, MAIN, **opts)


# ---------------------------------------------------------------------------
# baseline
# ---------------------------------------------------------------------------


def test_clean_file_has_no_findings(clean):
    cur, _ = clean
    findings = questions.check_item(cur, MAIN)
    assert findings == [], [f"{f.check_id}@{f.location}: {f.message}" for f in findings]


def test_stage_check_reports_lessons_without_questions(clean):
    cur, _ = clean
    findings = questions.check(cur)
    assert errors(findings) == []
    missing = [f for f in findings if f.check_id == "questions.missing"]
    assert {f.location for f in missing} == {PREREQ, SIBLING}


def test_check_item_filters_to_sub_item(clean):
    cur, data = clean
    data["embedded"][0]["prompt"] = "Is the old ADR deleted?"
    data["quiz"][0]["correct_index"] = 9
    write_questions(cur, data)
    only_q1 = questions.check_item(cur, f"{MAIN}#q1")
    assert {f.location for f in only_q1} == {f"{MAIN}#q1"}
    assert "questions.yes_no_stem" in errors(only_q1)
    assert "questions.correct_index" not in ids(only_q1)


def test_file_missing_and_invalid_json(cur):
    assert errors(questions.check_item(cur, MAIN)) == ["questions.file_missing"]
    path = cur / "questions" / item_id_to_filename(MAIN)
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    assert errors(questions.check_item(cur, MAIN)) == ["questions.invalid_json"]


def test_unknown_lesson_and_lesson_mismatch(cur):
    data = clean_questions()
    write_questions(cur, data, "g:nowhere")
    assert "questions.unknown_lesson" in errors(questions.check_item(cur, "g:nowhere"))
    data["lesson_id"] = PREREQ
    assert "questions.lesson_mismatch" in errors(check_with(cur, data))


def test_missing_groups_file_is_a_clear_error(tmp_path):
    (tmp_path / "empty").mkdir()
    findings = questions.check(tmp_path / "empty")
    assert errors(findings) == ["questions.groups_missing"]


# ---------------------------------------------------------------------------
# slots and placement
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("n, allow, expected", [
    (4, False, {"before": [1, 2, 3], "after": [0, 1, 2]}),
    (4, True, {"before": [0, 1, 2, 3], "after": [0, 1, 2, 3]}),
    (1, False, {"before": [], "after": []}),
    (1, True, {"before": [0], "after": [0]}),
    (0, False, {"before": [], "after": []}),
    (3, False, {"before": [1, 2], "after": [0, 1]}),
])
def test_compute_slots_table(n, allow, expected):
    assert questions.compute_slots(n, allow) == expected


def test_unreachable_slot_default_and_allow_edge(cur):
    data = clean_questions()
    data["embedded"][0]["page_index"] = 3  # check/after on the last page
    assert "questions.unreachable" in errors(check_with(cur, data))
    assert "questions.unreachable" not in ids(check_with(cur, data, allow_edge_questions=True))


def test_slot_overflow_per_page_position(cur):
    data = clean_questions()
    extra = copy.deepcopy(data["embedded"][0])
    extra["id"] = f"{MAIN}#q9"
    extra["prompt"] = "Consider a record whose decision was reversed last year. What state does that ADR end in, and why?"
    extra["source_quote"] = "accepted once the decision is made"
    data["embedded"].append(extra)
    assert "questions.slot_overflow" in errors(check_with(cur, data))


def test_position_must_match_kind(cur):
    data = clean_questions()
    data["embedded"][0]["position"] = "before"
    assert "questions.position_kind" in errors(check_with(cur, data))


def test_no_reachable_embedded_is_an_error(cur):
    data = clean_questions()
    data["embedded"] = []
    assert "questions.none_reachable" in errors(check_with(cur, data))


def test_embedded_budget(cur):
    data = clean_questions()
    base = data["embedded"][1]
    for n, page in zip(range(10, 15), (1, 3, 1, 3, 1)):
        q = copy.deepcopy(base)
        q["id"] = f"{MAIN}#q{n}"
        q["page_index"] = page
        data["embedded"].append(q)
    assert "questions.embedded_budget" in errors(check_with(cur, data))


# ---------------------------------------------------------------------------
# ids, enums, references
# ---------------------------------------------------------------------------


def test_id_format_wrong_kind_and_duplicate(cur):
    data = clean_questions()
    data["embedded"][0]["id"] = f"{MAIN}#k1"
    data["quiz"][1]["id"] = data["quiz"][0]["id"]
    found = errors(check_with(cur, data))
    assert "questions.id_format" in found and "questions.duplicate_id" in found


def test_enum_violations(cur):
    data = clean_questions()
    data["embedded"][0]["kind"] = "quiz"
    data["quiz"][0]["type"] = "essay"
    data["quiz"][1]["bloom_level"] = "know"
    data["exercises"][0]["fading"] = "guided"
    assert errors(check_with(cur, data)).count("questions.enum") == 4


def test_unresolved_references(cur):
    data = clean_questions()
    data["embedded"][0]["lo_id"] = f"{PREREQ}#lo1"
    data["embedded"][0]["concept_id"] = "c:architect-accountability"
    data["quiz"][0]["unit_ids"] = ["u:arch:0:1"]
    data["quiz"][1]["page_index"] = 4
    found = errors(check_with(cur, data))
    assert {"questions.unknown_lo", "questions.unknown_concept", "questions.unknown_unit", "questions.page_index"} <= set(found)


def test_quote_mismatch_and_missing(cur):
    data = clean_questions()
    data["embedded"][0]["source_quote"] = "records are always deleted"
    data["quiz"][0]["source_quote"] = ""
    findings = check_with(cur, data)
    assert "questions.quote_mismatch" in errors(findings)
    assert "questions.quote_missing" in ids(findings, "warning")


def test_quote_matches_whitespace_and_case_insensitively(cur):
    data = clean_questions()
    data["embedded"][0]["source_quote"] = "superseded   RECORDS are never deleted"
    assert "questions.quote_mismatch" not in ids(check_with(cur, data))


def test_acceptance_and_probes(cur):
    data = clean_questions()
    data["embedded"][0]["acceptance"]["causal_reason"] = ""
    data["embedded"][1]["acceptance"]["prediction"] = "x" * 601
    data["quiz"][0]["probes"] = {"correct_learner_answer": "ok"}
    found = errors(check_with(cur, data))
    assert found.count("questions.acceptance") == 2
    assert "questions.probes" in found


# ---------------------------------------------------------------------------
# prompt hygiene
# ---------------------------------------------------------------------------


def test_prompt_length(cur):
    data = clean_questions()
    data["embedded"][0]["prompt"] = "Why keep ADRs?"
    assert "questions.prompt_length" in errors(check_with(cur, data))


def test_not_a_question_and_multiple_interrogatives(cur):
    data = clean_questions()
    data["embedded"][0]["prompt"] = "The old ADR and what happens to it when a team reverses the decision later on."
    data["quiz"][0]["prompt"] = "A team reverses an ADR decision. What happens to the record? Why does the ADR history matter?"
    findings = check_with(cur, data)
    assert "questions.not_a_question" in ids(findings, "warning")
    assert "questions.multiple_interrogatives" in ids(findings, "warning")


def test_imperative_prompt_counts_as_question(cur):
    data = clean_questions()
    data["embedded"][0]["prompt"] = "Explain what happens to the old ADR when a team reverses the decision it records."
    assert "questions.not_a_question" not in ids(check_with(cur, data))


def test_answer_in_stem_free_text_and_mcq(cur):
    data = clean_questions()
    data["embedded"][0]["prompt"] = ("The old ADR stays in the log and is marked superseded by the new record. "
                                     "Why does the team keep the old ADR this way?")
    data["quiz"][0]["prompt"] = "Should a team keep the old ADR and mark it superseded by the new one, or delete it, and why?"
    found = errors(check_with(cur, data))
    assert found.count("questions.answer_in_stem") == 2


def test_answer_leaks_short_answer_whole_match_only():
    assert questions.answer_leaks("Which state ends the lifecycle: superseded or accepted?", "superseded")
    assert not questions.answer_leaks("Which state ends the lifecycle?", "superseded")
    assert not questions.answer_leaks("Keep the old record", "Keep the old ADR and mark it superseded")


def test_answer_on_page_warning_for_check_questions(cur):
    data = clean_questions()
    lesson_path = cur / "lessons" / item_id_to_filename(MAIN)
    lesson = read_json(lesson_path)
    lesson["pages"][1]["markdown"] += " The old ADR stays in the log and is marked superseded by the new record."
    write_json(lesson_path, lesson)
    findings = check_with(cur, data)
    assert "questions.answer_on_page" in ids(findings, "warning")
    assert questions.is_answer_on_page(lesson["pages"][1]["markdown"], data["embedded"][0]["expected_answer"])
    assert not questions.is_answer_on_page(lesson["pages"][0]["markdown"], data["embedded"][0]["expected_answer"])


def test_quote_copy_in_stem(cur):
    data = clean_questions()
    data["quiz"][0]["prompt"] = "A record is marked superseded when a newer record replaces it. Which option describes the old ADR, and why?"
    assert "questions.quote_copy" in errors(check_with(cur, data))


def test_quote_overlap_between_items(cur):
    data = clean_questions()
    data["quiz"][1]["source_quote"] = "is marked superseded when a newer record replaces it"
    assert "questions.quote_overlap" in ids(check_with(cur, data), "warning")


def test_concept_missing_warning(cur):
    data = clean_questions()
    data["embedded"][0]["prompt"] = "Suppose a team revisits a decision made two years ago. What should happen to the old record, and why?"
    assert "questions.concept_missing" in ids(check_with(cur, data), "warning")


def test_causal_cue_required_for_predict_and_apply(cur):
    data = clean_questions()
    data["embedded"][1]["prompt"] = "Predict which part of the decision's ADR a new engineer reads first when the context section is short?"
    data["quiz"][0]["prompt"] = "A team wants to reverse a decision recorded in an ADR two years ago. Which option is the correct treatment of the old record?"
    assert errors(check_with(cur, data)).count("questions.no_causal_cue") == 2


def test_literal_recall_and_yes_no_stem(cur):
    data = clean_questions()
    data["quiz"][2]["prompt"] = "What is the exact name of the section of an ADR that lists the forces, and why does it matter?"
    data["embedded"][0]["prompt"] = "Does the old ADR get deleted when a team reverses a decision two years later, and why?"
    found = errors(check_with(cur, data))
    assert "questions.literal_recall" in found and "questions.yes_no_stem" in found


# ---------------------------------------------------------------------------
# MCQ
# ---------------------------------------------------------------------------


def test_mcq_option_count_and_true_false_shape(cur):
    data = clean_questions()
    data["quiz"][0]["options"] = data["quiz"][0]["options"][:3]
    data["quiz"][1]["options"] = ["Yes", "No"]
    assert errors(check_with(cur, data)).count("questions.mcq_options") == 2


def test_correct_index_range(cur):
    data = clean_questions()
    data["quiz"][0]["correct_index"] = 4
    assert "questions.correct_index" in errors(check_with(cur, data))


def test_distractors_incomplete_warning(cur):
    data = clean_questions()
    data["quiz"][0]["distractors"] = data["quiz"][0]["distractors"][:1]
    assert "questions.distractors_incomplete" in ids(check_with(cur, data), "warning")


def test_mastery_level_mapping(cur):
    data = clean_questions()
    data["quiz"][0]["mastery_level"] = 4
    assert "questions.mastery_level" in errors(check_with(cur, data))


def test_mcq_flaws_counter_cases():
    stem = "Which treatment preserves the reasoning history of a reversed decision, and why?"
    clean = ["Delete the record", "Keep it marked superseded", "Rewrite it in place", "Archive it elsewhere"]
    assert questions.mcq_flaws(stem, clean, 1) == []
    longest = ["Delete it", "Keep the record and mark it superseded so later readers see why", "Rewrite it", "Archive it"]
    assert any(f.startswith("longest_option") for f in questions.mcq_flaws(stem, longest, 1))
    assert any(f.startswith("all_none_of_above") for f in questions.mcq_flaws(stem, [*clean[:3], "None of the above"], 1))
    assert any(f.startswith("absolute_terms") for f in questions.mcq_flaws(stem, [*clean[:3], "Records are always deleted"], 1))
    assert any(f.startswith("negative_stem") for f in questions.mcq_flaws("Which option is NOT a lifecycle state?", clean, 1))
    assert any(f.startswith("duplicate_options") for f in questions.mcq_flaws(stem, [*clean[:3], "Keep it marked superseded."], 1))
    repeat = questions.mcq_flaws("Why is the reasoning history kept?", ["Tidiness", "To keep the reasoning history readable", "Cost", "Speed"], 1)
    assert any(f.startswith("word_repeat") for f in repeat)
    # true/false: only the negative stem counts
    assert questions.mcq_flaws("An ADR is never deleted.", ["True", "False"], 1, true_false=True) == ["negative_stem: stem contains 'never'"]
    assert questions.mcq_flaws("Superseded ADRs are kept.", ["True", "False"], 0, true_false=True) == []


def test_mcq_flaws_threshold_error_vs_info(cur):
    data = clean_questions()
    data["quiz"][0]["options"][3] = "Keep all of the above records"  # absolute + all-of-the-above
    assert "questions.mcq_flaws" in errors(check_with(cur, data))
    data = clean_questions()
    data["quiz"][0]["options"][3] = "Move the old ADR to an archive folder outside the repository, always"
    findings = check_with(cur, data)
    assert "questions.mcq_flaws" in ids(findings, "info") and "questions.mcq_flaws" not in errors(findings)


def test_short_answer_with_options_warns(cur):
    data = clean_questions()
    data["quiz"][2]["options"] = ["a", "b", "c", "d"]
    assert "questions.extraneous_field" in ids(check_with(cur, data), "warning")


# ---------------------------------------------------------------------------
# quiz coverage, span, budget, recap
# ---------------------------------------------------------------------------


def test_objective_uncovered(cur):
    data = clean_questions()
    data["quiz"] = [q for q in data["quiz"] if q["lo_id"] != f"{MAIN}#lo2"]
    findings = check_with(cur, data)
    assert [f.location for f in findings if f.check_id == "questions.objective_uncovered"] == [f"{MAIN}#lo2"]


def test_recap_item_does_not_count_as_coverage(cur):
    data = clean_questions()
    data["quiz"][3]["lo_id"] = f"{MAIN}#lo2"
    data["quiz"] = [q for q in data["quiz"] if q["id"] != f"{MAIN}#q5"]
    assert "questions.objective_uncovered" in errors(check_with(cur, data))


def test_bloom_span(cur):
    data = clean_questions()
    for q in data["quiz"]:
        q["bloom_level"] = "understand"
        q["mastery_level"] = 2
    assert "questions.bloom_span" in errors(check_with(cur, data))


def test_quiz_budget_warning(cur):
    data = clean_questions()
    for n in range(20, 22):
        q = copy.deepcopy(data["quiz"][2])
        q["id"] = f"{MAIN}#q{n}"
        q["source_quote"] = "constraints, quality goals, and the team's situation" if n == 20 else "tempted to relitigate it"
        data["quiz"].append(q)
    assert "questions.quiz_budget" in ids(check_with(cur, data), "warning")


def test_recap_of_must_be_committed_prerequisite(cur):
    data = clean_questions()
    data["quiz"][3]["recap_of"] = SIBLING
    assert "questions.recap_of" in errors(check_with(cur, data))


def test_recap_of_rejected_when_status_not_committed(cur):
    groups_path = cur / "groups.json"
    groups = read_json(groups_path)
    groups["groups"][1]["prerequisites"][0]["status"] = "uncertain"
    write_json(groups_path, groups)
    assert "questions.recap_of" in errors(check_with(cur, clean_questions()))


def test_recap_of_accepts_proposed_when_no_statuses_exist(cur):
    groups_path = cur / "groups.json"
    groups = read_json(groups_path)
    for g in groups["groups"]:
        for p in g["prerequisites"]:
            p["status"] = "proposed"
    write_json(groups_path, groups)
    assert "questions.recap_of" not in ids(check_with(cur, clean_questions()))


# ---------------------------------------------------------------------------
# variants
# ---------------------------------------------------------------------------


def test_variant_similarity_rules():
    original = "A team wants to reverse a decision recorded in an ADR two years ago. What should happen to the old record, and why?"
    assert questions.variant_too_similar(original, original)
    assert questions.variant_too_similar(original, "A team wants to reverse a decision recorded in an ADR three years ago. What should happen to the old record, and why?")
    assert questions.variant_too_similar("Acme picks Postgres in 2019. What happens to the ADR, and why?",
                                         "Globex picks Mongo in 2021. What happens to the ADR, and why?")
    assert not questions.variant_too_similar(original, "Six months after adopting a queue, the platform group picks a different broker. How should the earlier ADR be treated, and why?")
    assert questions.variant_too_similar(original, "")


def test_variant_too_similar_and_bad_id(cur):
    data = clean_questions()
    data["quiz"][0]["variants"][0]["prompt"] = data["quiz"][0]["prompt"].replace("two", "three")
    data["quiz"][0]["variants"][0]["id"] = f"{MAIN}#q4v1"
    found = errors(check_with(cur, data))
    assert "questions.variant_too_similar" in found and "questions.variant_id" in found


def test_variant_options_checked(cur):
    data = clean_questions()
    data["quiz"][0]["variants"][0]["options"] = ["only", "two"]
    assert "questions.mcq_options" in errors(check_with(cur, data))


# ---------------------------------------------------------------------------
# exercises
# ---------------------------------------------------------------------------


def test_exercise_hints_exactly_three(cur):
    data = clean_questions()
    data["exercises"][0]["hints"].append("Fourth hint")
    assert "questions.exercise_hints" in errors(check_with(cur, data))


def test_fading_order(cur):
    data = clean_questions()
    data["exercises"][0]["fading"], data["exercises"][1]["fading"] = "completion", "worked"
    assert "questions.fading_order" in errors(check_with(cur, data))


def test_exercise_budget_and_difficulty(cur):
    data = clean_questions()
    for n in range(3, 5):
        e = copy.deepcopy(data["exercises"][1])
        e["id"] = f"{MAIN}#e{n}"
        e["fading"] = "conventional"
        data["exercises"].append(e)
    data["exercises"][0]["difficulty"] = 7
    findings = check_with(cur, data)
    assert "questions.exercise_budget" in errors(findings)
    assert "questions.difficulty" in ids(findings, "warning")


def test_exercise_references(cur):
    data = clean_questions()
    data["exercises"][0]["lo_id"] = f"{SIBLING}#lo1"
    data["exercises"][0]["unit_ids"] = ["u:arch:0:1"]
    found = errors(check_with(cur, data))
    assert "questions.unknown_lo" in found and "questions.unknown_unit" in found


# ---------------------------------------------------------------------------
# prepare
# ---------------------------------------------------------------------------


def test_prepare_writes_work_file_with_slots_and_prereq_recap(cur):
    written, findings = questions.prepare(cur)
    assert errors(findings) == []
    assert {p.name for p in written} == {item_id_to_filename(MAIN), item_id_to_filename(PREREQ), item_id_to_filename(SIBLING)}
    work = read_json(cur / "questions" / "work" / item_id_to_filename(MAIN))
    assert work["allowed_slots"] == {"before": [1, 2, 3], "after": [0, 1, 2]}
    assert work["budget"]["quiz_items"] == 4
    assert work["prerequisites"][0]["group_id"] == PREREQ
    assert work["prerequisites"][0]["recap_markdown"].startswith("## Recap")
    assert work["distractor_seeds"] == ["A reversed decision's ADR should be deleted"]
    assert {c["id"] for c in work["concepts"]} == {"c:adr", "c:decision-context"}
    assert work["brief_items"][0]["quote"] == "context, decision, and consequences"
    assert {u["id"] for u in work["units"]} == {"u:adr:0:1", "u:adr:0:2", "u:adr:1:1"}
    assert work["instructions"] and "two-layer key" in work["instructions"].lower()
    assert work["existing"] is None and work["revision"] == 0 and work["lessons_learned"] == []
    assert len(work["pages"]) == 4 and all(p["markdown"] for p in work["pages"])


def test_prepare_force_and_existing_revision(cur):
    data = clean_questions()
    data["lessons_learned"] = ["Avoid yes/no stems"]
    write_questions(cur, data)
    questions.prepare(cur, [MAIN])
    _, findings = questions.prepare(cur, [MAIN])
    assert "questions.work_exists" in ids(findings, "info")
    questions.prepare(cur, [MAIN], force=True, allow_edge_questions=True)
    work = read_json(cur / "questions" / "work" / item_id_to_filename(MAIN))
    assert work["revision"] == 1 and work["lessons_learned"] == ["Avoid yes/no stems"]
    assert work["allowed_slots"]["before"] == [0, 1, 2, 3]
    assert work["existing"]["lesson_id"] == MAIN


def test_prepare_refuses_outline_only_lesson(cur):
    lesson_path = cur / "lessons" / item_id_to_filename(SIBLING)
    lesson = read_json(lesson_path)
    lesson["pages"][0]["markdown"] = None
    write_json(lesson_path, lesson)
    written, findings = questions.prepare(cur, [SIBLING])
    assert written == [] and "questions.lesson_outline_only" in errors(findings)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_check_exit_codes_and_output(clean, capsys):
    cur, data = clean
    assert questions.main(["check", str(cur), "--lesson", MAIN]) == 0
    assert capsys.readouterr().out.strip().endswith("0 errors, 0 warnings")
    data["quiz"][0]["source_quote"] = ""
    write_questions(cur, data)
    assert questions.main(["check", str(cur), "--lesson", MAIN]) == 0
    out = capsys.readouterr().out
    assert f"[WARN] questions.quote_missing @ {MAIN}#q3:" in out and "0 errors, 1 warnings" in out
    assert questions.main(["check", str(cur), "--lesson", MAIN, "--strict"]) == 1
    data["embedded"] = []
    write_questions(cur, data)
    assert questions.main(["check", str(cur), "--lesson", MAIN]) == 1
    assert "[ERROR] questions.none_reachable @" in capsys.readouterr().out


def test_cli_prepare(cur, capsys):
    assert questions.main(["prepare", str(cur), "--lesson", MAIN, "--allow-edge-questions"]) == 0
    assert "wrote questions/work/" in capsys.readouterr().out
    assert questions.main(["check", str(cur / "nope")]) == 2
