"""Tests for cards.py (Stage 7 — design cards).

A synthetic three-lesson curriculum (units, concepts, qualification, groups,
syntheses, lessons, questions) is built in tmp_path by ``build_fixture``;
``clean_cards`` returns an authored file that passes every gate with zero
findings. Each gate test plants one defect and asserts the check_id, so the
clean baseline doubles as a regression guard against false positives. The
fixture mirrors tests/test_questions.py but is kept self-contained.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

import _compat  # noqa: F401

import cards
from contracts import CONTRACT_VERSION, Finding, item_id_to_filename

MAIN = "g:architecture-decision-records"
PREREQ = "g:what-architects-do"
SIBLING = "g:decision-context-forces"

UNITS = {
    "adr": [
        ("u:adr:0:1", "The ADR template", "concept",
         "An architecture decision record (ADR) is a short document that records one decision with its "
         "context, decision, and consequences. It is kept alongside the code so that the reasoning survives "
         "team changes and new readers can see the forces that shaped each choice."),
        ("u:adr:0:2", "Lifecycle", "procedure",
         "An ADR is proposed while under discussion, accepted once the decision is made, and marked superseded "
         "when a newer record replaces it. Superseded records are never deleted, because the history of "
         "reasoning is what later readers need."),
        ("u:adr:1:1", "Context", "principle",
         "The context section lists the forces in play: constraints, quality goals, and the team's situation at "
         "the time. Without the context, a later reader sees only an arbitrary choice and is tempted to "
         "relitigate it."),
    ],
    "arch": [
        ("u:arch:0:1", "Accountability", "principle",
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


def _lesson(lesson_id: str, title: str, module: str, objectives: list[dict], pages: list[dict], prereqs: list[str]) -> dict:
    return {
        "contract_version": CONTRACT_VERSION, "lesson_id": lesson_id, "group_id": lesson_id, "revision": 0,
        "title": title, "description": f"{title}.", "area_id": "a:architecture-leadership", "module_id": module,
        "suggested_level": 2, "estimated_minutes": 6, "key_terms": [],
        "objectives": [{"id": o["id"], "text": o["text"], "bloom_level": o["bloom_level"]} for o in objectives],
        "prerequisites": prereqs, "pages": pages, "outline_approved": True, "lessons_learned": [],
    }


def _page(index: int, role: str, title: str, lo_ids: list[str], kp: str | None, unit_ids: list[str], text: str) -> dict:
    return {"index": index, "role": role, "title": title, "lo_ids": lo_ids, "knowledge_point_id": kp,
            "unit_ids": unit_ids, "brief_item_ids": [], "markdown": _pad(f"## {title}\n\n{text}", 130)}


def build_fixture(root: Path) -> Path:
    """Lesson-pipeline curriculum: a prerequisite lesson, the main lesson, a same-module sibling."""
    cur = root / "cur"
    write_json(cur / "manifest.json", {"name": "Fixture", "domain": "fixture", "pipeline": "lesson",
                                       "sources": [{"type": "web", "path": "adr"}]})
    for source, units in UNITS.items():
        write_json(cur / "units" / f"{source}.json", {
            "contract_version": CONTRACT_VERSION, "source": source, "source_file": f"{source}.json",
            "source_type": "web", "title": source, "scope_applied": False,
            "units": [{"id": uid, "section_index": int(uid.split(":")[2]), "section_title": title,
                       "heading_path": [title], "block_type": "prose", "text": text, "token_count": 60,
                       "raw_span": [0, len(text)], "page": None, "images": [], "cleaning": {}}
                      for uid, title, _level, text in units],
        })
        write_json(cur / "qualification" / f"{source}.json", {
            "contract_version": CONTRACT_VERSION, "source": source,
            "units": {uid: {"concepts": [], "unit_type": "definition", "knowledge_level": level, "keep": True}
                      for uid, _title, level, _text in units},
            "gleaning": {},
        })
    write_json(cur / "units" / "index.json", {"contract_version": CONTRACT_VERSION, "sources": {}, "total_units": 4,
                                               "total_tokens": 240, "scope": None})
    write_json(cur / "concepts.json", {
        "contract_version": CONTRACT_VERSION,
        "concepts": [
            {"id": "c:adr", "label": "Architecture Decision Record", "aliases": ["ADR"], "description": "A short record of one decision.",
             "learning_step": "applied", "depends_on": [], "coverage": "strong", "sources": ["adr"]},
            {"id": "c:decision-context", "label": "Decision context", "aliases": [], "description": "The forces behind a decision.",
             "learning_step": "applied", "depends_on": [], "coverage": "moderate", "sources": ["adr"]},
            {"id": "c:architect-accountability", "label": "Architect accountability", "aliases": [], "description": "Owning outcomes.",
             "learning_step": "foundation", "depends_on": [], "coverage": "thin", "sources": ["arch"]},
        ],
        "gaps": [], "ordering": ["c:architect-accountability", "c:adr", "c:decision-context"], "merges": [],
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
             "watch": [{"id": f"{MAIN}#w1", "kind": "misconception", "text": "A reversed decision's ADR should be deleted", "unit_ids": ["u:adr:0:2"]},
                       {"id": f"{MAIN}#w2", "kind": "boundary", "text": "An ADR is not a design document", "unit_ids": ["u:adr:0:1"]}],
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

    main_pages = [
        _page(0, "orientation", "Why decisions get lost", [f"{MAIN}#lo2"], None, ["u:adr:1:1"],
              "Every long-lived system carries hundreds of choices nobody remembers making. Imagine joining a team whose "
              "service talks to two databases: the reasons for the second one left with the engineer who added it. An "
              "**Architecture Decision Record** (ADR) is the lightweight answer: a short document kept with the code."),
        _page(1, "core", "The ADR template", [f"{MAIN}#lo1"], f"{MAIN}#kp2", ["u:adr:0:1"],
              "An ADR captures one decision with its **context**, the **decision** itself and its **consequences**. The "
              "context part lists the forces at play. The consequences part names what becomes easier, what becomes harder "
              "and which follow-up work the choice creates. For example, choosing a message queue over direct calls might "
              "note that retries become free while debugging a request now spans two services."),
        _page(2, "pitfalls", "Lifecycle and the deletion trap", [f"{MAIN}#lo1", f"{MAIN}#lo2"], f"{MAIN}#kp3", ["u:adr:0:2"],
              "An ADR starts as **proposed**, becomes **accepted** once the choice is taken, and is later marked "
              "**superseded** when a newer record replaces it. Consider a team that reverses a choice after two years: the "
              "common mistake is to delete the earlier record, which destroys the reasoning history later readers need."),
        _page(3, "recap", "Recap", [f"{MAIN}#lo1", f"{MAIN}#lo2"], None, ["u:adr:0:1", "u:adr:0:2"],
              "Recording the context and consequences of each decision lets a team revisit choices without relitigating "
              "them. Three takeaways: an ADR has three parts; records move from proposed to accepted to superseded; and a "
              "replaced record is pointed at, never removed."),
    ]
    write_json(cur / "lessons" / item_id_to_filename(MAIN),
               _lesson(MAIN, "Architecture Decision Records", "m:decision-records", main_objectives, main_pages, [PREREQ]))
    write_json(cur / "lessons" / item_id_to_filename(PREREQ), _lesson(PREREQ, "What Architects Do", "m:role", prereq_objectives, [
        _page(0, "orientation", "Owning outcomes", [f"{PREREQ}#lo1"], None, ["u:arch:0:1"], "An architect decides less than people think and answers for more."),
        _page(1, "core", "Delegation", [f"{PREREQ}#lo1"], f"{PREREQ}#kp1", ["u:arch:0:1"], "Delegating a decision never delegates its consequences."),
        _page(2, "recap", "Recap", [f"{PREREQ}#lo1"], None, ["u:arch:0:1"], "An architect owns the outcome of every decision, even delegated ones."),
    ], []))
    write_json(cur / "lessons" / item_id_to_filename(SIBLING), _lesson(SIBLING, "Decision Context Forces", "m:decision-records", sibling_objectives, [
        _page(0, "orientation", "Forces", [f"{SIBLING}#lo1"], None, ["u:adr:1:1"], "A context section names constraints and goals."),
    ], []))
    write_json(cur / "syntheses" / item_id_to_filename(MAIN), {
        "contract_version": CONTRACT_VERSION, "group_id": MAIN, "revision": 0,
        "overview": "ADRs record one decision each so reasoning survives the team.",
        "items": [
            {"id": f"{MAIN}#b1", "kind": "map", "text": "An ADR records one decision with its context, decision and consequences.",
             "unit_ids": ["u:adr:0:1"], "quote": "context, decision, and consequences", "confidence": "stated",
             "concept_ids": ["c:adr"], "lo_ids": [f"{MAIN}#lo1"], "knowledge_point_id": f"{MAIN}#kp2"},
            {"id": f"{MAIN}#b2", "kind": "watch", "text": "Without context a later reader sees only an arbitrary choice.",
             "unit_ids": ["u:adr:1:1"], "quote": "sees only an arbitrary choice", "confidence": "stated",
             "concept_ids": ["c:decision-context"], "lo_ids": [f"{MAIN}#lo2"], "knowledge_point_id": f"{MAIN}#kp1"},
        ],
        "examples": [], "terminology": [{"term": "superseded", "definition": "Replaced by a newer record.", "unit_id": "u:adr:0:2"}],
        "lessons_learned": [],
    })
    write_json(cur / "questions" / item_id_to_filename(MAIN), {
        "contract_version": CONTRACT_VERSION, "lesson_id": MAIN, "revision": 0,
        "embedded": [{"id": f"{MAIN}#q1", "kind": "check", "page_index": 1, "position": "after",
                      "prompt": "Suppose a team revisits a decision made two years ago. What should happen to the old ADR, and why?"}],
        "quiz": [{"id": f"{MAIN}#q3", "type": "short_answer",
                  "prompt": "Explain how the context section of an ADR changes what a later team can do with the decision."}],
        "exercises": [], "lessons_learned": [],
    })
    return cur


def clean_cards() -> dict:
    """Authored cards file for MAIN that passes every gate with no findings."""
    common = {"unit_ids": ["u:adr:0:1"], "page_index": 1, "source_reference": "adr"}
    return {
        "contract_version": CONTRACT_VERSION, "lesson_id": MAIN, "revision": 0,
        "cards": [
            {"id": f"{MAIN}#k1", "card_type": "concept", "front": "What three sections does every ADR record?",
             "back": "Context, decision, consequences.", "card_data": {}, "lo_id": f"{MAIN}#lo1", "lens": "parts_wholes", **common},
            {"id": f"{MAIN}#k2", "card_type": "cloze",
             "front": "An ADR replaced by a newer decision is marked ___ rather than deleted.", "back": "superseded",
             "card_data": {"blank_answer": "superseded", "hint": "lifecycle status word"}, "lo_id": f"{MAIN}#lo1",
             "unit_ids": ["u:adr:0:2"], "page_index": 2, "source_reference": "adr", "lens": "recall"},
            {"id": f"{MAIN}#k3", "card_type": "ordering", "front": "Order the lifecycle states an ADR passes through:",
             "back": "proposed → accepted → superseded",
             "card_data": {"items": ["accepted", "superseded", "proposed"], "correct_order": [2, 0, 1]}, "lo_id": f"{MAIN}#lo1",
             "unit_ids": ["u:adr:0:2"], "page_index": 2, "source_reference": "adr", "lens": "procedure"},
            {"id": f"{MAIN}#k4", "card_type": "discrimination", "front": "Which of these belongs in the consequences section of an ADR?",
             "back": "Debugging a request now spans two services",
             "card_data": {"options": ["The team must ship by March", "Debugging a request now spans two services",
                                       "Use a message queue instead of direct calls", "Latency must stay under 200 ms"],
                           "correct_index": 1},
             "lo_id": f"{MAIN}#lo1", "lens": "attributes", **common},
            {"id": f"{MAIN}#k5", "card_type": "application",
             "front": "A new engineer cannot see why a service uses two databases. Which ADR section would have prevented that confusion?",
             "back": "The context section, which names the forces behind the choice.", "card_data": {}, "lo_id": f"{MAIN}#lo2",
             "unit_ids": ["u:adr:1:1"], "page_index": 0, "source_reference": "adr", "lens": "causes_effects"},
        ],
        "lessons_learned": [],
    }


def write_cards(cur: Path, data: dict, lesson_id: str = MAIN) -> Path:
    path = cur / "cards" / item_id_to_filename(lesson_id)
    write_json(path, data)
    return path


def ids(findings: list[Finding], severity: str | None = None) -> list[str]:
    return [f.check_id for f in findings if severity is None or f.severity == severity]


def errors(findings: list[Finding]) -> list[str]:
    return ids(findings, "error")


def warnings(findings: list[Finding]) -> list[str]:
    return ids(findings, "warning")


@pytest.fixture
def cur(tmp_path: Path) -> Path:
    return build_fixture(tmp_path)


@pytest.fixture
def clean(cur: Path) -> tuple[Path, dict]:
    data = clean_cards()
    write_cards(cur, data)
    return cur, data


def check_with(cur: Path, data: dict) -> list[Finding]:
    write_cards(cur, data)
    return cards.check_item(cur, MAIN)


# ---------------------------------------------------------------------------
# baseline and file plumbing
# ---------------------------------------------------------------------------


def test_clean_file_has_no_findings(clean):
    cur, _ = clean
    findings = cards.check_item(cur, MAIN)
    assert findings == [], [f"{f.check_id}@{f.location}: {f.message}" for f in findings]


def test_stage_check_reports_lessons_without_cards(clean):
    cur, _ = clean
    findings = cards.check(cur)
    assert errors(findings) == []
    assert {f.location for f in findings if f.check_id == "cards.missing"} == {PREREQ, SIBLING}


def test_stage_check_with_lesson_filter_and_strays(clean):
    cur, data = clean
    assert errors(cards.check(cur, [PREREQ])) == ["cards.file_missing"]
    write_cards(cur, {**data, "lesson_id": "g:stray"}, "g:stray")
    assert "cards.unknown_lesson" in errors(cards.check(cur))


def test_check_item_filters_to_card(clean):
    cur, data = clean
    data["cards"][0]["front"] = "Is an ADR kept with the code?"
    data["cards"][1]["card_data"]["blank_answer"] = ""
    write_cards(cur, data)
    only_k1 = cards.check_item(cur, f"{MAIN}#k1")
    assert {f.location for f in only_k1} == {f"{MAIN}#k1"}
    assert "cards.yes_no_front" in errors(only_k1)
    assert "cards.cloze_answer" not in ids(only_k1)


def test_file_missing_and_invalid_json(cur):
    assert errors(cards.check_item(cur, MAIN)) == ["cards.file_missing"]
    path = cur / "cards" / item_id_to_filename(MAIN)
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    assert errors(cards.check_item(cur, MAIN)) == ["cards.invalid_json"]
    path.write_text("[]", encoding="utf-8")
    assert errors(cards.check_item(cur, MAIN)) == ["cards.invalid_json"]


def test_unknown_lesson_and_lesson_mismatch(cur):
    data = clean_cards()
    write_cards(cur, data, "g:nowhere")
    assert errors(cards.check_item(cur, "g:nowhere")) == ["cards.unknown_lesson"]
    data["lesson_id"] = PREREQ
    assert "cards.lesson_mismatch" in errors(check_with(cur, data))


def test_missing_groups_file_is_a_clear_error(tmp_path):
    (tmp_path / "empty").mkdir()
    assert errors(cards.check(tmp_path / "empty")) == ["cards.groups_missing"]
    assert errors(cards.check_item(tmp_path / "empty", MAIN)) == ["cards.groups_missing"]


def test_lesson_file_missing_or_invalid(cur):
    lesson_path = cur / "lessons" / item_id_to_filename(MAIN)
    lesson_path.unlink()
    assert errors(check_with(cur, clean_cards())) == ["cards.lesson_missing"]
    lesson_path.write_text("{oops", encoding="utf-8")
    assert errors(check_with(cur, clean_cards())) == ["cards.lesson_missing"]


def test_contract_version_warning_and_cards_not_a_list(cur):
    data = clean_cards()
    data["contract_version"] = 0
    data["cards"] = {}
    findings = check_with(cur, data)
    assert "cards.contract_version" in warnings(findings)
    assert "cards.missing_field" in errors(findings)


def test_units_missing_is_a_warning_and_unit_check_falls_back_to_groups(cur):
    for p in (cur / "units").glob("*.json"):
        p.unlink()
    write_cards(cur, clean_cards())
    assert errors(cards.check_item(cur, MAIN)) == []            # check_item keeps load-time errors only
    assert "cards.units_missing" in warnings(cards.check(cur, [MAIN]))


# ---------------------------------------------------------------------------
# ids, enums, references
# ---------------------------------------------------------------------------


def test_id_format_wrong_kind_variant_and_duplicate(cur):
    data = clean_cards()
    data["cards"][0]["id"] = f"{MAIN}#q1"
    data["cards"][1]["id"] = f"{MAIN}#k2v1"
    data["cards"][2]["id"] = f"{PREREQ}#k3"
    data["cards"][4]["id"] = data["cards"][3]["id"]
    found = errors(check_with(cur, data))
    assert found.count("cards.id_format") == 3 and found.count("cards.duplicate_id") == 1


def test_enum_violations(cur):
    data = clean_cards()
    data["cards"][0]["card_type"] = "flashcard"
    data["cards"][1]["lens"] = "trivia"
    assert errors(check_with(cur, data)).count("cards.enum") == 2


def test_unresolved_references(cur):
    data = clean_cards()
    data["cards"][0]["lo_id"] = f"{PREREQ}#lo1"
    data["cards"][1]["unit_ids"] = ["u:arch:0:1"]
    data["cards"][2]["unit_ids"] = []
    data["cards"][3]["page_index"] = 4
    found = errors(check_with(cur, data))
    assert {"cards.unknown_lo", "cards.unknown_unit", "cards.missing_field", "cards.page_index"} <= set(found)


def test_unit_defined_in_group_but_not_in_units_files(cur):
    groups_path = cur / "groups.json"
    groups = read_json(groups_path)
    groups["groups"][1]["unit_ids"].append("u:adr:9:9")
    write_json(groups_path, groups)
    data = clean_cards()
    data["cards"][0]["unit_ids"] = ["u:adr:9:9"]
    assert "cards.unknown_unit" in errors(check_with(cur, data))


def test_missing_front_back_card_data_and_source_reference(cur):
    data = clean_cards()
    data["cards"][0]["front"] = ""
    data["cards"][1]["back"] = "   "
    data["cards"][2]["card_data"] = None
    data["cards"][3]["source_reference"] = ""
    findings = check_with(cur, data)
    assert errors(findings).count("cards.missing_field") == 2 + 1  # front, back, card_data
    assert [f.location for f in findings if f.check_id == "cards.missing_field" and f.severity == "warning"] == [f"{MAIN}#k4"]
    # a card whose card_data is None still gets its type-specific shape check (ordering)
    assert "cards.ordering_items" in errors(findings)


# ---------------------------------------------------------------------------
# lesson-level budgets and coverage
# ---------------------------------------------------------------------------


def test_card_ceiling_rule():
    budget = {"cards_min": 4, "cards_max": 7}
    assert cards.card_ceiling(1200, budget) == 7     # cards_max caps the per-word rule
    assert cards.card_ceiling(560, budget) == 5      # floor(5.6)
    assert cards.card_ceiling(120, budget) == 4      # never below cards_min
    assert cards.card_ceiling(0, {}) == 4            # defaults from CARDS_RANGE


def test_count_below_min_and_above_ceiling(cur):
    data = clean_cards()
    data["cards"] = data["cards"][:3]
    found = check_with(cur, data)
    count = [f for f in found if f.check_id == "cards.count"]
    assert len(count) == 1 and "3 cards; budget allows 4..5" in count[0].message
    data = clean_cards()
    extra = copy.deepcopy(data["cards"][4])
    extra["id"] = f"{MAIN}#k6"
    extra["front"] = "A reviewer finds an accepted ADR with no consequences section. What can she no longer judge about the choice?"
    extra["back"] = "What the choice made harder and which follow-up work it created."
    data["cards"].append(extra)
    assert "cards.count" in errors(check_with(cur, data))


def test_count_ceiling_follows_lesson_words(cur):
    lesson_path = cur / "lessons" / item_id_to_filename(MAIN)
    lesson = read_json(lesson_path)
    for page in lesson["pages"]:
        page["markdown"] = " ".join(page["markdown"].split()[:100])  # 400 words -> ceiling 4
    write_json(lesson_path, lesson)
    found = [f for f in check_with(cur, clean_cards()) if f.check_id == "cards.count"]
    assert len(found) == 1 and "5 cards; budget allows 4..4" in found[0].message


def test_min_card_types(cur):
    data = clean_cards()
    for card in data["cards"][1:]:
        card["card_type"] = "concept"
        card["card_data"] = {}
    assert "cards.types" in errors(check_with(cur, data))


def test_generalization_angles_warning_when_one_lens(cur):
    data = clean_cards()
    for card in data["cards"]:
        card["lens"] = "recall"
    findings = check_with(cur, data)
    assert "cards.generalization_angles" in warnings(findings) and errors(findings) == []


def test_objective_uncovered(cur):
    data = clean_cards()
    data["cards"][4]["lo_id"] = f"{MAIN}#lo1"
    findings = check_with(cur, data)
    assert [f.location for f in findings if f.check_id == "cards.objective_uncovered"] == [f"{MAIN}#lo2"]


# ---------------------------------------------------------------------------
# front / back hygiene
# ---------------------------------------------------------------------------


def test_front_length_code_exempt(cur):
    data = clean_cards()
    long_front = "Which section of an ADR " + "that a long-lived team keeps next to its code " * 3 + "lists the forces?"
    data["cards"][0]["front"] = long_front
    assert "cards.front_length" in errors(check_with(cur, data))
    data = clean_cards()
    data["cards"][0]["card_type"] = "code"
    data["cards"][0]["front"] = "```python\n" + "\n".join(f"x{i} = {i}" for i in range(20)) + "\n```\nWhat does the last line assign?"
    data["cards"][0]["back"] = "x19 = 19"
    assert "cards.front_length" not in ids(check_with(cur, data))


def test_back_length_by_type(cur):
    data = clean_cards()
    data["cards"][0]["back"] = "The context, the decision and the consequences, each written as a short paragraph a reader can skim quickly."
    data["cards"][4]["back"] = " ".join(["forces"] * 26)
    found = errors(check_with(cur, data))
    assert found.count("cards.back_length") == 2
    data = clean_cards()
    data["cards"][2]["back"] = " ".join(["step"] * 40)  # ordering allows 40
    assert "cards.back_length" not in ids(check_with(cur, data))


def test_is_compound_front_heuristic():
    assert cards.is_compound_front("What is an ADR and why is it kept next to the code?")
    assert cards.is_compound_front("Name the state a replaced ADR takes as well as the state it starts in.")
    assert not cards.is_compound_front("Which of these belongs in context and consequences?")
    assert not cards.is_compound_front("What three sections does every ADR record?")
    assert not cards.is_compound_front("")


def test_compound_front_warning_and_exemptions(cur):
    data = clean_cards()
    data["cards"][0]["front"] = "What does an ADR record and why is it kept with the code?"
    findings = check_with(cur, data)
    assert [f.location for f in findings if f.check_id == "cards.compound_front"] == [f"{MAIN}#k1"]
    data = clean_cards()
    data["cards"][0]["card_type"] = "comparison"
    data["cards"][0]["front"] = "How do the context and the consequences sections differ in what they describe?"
    data["cards"][0]["back"] = "Context lists the forces before the choice; consequences list its effects after."
    assert "cards.compound_front" not in ids(check_with(cur, data))


def test_pronoun_front(cur):
    data = clean_cards()
    data["cards"][0]["front"] = "Why this matters for later readers?"
    assert "cards.pronoun_front" in errors(check_with(cur, data))


def test_yes_no_front_applies_to_non_cloze_only(cur):
    data = clean_cards()
    data["cards"][0]["front"] = "Does every ADR record its consequences?"
    data["cards"][1]["front"] = "Is a replaced ADR marked ___ rather than deleted."
    findings = check_with(cur, data)
    assert [f.location for f in findings if f.check_id == "cards.yes_no_front"] == [f"{MAIN}#k1"]


def test_give_example_front(cur):
    data = clean_cards()
    data["cards"][0]["front"] = "Give an example of a force an ADR's context section lists."
    assert "cards.give_example_front" in errors(check_with(cur, data))


def test_front_back_overlap_helper():
    assert cards.front_back_overlap("What three sections does every ADR record?", "Context, decision, consequences.") == 0.0
    assert cards.front_back_overlap("Which section names the forces behind the choice?", "The forces behind the choice") == 1.0
    assert cards.front_back_overlap("anything", "") == 0.0


def test_overlap_error(cur):
    data = clean_cards()
    data["cards"][0]["front"] = "Which part of the ADR names the context and the consequences of the decision?"
    data["cards"][0]["back"] = "The context and consequences"
    findings = check_with(cur, data)
    assert "cards.overlap" in errors(findings) and "cards.answer_on_front" not in ids(findings)


def test_answer_on_front(cur):
    data = clean_cards()
    data["cards"][1]["front"] = "A superseded ADR is marked ___ rather than deleted."
    data["cards"][2]["front"] = "Order these: proposed, accepted, superseded."
    found = errors(check_with(cur, data))
    assert found.count("cards.answer_on_front") == 2 and "cards.overlap" not in found


def test_shared_answer_across_backs_and_cloze_blank(cur):
    data = clean_cards()
    data["cards"][4]["back"] = "Context, decision, consequences."
    findings = check_with(cur, data)
    shared = [f for f in findings if f.check_id == "cards.shared_answer"]
    assert len(shared) == 1 and shared[0].location == f"{MAIN}#k1, {MAIN}#k5"
    data = clean_cards()
    data["cards"][0]["back"] = "Superseded"  # same as the cloze blank_answer of k2
    assert "cards.shared_answer" in errors(check_with(cur, data))


# ---------------------------------------------------------------------------
# cloze / ordering / discrimination / comparison
# ---------------------------------------------------------------------------


def test_cloze_blank_count(cur):
    data = clean_cards()
    data["cards"][1]["front"] = "An ADR replaced by a newer decision is marked ___ rather than ___."
    assert "cards.cloze_blank" in errors(check_with(cur, data))
    data["cards"][1]["front"] = "An ADR replaced by a newer decision is marked rather than deleted."
    assert "cards.cloze_blank" in errors(check_with(cur, data))


def test_cloze_length(cur):
    data = clean_cards()
    data["cards"][1]["front"] = ("An ADR that a team replaces by a newer decision after months of discussion in several design "
                                 "reviews is marked ___ rather than deleted from the repository.")
    assert "cards.cloze_length" in errors(check_with(cur, data))


def test_cloze_answer_missing_or_stopword(cur):
    data = clean_cards()
    del data["cards"][1]["card_data"]["blank_answer"]
    assert "cards.cloze_answer" in errors(check_with(cur, data))
    data = clean_cards()
    data["cards"][1]["card_data"]["blank_answer"] = "the"
    data["cards"][1]["back"] = "the"
    assert "cards.cloze_answer" in errors(check_with(cur, data))


def test_cloze_hint_length_and_shared_stem(cur):
    data = clean_cards()
    data["cards"][1]["card_data"]["hint"] = "the lifecycle status word used after replacement"
    assert "cards.cloze_hint" in errors(check_with(cur, data))
    data["cards"][1]["card_data"]["hint"] = "supersedes the old"
    assert "cards.cloze_hint" in errors(check_with(cur, data))
    data["cards"][1]["card_data"]["hint"] = 7
    assert "cards.cloze_hint" in errors(check_with(cur, data))
    del data["cards"][1]["card_data"]["hint"]
    assert "cards.cloze_hint" not in ids(check_with(cur, data))


def test_ordering_items_range_and_permutation(cur):
    data = clean_cards()
    data["cards"][2]["card_data"]["items"] = ["proposed"]
    assert "cards.ordering_items" in errors(check_with(cur, data))
    data["cards"][2]["card_data"]["items"] = [f"step {i}" for i in range(6)]
    data["cards"][2]["card_data"]["correct_order"] = list(range(6))
    assert "cards.ordering_items" in errors(check_with(cur, data))
    data = clean_cards()
    data["cards"][2]["card_data"]["correct_order"] = [2, 0, 0]
    assert "cards.ordering_permutation" in errors(check_with(cur, data))
    data["cards"][2]["card_data"]["correct_order"] = [2, 0, True]
    assert "cards.ordering_permutation" in errors(check_with(cur, data))
    data["cards"][2]["card_data"]["correct_order"] = [0, 1, 2]
    findings = check_with(cur, data)
    assert "cards.ordering_unshuffled" in warnings(findings) and errors(findings) == []


def test_discrimination_options_and_correct_index(cur):
    data = clean_cards()
    data["cards"][3]["card_data"]["options"] = data["cards"][3]["card_data"]["options"][:2]
    assert "cards.discrimination_options" in errors(check_with(cur, data))
    data = clean_cards()
    data["cards"][3]["card_data"]["correct_index"] = 4
    assert "cards.correct_index" in errors(check_with(cur, data))


def test_comparison_terms_warning(cur):
    data = clean_cards()
    data["cards"][4]["card_type"] = "comparison"
    data["cards"][4]["front"] = "What separates the two optional sections of an ADR?"
    findings = check_with(cur, data)
    assert "cards.comparison_terms" in warnings(findings)
    data["cards"][4]["front"] = "What separates the context section from the consequences section of an ADR, context vs consequences?"
    assert "cards.comparison_terms" not in ids(check_with(cur, data))


# ---------------------------------------------------------------------------
# dedup
# ---------------------------------------------------------------------------


def test_dedup_within_lesson(cur):
    data = clean_cards()
    data["cards"][4]["front"] = "What three sections does every ADR contain?"   # ratio 0.89
    findings = check_with(cur, data)
    assert [f.location for f in findings if f.check_id == "cards.duplicate"] == [f"{MAIN}#k1, {MAIN}#k5"]
    data["cards"][4]["front"] = "What three items does an ADR record?"           # ratio 0.79
    findings = check_with(cur, data)
    assert "cards.near_duplicate" in warnings(findings) and "cards.duplicate" not in ids(findings)


def _neighbour_cards(lesson_id: str, front: str) -> dict:
    return {"contract_version": CONTRACT_VERSION, "lesson_id": lesson_id, "revision": 0,
            "cards": [{"id": f"{lesson_id}#k1", "card_type": "concept", "front": front, "back": "x", "card_data": {},
                       "lo_id": f"{lesson_id}#lo1", "unit_ids": [], "page_index": 0, "source_reference": "", "lens": "recall"}],
            "lessons_learned": []}


def test_dedup_against_prerequisite_and_same_module_neighbours(cur):
    write_cards(cur, _neighbour_cards(PREREQ, "What three sections does every ADR contain?"), PREREQ)
    write_cards(cur, _neighbour_cards(SIBLING, "What three items does an ADR record?"), SIBLING)
    findings = check_with(cur, clean_cards())
    assert [f.location for f in findings if f.check_id == "cards.duplicate"] == [f"{MAIN}#k1, {PREREQ}#k1"]
    assert [f.location for f in findings if f.check_id == "cards.near_duplicate"] == [f"{MAIN}#k1, {SIBLING}#k1"]


def test_dedup_ignores_non_neighbours(cur):
    groups_path = cur / "groups.json"
    groups = read_json(groups_path)
    groups["groups"][2]["module_id"] = "m:elsewhere"             # sibling leaves the module
    groups["groups"][1]["prerequisites"][0]["status"] = "rejected"  # prerequisite edge dropped
    write_json(groups_path, groups)
    write_cards(cur, _neighbour_cards(PREREQ, "What three sections does every ADR contain?"), PREREQ)
    write_cards(cur, _neighbour_cards(SIBLING, "What three sections does every ADR contain?"), SIBLING)
    findings = check_with(cur, clean_cards())
    assert "cards.duplicate" not in ids(findings)


def test_dependent_lesson_counts_as_neighbour(cur):
    groups_path = cur / "groups.json"
    groups = read_json(groups_path)
    groups["groups"][2]["module_id"] = "m:elsewhere"
    groups["groups"][2]["prerequisites"][0]["status"] = "committed"  # sibling now depends on MAIN
    write_json(groups_path, groups)
    write_cards(cur, _neighbour_cards(SIBLING, "What three sections does every ADR contain?"), SIBLING)
    assert "cards.duplicate" in errors(check_with(cur, clean_cards()))


def test_invalid_neighbour_file_is_a_warning(cur):
    path = cur / "cards" / item_id_to_filename(PREREQ)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{broken", encoding="utf-8")
    findings = check_with(cur, clean_cards())
    assert errors(findings) == [] and "cards.neighbour_invalid" in warnings(findings)


# ---------------------------------------------------------------------------
# prepare
# ---------------------------------------------------------------------------


def test_prepare_writes_work_file(cur):
    written, findings = cards.prepare(cur)
    assert errors(findings) == []
    assert {p.name for p in written} == {item_id_to_filename(MAIN), item_id_to_filename(PREREQ), item_id_to_filename(SIBLING)}
    work = read_json(cur / "cards" / "work" / item_id_to_filename(MAIN))
    assert work["stage"] == "cards" and work["lesson_id"] == MAIN and work["revision"] == 0
    assert len(work["pages"]) == 4 and all(p["markdown"] for p in work["pages"]) and work["pages"][1]["unit_ids"] == ["u:adr:0:1"]
    assert work["budget"] == {"cards_min": 4, "cards_max": 5, "cards_ceiling": 5,
                              "lesson_words": work["lesson_words"], "cards_per_100_words_max": 1.0}
    assert 500 <= work["lesson_words"] < 600
    assert work["knowledge_levels"]["counts"] == {"fact": 0, "concept": 1, "procedure": 1, "principle": 1}
    assert work["knowledge_levels"]["suggested_card_types"]["procedure"] == ["application", "code", "ordering"]
    assert [lens["lens"] for lens in work["lens_checklist"]][:5] == ["attributes", "similarities_differences", "parts_wholes", "causes_effects", "significance"]
    assert {lens["applies_to"] for lens in work["lens_checklist"]} == {"generalization", "fact", "procedure"}
    assert [q["id"] for q in work["question_prompts"]] == [f"{MAIN}#q1", f"{MAIN}#q3"]
    assert work["terminology"][0]["term"] == "superseded"
    assert [m["id"] for m in work["map_items"]] == [f"{MAIN}#b1"]
    assert {u["id"] for u in work["units"]} == {"u:adr:0:1", "u:adr:0:2", "u:adr:1:1"} and work["units"][0]["source"] == "adr"
    assert work["generalization"].startswith("Recording the context") and len(work["objectives"]) == 2
    assert work["do_not_duplicate"] == [] and work["existing"] is None and work["lessons_learned"] == []
    assert work["instructions"].startswith("# Cards Rubric") and "cards.compound_front" in work["instructions"]


def test_prepare_do_not_duplicate_lists_neighbour_fronts(cur):
    write_cards(cur, _neighbour_cards(PREREQ, "What does an architect still own after delegating?"), PREREQ)
    write_cards(cur, _neighbour_cards(SIBLING, "Which forces does a context section list?"), SIBLING)
    cards.prepare(cur, [MAIN])
    work = read_json(cur / "cards" / "work" / item_id_to_filename(MAIN))
    assert work["do_not_duplicate"] == [
        {"lesson_id": PREREQ, "id": f"{PREREQ}#k1", "front": "What does an architect still own after delegating?"},
        {"lesson_id": SIBLING, "id": f"{SIBLING}#k1", "front": "Which forces does a context section list?"},
    ]


def test_prepare_force_existing_revision_and_lessons_learned(cur):
    data = clean_cards()
    data["lessons_learned"] = ["Shuffle ordering items"]
    write_cards(cur, data)
    cards.prepare(cur, [MAIN])
    _, findings = cards.prepare(cur, [MAIN])
    assert "cards.work_exists" in ids(findings, "info")
    cards.prepare(cur, [MAIN], force=True)
    work = read_json(cur / "cards" / "work" / item_id_to_filename(MAIN))
    assert work["revision"] == 1 and work["lessons_learned"] == ["Shuffle ordering items"]
    assert work["existing"]["lesson_id"] == MAIN


def test_prepare_warns_when_questions_missing(cur):
    (cur / "questions" / item_id_to_filename(MAIN)).unlink()
    _, findings = cards.prepare(cur, [MAIN])
    assert errors(findings) == [] and "cards.questions_missing" in warnings(findings)
    work = read_json(cur / "cards" / "work" / item_id_to_filename(MAIN))
    assert work["question_prompts"] == []


def test_prepare_refuses_outline_only_and_unknown_lesson(cur):
    lesson_path = cur / "lessons" / item_id_to_filename(SIBLING)
    lesson = read_json(lesson_path)
    lesson["pages"][0]["markdown"] = None
    write_json(lesson_path, lesson)
    written, findings = cards.prepare(cur, [SIBLING, "g:nowhere"])
    assert written == [] and {"cards.lesson_outline_only", "cards.unknown_lesson"} <= set(errors(findings))


def test_prepare_without_rubric_carries_a_note(cur, monkeypatch):
    monkeypatch.setattr(cards, "RUBRIC_PATH", cur / "no-such-rubric.md")
    _, findings = cards.prepare(cur, [MAIN])
    assert "cards.rubric_missing" in warnings(findings)
    work = read_json(cur / "cards" / "work" / item_id_to_filename(MAIN))
    assert "rubrics/cards.md not found" in work["instructions"]


def test_prepare_without_qualification_warns(cur):
    for p in (cur / "qualification").glob("*.json"):
        p.unlink()
    _, findings = cards.prepare(cur, [MAIN])
    assert "cards.qualification_missing" in warnings(findings)
    work = read_json(cur / "cards" / "work" / item_id_to_filename(MAIN))
    assert work["knowledge_levels"]["suggested_card_types"] == {}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_check_exit_codes_and_output(clean, capsys):
    cur, data = clean
    assert cards.main(["check", str(cur), "--lesson", MAIN]) == 0
    assert capsys.readouterr().out.strip().endswith("0 errors, 0 warnings")
    data["cards"][2]["card_data"]["correct_order"] = [0, 1, 2]
    write_cards(cur, data)
    assert cards.main(["check", str(cur), "--lesson", MAIN]) == 0
    out = capsys.readouterr().out
    assert f"[WARN] cards.ordering_unshuffled @ {MAIN}#k3:" in out and "0 errors, 1 warnings" in out
    assert cards.main(["check", str(cur), "--lesson", MAIN, "--strict"]) == 1
    data["cards"] = data["cards"][:2]
    write_cards(cur, data)
    assert cards.main(["check", str(cur), "--lesson", MAIN]) == 1
    assert "[ERROR] cards.count @" in capsys.readouterr().out


def test_cli_prepare(cur, capsys):
    assert cards.main(["prepare", str(cur), "--lesson", MAIN]) == 0
    assert "wrote cards/work/" in capsys.readouterr().out
    assert cards.main(["check", str(cur / "nope")]) == 2


def test_prepare_hands_the_anchor_cards_to_other_lessons(cur):
    write_cards(cur, clean_cards())
    write_json(cur / "lessons" / "anchor.json", {"lesson_id": MAIN})
    _, findings = cards.prepare(cur, [MAIN, PREREQ])
    assert errors(findings) == []
    assert read_json(cur / "cards" / "work" / item_id_to_filename(MAIN))["anchor"] is None
    anchor = read_json(cur / "cards" / "work" / item_id_to_filename(PREREQ))["anchor"]
    assert anchor["lesson_id"] == MAIN and anchor["cards"] == clean_cards()["cards"]
