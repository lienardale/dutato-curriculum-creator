"""Tests for regroup.py — draft heuristics, every check gate (planted defect + clean pass),
prerequisite DAG hygiene, budgets and edge commitment.

Fixtures are tiny synthetic lesson-pipeline inputs built in tmp_path (units/,
qualification/, concepts.json, groups.json). token_count is set explicitly on every
unit so no tokenizer is needed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import _compat  # noqa: F401

import regroup
from contracts import (
    CONTRACT_VERSION,
    GROUP_TOKENS,
    PREREQ_FANIN_FAIL,
    PREREQ_FANIN_WARN,
    budget_for,
    item_id_to_filename,
)

from conftest import read_json, write_json


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def ids(findings, severity=None):
    return [f.check_id for f in findings if severity is None or f.severity == severity]


def errors(findings):
    return ids(findings, "error")


def warnings(findings):
    return ids(findings, "warning")


def messages(findings, check_id):
    return [f.message for f in findings if f.check_id == check_id]


def _unit(uid: str, section_title: str, tokens: int) -> dict:
    _, source, section, n = uid.split(":")
    return {
        "id": uid, "section_index": int(section), "section_title": section_title,
        "heading_path": [section_title], "block_type": "prose",
        "text": f"Text of {uid} about {section_title}.", "token_count": tokens,
        "raw_span": [0, 10], "page": 1, "images": [], "cleaning": {},
    }


def _qual(concepts, unit_type="mechanism", knowledge_level="concept", learning_step="building_block",
          keep=True, depends_on=(), bloom_ceiling=None, drop_reason=None) -> dict:
    return {
        "concepts": [{"concept_id": c, "quote": "q"} for c in concepts],
        "unit_type": unit_type, "knowledge_level": knowledge_level, "learning_step": learning_step,
        "bloom_ceiling": bloom_ceiling, "bloom_ceiling_reason": None, "quality": 4,
        "keep": keep, "drop_reason": drop_reason, "depends_on": list(depends_on), "notes": "",
    }


def _concept(cid, label, step, description="", aliases=(), depends_on=(), coverage="strong", sources=()) -> dict:
    return {
        "id": cid, "label": label, "description": description, "aliases": list(aliases),
        "learning_step": step, "depends_on": list(depends_on), "coverage": coverage, "sources": list(sources),
    }


def _prereq(group_id, strength="recommended", status=None, evidence=("u:src:1:0",)) -> dict:
    return {"group_id": group_id, "strength": strength, "evidence_unit_ids": list(evidence), "reason": "builds on", "status": status}


def write_inputs(cur: Path, units_by_source: dict, quals_by_source: dict, concepts: list, ordering: list, gaps=()) -> None:
    (cur / "units").mkdir(parents=True, exist_ok=True)
    (cur / "qualification").mkdir(exist_ok=True)
    for source, units in units_by_source.items():
        write_json(cur / "units" / f"{source}.json", {
            "contract_version": CONTRACT_VERSION, "source": source, "source_file": f"{source}.json",
            "source_type": "pdf", "title": source, "scope_applied": False, "units": units,
        })
        write_json(cur / "qualification" / f"{source}.json", {
            "contract_version": CONTRACT_VERSION, "source": source, "units": quals_by_source[source], "gleaning": {},
        })
    write_json(cur / "units" / "index.json", {"contract_version": CONTRACT_VERSION, "sources": {}, "total_units": 0, "total_tokens": 0, "scope": None})
    write_json(cur / "concepts.json", {"contract_version": CONTRACT_VERSION, "concepts": concepts, "gaps": list(gaps), "ordering": ordering, "merges": []})
    write_json(cur / "manifest.json", {"name": "Fixture", "domain": "fixture", "pipeline": "lesson", "sources": []})


def mutate(path: Path, fn) -> None:
    data = read_json(path)
    fn(data)
    write_json(path, data)


# ---------------------------------------------------------------------------
# Draft fixture: two sources, five concepts, one concept shared across sources
# ---------------------------------------------------------------------------

DRAFT_UNITS = {
    "alpha": [
        _unit("u:alpha:0:0", "Alpha Core", 150), _unit("u:alpha:0:1", "Alpha Core", 200), _unit("u:alpha:0:2", "Alpha Core", 100),
        _unit("u:alpha:1:0", "Shared Thing", 180), _unit("u:alpha:1:1", "Shared Thing", 120),
        _unit("u:alpha:2:0", "Gamma Step", 140),
        _unit("u:alpha:3:0", "Preface", 50),
    ],
    "beta": [
        _unit("u:beta:0:0", "Beta Core", 160), _unit("u:beta:0:1", "Beta Core", 170), _unit("u:beta:0:2", "Beta Core", 100),
        _unit("u:beta:1:0", "Shared Thing", 110),
        _unit("u:beta:2:0", "Delta Procedure", 150), _unit("u:beta:2:1", "Delta Procedure", 160), _unit("u:beta:2:2", "Delta Procedure", 90),
        _unit("u:beta:3:0", "Odds and Ends", 80), _unit("u:beta:3:1", "Odds and Ends", 70),
    ],
}
DRAFT_QUAL = {
    "alpha": {
        "u:alpha:0:0": _qual(["c:alpha-core"], "definition", learning_step="foundation"),
        "u:alpha:0:1": _qual(["c:alpha-core", "c:shared-thing"], "mechanism", learning_step="foundation"),
        "u:alpha:0:2": _qual(["c:alpha-core"], "mechanism", learning_step="foundation"),
        "u:alpha:1:0": _qual(["c:shared-thing"], "definition", learning_step="foundation"),
        "u:alpha:1:1": _qual(["c:shared-thing"], "example", learning_step="foundation"),
        "u:alpha:2:0": _qual(["c:gamma-step", "c:alpha-core"], "procedure", "procedure", "applied", depends_on=["c:alpha-core"]),
        "u:alpha:3:0": _qual([], "noise", keep=False, drop_reason="meta"),
    },
    "beta": {
        "u:beta:0:0": _qual(["c:beta-core", "c:shared-thing"], "definition"),
        "u:beta:0:1": _qual(["c:beta-core"], "mechanism"),
        "u:beta:0:2": _qual(["c:beta-core"], "example"),
        "u:beta:1:0": _qual(["c:shared-thing"], "example", learning_step="foundation"),
        "u:beta:2:0": _qual(["c:delta-proc"], "procedure", "procedure", "applied"),
        "u:beta:2:1": _qual(["c:delta-proc"], "procedure", "procedure", "applied"),
        "u:beta:2:2": _qual(["c:delta-proc"], "pitfall", "principle", "applied"),
        "u:beta:3:0": _qual(["c:not-registered"], "context"),
        "u:beta:3:1": _qual([], "context"),
    },
}
DRAFT_CONCEPTS = [
    _concept("c:alpha-core", "Alpha Core", "foundation", "The basic alpha building block.", aliases=["core alpha"], sources=["alpha"]),
    _concept("c:shared-thing", "Shared Thing", "foundation", "A thing both sources teach.", sources=["alpha", "beta"]),
    _concept("c:beta-core", "Beta Core", "building_block", "The basic beta building block.", sources=["beta"]),
    _concept("c:gamma-step", "Gamma Step", "applied", "Applies Alpha Core to a concrete step.", coverage="thin", sources=["alpha"]),
    _concept("c:delta-proc", "Delta Procedure", "applied", "A two-part procedure.", depends_on=["c:beta-core"], coverage="moderate", sources=["beta"]),
]
DRAFT_ORDERING = [c["id"] for c in DRAFT_CONCEPTS]


@pytest.fixture
def draft_dir(tmp_path: Path) -> Path:
    cur = tmp_path / "draft"
    write_inputs(cur, DRAFT_UNITS, DRAFT_QUAL, DRAFT_CONCEPTS, DRAFT_ORDERING,
                 gaps=[{"concept": "Epsilon", "severity": "nice-to-have", "suggestion": "", "resolution": "descoped"}])
    return cur


def candidate(result: dict, concept_id: str) -> dict:
    return next(c for c in result["candidates"] if c["concept_id"] == concept_id)


def pair(result: dict, a: str, b: str) -> dict | None:
    return next((p for p in result["prerequisite_candidates"] if {p["a"], p["b"]} == {a, b}), None)


# ---------------------------------------------------------------------------
# draft
# ---------------------------------------------------------------------------

def test_draft_writes_file_with_contract_version(draft_dir: Path):
    result, findings = regroup.draft(draft_dir)
    assert findings == []
    on_disk = read_json(draft_dir / "groups.draft.json")
    assert on_disk == result
    assert on_disk["contract_version"] == CONTRACT_VERSION
    assert set(on_disk) == {"contract_version", "candidates", "suggested_merges", "suggested_splits", "prerequisite_candidates", "unassigned_units"}


def test_draft_candidates_follow_step_then_ordering(draft_dir: Path):
    result, _ = regroup.draft(draft_dir)
    assert [c["id"] for c in result["candidates"]] == ["g:alpha-core", "g:shared-thing", "g:beta-core", "g:gamma-step", "g:delta-procedure"]
    assert [c["learning_step"] for c in result["candidates"]] == ["foundation", "foundation", "building_block", "applied", "applied"]


def test_draft_primary_is_first_concept_and_related_is_secondary(draft_dir: Path):
    result, _ = regroup.draft(draft_dir)
    alpha = candidate(result, "c:alpha-core")
    assert alpha["unit_ids"] == ["u:alpha:0:0", "u:alpha:0:1", "u:alpha:0:2"]
    assert alpha["related_unit_ids"] == ["u:alpha:2:0"]
    assert alpha["token_mass"] == 450
    assert alpha["unit_types"] == {"definition": 1, "mechanism": 2}
    assert alpha["knowledge_levels"] == {"concept": 3}
    shared = candidate(result, "c:shared-thing")
    assert shared["unit_ids"] == ["u:alpha:1:0", "u:alpha:1:1", "u:beta:1:0"]  # source order across sources
    assert shared["related_unit_ids"] == ["u:alpha:0:1", "u:beta:0:0"]
    assert shared["title"] == "Shared Thing"


def test_draft_unassigned_and_dropped_units(draft_dir: Path):
    result, _ = regroup.draft(draft_dir)
    unassigned = {u["unit_id"]: u["reason"] for u in result["unassigned_units"]}
    assert "not in concepts.json" in unassigned["u:beta:3:0"]
    assert "no concept tags" in unassigned["u:beta:3:1"]
    assert "u:alpha:3:0" not in unassigned  # keep=false units are simply absent
    all_units = {u for c in result["candidates"] for u in c["unit_ids"] + c["related_unit_ids"]}
    assert "u:alpha:3:0" not in all_units


def test_draft_merge_suggestion_targets_most_co_occurring_concept(draft_dir: Path):
    result, _ = regroup.draft(draft_dir)
    merges = {m["concept_id"]: m for m in result["suggested_merges"]}
    assert set(merges) == {"c:gamma-step"}
    gamma = merges["c:gamma-step"]
    assert gamma["target_concept_id"] == "c:alpha-core"
    assert gamma["target_candidate_id"] == "g:alpha-core"
    assert gamma["co_occurrence_units"] == 1
    assert f"< {GROUP_TOKENS[0]}" in gamma["reason"] and "< 3" in gamma["reason"]


def test_draft_merge_without_co_occurrence_has_no_target(draft_dir: Path):
    mutate(draft_dir / "qualification" / "alpha.json", lambda d: d["units"]["u:alpha:2:0"].update(concepts=[{"concept_id": "c:gamma-step", "quote": "q"}]))
    result, _ = regroup.draft(draft_dir)
    gamma = next(m for m in result["suggested_merges"] if m["concept_id"] == "c:gamma-step")
    assert gamma["target_concept_id"] is None and gamma["target_candidate_id"] is None


def test_draft_split_on_multiple_procedures(draft_dir: Path):
    result, _ = regroup.draft(draft_dir)
    delta = [s for s in result["suggested_splits"] if s["concept_id"] == "c:delta-proc"]
    assert len(delta) == 1
    assert "procedure" in delta[0]["reason"]
    assert delta[0]["unit_ids"] == ["u:beta:2:0", "u:beta:2:1"]


def test_draft_split_on_token_mass(draft_dir: Path):
    def grow(d):
        next(u for u in d["units"] if u["id"] == "u:beta:2:0")["token_count"] = GROUP_TOKENS[1] + 1
    mutate(draft_dir / "units" / "beta.json", grow)
    result, _ = regroup.draft(draft_dir)
    reasons = [s["reason"] for s in result["suggested_splits"] if s["concept_id"] == "c:delta-proc"]
    assert any(f"> {GROUP_TOKENS[1]}" in r for r in reasons)


def test_draft_split_on_definitions_for_distinct_concepts(draft_dir: Path):
    result, _ = regroup.draft(draft_dir)
    shared = [s for s in result["suggested_splits"] if s["concept_id"] == "c:shared-thing"]
    assert len(shared) == 1
    assert "definition units for distinct concepts" in shared[0]["reason"]
    assert shared[0]["unit_ids"] == ["u:alpha:1:0", "u:beta:0:0"]
    assert not [s for s in result["suggested_splits"] if s["concept_id"] == "c:alpha-core"]


def test_draft_prerequisite_candidates_carry_each_evidence_tag(draft_dir: Path):
    result, _ = regroup.draft(draft_dir)
    alpha_gamma = pair(result, "c:alpha-core", "c:gamma-step")
    assert alpha_gamma["a"] == "c:alpha-core" and alpha_gamma["b"] == "c:gamma-step"
    assert alpha_gamma["evidence"] == ["co_occurrence", "depends_on", "label_in_description", "source_order"]
    assert alpha_gamma["unit_ids"] == ["u:alpha:0:0", "u:alpha:2:0"]
    assert alpha_gamma["reverse_evidence"] == []

    beta_delta = pair(result, "c:beta-core", "c:delta-proc")  # concepts.json depends_on
    assert (beta_delta["a"], beta_delta["evidence"]) == ("c:beta-core", ["depends_on", "source_order"])

    alpha_shared = pair(result, "c:alpha-core", "c:shared-thing")
    assert (alpha_shared["a"], alpha_shared["evidence"]) == ("c:alpha-core", ["co_occurrence", "source_order"])
    assert alpha_shared["unit_ids"] == ["u:alpha:0:0", "u:alpha:0:1", "u:alpha:1:0"]

    shared_gamma = pair(result, "c:shared-thing", "c:gamma-step")  # adjacent in alpha only
    assert (shared_gamma["a"], shared_gamma["evidence"]) == ("c:shared-thing", ["source_order"])


def test_draft_no_blind_pairs(draft_dir: Path):
    result, _ = regroup.draft(draft_dir)
    assert pair(result, "c:alpha-core", "c:beta-core") is None       # never share a unit or a source
    assert pair(result, "c:gamma-step", "c:delta-proc") is None
    assert all(p["evidence"] for p in result["prerequisite_candidates"])


def test_draft_orientation_prefers_depends_on_and_records_reverse_evidence(draft_dir: Path):
    mutate(draft_dir / "concepts.json", lambda d: d["concepts"][0].update(description="Prepares the ground for Gamma Step."))
    result, _ = regroup.draft(draft_dir)
    p = pair(result, "c:alpha-core", "c:gamma-step")
    assert p["a"] == "c:alpha-core"
    assert "label_in_description" in p["evidence"]
    assert p["reverse_evidence"] == ["label_in_description"]


def test_draft_candidate_ids_dedupe_label_collisions(draft_dir: Path):
    mutate(draft_dir / "concepts.json", lambda d: d["concepts"][2].update(label="Alpha Core"))
    result, _ = regroup.draft(draft_dir)
    assert candidate(result, "c:alpha-core")["id"] == "g:alpha-core"
    assert candidate(result, "c:beta-core")["id"] == "g:alpha-core-2"


def test_draft_missing_inputs_report_instead_of_traceback(tmp_path: Path):
    result, findings = regroup.draft(tmp_path)
    assert result is None
    assert errors(findings) == ["groups.missing_input", "groups.missing_input"]
    assert not (tmp_path / "groups.draft.json").exists()


def test_draft_missing_qualification_for_one_source(draft_dir: Path):
    (draft_dir / "qualification" / "beta.json").unlink()
    result, findings = regroup.draft(draft_dir)
    assert result is None
    assert any("beta" in f.location for f in findings if f.check_id == "groups.missing_input")


def test_draft_cli(draft_dir: Path, capsys):
    assert regroup.main(["draft", str(draft_dir)]) == 0
    out = capsys.readouterr().out
    assert "0 errors, 0 warnings" in out and "wrote groups.draft.json" in out
    assert regroup.main(["draft", str(draft_dir / "nope")]) == 2


# ---------------------------------------------------------------------------
# Groups fixture: n concepts/groups, one source, clean by construction
# ---------------------------------------------------------------------------

def _label(i: int) -> str:
    return f"Topic {i:02d}"


def _gid(i: int) -> str:
    return f"g:topic-{i:02d}"


def _cid(i: int) -> str:
    return f"c:topic-{i:02d}"


def make_group(i: int, n: int, area_id: str, sort_order: int, prereqs: list[dict]) -> dict:
    gid, label, other = _gid(i), _label(i), _label(i % n + 1)
    unit_ids = [f"u:src:{i}:{k}" for k in range(3)]
    return {
        "id": gid, "title": label, "area_id": area_id, "module_id": None, "sort_order": sort_order,
        "concept_ids": [_cid(i)], "unit_ids": unit_ids, "related_unit_ids": [],
        "learning_step": "building_block", "suggested_level": 1,
        "generalization": f"{label} shapes how {other} behaves under load.",
        "knowledge_points": [{"id": f"{gid}#kp{k + 1}", "title": f"{label} facet {k + 1}", "unit_ids": [unit_ids[k]]} for k in range(3)],
        "objectives": [
            {"id": f"{gid}#lo1", "text": f"Explain how {label} works", "bloom_level": "understand",
             "concept_ids": [_cid(i)], "unit_ids": [unit_ids[1]], "knowledge_point_ids": [f"{gid}#kp2"]},
            {"id": f"{gid}#lo2", "text": f"Compare {label} with {other}", "bloom_level": "analyze",
             "concept_ids": [_cid(i)], "unit_ids": [unit_ids[2]], "knowledge_point_ids": [f"{gid}#kp3"]},
        ],
        "watch": [
            {"id": f"{gid}#w1", "kind": "misconception", "text": f"{label} is a synonym for {other}", "unit_ids": [unit_ids[0]]},
            {"id": f"{gid}#w2", "kind": "boundary", "text": f"{label} does not cover the failure path", "unit_ids": [unit_ids[2]]},
        ],
        "duplicates": [], "conflicts": [], "prerequisites": prereqs,
        "consequential": False, "rationale": "fixture", "budget": None,
    }


def build_groups_fixture(root: Path, n: int, prereqs: dict[str, list[dict]] | None = None) -> Path:
    """n groups (3 kept units of 150 tokens each) in areas of up to 5; passes check() with no findings."""
    cur = root / "lesson"
    units, qual = [], {}
    for i in range(1, n + 1):
        for k, unit_type in enumerate(("definition", "mechanism", "example")):
            uid = f"u:src:{i}:{k}"
            units.append(_unit(uid, _label(i), 150))
            qual[uid] = _qual([_cid(i)], unit_type)
    units.append(_unit("u:src:0:0", "Preface", 30))
    qual["u:src:0:0"] = _qual([], "noise", keep=False, drop_reason="meta")
    concepts = [_concept(_cid(i), _label(i), "building_block", f"Explains {_label(i)}.", sources=["src"]) for i in range(1, n + 1)]
    write_inputs(cur, {"src": units}, {"src": qual}, concepts, [c["id"] for c in concepts])

    chunks = [list(range(s, min(s + 5, n + 1))) for s in range(1, n + 1, 5)]
    if len(chunks) > 1 and len(chunks[-1]) < 2:
        chunks[-2].extend(chunks.pop())
    areas, groups = [], []
    for a_idx, members in enumerate(chunks, 1):
        aid = f"a:area-{a_idx}"
        areas.append({"id": aid, "title": f"Area {a_idx}", "sort_order": a_idx - 1, "suggested_level": 1, "modules": []})
        for order, i in enumerate(members):
            groups.append(make_group(i, n, aid, order, list((prereqs or {}).get(_gid(i), []))))
    write_json(cur / "groups.json", {"contract_version": CONTRACT_VERSION, "areas": areas, "groups": groups, "dropped": []})
    return cur


@pytest.fixture
def groups_dir(tmp_path: Path) -> Path:
    return build_groups_fixture(tmp_path, 3)


def groups_file(cur: Path) -> Path:
    return cur / "groups.json"


# ---------------------------------------------------------------------------
# check: clean pass + planted defects
# ---------------------------------------------------------------------------

def test_check_clean_fixture_has_no_findings(groups_dir: Path):
    findings = regroup.check(groups_dir)
    assert findings == [], [f"{f.check_id}: {f.location}: {f.message}" for f in findings]
    assert regroup.check_item(groups_dir, "g:topic-01") == []


def test_check_clean_large_fixture(tmp_path: Path):
    cur = build_groups_fixture(tmp_path, 12)
    assert regroup.check(cur) == []


PLANTED = [
    ("groups.id", "error", lambda d: d["groups"][0].update(id="G:Bad Id")),
    ("groups.id", "error", lambda d: d["groups"][1].update(id=d["groups"][0]["id"])),
    ("groups.title", "error", lambda d: d["groups"][0].update(title="  ")),
    ("groups.title_duplicate", "error", lambda d: d["groups"][1].update(title=d["groups"][0]["title"])),
    ("groups.area_ref", "error", lambda d: d["groups"][0].update(area_id="a:nope")),
    ("groups.module_ref", "error", lambda d: d["groups"][0].update(module_id="m:nope")),
    ("groups.area_id", "error", lambda d: d["areas"][0].update(id="area-1")),
    ("groups.module_id", "error", lambda d: d["areas"][0]["modules"].append({"id": "bad", "title": "M", "sort_order": 0})),
    ("groups.sort_order", "error", lambda d: d["groups"][1].update(sort_order=d["groups"][0]["sort_order"])),
    ("groups.sort_order", "error", lambda d: d["areas"][0]["modules"].extend(
        [{"id": "m:a", "title": "A", "sort_order": 0}, {"id": "m:b", "title": "B", "sort_order": 0}])),
    ("groups.level", "warning", lambda d: d["groups"][0].update(suggested_level=7)),
    ("groups.learning_step", "error", lambda d: d["groups"][0].update(learning_step="expert")),
    ("groups.unknown_unit", "error", lambda d: d["groups"][0]["unit_ids"].append("u:src:99:9")),
    ("groups.unknown_unit", "error", lambda d: d["groups"][0]["knowledge_points"][0]["unit_ids"].append("u:src:99:9")),
    ("groups.unit_not_kept", "warning", lambda d: d["groups"][0]["unit_ids"].append("u:src:0:0")),
    ("groups.units_empty", "error", lambda d: d["groups"][0].update(unit_ids=[])),
    ("groups.concepts_empty", "error", lambda d: d["groups"][0].update(concept_ids=[])),
    ("groups.unknown_concept", "error", lambda d: d["groups"][0].update(concept_ids=["c:ghost"])),
    ("groups.unknown_concept", "error", lambda d: d["groups"][0]["objectives"][0].update(concept_ids=["c:ghost"])),
    ("groups.knowledge_points", "error", lambda d: d["groups"][0]["knowledge_points"].pop()),
    ("groups.subitem_id", "error", lambda d: d["groups"][1]["knowledge_points"][0].update(id="g:topic-01#kp1")),
    ("groups.subitem_id", "error", lambda d: d["groups"][0]["objectives"][0].update(id="g:topic-01#kp9")),
    ("groups.subitem_id", "error", lambda d: d["groups"][0]["watch"][1].update(id="g:topic-01#w1")),
    ("groups.kp_title", "error", lambda d: d["groups"][0]["knowledge_points"][0].update(title="")),
    ("groups.objectives", "error", lambda d: d["groups"][0]["objectives"].pop()),
    ("groups.objective_verb", "error", lambda d: d["groups"][0]["objectives"][0].update(text="Given a diagram, label its parts")),
    ("groups.objective_verb", "error", lambda d: d["groups"][0]["objectives"][0].update(text="Understand how Topic 01 works")),
    ("groups.objective_weak_verb", "error", lambda d: d["groups"][0]["objectives"][0].update(text="Understand how Topic 01 works")),
    ("groups.objective_weak_verb", "error", lambda d: d["groups"][0]["objectives"][0].update(text="Describe and understand Topic 01")),
    ("groups.objective_weak_verb", "error", lambda d: d["groups"][0]["objectives"][0].update(text="Be familiar with Topic 01")),
    ("groups.objective_bloom", "error", lambda d: d["groups"][0]["objectives"][0].update(bloom_level="mastery")),
    ("groups.objective_above_ceiling", "error", lambda d: d["groups"][0]["objectives"][0].update(bloom_level="evaluate")),
    ("groups.kp_ref", "error", lambda d: d["groups"][0]["objectives"][0].update(knowledge_point_ids=["g:topic-01#kp9"])),
    ("groups.watch", "error", lambda d: d["groups"][0]["watch"].pop()),
    ("groups.watch_kind", "error", lambda d: d["groups"][0]["watch"][0].update(kind="myth")),
    ("groups.watch_text", "error", lambda d: d["groups"][0]["watch"][0].update(text="")),
    ("groups.generalization_missing", "error", lambda d: d["groups"][0].update(generalization="")),
    ("groups.generalization_concepts", "error", lambda d: d["groups"][0].update(generalization="Something general about load.")),
    ("groups.generalization_weak_verb", "warning", lambda d: d["groups"][0].update(generalization="Topic 01 is a form of Topic 02.")),
    ("groups.duplicates_prefer", "error", lambda d: d["groups"][0].update(
        duplicates=[{"unit_ids": ["u:src:1:0", "u:src:1:1"], "prefer": "u:src:1:2", "reason": "x"}])),
    ("groups.unknown_unit", "error", lambda d: d["groups"][0].update(conflicts=[{"unit_ids": ["u:src:9:9"], "note": "x"}])),
    ("groups.prereq_ref", "error", lambda d: d["groups"][0].update(prerequisites=[_prereq("g:ghost")])),
    ("groups.prereq_self", "error", lambda d: d["groups"][0].update(prerequisites=[_prereq("g:topic-01")])),
    ("groups.prereq_strength", "error", lambda d: d["groups"][0].update(prerequisites=[_prereq("g:topic-02", strength="optional")])),
    ("groups.prereq_status", "error", lambda d: d["groups"][0].update(prerequisites=[_prereq("g:topic-02", status="maybe")])),
    ("groups.prereq_duplicate", "warning", lambda d: d["groups"][0].update(prerequisites=[_prereq("g:topic-02"), _prereq("g:topic-02")])),
    ("groups.unknown_unit", "error", lambda d: d["groups"][0].update(prerequisites=[_prereq("g:topic-02", evidence=["u:src:9:9"])])),
    ("groups.unit_unassigned", "error", lambda d: d["groups"][0]["unit_ids"].pop()),
    ("groups.unit_multi_assigned", "error", lambda d: d["groups"][1]["unit_ids"].append("u:src:1:0")),
    ("groups.unit_multi_assigned", "error", lambda d: d["dropped"].append({"unit_id": "u:src:1:0", "reason": "x"})),
    ("groups.unknown_unit", "error", lambda d: d["dropped"].append({"unit_id": "u:src:9:9", "reason": "x"})),
    ("groups.per_area", "warning", lambda d: (
        d["areas"].append({"id": "a:area-2", "title": "Area 2", "sort_order": 1, "suggested_level": 1, "modules": []}),
        d["groups"][2].update(area_id="a:area-2"))),
    ("groups.areas_count", "error", lambda d: d.update(areas=[])),
    ("groups.contract_version", "warning", lambda d: d.update(contract_version=2)),
    ("groups.shape", "error", lambda d: d["groups"][0].update(unit_ids="u:src:1:0")),
    ("groups.budget_stale", "warning", lambda d: d["groups"][0].update(budget={"pages": 99})),
]


@pytest.mark.parametrize("check_id,severity,mutator", PLANTED, ids=[f"{c}-{i}" for i, (c, _, _) in enumerate(PLANTED)])
def test_planted_defect_is_caught(groups_dir: Path, check_id: str, severity: str, mutator):
    mutate(groups_file(groups_dir), mutator)
    findings = regroup.check(groups_dir)
    assert check_id in ids(findings, severity), [f"{f.severity} {f.check_id}: {f.message}" for f in findings]


def test_check_mass_warning_from_unit_token_counts(groups_dir: Path):
    def shrink(d):
        for u in d["units"]:
            u["token_count"] = 50
    mutate(groups_dir / "units" / "src.json", shrink)
    findings = regroup.check(groups_dir)
    assert warnings(findings).count("groups.mass") == 3
    assert errors(findings) == []


def test_check_mass_falls_back_to_counting_text(groups_dir: Path):
    def drop_counts(d):
        for u in d["units"]:
            del u["token_count"]
    mutate(groups_dir / "units" / "src.json", drop_counts)
    findings = regroup.check(groups_dir)
    assert "groups.mass" in warnings(findings)  # the fixture texts are a handful of tokens each


def test_check_concept_uncovered_only_for_non_gap_concepts(groups_dir: Path):
    def add(d):
        d["concepts"].append(_concept("c:extra", "Extra", "applied", coverage="thin"))
        d["concepts"].append(_concept("c:gap", "Gap", "applied", coverage="gap"))
    mutate(groups_dir / "concepts.json", add)
    findings = regroup.check(groups_dir)
    assert [f.location for f in findings if f.check_id == "groups.concept_uncovered"] == ["c:extra"]


def test_check_ceiling_override_in_qualification_lifts_the_bar(groups_dir: Path):
    mutate(groups_file(groups_dir), lambda d: d["groups"][0]["objectives"][0].update(bloom_level="evaluate"))
    assert "groups.objective_above_ceiling" in errors(regroup.check(groups_dir))
    mutate(groups_dir / "qualification" / "src.json", lambda d: d["units"]["u:src:1:1"].update(bloom_ceiling="evaluate", bloom_ceiling_reason="argues trade-offs"))
    assert regroup.check(groups_dir) == []


def test_check_objective_without_unit_ids_uses_group_ceiling(groups_dir: Path):
    mutate(groups_file(groups_dir), lambda d: d["groups"][0]["objectives"][0].update(unit_ids=[], bloom_level="create"))
    assert "groups.objective_above_ceiling" in errors(regroup.check(groups_dir))


def test_check_module_in_other_area_and_area_sort_order(tmp_path: Path):
    cur = build_groups_fixture(tmp_path, 7)  # areas of 5 + 2
    assert regroup.check(cur) == []

    def wrong_area(d):
        d["areas"][1]["modules"].append({"id": "m:mod", "title": "Mod", "sort_order": 0})
        d["groups"][0]["module_id"] = "m:mod"  # group lives in area 1
    mutate(groups_file(cur), wrong_area)
    assert "groups.module_ref" in errors(regroup.check(cur))

    mutate(groups_file(cur), lambda d: d["areas"][1].update(sort_order=0))
    assert "groups.sort_order" in errors(regroup.check(cur))


def test_check_module_ref_clean_and_group_sort_order_scoped_by_module(tmp_path: Path):
    cur = build_groups_fixture(tmp_path, 3)

    def modules(d):
        d["areas"][0]["modules"] = [{"id": "m:one", "title": "One", "sort_order": 0}, {"id": "m:two", "title": "Two", "sort_order": 1}]
        d["groups"][0].update(module_id="m:one", sort_order=0)
        d["groups"][1].update(module_id="m:two", sort_order=0)  # same sort_order, different parent
        d["groups"][2].update(module_id="m:two", sort_order=1)
    mutate(groups_file(cur), modules)
    assert regroup.check(cur) == []


def test_check_concepts_max_warning(tmp_path: Path):
    cur = build_groups_fixture(tmp_path, 7)
    mutate(groups_file(cur), lambda d: d["groups"][0].update(concept_ids=[_cid(i) for i in range(1, 8)]))
    findings = regroup.check(cur)
    assert warnings(findings) == ["groups.concepts_max"] and errors(findings) == []


def test_check_missing_groups_file(groups_dir: Path):
    groups_file(groups_dir).unlink()
    assert errors(regroup.check(groups_dir)) == ["groups.file_missing"]


def test_check_invalid_json(groups_dir: Path):
    groups_file(groups_dir).write_text("{not json", encoding="utf-8")
    assert errors(regroup.check(groups_dir)) == ["groups.invalid_json"]


def test_check_missing_concepts_reports_and_continues(groups_dir: Path):
    (groups_dir / "concepts.json").unlink()
    findings = regroup.check(groups_dir)
    assert "groups.missing_input" in errors(findings)
    assert errors(findings).count("groups.unknown_concept") == 3  # one per group, checks still ran


def test_check_item_scopes_to_one_group(groups_dir: Path):
    mutate(groups_file(groups_dir), lambda d: d["groups"][1]["watch"].pop())
    assert regroup.check_item(groups_dir, "g:topic-01") == []
    assert errors(regroup.check_item(groups_dir, "g:topic-02")) == ["groups.watch"]
    assert errors(regroup.check_item(groups_dir, "g:ghost")) == ["groups.unknown_group"]


# ---------------------------------------------------------------------------
# check: prerequisite DAG hygiene
# ---------------------------------------------------------------------------

def test_dag_cycle_is_an_error_and_provisional_before_critique(tmp_path: Path):
    cur = build_groups_fixture(tmp_path, 12, prereqs={
        _gid(2): [_prereq(_gid(1))], _gid(3): [_prereq(_gid(2))], _gid(1): [_prereq(_gid(3))],
    })
    findings = regroup.check(cur, write_reduced=True)
    assert errors(findings) == ["groups.prereq_cycle"]
    cycle = next(f for f in findings if f.check_id == "groups.prereq_cycle")
    assert "provisional" in cycle.message
    assert cycle.location.count("->") == 3
    assert "prerequisite_edges_reduced" not in read_json(groups_file(cur))


def test_dag_committed_mode_ignores_uncertain_and_rejected_edges(tmp_path: Path):
    cur = build_groups_fixture(tmp_path, 12, prereqs={
        _gid(2): [_prereq(_gid(1), status="committed")],
        _gid(1): [_prereq(_gid(2), status="uncertain")],
        _gid(3): [_prereq(_gid(1), status="rejected")],
    })
    findings = regroup.check(cur, write_reduced=True)
    assert errors(findings) == []
    assert not any("provisional" in f.message for f in findings)
    assert read_json(groups_file(cur))["prerequisite_edges_reduced"] == [{"from": _gid(1), "to": _gid(2)}]


def test_dag_proposed_statuses_still_count_as_provisional(tmp_path: Path):
    cur = build_groups_fixture(tmp_path, 3, prereqs={
        _gid(2): [_prereq(_gid(1), status="proposed")], _gid(1): [_prereq(_gid(2), status="proposed")],
    })
    assert errors(regroup.check(cur)) == ["groups.prereq_cycle"]


def test_dag_fan_in_warning_then_error(tmp_path: Path):
    warn = build_groups_fixture(tmp_path / "w", 12, prereqs={_gid(1): [_prereq(_gid(i)) for i in range(2, 2 + PREREQ_FANIN_WARN + 1)]})
    findings = regroup.check(warn)
    assert "groups.prereq_fanin" in warnings(findings) and "groups.prereq_fanin" not in errors(findings)

    fail = build_groups_fixture(tmp_path / "f", 12, prereqs={_gid(1): [_prereq(_gid(i)) for i in range(2, 2 + PREREQ_FANIN_FAIL)]})
    findings = regroup.check(fail, write_reduced=True)
    assert "groups.prereq_fanin" in errors(findings)
    assert "prerequisite_edges_reduced" not in read_json(groups_file(fail))


def test_dag_roots_warning_for_large_curricula_only(tmp_path: Path):
    prereqs = {_gid(i): [_prereq(_gid(1))] for i in range(2, 7)}
    prereqs.update({_gid(i): [_prereq(_gid(12))] for i in range(7, 12)})
    cur = build_groups_fixture(tmp_path / "big", 12, prereqs=prereqs)
    findings = regroup.check(cur)
    assert warnings(findings) == ["groups.prereq_roots"]
    assert "2 root group(s)" in messages(findings, "groups.prereq_roots")[0]

    small = build_groups_fixture(tmp_path / "small", 3, prereqs={_gid(2): [_prereq(_gid(1))], _gid(3): [_prereq(_gid(2))]})
    assert regroup.check(small) == []  # chain gates everything, but graph-shape checks need >= 10 groups


def test_dag_bottleneck_warning(tmp_path: Path):
    prereqs = {_gid(2): [_prereq(_gid(1))]}
    prereqs.update({_gid(i): [_prereq(_gid(2))] for i in range(3, 13)})
    cur = build_groups_fixture(tmp_path, 12, prereqs=prereqs)
    findings = regroup.check(cur)
    assert [f.location for f in findings if f.check_id == "groups.prereq_bottleneck"] == [_gid(1), _gid(2)]
    assert "gates 11 of 11" in messages(findings, "groups.prereq_bottleneck")[0]


def test_dag_transitive_reduction_written_and_idempotent(tmp_path: Path):
    cur = build_groups_fixture(tmp_path, 3, prereqs={
        _gid(2): [_prereq(_gid(1))], _gid(3): [_prereq(_gid(2)), _prereq(_gid(1))],
    })
    findings = regroup.check(cur, write_reduced=True)
    assert errors(findings) == [] and warnings(findings) == []
    assert ids(findings, "info") == ["groups.prereq_transitive"]
    assert next(f.location for f in findings if f.check_id == "groups.prereq_transitive") == f"{_gid(1)} -> {_gid(3)}"
    data = read_json(groups_file(cur))
    assert data["prerequisite_edges_reduced"] == [{"from": _gid(1), "to": _gid(2)}, {"from": _gid(2), "to": _gid(3)}]
    before = groups_file(cur).read_text(encoding="utf-8")
    regroup.check(cur, write_reduced=True)
    assert groups_file(cur).read_text(encoding="utf-8") == before


def test_dag_reduction_not_written_without_flag_or_with_edge_errors(groups_dir: Path):
    mutate(groups_file(groups_dir), lambda d: d["groups"][1].update(prerequisites=[_prereq(_gid(1))]))
    regroup.check(groups_dir)
    assert "prerequisite_edges_reduced" not in read_json(groups_file(groups_dir))
    mutate(groups_file(groups_dir), lambda d: d["groups"][2].update(prerequisites=[_prereq("g:ghost")]))
    regroup.check(groups_dir, write_reduced=True)
    assert "prerequisite_edges_reduced" not in read_json(groups_file(groups_dir))


# ---------------------------------------------------------------------------
# budget
# ---------------------------------------------------------------------------

def test_budget_values_written_and_idempotent(tmp_path: Path):
    cur = build_groups_fixture(tmp_path, 3, prereqs={_gid(2): [_prereq(_gid(1))], _gid(3): [_prereq(_gid(1), status="rejected")]})
    mutate(groups_file(cur), lambda d: d["groups"][2].update(learning_step="applied"))
    rows, findings = regroup.apply_budget(cur)
    assert findings == [] and [r["group_id"] for r in rows] == [_gid(1), _gid(2), _gid(3)]

    groups = {g["id"]: g for g in read_json(groups_file(cur))["groups"]}
    assert groups[_gid(1)]["budget"] == budget_for(3, 2, "building_block", False)
    assert groups[_gid(1)]["budget"] == {
        "pages": 5, "embedded_questions_max": 8, "embedded_questions_min": 1, "quiz_items": 4,
        "quiz_min_bloom_levels": 3, "recap_item": False, "cards_min": 4, "cards_max": 5, "exercises_max": 1,
    }
    assert groups[_gid(2)]["budget"]["recap_item"] is True          # proposed (status None) prerequisite
    assert groups[_gid(3)]["budget"]["recap_item"] is False         # rejected edge does not count
    assert groups[_gid(3)]["budget"]["exercises_max"] == 3          # applied step

    before = groups_file(cur).read_text(encoding="utf-8")
    regroup.apply_budget(cur)
    assert groups_file(cur).read_text(encoding="utf-8") == before
    assert regroup.check(cur) == []                                  # no groups.budget_stale


def test_budget_missing_file(tmp_path: Path):
    rows, findings = regroup.apply_budget(tmp_path)
    assert rows == [] and errors(findings) == ["groups.file_missing"]


# ---------------------------------------------------------------------------
# commit-edges
# ---------------------------------------------------------------------------

def _verdict(cur: Path, gid: str, edges: list[dict]) -> None:
    write_json(cur / "critique" / "groups" / item_id_to_filename(gid), {
        "contract_version": CONTRACT_VERSION, "stage": "groups", "item_id": gid, "round": 0, "critic": "group-critic",
        "criteria": [], "edges": edges, "notes": "",
    })


def test_commit_edges_maps_verdicts_to_statuses(tmp_path: Path):
    cur = build_groups_fixture(tmp_path, 6, prereqs={
        _gid(2): [_prereq(_gid(1)), _prereq(_gid(3), evidence=[])],
        _gid(4): [_prereq(_gid(1))],
        _gid(5): [_prereq(_gid(1))],
        _gid(6): [_prereq(_gid(1), evidence=[])],
    })
    (cur / "critique" / "groups").mkdir(parents=True)
    _verdict(cur, _gid(2), [
        {"group_id": _gid(1), "verdict": "confirmed", "reason": "r", "unit_ids": ["u:src:1:0"]},
        {"group_id": _gid(3), "verdict": "confirmed", "reason": "r", "unit_ids": []},
    ])
    _verdict(cur, _gid(4), [{"group_id": _gid(1), "verdict": "either", "reason": "r", "unit_ids": []}])
    _verdict(cur, _gid(6), [{"group_id": _gid(1), "verdict": "confirmed", "reason": "r", "unit_ids": ["u:src:6:1"]}])

    rows, findings = regroup.commit_edges(cur)
    status = {(r["group_id"], r["prerequisite"]): r["status"] for r in rows}
    assert status == {
        (_gid(2), _gid(1)): "committed",    # confirmed + author evidence
        (_gid(2), _gid(3)): "uncertain",    # confirmed but no evidence anywhere
        (_gid(4), _gid(1)): "rejected",     # either
        (_gid(5), _gid(1)): "uncertain",    # no verdict file
        (_gid(6), _gid(1)): "committed",    # confirmed, evidence supplied by the critic
    }
    assert warnings(findings) == ["groups.verdict_missing"]
    on_disk = {g["id"]: g for g in read_json(groups_file(cur))["groups"]}
    assert on_disk[_gid(2)]["prerequisites"][0]["status"] == "committed"
    assert on_disk[_gid(4)]["prerequisites"][0]["status"] == "rejected"

    before = groups_file(cur).read_text(encoding="utf-8")
    regroup.commit_edges(cur)
    assert groups_file(cur).read_text(encoding="utf-8") == before

    findings = regroup.check(cur)                                   # committed mode now
    assert errors(findings) == [] and not any("provisional" in f.message for f in findings)


def test_commit_edges_invalid_verdict_and_unparseable_file(tmp_path: Path):
    cur = build_groups_fixture(tmp_path, 3, prereqs={_gid(2): [_prereq(_gid(1))], _gid(3): [_prereq(_gid(1))]})
    (cur / "critique" / "groups").mkdir(parents=True)
    _verdict(cur, _gid(2), [{"group_id": _gid(1), "verdict": "maybe", "reason": "", "unit_ids": []}])
    (cur / "critique" / "groups" / item_id_to_filename(_gid(3))).write_text("{oops", encoding="utf-8")
    rows, findings = regroup.commit_edges(cur)
    assert sorted(warnings(findings)) == ["groups.invalid_json", "groups.verdict_invalid"]
    assert {r["status"] for r in rows} == {"uncertain"}


def test_commit_edges_missing_groups_file(tmp_path: Path):
    rows, findings = regroup.commit_edges(tmp_path)
    assert rows == [] and errors(findings) == ["groups.file_missing"]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def test_cli_check_exit_codes_and_output(groups_dir: Path, capsys):
    assert regroup.main(["check", str(groups_dir)]) == 0
    assert "0 errors, 0 warnings" in capsys.readouterr().out

    mutate(groups_file(groups_dir), lambda d: d["groups"][0].update(suggested_level=9))
    assert regroup.main(["check", str(groups_dir)]) == 0
    out = capsys.readouterr().out
    assert "[WARN] groups.level @ groups.json groups[g:topic-01]:" in out and "0 errors, 1 warnings" in out
    assert regroup.main(["check", str(groups_dir), "--strict"]) == 1
    capsys.readouterr()

    mutate(groups_file(groups_dir), lambda d: d["groups"][1]["watch"].pop())
    assert regroup.main(["check", str(groups_dir)]) == 1
    out = capsys.readouterr().out
    assert "[ERROR] groups.watch @ groups.json groups[g:topic-02]:" in out
    assert out.index("[ERROR]") < out.index("[WARN]")
    assert "1 errors, 1 warnings" in out
    assert regroup.main(["check", str(groups_dir), "--item", "g:topic-01"]) == 0
    capsys.readouterr()
    assert regroup.main(["check", str(groups_dir / "missing")]) == 2


def test_cli_check_no_write_keeps_reduction_out(groups_dir: Path):
    mutate(groups_file(groups_dir), lambda d: d["groups"][1].update(prerequisites=[_prereq(_gid(1))]))
    assert regroup.main(["check", str(groups_dir), "--no-write"]) == 0
    assert "prerequisite_edges_reduced" not in read_json(groups_file(groups_dir))
    assert regroup.main(["check", str(groups_dir)]) == 0
    assert read_json(groups_file(groups_dir))["prerequisite_edges_reduced"] == [{"from": _gid(1), "to": _gid(2)}]


def test_cli_budget_and_commit_edges(groups_dir: Path, capsys):
    assert regroup.main(["budget", str(groups_dir)]) == 0
    assert "Budgets" in capsys.readouterr().out
    assert all(g["budget"] is not None for g in read_json(groups_file(groups_dir))["groups"])
    assert regroup.main(["commit-edges", str(groups_dir)]) == 0
    assert "0 errors, 0 warnings" in capsys.readouterr().out
