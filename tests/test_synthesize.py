"""Tests for synthesize.py — prepare work files and check briefs on a synthetic
lesson-mode curriculum (2 sources, 2 groups). Planted defects must be caught;
the clean briefs must produce no findings at all."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

import _compat  # noqa: F401

from chunk import count_tokens
from contracts import CONTRACT_VERSION, budget_for, dump_json, item_id_to_filename
import synthesize
from synthesize import check, check_item, main, prepare

from conftest import read_json

write_json = dump_json  # creates parent directories (units/, qualification/, syntheses/)

# ---------------------------------------------------------------------------
# Synthetic lesson-mode curriculum
# ---------------------------------------------------------------------------

SL = "g:service-layer"
DM = "g:domain-model"

UNITS = {
    "peaa": [
        ("u:peaa:12:0", 12, "Service Layer", "prose",
         "A Service Layer defines an application's boundary with a layer of services that establishes a set "
         "of available operations and coordinates the application's response in each operation. Clients call "
         "the service layer rather than reaching into the domain model directly."),
        ("u:peaa:12:1", 12, "Service Layer", "prose",
         "Business logic splits into two kinds: domain logic, which concerns the problem domain, and "
         "application logic, which concerns the responsibilities of the application itself, such as "
         "coordinating transactions and notifying other systems."),
        ("u:peaa:12:2", 12, "Service Layer", "code",
         "```java\npublic class RecognitionService {\n  public void calculateRevenueRecognitions(long contractNumber) { }\n}\n```"),
        ("u:peaa:12:3", 12, "Service Layer", "prose",
         "A common mistake is to put domain logic into the service layer, producing an anemic domain model "
         "where entities are mere data holders and the services hold all the behaviour."),
        ("u:peaa:10:0", 10, "Domain Model", "prose",
         "A Domain Model is an object model of the domain that incorporates both behaviour and data. Each "
         "object represents something meaningful in the business and carries the rules that apply to it."),
        ("u:peaa:10:1", 10, "Domain Model", "prose",
         "Domain models are worth the effort when the business rules are complex and change frequently; for "
         "simple validation and calculation a Transaction Script is enough."),
    ],
    "clean-arch": [
        ("u:clean-arch:4:0", 4, "Use Cases", "prose",
         "Use cases sit between the delivery mechanism and the entities. They orchestrate the flow of data to "
         "and from the entities and direct them to apply their enterprise business rules to achieve the goals "
         "of the use case."),
        ("u:clean-arch:4:1", 4, "Use Cases", "prose",
         "Use cases do not know how the data is presented. They are indifferent to whether the request "
         "arrived from a web page or a batch job."),
        ("u:clean-arch:4:2", 4, "Use Cases", "prose",
         "An application boundary is defined by a layer of services establishing the set of available "
         "operations and coordinating the application's response in each operation."),
    ],
}

QUALIFICATION = {
    "u:peaa:12:0": ("definition", "concept", "c:service-layer", "defines an application's boundary"),
    "u:peaa:12:1": ("mechanism", "concept", "c:application-logic", "application logic"),
    "u:peaa:12:2": ("code", "procedure", "c:service-layer", "RecognitionService"),
    "u:peaa:12:3": ("pitfall", "principle", "c:service-layer", "anemic domain model"),
    "u:peaa:10:0": ("definition", "concept", "c:domain-model", "object model of the domain"),
    "u:peaa:10:1": ("trade_off", "principle", "c:transaction-script", "Transaction Script is enough"),
    "u:clean-arch:4:0": ("definition", "concept", "c:service-layer", "Use cases sit between"),
    "u:clean-arch:4:1": ("context", "concept", "c:service-layer", "do not know how the data is presented"),
    "u:clean-arch:4:2": ("definition", "concept", "c:service-layer", "layer of services"),
}


def build_lesson_curriculum(root: Path) -> Path:
    """units/, qualification/, concepts.json, groups.json for two groups."""
    cur = root / "arch"
    cur.mkdir(parents=True)
    write_json(cur / "manifest.json", {"name": "Arch", "domain": "arch", "pipeline": "lesson", "sources": []})

    for source, units in UNITS.items():
        write_json(cur / "units" / f"{source}.json", {
            "contract_version": CONTRACT_VERSION, "source": source, "source_file": f"{source}.json",
            "source_type": "pdf", "title": source.upper(), "scope_applied": False,
            "units": [
                {"id": uid, "section_index": sec, "section_title": title, "heading_path": [title],
                 "block_type": block, "text": text, "token_count": count_tokens(text),
                 "raw_span": [0, len(text)], "page": 1, "images": [], "cleaning": {}}
                for uid, sec, title, block, text in units
            ],
        })
        write_json(cur / "qualification" / f"{source}.json", {
            "contract_version": CONTRACT_VERSION, "source": source,
            "units": {
                uid: {"concepts": [{"concept_id": cid, "quote": quote}], "unit_type": utype,
                      "knowledge_level": klevel, "learning_step": "building_block",
                      "bloom_ceiling": "analyze", "bloom_ceiling_reason": None, "quality": 4,
                      "keep": True, "drop_reason": None, "depends_on": [], "notes": ""}
                for uid, (utype, klevel, cid, quote) in QUALIFICATION.items() if uid.startswith(f"u:{source}:")
            },
            "gleaning": {},
        })
    write_json(cur / "units" / "index.json", {"contract_version": CONTRACT_VERSION, "sources": {}, "total_units": 9})

    write_json(cur / "concepts.json", {
        "contract_version": CONTRACT_VERSION,
        "concepts": [
            {"id": "c:service-layer", "label": "Service Layer", "aliases": ["application service layer"],
             "description": "Application boundary coordinating operations.", "learning_step": "building_block",
             "depends_on": ["c:domain-model"], "coverage": "strong", "sources": ["peaa", "clean-arch"]},
            {"id": "c:application-logic", "label": "Application Logic", "aliases": [], "description": "",
             "learning_step": "building_block", "depends_on": [], "coverage": "moderate", "sources": ["peaa"]},
            {"id": "c:domain-model", "label": "Domain Model", "aliases": ["rich domain model"], "description": "",
             "learning_step": "foundation", "depends_on": [], "coverage": "strong", "sources": ["peaa"]},
            {"id": "c:transaction-script", "label": "Transaction Script", "aliases": [], "description": "",
             "learning_step": "foundation", "depends_on": [], "coverage": "thin", "sources": ["peaa"]},
        ],
        "gaps": [], "ordering": ["c:domain-model", "c:transaction-script", "c:service-layer", "c:application-logic"],
        "merges": [],
    })

    def kps(gid, titles):
        return [{"id": f"{gid}#kp{i + 1}", "title": t, "unit_ids": []} for i, t in enumerate(titles)]

    def los(gid, texts):
        return [{"id": f"{gid}#lo{i + 1}", "text": t, "bloom_level": "understand", "concept_ids": [],
                 "unit_ids": [], "knowledge_point_ids": []} for i, t in enumerate(texts)]

    def watch(gid, texts):
        return [{"id": f"{gid}#w{i + 1}", "kind": "misconception", "text": t, "unit_ids": []}
                for i, t in enumerate(texts)]

    write_json(cur / "groups.json", {
        "contract_version": CONTRACT_VERSION,
        "areas": [{"id": "a:domain-logic", "title": "Domain Logic", "sort_order": 0, "suggested_level": 1,
                   "modules": [{"id": "m:patterns", "title": "Patterns", "sort_order": 0}]}],
        "groups": [
            {"id": DM, "title": "Domain Model", "area_id": "a:domain-logic", "module_id": "m:patterns",
             "sort_order": 0, "concept_ids": ["c:domain-model", "c:transaction-script"],
             "unit_ids": ["u:peaa:10:0", "u:peaa:10:1"], "related_unit_ids": [],
             "learning_step": "foundation", "suggested_level": 1,
             "generalization": "A Domain Model binds behaviour to data so rules live with the objects they govern.",
             "knowledge_points": kps(DM, ["Behaviour with data", "Objects mirror the business", "When it pays off"]),
             "objectives": los(DM, ["Explain how a domain model binds rules to objects",
                                    "Decide when a domain model beats a transaction script"]),
             "watch": watch(DM, ["Domain models are just data classes", "Always use a domain model"]),
             "duplicates": [], "conflicts": [], "prerequisites": [], "consequential": False, "rationale": "",
             "budget": budget_for(3, 2, "foundation", False)},
            {"id": SL, "title": "Service Layer", "area_id": "a:domain-logic", "module_id": "m:patterns",
             "sort_order": 1, "concept_ids": ["c:service-layer", "c:application-logic"],
             "unit_ids": ["u:peaa:12:0", "u:peaa:12:1", "u:peaa:12:2", "u:peaa:12:3"],
             "related_unit_ids": ["u:clean-arch:4:0", "u:clean-arch:4:1", "u:clean-arch:4:2"],
             "learning_step": "building_block", "suggested_level": 2,
             "generalization": "A Service Layer coordinates application logic while the Domain Model keeps the rules.",
             "knowledge_points": kps(SL, ["The boundary", "Application vs domain logic", "Delivery independence"]),
             "objectives": los(SL, ["Describe what a service layer coordinates",
                                    "Distinguish application logic from domain logic"]),
             "watch": watch(SL, ["Services should hold the business rules", "Use cases know the UI"]),
             "duplicates": [{"unit_ids": ["u:peaa:12:0", "u:clean-arch:4:2"], "prefer": "u:peaa:12:0",
                             "reason": "same definition, PEAA wording clearer"}],
             "conflicts": [],
             "prerequisites": [{"group_id": DM, "strength": "required", "evidence_unit_ids": ["u:peaa:12:0"],
                                "reason": "assumes the domain model", "status": "committed"}],
             "consequential": False, "rationale": "",
             "budget": budget_for(3, 2, "building_block", True)},
        ],
        "dropped": [],
    })
    return cur


def _item(gid, n, kind, text, unit_ids, quote, confidence, lo, kp, concepts=()):
    return {"id": f"{gid}#b{n}", "kind": kind, "text": text, "unit_ids": unit_ids, "quote": quote,
            "confidence": confidence, "concept_ids": list(concepts), "lo_ids": [f"{gid}#lo{lo}"],
            "knowledge_point_id": f"{gid}#kp{kp}"}


def service_layer_brief() -> dict:
    return {
        "contract_version": CONTRACT_VERSION, "group_id": SL, "revision": 0,
        "overview": "This lesson positions the service layer as the entry point clients use instead of touching "
                    "the domain model, and it separates coordination duties from the rules of the business.",
        "items": [
            _item(SL, 1, "map", "The service layer forms the entry point of an application and lists the operations a client may invoke.",
                  ["u:peaa:12:0", "u:clean-arch:4:0"], "defines an application's boundary", "stated", 1, 1, ["c:service-layer"]),
            _item(SL, 2, "map", "Application logic covers coordination duties such as running transactions and notifying other systems.",
                  ["u:peaa:12:1"], "coordinating transactions and notifying other systems", "attributed", 1, 2, ["c:application-logic"]),
            _item(SL, 3, "map", "Use cases stay unaware of the channel that delivered a request, whether it came from the web or a batch run.",
                  ["u:clean-arch:4:1"], "do not know how the data is presented", "attributed", 2, 3),
            _item(SL, 4, "watch", "Moving domain rules into services hollows the entities into an anemic domain model.",
                  ["u:peaa:12:3"], "anemic domain model", "attributed", 2, 2, ["c:service-layer"]),
            _item(SL, 5, "watch", "The service layer is a boundary for coordination, not the home of the business rules themselves.",
                  ["u:peaa:12:0", "u:peaa:12:3"], "Clients call the service layer", "attributed", 1, 1),
            _item(SL, 6, "map", "Use cases drive the entities to apply their business rules in service of one goal.",
                  ["u:clean-arch:4:0"], "direct them to apply their enterprise business rules", "attributed", 2, 3),
        ],
        "examples": [{"id": f"{SL}#ex1", "unit_id": "u:peaa:12:2", "language": "java", "note": "Revenue recognition service"}],
        "terminology": [{"term": "anemic domain model",
                         "definition": "A domain model whose objects carry data but no behaviour because the rules moved into services.",
                         "unit_id": "u:peaa:12:3"}],
        "lessons_learned": [],
    }


def domain_model_brief() -> dict:
    return {
        "contract_version": CONTRACT_VERSION, "group_id": DM, "revision": 0,
        "overview": "This lesson explains why a domain model binds behaviour to data and when the extra structure "
                    "pays off compared with a transaction script.",
        "items": [
            _item(DM, 1, "map", "A domain model couples the rules of the business to the objects that hold its data.",
                  ["u:peaa:10:0"], "incorporates both behaviour and data", "attributed", 1, 1, ["c:domain-model"]),
            _item(DM, 2, "map", "Each object in the model stands for something the business recognises and owns the rules that govern it.",
                  ["u:peaa:10:0"], "carries the rules that apply to it", "attributed", 1, 2),
            _item(DM, 3, "map", "Rich models repay their cost only when business rules are complex and shift often.",
                  ["u:peaa:10:1"], "worth the effort when the business rules are complex", "attributed", 2, 3,
                  ["c:domain-model", "c:transaction-script"]),
            _item(DM, 4, "watch", "Choosing a domain model for simple validation adds structure without benefit; a transaction script suffices there.",
                  ["u:peaa:10:1"], "a Transaction Script is enough", "attributed", 2, 3),
            _item(DM, 5, "watch", "A domain model is more than a set of data holders: each object brings its own behaviour.",
                  ["u:peaa:10:0"], "incorporates both behaviour and data", "attributed", 1, 1),
        ],
        "examples": [],
        "terminology": [{"term": "transaction script",
                         "definition": "A procedure that handles one request end to end, suited to simple validation and calculation.",
                         "unit_id": "u:peaa:10:1"}],
        "lessons_learned": [],
    }


def brief_path(cur: Path, gid: str) -> Path:
    return cur / "syntheses" / item_id_to_filename(gid)


def write_brief(cur: Path, brief: dict) -> Path:
    path = brief_path(cur, brief["group_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, brief)
    return path


@pytest.fixture
def lesson_dir(tmp_path: Path) -> Path:
    cur = build_lesson_curriculum(tmp_path)
    write_brief(cur, service_layer_brief())
    write_brief(cur, domain_model_brief())
    return cur


def ids(findings, severity=None):
    return [f.check_id for f in findings if severity is None or f.severity == severity]


def errors(findings):
    return ids(findings, "error")


def warnings(findings):
    return ids(findings, "warning")


def mutate_sl(cur: Path, fn) -> list:
    """Apply fn to the service-layer brief, write it back, check that group."""
    brief = service_layer_brief()
    fn(brief)
    write_brief(cur, brief)
    return check_item(cur, SL)


# ---------------------------------------------------------------------------
# check: clean
# ---------------------------------------------------------------------------

def test_clean_briefs_produce_no_findings(lesson_dir: Path):
    findings = check(lesson_dir)
    assert findings == [], [f"{f.severity} {f.check_id} @ {f.location}: {f.message}" for f in findings]


def test_check_item_single_group_is_clean(lesson_dir: Path):
    assert check_item(lesson_dir, SL) == []
    assert check_item(lesson_dir, DM) == []


def test_valid_inference_within_share_passes(lesson_dir: Path):
    def add(brief):
        for n in (7, 8, 9):
            clone = copy.deepcopy(brief["items"][1])
            clone["id"] = f"{SL}#b{n}"
            brief["items"].append(clone)
        brief["items"].append(_item(
            SL, 10, "inference",
            "Because services only coordinate while entities keep the rules, a thin service layer and a rich model reinforce each other.",
            ["u:peaa:12:1", "u:peaa:12:3"], "", "attributed", 2, 2))
    findings = mutate_sl(lesson_dir, add)  # 1 inference of 10 items = 10%, not above the cap
    assert "syntheses.inference_share" not in errors(findings)
    assert "syntheses.inference_single_source" not in errors(findings)


# ---------------------------------------------------------------------------
# check: planted defects
# ---------------------------------------------------------------------------

def test_missing_brief_is_error_only_for_that_group(lesson_dir: Path):
    brief_path(lesson_dir, DM).unlink()
    findings = check(lesson_dir)
    assert errors(findings) == ["syntheses.missing"]
    assert findings[0].location.endswith(item_id_to_filename(DM))
    assert check_item(lesson_dir, SL) == []


def test_quote_mismatch(lesson_dir: Path):
    def plant(b):
        b["items"][0]["quote"] = "defines the application boundary"
    assert errors(mutate_sl(lesson_dir, plant)) == ["syntheses.quote_mismatch"]


def test_quote_missing_for_map_item(lesson_dir: Path):
    def plant(b):
        b["items"][0]["quote"] = ""
    assert errors(mutate_sl(lesson_dir, plant)) == ["syntheses.quote_missing"]


def test_unit_out_of_group(lesson_dir: Path):
    def plant(b):
        b["items"][2]["unit_ids"] = ["u:peaa:10:0"]  # belongs to g:domain-model
    findings = mutate_sl(lesson_dir, plant)
    assert "syntheses.unit_out_of_group" in errors(findings)
    assert "syntheses.quote_mismatch" not in errors(findings)  # no valid unit to compare against


def test_inference_share_exceeded(lesson_dir: Path):
    def plant(b):
        b["items"].append(_item(
            SL, 7, "inference", "A thin service layer and a rich domain model reinforce one another.",
            ["u:peaa:12:1", "u:peaa:12:3"], "", "attributed", 2, 2))
    findings = mutate_sl(lesson_dir, plant)  # 1 of 7 = 14% > 10%
    assert errors(findings) == ["syntheses.inference_share"]


def test_inference_needs_two_sources(lesson_dir: Path):
    def plant(b):
        b["items"].append(_item(
            SL, 7, "inference", "A thin service layer and a rich domain model reinforce one another.",
            ["u:peaa:12:3"], "", "attributed", 2, 2))
    assert "syntheses.inference_single_source" in errors(mutate_sl(lesson_dir, plant))


def test_inference_with_wrong_quote_is_caught(lesson_dir: Path):
    def plant(b):
        b["items"].append(_item(
            SL, 7, "inference", "A thin service layer and a rich domain model reinforce one another.",
            ["u:peaa:12:1", "u:peaa:12:3"], "not in any unit", "attributed", 2, 2))
    assert "syntheses.quote_mismatch" in errors(mutate_sl(lesson_dir, plant))


def test_objective_uncovered(lesson_dir: Path):
    def plant(b):
        for item in b["items"]:
            if item["kind"] == "map":
                item["lo_ids"] = [f"{SL}#lo1"]
    findings = mutate_sl(lesson_dir, plant)
    assert errors(findings) == ["syntheses.objective_uncovered"]
    assert f"{SL}#lo2" in findings[0].message


def test_verbatim_fragment_of_eight_words(lesson_dir: Path):
    def plant(b):
        b["items"][0]["text"] = ("A service layer defines an application's boundary with a layer of services "
                                 "that establishes a set of available operations.")
    findings = mutate_sl(lesson_dir, plant)
    assert "syntheses.verbatim.fragment" in errors(findings)
    msg = next(f.message for f in findings if f.check_id == "syntheses.verbatim.fragment")
    assert "defines an application's boundary" in msg  # the label itself is whitelisted, the sentence is not


def test_verbatim_whitelist_spares_concept_labels(lesson_dir: Path):
    def plant(b):
        b["items"][0]["text"] = "Clients call the service layer, the application service layer, rather than the model."
    findings = mutate_sl(lesson_dir, plant)
    assert "syntheses.verbatim.fragment" not in errors(findings)


def test_confidence_overstated_single_source(lesson_dir: Path):
    def plant(b):
        b["items"][1]["confidence"] = "stated"
    findings = mutate_sl(lesson_dir, plant)
    assert warnings(findings) == ["syntheses.confidence_overstated"]
    assert errors(findings) == []


def test_too_many_citations(lesson_dir: Path):
    def plant(b):
        b["items"][0]["unit_ids"] = ["u:peaa:12:0", "u:peaa:12:1", "u:peaa:12:3", "u:clean-arch:4:0"]
    assert errors(mutate_sl(lesson_dir, plant)) == ["syntheses.citations"]


def test_item_cites_nothing(lesson_dir: Path):
    def plant(b):
        b["items"][0]["unit_ids"] = []
    assert "syntheses.item_no_units" in errors(mutate_sl(lesson_dir, plant))


def test_item_id_prefix_and_duplicates(lesson_dir: Path):
    def wrong_prefix(b):
        b["items"][0]["id"] = f"{DM}#b1"
    assert errors(mutate_sl(lesson_dir, wrong_prefix)) == ["syntheses.item_id"]

    def wrong_kind(b):
        b["items"][0]["id"] = f"{SL}#q1"
    assert errors(mutate_sl(lesson_dir, wrong_kind)) == ["syntheses.item_id"]

    def duplicate(b):
        b["items"][1]["id"] = b["items"][0]["id"]
    assert errors(mutate_sl(lesson_dir, duplicate)) == ["syntheses.item_id_duplicate"]


def test_invalid_enums(lesson_dir: Path):
    def plant(b):
        b["items"][0]["kind"] = "fact"
        b["items"][1]["confidence"] = "certain"
    found = errors(mutate_sl(lesson_dir, plant))
    assert "syntheses.item_kind" in found and "syntheses.confidence" in found


def test_item_word_count_warning(lesson_dir: Path):
    def plant(b):
        b["items"][0]["text"] = "Too short."
    findings = mutate_sl(lesson_dir, plant)
    assert "syntheses.item_words" in warnings(findings)


def test_watch_few_warning(lesson_dir: Path):
    def plant(b):
        b["items"][4]["kind"] = "map"
    findings = mutate_sl(lesson_dir, plant)
    assert "syntheses.watch_few" in warnings(findings)
    assert errors(findings) == []


def test_coverage_warning_lists_uncited_units(lesson_dir: Path):
    def plant(b):
        del b["items"][1]      # u:peaa:12:1 no longer cited
        b["examples"] = []     # u:peaa:12:2 no longer cited -> 2 of 4 kept units
    findings = mutate_sl(lesson_dir, plant)
    assert "syntheses.coverage" in warnings(findings)
    msg = next(f.message for f in findings if f.check_id == "syntheses.coverage")
    assert "u:peaa:12:1" in msg and "u:peaa:12:2" in msg


def test_coverage_ignores_dropped_and_duplicate_units(lesson_dir: Path):
    qual_path = lesson_dir / "qualification" / "peaa.json"
    qual = read_json(qual_path)
    qual["units"]["u:peaa:12:1"]["keep"] = False
    qual["units"]["u:peaa:12:1"]["drop_reason"] = "noise"
    write_json(qual_path, qual)

    def plant(b):
        del b["items"][1]  # the only citation of the now-dropped unit
    assert "syntheses.coverage" not in warnings(mutate_sl(lesson_dir, plant))


def test_example_must_be_code_unit(lesson_dir: Path):
    def plant(b):
        b["examples"][0]["unit_id"] = "u:peaa:12:0"
    assert "syntheses.example_not_code" in warnings(mutate_sl(lesson_dir, plant))

    def out_of_group(b):
        b["examples"][0]["unit_id"] = "u:peaa:10:0"
    assert "syntheses.unit_out_of_group" in errors(mutate_sl(lesson_dir, out_of_group))


def test_terminology_must_cite_a_unit_in_group(lesson_dir: Path):
    def uncited(b):
        del b["terminology"][0]["unit_id"]
    assert errors(mutate_sl(lesson_dir, uncited)) == ["syntheses.terminology_uncited"]

    def foreign(b):
        b["terminology"][0]["unit_id"] = "u:peaa:10:1"
    assert errors(mutate_sl(lesson_dir, foreign)) == ["syntheses.unit_out_of_group"]


def test_unknown_lo_kp_concept_references(lesson_dir: Path):
    def plant(b):
        b["items"][0]["lo_ids"] = [f"{SL}#lo9"]
        b["items"][1]["knowledge_point_id"] = f"{SL}#kp9"
        b["items"][2]["concept_ids"] = ["c:domain-model"]
    found = errors(mutate_sl(lesson_dir, plant))
    assert {"syntheses.lo_unknown", "syntheses.kp_unknown", "syntheses.concept_unknown"} <= set(found)


def test_items_max_exceeded(lesson_dir: Path):
    def plant(b):
        base = b["items"][1]
        for n in range(7, 32):
            clone = copy.deepcopy(base)
            clone["id"] = f"{SL}#b{n}"
            b["items"].append(clone)
    assert "syntheses.items_max" in errors(mutate_sl(lesson_dir, plant))


def test_group_id_mismatch_and_unknown_brief_file(lesson_dir: Path):
    brief = service_layer_brief()
    brief["group_id"] = DM
    write_json(brief_path(lesson_dir, SL), brief)  # file name says service-layer, content says domain-model
    assert "syntheses.group_id_mismatch" in errors(check_item(lesson_dir, SL))

    write_brief(lesson_dir, service_layer_brief())
    write_json(lesson_dir / "syntheses" / "g_nope.json", {"group_id": "g:nope", "items": []})
    findings = check(lesson_dir)
    assert errors(findings) == ["syntheses.unknown_group"]


def test_schema_errors_do_not_traceback(lesson_dir: Path):
    def plant(b):
        b["items"] = {"not": "a list"}
        b["revision"] = "zero"
        del b["overview"]
    found = mutate_sl(lesson_dir, plant)
    assert errors(found).count("syntheses.schema") >= 3  # items, revision, missing key
    assert "syntheses.overview_empty" in warnings(found)

    brief_path(lesson_dir, SL).write_text("{not json", encoding="utf-8")
    assert errors(check_item(lesson_dir, SL)) == ["syntheses.invalid_json"]


def test_missing_contract_version_is_warning(lesson_dir: Path):
    def plant(b):
        del b["contract_version"]
    findings = mutate_sl(lesson_dir, plant)
    assert warnings(findings) == ["syntheses.contract_version"] and errors(findings) == []


def test_missing_groups_json(lesson_dir: Path):
    (lesson_dir / "groups.json").unlink()
    assert errors(check(lesson_dir)) == ["syntheses.groups_missing"]
    assert errors(prepare(lesson_dir)) == ["syntheses.groups_missing"]


def test_missing_units_dir(lesson_dir: Path):
    for p in (lesson_dir / "units").iterdir():
        p.unlink()
    (lesson_dir / "units").rmdir()
    assert errors(check(lesson_dir)) == ["syntheses.units_missing"]


def test_check_unknown_group_filter(lesson_dir: Path):
    assert errors(check(lesson_dir, group_id="g:nope")) == ["syntheses.unknown_group"]


# ---------------------------------------------------------------------------
# prepare
# ---------------------------------------------------------------------------

def work_path(cur: Path, gid: str) -> Path:
    return cur / "syntheses" / "work" / item_id_to_filename(gid)


def test_prepare_writes_work_files(lesson_dir: Path):
    findings = prepare(lesson_dir)
    assert errors(findings) == [] and warnings(findings) == []
    assert ids(findings, "info").count("syntheses.prepare.written") == 2

    work = read_json(work_path(lesson_dir, SL))
    assert work["contract_version"] == CONTRACT_VERSION
    assert work["group_id"] == SL
    assert work["group"]["budget"] == budget_for(3, 2, "building_block", True)
    assert [u["id"] for u in work["units"]] == [
        "u:peaa:12:0", "u:peaa:12:1", "u:peaa:12:2", "u:peaa:12:3",
        "u:clean-arch:4:0", "u:clean-arch:4:1", "u:clean-arch:4:2",
    ]
    assert [u["role"] for u in work["units"]] == ["primary"] * 4 + ["related"] * 3
    dup = work["units"][-1]
    assert dup["duplicate_of_preferred"] is True and dup["preferred_unit_id"] == "u:peaa:12:0"
    assert all("duplicate_of_preferred" not in u for u in work["units"][:-1])
    first = work["units"][0]
    assert first["source"] == "peaa" and first["block_type"] == "prose" and first["heading_path"] == ["Service Layer"]
    assert first["qualification"] == {"unit_type": "definition", "knowledge_level": "concept",
                                      "concepts": ["c:service-layer"], "quality": 4}
    assert first["token_count"] == count_tokens(first["text"])
    assert [c["id"] for c in work["concepts"]] == ["c:service-layer", "c:application-logic"]
    assert work["neighbours"] == {"prerequisites": ["Domain Model"], "following": []}
    assert work["rubric"].startswith("# Synthesis Rubric")
    assert work["lessons_learned"] == []
    assert work["truncated"] is False and work["dropped_unit_ids"] == []

    dm = read_json(work_path(lesson_dir, DM))
    assert dm["neighbours"] == {"prerequisites": [], "following": ["Service Layer"]}


def test_prepare_carries_lessons_learned_from_existing_brief(lesson_dir: Path):
    brief = service_layer_brief()
    brief["lessons_learned"] = ["Cite the pitfall unit for every watch item."]
    write_brief(lesson_dir, brief)
    prepare(lesson_dir, group_ids=[SL])
    assert read_json(work_path(lesson_dir, SL))["lessons_learned"] == brief["lessons_learned"]
    assert not work_path(lesson_dir, DM).exists()


def test_prepare_skips_existing_unless_force(lesson_dir: Path):
    prepare(lesson_dir)
    path = work_path(lesson_dir, SL)
    path.write_text("{}", encoding="utf-8")
    second = prepare(lesson_dir)
    assert ids(second, "info").count("syntheses.prepare.skipped") == 2
    assert path.read_text(encoding="utf-8") == "{}"
    third = prepare(lesson_dir, force=True)
    assert ids(third, "info").count("syntheses.prepare.written") == 2
    assert read_json(path)["group_id"] == SL


def test_prepare_truncates_related_units_first(lesson_dir: Path):
    prepare(lesson_dir)
    work = read_json(work_path(lesson_dir, SL))
    base = count_tokens(json.dumps({**work, "units": []}, ensure_ascii=False))
    unit_costs = [count_tokens(json.dumps(u, ensure_ascii=False)) for u in work["units"]]
    budget = base + sum(unit_costs) - 1  # one token short of fitting everything

    findings = prepare(lesson_dir, force=True, max_tokens=budget)
    assert "syntheses.prepare.truncated" in warnings(findings)
    work = read_json(work_path(lesson_dir, SL))
    assert work["truncated"] is True
    assert work["dropped_unit_ids"] == ["u:clean-arch:4:2"]
    assert [u["id"] for u in work["units"]] == [
        "u:peaa:12:0", "u:peaa:12:1", "u:peaa:12:2", "u:peaa:12:3", "u:clean-arch:4:0", "u:clean-arch:4:1",
    ]

    prepare(lesson_dir, force=True, max_tokens=10)
    work = read_json(work_path(lesson_dir, SL))
    assert work["units"] == []
    assert work["dropped_unit_ids"] == [
        "u:clean-arch:4:2", "u:clean-arch:4:1", "u:clean-arch:4:0",
        "u:peaa:12:3", "u:peaa:12:2", "u:peaa:12:1", "u:peaa:12:0",
    ]


def test_prepare_unknown_group_filter(lesson_dir: Path):
    findings = prepare(lesson_dir, group_ids=["g:nope"])
    assert errors(findings) == ["syntheses.unknown_group"]
    assert not (lesson_dir / "syntheses" / "work").exists()


def test_prepare_warns_on_missing_unit_and_concept(lesson_dir: Path):
    groups_path = lesson_dir / "groups.json"
    groups = read_json(groups_path)
    sl = next(g for g in groups["groups"] if g["id"] == SL)
    sl["unit_ids"].append("u:peaa:12:9")
    sl["concept_ids"].append("c:ghost")
    write_json(groups_path, groups)
    findings = prepare(lesson_dir, group_ids=[SL])
    assert "syntheses.prepare.unit_missing" in warnings(findings)
    assert "syntheses.prepare.concept_missing" in warnings(findings)
    work = read_json(work_path(lesson_dir, SL))
    assert "u:peaa:12:9" not in [u["id"] for u in work["units"]]
    assert [c["id"] for c in work["concepts"]] == ["c:service-layer", "c:application-logic"]


def test_prepare_without_qualification_degrades(lesson_dir: Path):
    for p in (lesson_dir / "qualification").iterdir():
        p.unlink()
    (lesson_dir / "qualification").rmdir()
    findings = prepare(lesson_dir, group_ids=[SL])
    assert "syntheses.qualification_missing" in warnings(findings)
    assert errors(findings) == []
    assert read_json(work_path(lesson_dir, SL))["units"][0]["qualification"] is None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def test_cli_check_clean_exits_zero(lesson_dir: Path, capsys):
    assert main(["check", str(lesson_dir)]) == 0
    assert capsys.readouterr().out.strip().endswith("0 errors, 0 warnings")


def test_cli_check_reports_errors(lesson_dir: Path, capsys):
    brief = service_layer_brief()
    brief["items"][0]["quote"] = "nowhere"
    write_brief(lesson_dir, brief)
    assert main(["check", str(lesson_dir), "--group", SL]) == 1
    out = capsys.readouterr().out
    assert "[ERROR] syntheses.quote_mismatch @ syntheses/g_service-layer.json items[0] (g:service-layer#b1):" in out
    assert out.strip().endswith("1 errors, 0 warnings")


def test_cli_strict_fails_on_warnings(lesson_dir: Path, capsys):
    brief = service_layer_brief()
    brief["items"][1]["confidence"] = "stated"
    write_brief(lesson_dir, brief)
    assert main(["check", str(lesson_dir)]) == 0
    assert main(["check", str(lesson_dir), "--strict"]) == 1
    assert "[WARN] syntheses.confidence_overstated" in capsys.readouterr().out


def test_cli_prepare(lesson_dir: Path, capsys):
    assert main(["prepare", str(lesson_dir), "--group", SL, "--force"]) == 0
    assert "[INFO] syntheses.prepare.written" in capsys.readouterr().out
    assert work_path(lesson_dir, SL).exists()


def test_cli_rejects_non_directory(tmp_path: Path):
    assert main(["check", str(tmp_path / "nope")]) == 2


def test_module_api_surface():
    for name in ("check", "check_item", "prepare", "main"):
        assert callable(getattr(synthesize, name))
