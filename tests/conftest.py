"""Shared fixtures.

* ``curriculum_dir`` — a synthetic raw-pipeline mini-curriculum that passes every
  validator check. Tests inject violations by mutating the JSON files in the
  returned directory.
* ``lesson_curriculum_dir`` — a COMPLETE lesson-pipeline curriculum (docs/artifacts.md)
  built by running the real stage scripts (``qualify.prepare``, ``regroup.budget`` /
  ``commit-edges``, ``lessons.build``, ``critique.prepare`` / ``check``) over authored
  files, so that every stage ``check()`` and ``validate.py --stage all`` / ``upload``
  report zero errors. It is the integration test of the whole artifact contract:
  when a stage check starts to error, the fixture (or the module, when it violates
  the contract) needs fixing.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import _compat  # noqa: F401

from chunk import count_tokens
from contracts import (
    CONTRACT_VERSION,
    CRITERIA,
    PER_ITEM_CRITERIA,
    dump_json,
    item_id_to_filename,
    word_count,
)

# Section titles per synthetic source file. Leaf topics in the structure
# reference these 1:1, so coverage is complete by construction.
SOURCE_SECTIONS = {
    "widgets.json": [
        "Intro to Widgets",
        "Widget Anatomy",
        "Using Widgets",
        "Widget Patterns",
        "Advanced Widget Tuning",
    ],
    "gadgets.json": [
        "Gadget Basics",
        "Gadget Wiring",
        "Gadget Safety",
        "Gadget Diagnostics",
        "Gadget Performance",
    ],
}

# (depth-0 title, suggested_level, [leaf titles])
# Levels 1,1,2,2,3 -> 40/40/20, within ±15pts of the 30/40/30 target.
TOPIC_LAYOUT = [
    ("Widget Foundations", 1, ["Intro to Widgets", "Widget Anatomy"]),
    ("Working with Widgets", 1, ["Using Widgets", "Widget Patterns"]),
    ("Gadget Foundations", 2, ["Gadget Basics", "Gadget Wiring"]),
    ("Gadget Operations", 2, ["Gadget Safety", "Gadget Diagnostics"]),
    ("Advanced Tuning", 3, ["Advanced Widget Tuning", "Gadget Performance"]),
]


def make_text(target_tokens: int, seed: str = "concept") -> str:
    """Build filler prose of at least target_tokens tokens."""
    sentence = (
        f"The {seed} is explained here with enough detail that a learner can "
        f"follow each step, see a worked example, and connect it to practice. "
    )
    paragraph = sentence * 4
    text = paragraph
    while count_tokens(text) < target_tokens:
        text += "\n\n" + paragraph
    return text


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def build_curriculum(root: Path) -> Path:
    """Create a complete, validator-clean curriculum under root."""
    cur = root / "mini"
    extracted = cur / "extracted"
    extracted.mkdir(parents=True)

    # --- extracted/*.json ---
    for fname, titles in SOURCE_SECTIONS.items():
        write_json(extracted / fname, {
            "source_type": "pdf",
            "source_path": f"/src/{fname.replace('.json', '.pdf')}",
            "title": fname.replace(".json", ""),
            "sections": [
                {
                    "title": title,
                    "content": make_text(550, seed=title.lower()),
                    "depth": 1,
                    "metadata": {"page_start": 10 * i + 1, "page_end": 10 * i + 10},
                }
                for i, title in enumerate(titles)
            ],
        })

    # --- manifest.json ---
    write_json(cur / "manifest.json", {
        "name": "Mini Curriculum",
        "domain": "mini",
        "domain_family": "mini",
        "variant": "extensive",
        "pipeline": "raw",
        "description": "Synthetic fixture curriculum",
        "sources": [
            {"type": "pdf", "path": "/src/widgets.pdf", "title": "Widgets"},
            {"type": "pdf", "path": "/src/gadgets.pdf", "title": "Gadgets"},
        ],
        "created_at": "2026-01-01T00:00:00Z",
        "created_by": "test",
    })

    # --- exploration.json ---
    write_json(cur / "exploration.json", {
        "concepts": [
            {"name": "Widgets", "sources": ["widgets.json"], "coverage": "strong"},
            {"name": "Gadgets", "sources": ["gadgets.json"], "coverage": "strong"},
        ],
        "gaps": [
            {"concept": "Widget History", "severity": "nice-to-have",
             "suggestion": "Optional background reading"},
        ],
        "concept_order": ["Widgets", "Gadgets"],
        "coverage_summary": "Both areas well covered.",
        "per_source_analysis": {
            fname: {
                "section_count": len(titles),
                "key_concepts": titles[:2],
                "quality": 4,
                "notes": "Synthetic source",
            }
            for fname, titles in SOURCE_SECTIONS.items()
        },
    })

    # --- structure.json ---
    structure = []
    for i, (d0_title, level, leaves) in enumerate(TOPIC_LAYOUT):
        children = []
        for j, leaf_title in enumerate(leaves):
            children.append({
                "title": leaf_title,
                "depth": 1,
                "sort_order": j,
                "description": f"Covers {leaf_title.lower()}",
                "source_sections": [leaf_title],
                "learning_objectives": [
                    {"text": f"Define the key terms of {leaf_title.lower()}",
                     "bloom_level": "remember"},
                    {"text": f"Explain how {leaf_title.lower()} works",
                     "bloom_level": "understand"},
                ],
                "children": [],
            })
        topic = {
            "title": d0_title,
            "depth": 0,
            "sort_order": i,
            "description": f"Major area: {d0_title.lower()}",
            "suggested_level": level,
            "learning_objectives": [
                {"text": f"Summarize the scope of {d0_title.lower()}",
                 "bloom_level": "understand"},
            ],
            "children": children,
        }
        structure.append(topic)
    # One valid prerequisite: Gadget Foundations depends on Widget Foundations
    structure[2]["prerequisites"] = [
        {"topic": "Widget Foundations", "strength": "required"},
    ]
    write_json(cur / "structure.json", structure)

    # --- chunks.json (one ~550-token chunk per leaf) ---
    chunks = []
    for d0_title, _, leaves in TOPIC_LAYOUT:
        for leaf_title in leaves:
            content = make_text(550, seed=leaf_title.lower())
            chunks.append({
                "content": content,
                "token_count": count_tokens(content),
                "has_code": False,
                "topic_path": [d0_title, leaf_title],
                "chunk_index": 0,
                "page_number": 1,
            })
    write_json(cur / "chunks.json", chunks)

    # --- exercises.json (one per depth-0 group) ---
    exercises = []
    for d0_title, _, leaves in TOPIC_LAYOUT:
        exercises.append({
            "topic_path": [d0_title, leaves[0]],
            "exercises": [{
                "title": f"Apply {leaves[0]}",
                "problem_statement": f"Work through a scenario using {leaves[0].lower()}.",
                "hints": ["Start from the definition", "Sketch the steps",
                          "Check the edge cases"],
                "expected_solution": "A step-by-step application of the concept.",
                "common_mistakes": ["Skipping the setup step"],
                "bloom_level": "apply",
                "difficulty": 1,
            }],
        })
    write_json(cur / "exercises.json", exercises)

    # --- review.json ---
    write_json(cur / "review.json", {
        "total_topics": 15,
        "total_chunks": len(chunks),
        "quality_concerns": [],
        "approved": True,
        "validation": {"errors": 0, "warnings": 0},
        "reviewed_at": "2026-01-01T00:00:00Z",
    })

    # --- condensation_plan.json (detailed tier only; ~10% budget) ---
    write_json(cur / "condensation_plan.json", {
        "detailed": [
            {
                "title": "Widget Essentials",
                "description": "Condensed widget coverage",
                "suggested_level": 1,
                "children": [
                    {
                        "title": "Intro to Widgets",
                        "description": "Widget basics",
                        "condensation_strategy": "keep",
                        "source_children": ["Intro to Widgets"],
                    },
                    {
                        "title": "Widget Practice",
                        "description": "Merged practice content",
                        "condensation_strategy": "merge",
                        "source_children": ["Using Widgets", "Widget Patterns"],
                        "content": make_text(120, seed="widget practice"),
                    },
                ],
            },
        ],
    })

    return cur


@pytest.fixture
def curriculum_dir(tmp_path: Path) -> Path:
    """A complete synthetic curriculum that passes all validator checks."""
    return build_curriculum(tmp_path)


# ===========================================================================
# Lesson-mode fixture — a complete curriculum, clean at every stage check
# ===========================================================================
#
# Two sources (peaa, clean-arch), two lessons: "Domain Model" (DM, foundation,
# no prerequisite) and "Service Layer" (SL, building_block, requires DM). The
# unit texts, page prose and briefs are shared with tests/test_lessons.py so the
# verbatim / term-density tuning of its page generators carries over; every
# other authored artifact (qualification, concepts, groups, questions, cards,
# critique verdicts) is defined here. Scripts fill the script-owned parts.

LESSON_DM = "g:domain-model"
LESSON_SL = "g:service-layer"
LESSON_IMAGE_ID = "p142_img0"
LESSON_AREA = "a:domain-logic"
LESSON_MODULE = "m:patterns"
# The regroup gate needs >= 2 concept labels in a generalization; the DM recap
# page (from test_lessons) restates it verbatim, so the page is patched too.
LESSON_DM_GENERALIZATION = (
    "A Domain Model binds behaviour to data so rules live with the objects they govern "
    "instead of in a Transaction Script."
)

# One extracted section per knowledge unit: (section title, unit key, test_lessons unit id
# or None for a filler unit whose text is in LESSON_FILLER_TEXTS). qualify.prepare then
# yields exactly one unit per section, u:<source>:<section>:0. The filler units give each
# group the source mass the regroup gate expects (GROUP_TOKENS) and are cited by the briefs
# but by no lesson page, so the page generators' verbatim tuning is untouched.
LESSON_SECTIONS = {
    "peaa": [
        ("Service layer boundary", "sl_def", "u:peaa:12:0"),
        ("Application and domain logic", "sl_logic", "u:peaa:12:1"),
        ("Revenue recognition service", "sl_code", "u:peaa:12:2"),
        ("Anemic domain model", "sl_anemic", "u:peaa:12:3"),
        ("Transaction boundaries", "sl_transactions", None),
        ("Keeping services thin", "sl_thin", None),
        ("Domain model definition", "dm_def", "u:peaa:10:0"),
        ("When a domain model pays off", "dm_tradeoff", "u:peaa:10:1"),
        ("Objects that decide", "dm_objects", None),
        ("Changing a rule", "dm_change", None),
        ("The transaction script shape", "dm_script", None),
        ("Paying for the model", "dm_cost", None),
    ],
    "clean-arch": [
        ("Use cases between layers", "ca_usecases", "u:clean-arch:4:0"),
        ("Use cases and delivery", "ca_delivery", "u:clean-arch:4:1"),
    ],
}
LESSON_FILLER_TEXTS = {
    "sl_transactions": (
        "The service layer is also where the application fixes the transaction boundary of an operation: it begins "
        "the unit of work, asks the domain objects that must change together to do their part, and then commits or "
        "rolls back as a whole. The domain objects themselves stay unaware of where a transaction starts or ends, "
        "which keeps them reusable from any operation that needs them."
    ),
    "sl_thin": (
        "A service layer should stay thin. When a service method begins to accumulate conditionals about customers, "
        "contracts or prices, the business rules have drifted out of the domain model and the service has become the "
        "one place where every change must be made. The remedy is to push each such decision back into the object "
        "whose data it reads, leaving the service with sequencing and plumbing only."
    ),
    "dm_objects": (
        "In a rich model an invoice knows how to work out its own late fee, an order settles whether a discount "
        "applies to it, and a customer watches its own credit limit. Each of these decisions sits in the object that "
        "holds the data the decision needs, so reading the model feels like reading a description of the business "
        "rather than a list of database tables."
    ),
    "dm_change": (
        "When a pricing rule is revised, the developer opens the object that prices an order and edits one method. "
        "Nothing else in the system has to be searched, because the rule was never copied into the procedures that "
        "call the object; they only ever asked the order for its price and trusted the answer it returned."
    ),
    "dm_script": (
        "A transaction script organises the logic as one procedure per request: it loads the rows it needs, applies "
        "its checks in sequence and writes the result back. The shape is quick to write and easy to follow while the "
        "rules stay few, and it leaves the records it touches as plain holders of fields with no decisions of their own."
    ),
    "dm_cost": (
        "The first version of a domain model costs more than a script because the objects and their collaborations "
        "have to be designed before any rule can run. That investment is repaid when the rules begin to change, since "
        "every later revision touches a single object where a script would need a hunt through every path that uses "
        "the rule."
    ),
}

# Authored qualification per unit key: unit_type, knowledge_level, learning_step, [(concept, verbatim quote)].
# Tags are spread so no concept exceeds the too-broad share and none is thin (qualify.check).
LESSON_QUALIFICATION = {
    "sl_def": ("definition", "concept", "building_block",
               [("c:service-layer", "defines an application's boundary")]),
    "sl_logic": ("mechanism", "concept", "building_block", [("c:application-logic", "application logic")]),
    "sl_code": ("code", "procedure", "building_block", [("c:service-layer", "RecognitionService")]),
    "sl_anemic": ("pitfall", "principle", "building_block",
                  [("c:application-logic", "domain logic into the service layer")]),
    "sl_transactions": ("mechanism", "concept", "building_block", [("c:service-layer", "fixes the transaction boundary")]),
    "sl_thin": ("pitfall", "principle", "building_block",
                [("c:application-logic", "the business rules have drifted out of the domain model")]),
    "dm_def": ("definition", "concept", "foundation", [("c:domain-model", "object model of the domain")]),
    "dm_tradeoff": ("trade_off", "principle", "foundation",
                    [("c:transaction-script", "Transaction Script is enough"), ("c:domain-model", "Domain models are worth the effort")]),
    "dm_objects": ("example", "concept", "foundation", [("c:domain-model", "the object that holds the data the decision needs")]),
    "dm_change": ("mechanism", "concept", "foundation", [("c:domain-model", "edits one method")]),
    "dm_script": ("definition", "concept", "foundation", [("c:transaction-script", "one procedure per request")]),
    "dm_cost": ("trade_off", "principle", "foundation",
                [("c:domain-model", "costs more than a script"), ("c:transaction-script", "a script would need a hunt")]),
    "ca_usecases": ("definition", "concept", "building_block", [("c:application-logic", "orchestrate the flow of data")]),
    "ca_delivery": ("context", "concept", "building_block",
                    [("c:service-layer", "do not know how the data is presented")]),
}
_BLOOM_CEILING = {"fact": "understand", "concept": "analyze", "procedure": "apply", "principle": "evaluate"}

LESSON_CONCEPTS = [
    {"id": "c:domain-model", "label": "Domain Model", "aliases": ["rich domain model"],
     "description": "An object model of the business where each object owns its data and the behaviour that changes it.",
     "learning_step": "foundation", "depends_on": [], "coverage": "strong", "sources": ["peaa"]},
    {"id": "c:transaction-script", "label": "Transaction Script", "aliases": [],
     "description": "A single procedure that handles one request from start to finish.",
     "learning_step": "foundation", "depends_on": [], "coverage": "thin", "sources": ["peaa"]},
    {"id": "c:service-layer", "label": "Service Layer", "aliases": ["application service layer"],
     "description": "Application boundary coordinating operations.",
     "learning_step": "building_block", "depends_on": ["c:domain-model"], "coverage": "strong", "sources": ["peaa", "clean-arch"]},
    {"id": "c:application-logic", "label": "Application Logic", "aliases": [],
     "description": "Coordination duties of the application itself: transactions, ordering of calls, notifications.",
     "learning_step": "building_block", "depends_on": [], "coverage": "moderate", "sources": ["peaa", "clean-arch"]},
]
LESSON_CONCEPT_ORDER = ["c:domain-model", "c:transaction-script", "c:service-layer", "c:application-logic"]


class LessonFixtureError(AssertionError):
    """A stage check of the lesson fixture reported errors (lists them)."""


def _assert_clean(label: str, findings) -> None:
    errors = [f for f in findings if f.severity == "error"]
    if errors:
        lines = "\n".join(f"  [{f.severity}] {f.check_id} @ {f.location}: {f.message}" for f in errors)
        raise LessonFixtureError(f"{label}: {len(errors)} error(s)\n{lines}")


def _lesson_texts() -> dict[str, str]:
    """Unit key -> verbatim text, taken from tests/test_lessons.py (single source of truth)."""
    import test_lessons as tl

    by_old_id = {uid: text for units in tl.UNITS.values() for uid, _sec, _title, _block, text in units}
    return {key: (by_old_id[old_id] if old_id else LESSON_FILLER_TEXTS[key])
            for sections in LESSON_SECTIONS.values() for _title, key, old_id in sections}


def _write_extracted(cur: Path, texts: dict[str, str]) -> None:
    images_dir = cur / "extracted" / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    (images_dir / f"{LESSON_IMAGE_ID}.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    image = {"id": LESSON_IMAGE_ID, "local_path": f"images/{LESSON_IMAGE_ID}.png",
             "alt_text": "Service layer boundary diagram", "mime_type": "image/png"}
    for source, sections in LESSON_SECTIONS.items():
        data = {
            "source_type": "web",
            "source_path": f"https://example.test/{source}",
            "title": source.upper(),
            "sections": [
                {"title": title, "content": texts[key], "depth": 1, "metadata": {},
                 "images": [image] if key == "sl_def" else []}
                for title, key, _old in sections
            ],
            "images": [image],
        }
        write_json(cur / "extracted" / f"{source}.json", data)


def _write_qualification(cur: Path, U: dict[str, str]) -> None:
    for source, sections in LESSON_SECTIONS.items():
        entries = {}
        for index, (_title, key, _old) in enumerate(sections):
            unit_type, level, step, tags = LESSON_QUALIFICATION[key]
            entries[U[key]] = {
                "concepts": [{"concept_id": cid, "quote": quote} for cid, quote in tags],
                "unit_type": unit_type, "knowledge_level": level, "learning_step": step,
                "bloom_ceiling": _BLOOM_CEILING[level], "bloom_ceiling_reason": None,
                "quality": 4, "keep": True, "drop_reason": None, "depends_on": [], "notes": "",
            }
        write_json(cur / "qualification" / f"{source}.json", {
            "contract_version": CONTRACT_VERSION, "source": source, "units": entries,
            "gleaning": {str(i): {"missed": False, "note": ""} for i in range(len(sections))},
        })
    write_json(cur / "concepts.json", {
        "contract_version": CONTRACT_VERSION, "concepts": LESSON_CONCEPTS, "gaps": [],
        "ordering": LESSON_CONCEPT_ORDER, "merges": [],
    })


def _kps(gid: str, entries: list[tuple[str, list[str]]]) -> list[dict]:
    return [{"id": f"{gid}#kp{i + 1}", "title": title, "unit_ids": units} for i, (title, units) in enumerate(entries)]


def _los(gid: str, entries: list[tuple[str, list[str], list[str], list[str]]]) -> list[dict]:
    return [{"id": f"{gid}#lo{i + 1}", "text": text, "bloom_level": "understand", "concept_ids": concepts,
             "unit_ids": units, "knowledge_point_ids": kps} for i, (text, concepts, units, kps) in enumerate(entries)]


def _watch(gid: str, entries: list[tuple[str, str, list[str]]]) -> list[dict]:
    return [{"id": f"{gid}#w{i + 1}", "kind": kind, "text": text, "unit_ids": units}
            for i, (kind, text, units) in enumerate(entries)]


def lesson_groups(U: dict[str, str]) -> dict:
    """groups.json as the agent authors it (status proposed, budget None)."""
    import test_lessons as tl

    DM, SL = LESSON_DM, LESSON_SL
    return {
        "contract_version": CONTRACT_VERSION,
        "areas": [{"id": LESSON_AREA, "title": "Domain Logic", "sort_order": 0, "suggested_level": 1,
                   "modules": [{"id": LESSON_MODULE, "title": "Patterns", "sort_order": 0}]}],
        "groups": [
            {"id": DM, "title": "Domain Model", "area_id": LESSON_AREA, "module_id": LESSON_MODULE,
             "sort_order": 0, "concept_ids": ["c:domain-model", "c:transaction-script"],
             "unit_ids": [U["dm_def"], U["dm_tradeoff"], U["dm_objects"], U["dm_change"], U["dm_script"], U["dm_cost"]],
             "related_unit_ids": [],
             "learning_step": "foundation", "suggested_level": 1,
             "generalization": LESSON_DM_GENERALIZATION,
             "knowledge_points": _kps(DM, [("Behaviour with data", [U["dm_def"], U["dm_change"]]),
                                           ("Objects mirror the business", [U["dm_def"], U["dm_objects"]]),
                                           ("When it pays off", [U["dm_tradeoff"], U["dm_script"], U["dm_cost"]])]),
             "objectives": _los(DM, [
                 ("Explain how a domain model binds rules to objects", ["c:domain-model"], [U["dm_def"]], [f"{DM}#kp1", f"{DM}#kp2"]),
                 ("Judge when a domain model beats a transaction script", ["c:domain-model", "c:transaction-script"], [U["dm_tradeoff"]], [f"{DM}#kp3"]),
             ]),
             "watch": _watch(DM, [("misconception", "Domain models are just data classes", [U["dm_def"]]),
                                  ("misconception", "Always use a domain model", [U["dm_tradeoff"]])]),
             "duplicates": [], "conflicts": [], "prerequisites": [], "consequential": False,
             "rationale": "Foundation every later pattern builds on.", "budget": None},
            {"id": SL, "title": "Service Layer", "area_id": LESSON_AREA, "module_id": LESSON_MODULE,
             "sort_order": 1, "concept_ids": ["c:service-layer", "c:application-logic"],
             "unit_ids": [U["sl_def"], U["sl_logic"], U["sl_code"], U["sl_anemic"], U["sl_transactions"], U["sl_thin"],
                          U["ca_usecases"], U["ca_delivery"]],
             "related_unit_ids": [],
             "learning_step": "building_block", "suggested_level": 2,
             "generalization": tl.GENERALIZATION[tl.SL],
             "knowledge_points": _kps(SL, [("The boundary", [U["sl_def"], U["ca_usecases"]]),
                                           ("Application vs domain logic", [U["sl_logic"], U["sl_code"], U["sl_anemic"], U["sl_transactions"], U["sl_thin"]]),
                                           ("Delivery independence", [U["ca_delivery"]])]),
             "objectives": _los(SL, [
                 ("Describe what a service layer coordinates", ["c:service-layer"], [U["sl_def"]], [f"{SL}#kp1"]),
                 ("Distinguish application logic from domain logic", ["c:application-logic"], [U["sl_logic"]], [f"{SL}#kp2", f"{SL}#kp3"]),
             ]),
             "watch": _watch(SL, [("misconception", "Services should hold the business rules", [U["sl_anemic"]]),
                                  ("boundary", "Use cases know the UI", [U["ca_delivery"]])]),
             "duplicates": [], "conflicts": [],
             "prerequisites": [{"group_id": DM, "strength": "required", "evidence_unit_ids": [U["sl_def"]],
                                "reason": "assumes the domain model", "status": "proposed"}],
             "consequential": False, "rationale": "Coordination layer over the domain model.", "budget": None},
        ],
        "dropped": [],
    }


def _brief_item(gid: str, n: int, kind: str, text: str, units: list[str], quote: str, lo: list[int], kp: int | None,
                concepts: list[str] = ()) -> dict:
    return {"id": f"{gid}#b{n}", "kind": kind, "text": text, "unit_ids": units, "quote": quote,
            "confidence": "attributed", "concept_ids": list(concepts), "lo_ids": [f"{gid}#lo{i}" for i in lo],
            "knowledge_point_id": f"{gid}#kp{kp}" if kp else None}


def lesson_briefs(U: dict[str, str]) -> dict[str, dict]:
    DM, SL = LESSON_DM, LESSON_SL
    dm = {
        "contract_version": CONTRACT_VERSION, "group_id": DM, "revision": 0,
        "overview": "Why a domain model binds behaviour to data and when that structure pays off compared with a plain script.",
        "items": [
            _brief_item(DM, 1, "map", "A domain model couples the rules of the business to the objects that hold its data.",
                        [U["dm_def"]], "incorporates both behaviour and data", [1], 1, ["c:domain-model"]),
            _brief_item(DM, 2, "map", "Each object stands for something the business recognises and owns its rules.",
                        [U["dm_def"]], "carries the rules that apply to it", [1], 2),
            _brief_item(DM, 3, "map", "Rich models repay their cost only when rules are complex and shift often.",
                        [U["dm_tradeoff"]], "worth the effort when the business rules are complex", [2], 3),
            _brief_item(DM, 4, "watch", "For simple validation a transaction script suffices.",
                        [U["dm_tradeoff"]], "a Transaction Script is enough", [2], 3, ["c:transaction-script"]),
            _brief_item(DM, 5, "watch", "A domain model is more than a set of data holders: each object brings its own behaviour.",
                        [U["dm_def"]], "incorporates both behaviour and data", [1], 1),
            _brief_item(DM, 6, "map", "Invoices, orders and customers each settle the questions that concern their own data.",
                        [U["dm_objects"]], "the object that holds the data the decision needs", [1], 2),
            _brief_item(DM, 7, "map", "Revising a rule means editing one method on the owning object, with nothing else to search.",
                        [U["dm_change"]], "edits one method", [1], 1),
            _brief_item(DM, 8, "map", "A script keeps one procedure per request and leaves its records as plain holders of fields.",
                        [U["dm_script"]], "one procedure per request", [2], 3, ["c:transaction-script"]),
            _brief_item(DM, 9, "watch", "The model's upfront design cost is only repaid once rules begin to change.",
                        [U["dm_cost"]], "costs more than a script", [2], 3),
        ],
        "examples": [],
        "terminology": [{"term": "transaction script", "definition": "A procedure that handles one request end to end.",
                         "unit_id": U["dm_tradeoff"]}],
        "lessons_learned": [],
    }
    sl = {
        "contract_version": CONTRACT_VERSION, "group_id": SL, "revision": 0,
        "overview": "The service layer as the entry point that coordinates while the domain model keeps the rules.",
        "items": [
            _brief_item(SL, 1, "map", "The service layer is the entry point and lists the operations a client may invoke.",
                        [U["sl_def"]], "defines an application's boundary", [1], 1, ["c:service-layer"]),
            _brief_item(SL, 2, "map", "Application logic covers coordination duties such as transactions and notifications.",
                        [U["sl_logic"]], "coordinating transactions and notifying other systems", [1, 2], 2, ["c:application-logic"]),
            _brief_item(SL, 3, "map", "Use cases stay unaware of the channel that delivered a request.",
                        [U["ca_delivery"]], "do not know how the data is presented", [2], 3),
            _brief_item(SL, 4, "watch", "Moving domain rules into services hollows the entities into an anemic domain model.",
                        [U["sl_anemic"]], "anemic domain model", [2], 2),
            _brief_item(SL, 5, "watch", "The service layer is a boundary for coordination, not the home of the business rules themselves.",
                        [U["sl_def"], U["sl_anemic"]], "Clients call the service layer", [1], 1),
            _brief_item(SL, 6, "map", "Use cases drive the entities to apply their business rules in service of one goal.",
                        [U["ca_usecases"]], "direct them to apply their enterprise business rules", [2], 3),
            _brief_item(SL, 7, "map", "The service marks the transaction boundary, committing or rolling back the objects that change together.",
                        [U["sl_transactions"]], "commits or rolls back as a whole", [2], 2, ["c:service-layer"]),
            _brief_item(SL, 8, "watch", "A service that collects conditionals about contracts and prices has quietly taken over the rules.",
                        [U["sl_thin"]], "the business rules have drifted out of the domain model", [2], 2),
        ],
        "examples": [{"id": f"{SL}#ex1", "unit_id": U["sl_code"], "language": "java", "note": "Revenue recognition service"}],
        "terminology": [{"term": "anemic domain model", "definition": "Objects that carry data but no behaviour.",
                         "unit_id": U["sl_anemic"]}],
        "lessons_learned": [],
    }
    return {DM: dm, SL: sl}


def _page(index: int, role: str, title: str, lo: list[str], kp: str | None, units: list[str], briefs: list[str],
          markdown: str) -> dict:
    return {"index": index, "role": role, "title": title, "lo_ids": lo, "knowledge_point_id": kp,
            "unit_ids": units, "brief_item_ids": briefs, "markdown": markdown}


def _lesson_file(gid: str, group: dict, title: str, description: str, key_terms: list[str],
                 prerequisites: list[str], pages: list[dict]) -> dict:
    import lessons

    words = sum(word_count(lessons._prose(p["markdown"])) for p in pages)
    return {
        "contract_version": CONTRACT_VERSION, "lesson_id": gid, "group_id": gid, "revision": 0,
        "title": title, "description": description, "area_id": group["area_id"], "module_id": group["module_id"],
        "suggested_level": group["suggested_level"], "estimated_minutes": max(1, round(words / 238)),
        "key_terms": key_terms,
        "objectives": [{"id": o["id"], "text": o["text"], "bloom_level": o["bloom_level"]} for o in group["objectives"]],
        "prerequisites": prerequisites, "pages": pages, "outline_approved": True, "lessons_learned": [],
    }


def lesson_lessons(U: dict[str, str], groups: dict[str, dict]) -> dict[str, dict]:
    """Two full lessons: the page prose comes from tests/test_lessons.py's generators
    (original, non-verbatim text tuned to the metric bands)."""
    import test_lessons as tl

    DM, SL = LESSON_DM, LESSON_SL
    dm_md = tl.domain_model_pages()
    dm_md[4] = dm_md[4].replace(tl.GENERALIZATION[tl.DM], LESSON_DM_GENERALIZATION)
    dm_pages = [
        _page(0, "orientation", "Why rules belong with data", [f"{DM}#lo1"], None, [U["dm_def"]], [f"{DM}#b1"], dm_md[0]),
        _page(1, "core", "Behaviour with data", [f"{DM}#lo1"], f"{DM}#kp1", [U["dm_def"]], [f"{DM}#b1"], dm_md[1]),
        _page(2, "core", "Objects mirror the business", [f"{DM}#lo1", f"{DM}#lo2"], f"{DM}#kp2",
              [U["dm_def"], U["dm_tradeoff"]], [f"{DM}#b2", f"{DM}#b4"], dm_md[2]),
        _page(3, "pitfalls", "When it pays off", [f"{DM}#lo2"], f"{DM}#kp3", [U["dm_tradeoff"]], [f"{DM}#b3", f"{DM}#b4"], dm_md[3]),
        _page(4, "recap", "Recap", [f"{DM}#lo1", f"{DM}#lo2"], None, [U["dm_def"], U["dm_tradeoff"]], [f"{DM}#b1", f"{DM}#b3"], dm_md[4]),
    ]
    sl_md = tl.service_layer_pages()
    sl_pages = [
        _page(0, "orientation", "The front door of an application", [f"{SL}#lo1"], None, [U["sl_def"]], [f"{SL}#b1"], sl_md[0]),
        _page(1, "core", "The boundary", [f"{SL}#lo1"], f"{SL}#kp1", [U["sl_def"]], [f"{SL}#b1"], sl_md[1]),
        _page(2, "worked_example", "A revenue recognition service", [f"{SL}#lo2"], f"{SL}#kp2",
              [U["sl_logic"], U["sl_code"]], [f"{SL}#b2"], sl_md[2]),
        _page(3, "pitfalls", "Delivery independence", [f"{SL}#lo2"], f"{SL}#kp3",
              [U["sl_anemic"], U["ca_delivery"]], [f"{SL}#b3", f"{SL}#b4"], sl_md[3]),
        _page(4, "recap", "Recap", [f"{SL}#lo1", f"{SL}#lo2"], None, [U["sl_def"]], [f"{SL}#b1", f"{SL}#b2"], sl_md[4]),
    ]
    return {
        DM: _lesson_file(DM, groups[DM], "Domain Model", "Rules live with the objects they govern.",
                         ["behaviour", "data holder"], [], dm_pages),
        SL: _lesson_file(SL, groups[SL], "Service Layer", "One front door that coordinates while the model keeps the rules.",
                         ["boundary", "coordination"], [DM], sl_pages),
    }


def _acceptance(prediction: str, reason: str) -> dict:
    return {"prediction": prediction, "causal_reason": reason}


def _probes(correct: str, wrong: str) -> dict:
    return {"correct_learner_answer": correct, "wrong_learner_answer": wrong}


def _quiz_common(explanation: str, confirm: str, hint: str) -> dict:
    return {"explanation": explanation, "feedback": {"confirmatory": confirm, "remedial_hint": hint},
            "variants": [], "recap_of": None}


def lesson_questions(U: dict[str, str]) -> dict[str, dict]:
    DM, SL = LESSON_DM, LESSON_SL
    dm = {
        "contract_version": CONTRACT_VERSION, "lesson_id": DM, "revision": 0,
        "embedded": [
            {"id": f"{DM}#q1", "kind": "check", "page_index": 1, "position": "after", "lo_id": f"{DM}#lo1",
             "concept_id": "c:domain-model", "unit_ids": [U["dm_def"]], "source_quote": "carries the rules that apply to it",
             "prompt": "Suppose a pricing rule for invoices is revised next month. Where does that revision land in a domain model, and why does it land there?",
             "acceptance": _acceptance("The revision lands in the invoice object that owns the rule",
                                       "each object keeps the rules about its own data, so a rule is edited in one place"),
             "expected_answer": "In the invoice object itself, since the object owning the data also owns the rules about it.",
             "rationale": "Checks the binding of rules to objects.", "key_points": ["invoice object", "one place"],
             "bloom_level": "understand",
             "probes": _probes("Inside the invoice object, because it owns the rule about its own data.",
                               "In a shared pricing procedure that every screen calls.")},
            {"id": f"{DM}#q2", "kind": "predict", "page_index": 3, "position": "before", "lo_id": f"{DM}#lo2",
             "concept_id": "c:transaction-script", "unit_ids": [U["dm_tradeoff"]], "source_quote": "a Transaction Script is enough",
             "prompt": "Imagine a sign-up form that validates three fields and stores them. Predict which fits it better, a full domain model or a transaction script, and explain why?",
             "acceptance": _acceptance("A transaction script fits better",
                                       "the rules are few and stable, so an object model adds structure without a return"),
             "expected_answer": "A transaction script, because three stable validation rules never repay the design of an object model.",
             "rationale": "Primes the pay-off page.", "key_points": ["transaction script", "few stable rules"],
             "bloom_level": "apply",
             "probes": _probes("A transaction script: the rules are few and will hardly change.",
                               "A domain model, because objects are always cleaner than scripts.")},
        ],
        "quiz": [
            {"id": f"{DM}#q3", "type": "multiple_choice", "lo_id": f"{DM}#lo1", "concept_id": "c:domain-model",
             "unit_ids": [U["dm_def"]], "page_index": 1, "source_quote": "incorporates both behaviour and data",
             "prompt": "A team generates classes from its database tables and calls the result a domain model. Which feature would those classes still need before the label fits?",
             "options": ["Behaviour that applies the business rules to their own data",
                         "A separate script that runs each request from start to finish",
                         "Getters and setters for each column of the table",
                         "A cache that keeps recently loaded rows in memory"],
             "correct_index": 0,
             "distractors": [{"index": 1, "misconception": "A script around data holders is mistaken for a model"},
                             {"index": 2, "misconception": "Accessors are mistaken for behaviour"},
                             {"index": 3, "misconception": "Performance plumbing is mistaken for domain behaviour"}],
             "acceptance": _acceptance("The classes need behaviour", "a domain model binds the rules to the objects that hold the data"),
             "expected_answer": "Behaviour that applies the business rules to their own data.",
             "rationale": "Definition of a domain model.", "key_points": ["behaviour", "rules with data"],
             "bloom_level": "understand", "mastery_level": 2,
             "probes": _probes("They need behaviour that enforces the rules on their data.", "They need getters and setters."),
             **_quiz_common("A domain model couples behaviour to data; generated data classes have none.",
                            "Right: behaviour is what makes a model.", "What decision does a generated class make?")},
            {"id": f"{DM}#q4", "type": "true_false", "lo_id": f"{DM}#lo2", "concept_id": "c:transaction-script",
             "unit_ids": [U["dm_tradeoff"]], "page_index": 3, "source_quote": "simple validation and calculation",
             "prompt": "A Transaction Script suits a request whose rules are few and stable.",
             "options": ["True", "False"], "correct_index": 0,
             "distractors": [{"index": 1, "misconception": "Every application needs an object model"}],
             "acceptance": _acceptance("True", "a script is the honest shape for few, stable rules"),
             "expected_answer": "True", "rationale": "When a script is enough.", "key_points": ["few rules", "stable"],
             "bloom_level": "remember", "mastery_level": 1,
             "probes": _probes("True", "False"),
             **_quiz_common("Simple validation and calculation are what a transaction script is for.",
                            "Correct.", "How often do the rules of such a request change?")},
            {"id": f"{DM}#q5", "type": "short_answer", "lo_id": f"{DM}#lo2", "concept_id": "c:domain-model",
             "unit_ids": [U["dm_tradeoff"]], "page_index": 2, "source_quote": "worth the effort when the business rules are complex",
             "prompt": "Explain why a rich domain model repays its design cost for a billing team whose fee rules change monthly, while a form that stores three fields would waste it.",
             "acceptance": _acceptance("The model pays off for the billing team and wastes effort for the form",
                                       "each frequent change lands in one object, so the saving recurs only when rules keep changing"),
             "expected_answer": "Each monthly fee change lands in one object instead of across many procedures, so the saving recurs; the form's three rules hardly change, so the extra structure never pays back.",
             "rationale": "Judging when the model pays off.", "key_points": ["change lands in one object", "recurring saving", "stable rules"],
             "bloom_level": "evaluate", "mastery_level": 4,
             "probes": _probes("Frequent rule changes land in one object each time, which the form never needs.",
                               "Because objects are more modern than forms."),
             **_quiz_common("The return on a domain model comes from frequent change.",
                            "Yes.", "Where does a monthly change land in each design?")},
        ],
        "exercises": [
            {"id": f"{DM}#e1", "title": "Spot the behaviour", "problem_statement": "Read the worked order object below and mark each method that applies a business rule to the object's own data.",
             "hints": ["Look for decisions, not accessors", "A rule changes state or refuses a change", "Count the methods that touch only this object's fields"],
             "expected_solution": "The approve and discount methods apply rules; the getters do not.",
             "common_mistakes": ["Counting getters as behaviour"], "bloom_level": "apply", "difficulty": 1,
             "fading": "worked", "lo_id": f"{DM}#lo1", "unit_ids": [U["dm_def"]]},
        ],
        "lessons_learned": [],
    }
    sl = {
        "contract_version": CONTRACT_VERSION, "lesson_id": SL, "revision": 0,
        "embedded": [
            {"id": f"{SL}#q1", "kind": "check", "page_index": 1, "position": "after", "lo_id": f"{SL}#lo1",
             "concept_id": "c:service-layer", "unit_ids": [U["sl_def"]],
             "source_quote": "Clients call the service layer rather than reaching into the domain model directly",
             "prompt": "A nightly batch job needs to close a month of accounts. Which kind of operation does it ask the service layer for, and why does it stop at that line instead of editing invoice objects?",
             "acceptance": _acceptance("It asks for a goal-level close-month operation and stays outside the objects",
                                       "the service layer is the single front door, so each caller shares one coordinated path instead of copying it"),
             "expected_answer": "A goal-level operation such as closing the month; staying outside keeps one coordinated path that every caller shares.",
             "rationale": "Checks the boundary idea.", "key_points": ["goal-level operation", "one shared path"],
             "bloom_level": "understand",
             "probes": _probes("It asks for the close-month operation and leaves the objects to the service, so all callers share one path.",
                               "It updates the invoice totals itself because batch jobs are trusted.")},
            {"id": f"{SL}#q2", "kind": "predict", "page_index": 2, "position": "before", "lo_id": f"{SL}#lo2",
             "concept_id": "c:application-logic", "unit_ids": [U["sl_logic"], U["sl_code"]],
             "source_quote": "coordinating transactions and notifying other systems",
             "prompt": "Predict what a revenue recognition service does with a contract number before any business rule runs, and explain why that step counts as application logic?",
             "acceptance": _acceptance("It loads the contract and opens a transaction before any rule runs",
                                       "coordination work around the rules belongs to the application, while the rules stay in the objects"),
             "expected_answer": "It loads the contract and starts a transaction, which is coordination around the rules rather than a rule itself.",
             "rationale": "Primes the worked example.", "key_points": ["load", "transaction", "coordination"],
             "bloom_level": "apply",
             "probes": _probes("It fetches the contract and opens a transaction; that is coordination, so it is application logic.",
                               "It computes the revenue itself, since the service owns the rules.")},
        ],
        "quiz": [
            {"id": f"{SL}#q3", "type": "multiple_choice", "lo_id": f"{SL}#lo2", "concept_id": "c:application-logic",
             "unit_ids": [U["sl_logic"]], "page_index": 2,
             "source_quote": "application logic, which concerns the responsibilities of the application itself",
             "prompt": "A service method grows an if-statement deciding whether a contract qualifies for a discount. Which kind of logic has crept into the application logic layer, and why does that matter?",
             "options": ["Domain logic, because a business rule now lives outside the object it governs",
                         "Presentation logic, because the decision depends on which screen asked",
                         "Infrastructure logic, because the discount must be stored somewhere",
                         "Transaction logic, because the check runs inside a database transaction"],
             "correct_index": 0,
             "distractors": [{"index": 1, "misconception": "Any decision in a service is about the UI"},
                             {"index": 2, "misconception": "Storage concerns are confused with rules"},
                             {"index": 3, "misconception": "Running inside a transaction makes a rule application logic"}],
             "acceptance": _acceptance("Domain logic has leaked into the service", "the rule now lives away from the object it governs, which hollows the model"),
             "expected_answer": "Domain logic, because a business rule now lives outside the object it governs.",
             "rationale": "Separating the two kinds of logic.", "key_points": ["domain logic", "outside the object"],
             "bloom_level": "analyze", "mastery_level": 3,
             "probes": _probes("Domain logic leaked into the service; the rule should sit in the contract object.",
                               "Presentation logic, since the service is close to the screen."),
             **_quiz_common("A discount decision is a business rule, so it belongs in the domain model.",
                            "Right: the rule leaked out of the object.", "Who should decide whether a contract qualifies?")},
            {"id": f"{SL}#q4", "type": "true_false", "lo_id": f"{SL}#lo1", "concept_id": "c:service-layer",
             "unit_ids": [U["ca_delivery"]], "page_index": 3, "source_quote": "do not know how the data is presented",
             "prompt": "A use case behind the service layer stays unaware of whether a web page or a batch job sent the request.",
             "options": ["True", "False"], "correct_index": 0,
             "distractors": [{"index": 1, "misconception": "Services format their output for the caller"}],
             "acceptance": _acceptance("True", "the boundary hides the delivery mechanism from the use case"),
             "expected_answer": "True", "rationale": "Delivery independence.", "key_points": ["unaware of the channel"],
             "bloom_level": "remember", "mastery_level": 1,
             "probes": _probes("True", "False"),
             **_quiz_common("Use cases are indifferent to the delivery mechanism.", "Correct.",
                            "Would the same operation serve a batch job?")},
            {"id": f"{SL}#q5", "type": "short_answer", "lo_id": f"{SL}#lo1", "concept_id": "c:service-layer",
             "unit_ids": [U["sl_def"]], "page_index": 1, "source_quote": "defines an application's boundary",
             "prompt": "Describe what a service layer coordinates for a request that arrives at the front door of an application.",
             "acceptance": _acceptance("It chooses and orders the domain operations for the request",
                                       "the service decides which objects run and in what order, while the objects keep the rules"),
             "expected_answer": "It picks which domain operations run and in what order, wraps them in a transaction and tells other systems when the request is done.",
             "rationale": "What coordination means.", "key_points": ["which operations", "what order", "transaction"],
             "bloom_level": "understand", "mastery_level": 2,
             "probes": _probes("It decides which domain objects to call, in what order, and wraps the calls in a transaction.",
                               "It validates the invoice totals itself."),
             **_quiz_common("Coordination is choosing and sequencing domain operations.", "Yes.",
                            "What does the service do that no single object can?")},
            {"id": f"{SL}#q6", "type": "short_answer", "lo_id": None, "concept_id": "c:domain-model",
             "unit_ids": [U["dm_def"]], "page_index": 0, "source_quote": "carries the rules that apply to it",
             "prompt": "Explain how a domain model keeps a business rule in one place when that rule is revised.",
             "acceptance": _acceptance("The rule sits in the object that owns the data", "behaviour lives with the data it governs, so a revision touches one object"),
             "expected_answer": "The object that owns the data also owns the rule, so a revision touches that one object.",
             "rationale": "Recap of the prerequisite lesson.", "key_points": ["object owns the rule", "one object"],
             "bloom_level": "understand", "mastery_level": 2,
             "probes": _probes("Because the object holding the data holds the rule too.", "Because a central script lists the rules."),
             **{**_quiz_common("Rules bound to objects change in one place.", "Yes.", "Where does the rule live?"), "recap_of": DM}},
        ],
        "exercises": [
            {"id": f"{SL}#e1", "title": "Trace the revenue recognition service", "problem_statement": "Walk through the worked RecognitionService below and label each line as application logic or domain logic.",
             "hints": ["Loading and committing are coordination", "Ask who computes the recognitions", "Notifications never belong to the contract object"],
             "expected_solution": "Load, commit and notify are application logic; the contract's calculation is domain logic.",
             "common_mistakes": ["Labelling the calculation as application logic"], "bloom_level": "apply", "difficulty": 2,
             "fading": "worked", "lo_id": f"{SL}#lo2", "unit_ids": [U["sl_code"]]},
        ],
        "lessons_learned": [],
    }
    return {DM: dm, SL: sl}


def _card(gid: str, n: int, ctype: str, front: str, back: str, lo: int, units: list[str], page: int, lens: str,
          card_data: dict | None = None, source: str = "peaa") -> dict:
    return {"id": f"{gid}#k{n}", "card_type": ctype, "front": front, "back": back, "card_data": card_data or {},
            "lo_id": f"{gid}#lo{lo}", "unit_ids": units, "page_index": page, "source_reference": source, "lens": lens}


def lesson_cards(U: dict[str, str]) -> dict[str, dict]:
    DM, SL = LESSON_DM, LESSON_SL
    dm = {
        "contract_version": CONTRACT_VERSION, "lesson_id": DM, "revision": 0,
        "cards": [
            _card(DM, 1, "concept", "What does each object in a domain model keep together with its data?",
                  "The behaviour that changes that data.", 1, [U["dm_def"]], 1, "attributes"),
            _card(DM, 2, "cloze", "An object that stores fields but makes no decisions about them is a ___ holder.",
                  "data", 1, [U["dm_def"]], 2, "recall", {"blank_answer": "data", "hint": "plain storage"}),
            _card(DM, 3, "ordering", "Order the stages a team passes through when a domain model starts to pay off:",
                  "design the objects up front → rules change monthly → each change lands in one object", 2,
                  [U["dm_tradeoff"]], 3, "causes_effects",
                  {"items": ["rules change monthly", "design the objects up front", "each change lands in one object"],
                   "correct_order": [1, 0, 2]}),
            _card(DM, 4, "discrimination", "Which situation calls for a transaction script rather than a domain model?",
                  "A form that validates three fields and stores them", 2, [U["dm_tradeoff"]], 3, "similarities_differences",
                  {"options": ["A form that validates three fields and stores them",
                               "Fee rules that change monthly for years",
                               "Pricing rules spread across many procedures",
                               "Objects that each own their own behaviour"],
                   "correct_index": 0}),
            _card(DM, 5, "application", "A pricing rule changes for one product line. Which single place absorbs that change in a rich domain model?",
                  "The one business object owning that rule.", 1, [U["dm_def"]], 1, "significance"),
        ],
        "lessons_learned": [],
    }
    sl = {
        "contract_version": CONTRACT_VERSION, "lesson_id": SL, "revision": 0,
        "cards": [
            _card(SL, 1, "concept", "What sits on the line between an application's callers and its domain objects?",
                  "The service layer.", 1, [U["sl_def"]], 1, "parts_wholes"),
            _card(SL, 2, "cloze", "When services absorb the business rules, the hollowed-out objects form an ___ domain model.",
                  "anemic", 2, [U["sl_anemic"]], 3, "recall", {"blank_answer": "anemic", "hint": "bloodless"}),
            _card(SL, 3, "comparison", "In a revenue recognition service, what distinguishes application logic versus domain logic?",
                  "Coordination around the rules versus the rules themselves.", 2, [U["sl_logic"]], 2, "similarities_differences"),
            _card(SL, 4, "ordering", "Order the steps the revenue recognition service takes for one contract:",
                  "receive the number → let the contract calculate → commit and notify", 2, [U["sl_code"]], 2, "procedure",
                  {"items": ["commit and notify the ledger", "receive the contract number",
                             "ask the contract to calculate its recognitions"],
                   "correct_order": [1, 2, 0]}),
            _card(SL, 5, "discrimination", "Which of these may a well-formed service layer operation know about its caller?",
                  "Nothing beyond the request it received", 1, [U["ca_delivery"]], 3, "attributes",
                  {"options": ["Nothing beyond the request it received", "Whether the caller was a web page",
                               "Which screen will render the result", "The schedule of the batch job"],
                   "correct_index": 0}, source="clean-arch"),
        ],
        "lessons_learned": [],
    }
    return {DM: dm, SL: sl}


# --- critique verdicts (independent critic output that passes every gate) -----

def _passed(catalogue, exclude=()) -> list[dict]:
    return [{"criterion_id": cid, "passed": True, "evidence_span": "verbatim evidence from the artifact",
             "failure_reason": None, "correction_instruction": None} for cid, *_ in catalogue if cid not in exclude]


def _verdict_path(cur: Path, stage: str, item_id: str, suffix: str = ".json") -> Path:
    return cur / "critique" / stage / (item_id_to_filename(item_id)[:-5] + suffix)


def _groups_verdict(gid: str, edges: list[dict]) -> dict:
    verdict = {"contract_version": CONTRACT_VERSION, "stage": "groups", "item_id": gid, "round": 0,
               "critic": "group-critic", "criteria": _passed(CRITERIA["groups"]), "actionable_items": [], "notes": ""}
    if edges:
        verdict["edges"] = edges
    return verdict


def _syntheses_verdict(gid: str, brief: dict, unit_id: str) -> dict:
    return {"contract_version": CONTRACT_VERSION, "stage": "syntheses", "item_id": gid, "round": 0,
            "critic": "brief-critic", "criteria": _passed(CRITERIA["syntheses"]),
            "claims": [{"ref": item["id"], "verdict": "supported", "unit_ids": item["unit_ids"] or [unit_id], "reason": ""}
                       for item in brief["items"]],
            "actionable_items": [], "notes": ""}


def _lessons_verdict(gid: str, lesson: dict) -> dict:
    import critique

    claims = [{"ref": f"page:{page['index']}:para:{j}", "verdict": "supported", "unit_ids": page["unit_ids"][:1], "reason": ""}
              for page in lesson["pages"] for j, _ in enumerate(critique._paragraphs(page["markdown"]))]
    return {"contract_version": CONTRACT_VERSION, "stage": "lessons", "item_id": gid, "round": 0,
            "critic": "lesson-critic", "criteria": _passed(CRITERIA["lessons"]), "claims": claims,
            "bloom_relabels": {lo["id"]: lo["bloom_level"] for lo in lesson["objectives"]},
            "actionable_items": [], "notes": ""}


def _questions_verdict(gid: str, data: dict) -> dict:
    import critique

    criteria = PER_ITEM_CRITERIA["questions"]
    items = [{"item_id": q["id"], "verdict": "keep", "criteria": _passed(criteria, (critique.PROBE_CRITERION,)),
              "rewrite": None, "bloom_relabel": q["bloom_level"], "feedback_to_author": ""}
             for section in ("embedded", "quiz") for q in data[section]]
    n_embedded = len(data["embedded"])
    return {"contract_version": CONTRACT_VERSION, "stage": "questions", "item_id": gid, "round": 0,
            "critic": "question-critic", "items": items,
            "test_student": {"answered": n_embedded, "correct": n_embedded, "unanswerable": [], "undefined_terms": []},
            "notes": ""}


def _cards_verdict(gid: str, data: dict) -> dict:
    return {"contract_version": CONTRACT_VERSION, "stage": "cards", "item_id": gid, "round": 0, "critic": "card-critic",
            "items": [{"item_id": c["id"], "verdict": "keep", "criteria": _passed(PER_ITEM_CRITERIA["cards"]),
                       "alternatives": [], "feedback_to_author": ""} for c in data["cards"]],
            "notes": ""}


def _qualification_verdict(cur: Path, source: str) -> dict:
    author = read_json(cur / "qualification" / f"{source}.json")["units"]
    return {"contract_version": CONTRACT_VERSION, "stage": "qualification", "item_id": source, "round": 0,
            "critic": "tag-critic",
            "items": [{"item_id": uid, "verdict": "keep", "concepts": [c["concept_id"] for c in entry["concepts"]],
                       "unit_type": entry["unit_type"], "notes": ""} for uid, entry in author.items()],
            "notes": ""}


def _grader_from_key(cur: Path, gid: str) -> dict:
    """Blind grades for every candidate, derived from the hidden key the script wrote
    (a real grader never sees the key; the fixture plays a perfect grader)."""
    import critique

    key = read_json(cur / "critique" / "questions" / "work" / (item_id_to_filename(gid)[:-5] + ".grader.key.json"))
    grades = {}
    for qid, mapping in key["questions"].items():
        grades[qid] = {label: ("pass" if role in (critique.ROLE_CORRECT, critique.ROLE_STUDENT) else "fail")
                       for label, role in mapping.items()}
    return {"contract_version": CONTRACT_VERSION, "stage": "questions", "item_id": gid, "round": 0,
            "grader": "blind-grader", "grades": grades}


def _critique_stage(cur: Path, stage: str, verdicts: dict[str, dict], graders: dict[str, dict] | None = None) -> None:
    """prepare -> write the verdict files -> check; every step must be error-free."""
    import critique

    _assert_clean(f"critique.prepare {stage}", critique.prepare(cur, stage))
    for item_id, grader in (graders or {}).items():
        dump_json(_verdict_path(cur, stage, item_id, ".grader.json"), grader)
    for item_id, verdict in verdicts.items():
        dump_json(_verdict_path(cur, stage, item_id), verdict)
    _assert_clean(f"critique.check {stage}", critique.check(cur, stage))
    summary = critique.status(cur)["stages"][stage]
    failing = {k: v["status"] for k, v in summary["by_item"].items() if v["status"] != "pass"}
    if failing:
        raise LessonFixtureError(f"critique.check {stage}: items not passing: {failing}")


def build_lesson_curriculum(root: Path) -> Path:
    """Build the complete lesson-mode curriculum under root/arch (see the module docstring)."""
    import cards
    import lessons
    import qualify
    import questions
    import regroup
    import synthesize

    DM, SL = LESSON_DM, LESSON_SL
    cur = root / "arch"
    cur.mkdir(parents=True)
    write_json(cur / "manifest.json", {
        "name": "Arch", "domain": "arch", "domain_family": "arch", "variant": "extensive", "pipeline": "lesson",
        "description": "Synthetic lesson-pipeline fixture",
        "sources": [{"type": "web", "path": "https://example.test/peaa", "title": "PEAA"},
                    {"type": "web", "path": "https://example.test/clean-arch", "title": "Clean Architecture"}],
        "created_at": "2026-01-01T00:00:00Z", "created_by": "test",
    })

    # Stage 2 (script): extracted -> units
    texts = _lesson_texts()
    _write_extracted(cur, texts)
    _assert_clean("qualify.prepare", qualify.prepare(cur))
    U: dict[str, str] = {}
    for source, sections in LESSON_SECTIONS.items():
        units = {u["id"]: u for u in read_json(cur / "units" / f"{source}.json")["units"]}
        expected = {f"u:{source}:{i}:0" for i in range(len(sections))}
        if set(units) != expected:
            raise LessonFixtureError(f"qualify.prepare segmented {source} into {sorted(units)}, expected one unit per section {sorted(expected)}")
        for i, (_title, key, _old) in enumerate(sections):
            uid = f"u:{source}:{i}:0"
            if units[uid]["text"] != texts[key]:
                raise LessonFixtureError(f"unit {uid} text differs from the section content after cleaning:\n{units[uid]['text']!r}\n!=\n{texts[key]!r}")
            U[key] = uid

    # Stage 2 (authored): qualification + concepts
    _write_qualification(cur, U)
    _assert_clean("qualify.check", qualify.check(cur))

    # Stage 3: groups (authored, status proposed) -> budget -> critique -> commit-edges -> reduced edges
    write_json(cur / "groups.json", lesson_groups(U))
    _, budget_findings = regroup.apply_budget(cur)
    _assert_clean("regroup.budget", budget_findings)
    _assert_clean("regroup.check (proposed edges)", regroup.check(cur, write_reduced=True))
    _critique_stage(cur, "groups", {
        DM: _groups_verdict(DM, []),
        SL: _groups_verdict(SL, [{"group_id": DM, "verdict": "confirmed", "reason": "the service layer assumes a domain model",
                                  "unit_ids": [U["sl_def"]]}]),
    })
    _, commit_findings = regroup.commit_edges(cur)
    _assert_clean("regroup.commit-edges", commit_findings)
    groups = {g["id"]: g for g in read_json(cur / "groups.json")["groups"]}
    if groups[SL]["prerequisites"][0]["status"] != "committed":
        raise LessonFixtureError(f"commit-edges left the SL -> DM edge {groups[SL]['prerequisites'][0]['status']!r}, expected committed")
    _assert_clean("regroup.check (committed edges)", regroup.check(cur, write_reduced=True))
    groups = {g["id"]: g for g in read_json(cur / "groups.json")["groups"]}

    # Qualification critique (needs concepts + units; independent of the groups)
    _critique_stage(cur, "qualification", {source: _qualification_verdict(cur, source) for source in LESSON_SECTIONS})

    # Stage 4: briefs
    briefs = lesson_briefs(U)
    for gid, brief in briefs.items():
        write_json(cur / "syntheses" / item_id_to_filename(gid), brief)
    _assert_clean("synthesize.check", synthesize.check(cur))
    _critique_stage(cur, "syntheses", {gid: _syntheses_verdict(gid, brief, U["dm_def"]) for gid, brief in briefs.items()})

    # Stage 5: full lessons -> build
    lesson_files = lesson_lessons(U, groups)
    for gid, lesson in lesson_files.items():
        write_json(cur / "lessons" / item_id_to_filename(gid), lesson)
    _assert_clean("lessons.check", lessons.check(cur))
    result, build_findings = lessons.build(cur)
    _assert_clean("lessons.build", build_findings)
    if result is None:
        raise LessonFixtureError("lessons.build produced nothing")
    _critique_stage(cur, "lessons", {gid: _lessons_verdict(gid, lesson) for gid, lesson in lesson_files.items()})

    # Stage 6: questions (+ blind grader) and Stage 7: cards
    question_files = lesson_questions(U)
    for gid, data in question_files.items():
        write_json(cur / "questions" / item_id_to_filename(gid), data)
    _assert_clean("questions.check", questions.check(cur))
    import critique

    _assert_clean("critique.prepare questions", critique.prepare(cur, "questions"))
    _critique_stage(cur, "questions", {gid: _questions_verdict(gid, data) for gid, data in question_files.items()},
                    graders={gid: _grader_from_key(cur, gid) for gid in question_files})

    card_files = lesson_cards(U)
    for gid, data in card_files.items():
        write_json(cur / "cards" / item_id_to_filename(gid), data)
    _assert_clean("cards.check", cards.check(cur))
    _critique_stage(cur, "cards", {gid: _cards_verdict(gid, data) for gid, data in card_files.items()})

    # Review
    write_json(cur / "review.json", {
        "total_topics": 4, "total_chunks": len(result["chunks"]), "quality_concerns": [], "approved": True,
        "validation": {"errors": 0, "warnings": 0}, "reviewed_at": "2026-01-01T00:00:00Z",
    })
    return cur


@pytest.fixture(scope="session")
def _lesson_curriculum_template(tmp_path_factory) -> Path:
    """Built once per session (the stage scripts run on every build); tests get copies."""
    return build_lesson_curriculum(tmp_path_factory.mktemp("lesson-template"))


@pytest.fixture
def lesson_curriculum_dir(tmp_path: Path, _lesson_curriculum_template: Path) -> Path:
    """A complete lesson-mode curriculum that is clean at every stage check and at
    `validate.py --stage all` / `--stage upload`."""
    dst = tmp_path / "arch"
    shutil.copytree(_lesson_curriculum_template, dst)
    return dst
