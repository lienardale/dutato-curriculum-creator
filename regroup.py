"""
Stage 3 of the lesson pipeline — lesson groups (REGROUP).

PURE DATA UTILITY — no AI calls. Four mechanical commands around the authored
``groups.json`` (contract: docs/artifacts.md "Stage 3"; thresholds, enums and
id formats come from shared/contracts.py and are never redefined here):

  draft         units/*.json + qualification/*.json + concepts.json
                -> groups.draft.json: one candidate per concept (kept units
                   whose FIRST concept is it), merge/split suggestions and
                   evidence-tagged prerequisite candidates. The agent turns
                   this into groups.json following rubrics/regrouping.md.
  check         gate groups.json: ids/enums, every kept unit primary in exactly
                one group or dropped, counts, observable objectives under the
                units' Bloom ceiling, watch items, generalization, concept
                coverage, prerequisite DAG hygiene. Writes the transitive
                reduction to groups.json top-level "prerequisite_edges_reduced"
                (script-owned) when the edges carry no error.
  budget        fill each group's script-owned "budget" via contracts.budget_for
                (pages = knowledge points + 3, + 1 when the group has an example
                unit — block_type "code" or unit_type example/code — clamped
                to 4–8; the table prints has_example per group).
  commit-edges  map critique/groups/<item>.json edge verdicts onto
                prerequisites[].status: committed | uncertain | rejected.

Usage:
    python regroup.py draft output/<name>/
    python regroup.py check output/<name>/ [--strict] [--item g:<slug>] [--no-write]
    python regroup.py budget output/<name>/
    python regroup.py commit-edges output/<name>/

Module API (called by validate.py / critique.py):
    check(input_dir, write_reduced=False) -> list[Finding]
    check_item(input_dir, group_id)       -> list[Finding]   (one group)
    draft(input_dir)                      -> (draft dict | None, findings)
    apply_budget(input_dir)               -> (rows, findings)
    commit_edges(input_dir)               -> (rows, findings)
    main(argv)                            -> 0 when no errors, 1 otherwise
                                             (warnings also fail with --strict)

Findings print as "[ERROR] <check_id> @ <location>: <message>" ([WARN]/[INFO])
followed by "<n> errors, <m> warnings".

Draft heuristics worth knowing (all deterministic):
  * ``source_order`` evidence is attached to concept pairs that are adjacent in
    a source's first-appearance sequence, and to pairs that already carry other
    evidence — never to every pair of a source, so the candidate list stays
    reviewable and TOC order alone never manufactures an edge.
  * The "definitions for distinct concepts" split rule looks at the candidate's
    primary AND related definition units (primary units share one first concept
    by construction), so it flags a candidate whose unit set defines two things.
  * Graph-shape warnings (roots, bottleneck) need a graph of some size and only
    run when the curriculum has >= PREREQ_ROOTS_MIN_LESSONS groups.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import _compat  # noqa: F401

from chunk import count_tokens
from contracts import (
    AREA_ID_RE,
    AREAS_PER_CURRICULUM,
    BLOOM_CEILING_DEFAULT,
    BLOOM_LEVELS,
    BLOOM_ORDER,
    CONCEPTS_FILE,
    CONTRACT_VERSION,
    CRITIQUE_DIR,
    GROUP_ID_RE,
    GROUP_KNOWLEDGE_POINTS,
    GROUP_OBJECTIVES,
    GROUP_TOKENS,
    GROUP_WATCH_MIN,
    GROUPS_DRAFT_FILE,
    GROUPS_FILE,
    GROUPS_PER_AREA,
    LEARNING_STEPS,
    MODULE_ID_RE,
    NEW_CONCEPTS_PER_GROUP_MAX,
    OBSERVABLE_VERBS,
    PREREQ_BOTTLENECK_WARN,
    PREREQ_FANIN_FAIL,
    PREREQ_FANIN_WARN,
    PREREQ_ROOTS_MIN,
    PREREQ_ROOTS_MIN_LESSONS,
    PREREQ_STATUSES,
    PREREQ_STRENGTHS,
    PREREQ_VALIDATOR_VERDICTS,
    QUALIFICATION_DIR,
    SUBITEM_ID_RE,
    UNITS_DIR,
    WATCH_KINDS,
    WEAK_GENERALIZATION_VERBS,
    WEAK_OBJECTIVE_VERBS,
    Finding,
    budget_for,
    dump_json,
    group_has_example,
    item_id_to_filename,
    load_json,
    normalize_ws,
    slugify,
    source_key,
)

STAGE = "groups"
CRITIQUE_STAGE_DIR = "groups"  # critique/<stage>/<item>.json

# Files inside units/ that are not per-source units files.
_NON_SOURCE_UNIT_FILES = {"index.json", "dedup.json"}

# Lead-in words skipped before the first verb of an objective
# ("The learner will be able to explain ..." -> "explain").
_OBJECTIVE_LEADIN = {
    "the", "a", "an", "learner", "learners", "student", "students", "you", "they",
    "will", "be", "able", "to", "can", "should", "by", "end", "of", "this", "lesson", "and",
}
_WORD_RE = re.compile(r"[a-z][a-z'-]*")

# Evidence tags for prerequisite candidates; directional ones in priority order
# used to orient a pair when evidence points both ways.
EVIDENCE_CO_OCCURRENCE = "co_occurrence"
EVIDENCE_SOURCE_ORDER = "source_order"
EVIDENCE_LABEL = "label_in_description"
EVIDENCE_DEPENDS_ON = "depends_on"
_DIRECTIONAL_PRIORITY = (EVIDENCE_DEPENDS_ON, EVIDENCE_LABEL, EVIDENCE_SOURCE_ORDER)

# Statuses that count as "has a prerequisite" for the budget's recap item.
_BUDGET_PREREQ_STATUSES = (None, "proposed", "committed")


# ---------------------------------------------------------------------------
# Inputs: units, qualification, concepts
# ---------------------------------------------------------------------------


@dataclass
class _Inputs:
    """Everything the stage reads besides groups.json, with load findings."""

    units: dict[str, dict] = field(default_factory=dict)        # unit id -> unit (+ "source")
    unit_order: dict[str, int] = field(default_factory=dict)    # unit id -> global source position
    qual: dict[str, dict] = field(default_factory=dict)         # unit id -> qualification entry
    concepts: dict[str, dict] = field(default_factory=dict)     # concept id -> concept
    concept_list: list[dict] = field(default_factory=list)      # concepts.json order
    ordering: list[str] = field(default_factory=list)           # concepts.json "ordering"
    findings: list[Finding] = field(default_factory=list)

    def is_kept(self, uid: str) -> bool:
        entry = self.qual.get(uid)
        return bool(entry) and entry.get("keep") is True

    def concept_ids_of(self, uid: str) -> list[str]:
        """Concept ids tagged on a unit, in qualification order, de-duplicated."""
        entry = self.qual.get(uid) or {}
        seen: dict[str, None] = {}
        for tag in entry.get("concepts") or []:
            cid = tag.get("concept_id") if isinstance(tag, dict) else None
            if cid:
                seen.setdefault(cid, None)
        return list(seen)

    def token_count(self, uid: str) -> int:
        unit = self.units.get(uid) or {}
        tc = unit.get("token_count")
        if isinstance(tc, int) and not isinstance(tc, bool) and tc >= 0:
            return tc
        return count_tokens(unit.get("text") or "")

    def ceiling(self, uid: str) -> str | None:
        """Bloom ceiling of a unit: explicit override, else the knowledge-level default."""
        entry = self.qual.get(uid) or {}
        explicit = entry.get("bloom_ceiling")
        if explicit in BLOOM_ORDER:
            return explicit
        return BLOOM_CEILING_DEFAULT.get(entry.get("knowledge_level"))

    def units_in_order(self) -> list[str]:
        return sorted(self.units, key=lambda u: self.unit_order[u])


def _load_inputs(input_dir: Path) -> _Inputs:
    inp = _Inputs()
    units_dir = input_dir / UNITS_DIR
    sources: list[str] = []
    if not units_dir.is_dir():
        inp.findings.append(Finding(
            "groups.missing_input", "error", f"{UNITS_DIR}/",
            "units directory not found — run `qualify.py prepare` first",
        ))
    else:
        pos = 0
        for path in sorted(units_dir.glob("*.json")):
            if path.name in _NON_SOURCE_UNIT_FILES:
                continue
            try:
                data = load_json(path)
            except (OSError, json.JSONDecodeError) as e:
                inp.findings.append(Finding("groups.invalid_json", "error", f"{UNITS_DIR}/{path.name}", str(e)))
                continue
            units = data.get("units") if isinstance(data, dict) else None
            if not isinstance(units, list):
                continue  # not a units file
            source = data.get("source") or source_key(path.name)
            sources.append(source)
            for unit in units:
                uid = unit.get("id") if isinstance(unit, dict) else None
                if not uid:
                    continue
                if uid in inp.units:
                    inp.findings.append(Finding(
                        "groups.duplicate_unit", "error", f"{UNITS_DIR}/{path.name}",
                        f"unit id {uid} appears in more than one units file",
                    ))
                    continue
                inp.units[uid] = {**unit, "source": source}
                inp.unit_order[uid] = pos
                pos += 1
        if not sources:
            inp.findings.append(Finding(
                "groups.missing_input", "error", f"{UNITS_DIR}/",
                "no units/<source>.json files found — run `qualify.py prepare` first",
            ))

    for source in sources:
        qpath = input_dir / QUALIFICATION_DIR / f"{source}.json"
        if not qpath.exists():
            inp.findings.append(Finding(
                "groups.missing_input", "error", f"{QUALIFICATION_DIR}/{source}.json",
                f"qualification for source {source!r} not found — its units count as not kept",
            ))
            continue
        try:
            data = load_json(qpath)
        except (OSError, json.JSONDecodeError) as e:
            inp.findings.append(Finding("groups.invalid_json", "error", f"{QUALIFICATION_DIR}/{source}.json", str(e)))
            continue
        entries = data.get("units") if isinstance(data, dict) else None
        if not isinstance(entries, dict):
            inp.findings.append(Finding(
                "groups.missing_input", "error", f"{QUALIFICATION_DIR}/{source}.json",
                "qualification file has no \"units\" map",
            ))
            continue
        for uid, entry in entries.items():
            if isinstance(entry, dict):
                inp.qual[uid] = entry

    cpath = input_dir / CONCEPTS_FILE
    if not cpath.exists():
        inp.findings.append(Finding(
            "groups.missing_input", "error", CONCEPTS_FILE,
            "concepts.json not found — the agent writes it after qualification",
        ))
        return inp
    try:
        data = load_json(cpath)
    except (OSError, json.JSONDecodeError) as e:
        inp.findings.append(Finding("groups.invalid_json", "error", CONCEPTS_FILE, str(e)))
        return inp
    for concept in (data.get("concepts") or []) if isinstance(data, dict) else []:
        cid = concept.get("id") if isinstance(concept, dict) else None
        if not cid or cid in inp.concepts:
            continue
        inp.concepts[cid] = concept
        inp.concept_list.append(concept)
    ordering = data.get("ordering") if isinstance(data, dict) else None
    inp.ordering = [c for c in (ordering or []) if isinstance(c, str)]
    return inp


def _concept_matcher(inp: _Inputs) -> tuple[re.Pattern | None, dict[str, set[str]]]:
    """One alternation regex over every concept label/alias -> matched text -> concept ids.

    Word boundaries are lookarounds on [a-z0-9] so labels such as "C++" still match.
    """
    phrase_to_ids: dict[str, set[str]] = defaultdict(set)
    for concept in inp.concept_list:
        phrases = [concept.get("label") or ""] + [a for a in (concept.get("aliases") or []) if isinstance(a, str)]
        for phrase in phrases:
            key = normalize_ws(phrase).lower()
            if len(key) >= 2:
                phrase_to_ids[key].add(concept["id"])
    if not phrase_to_ids:
        return None, {}
    alternation = "|".join(re.escape(p) for p in sorted(phrase_to_ids, key=len, reverse=True))
    return re.compile(rf"(?<![a-z0-9])(?:{alternation})(?![a-z0-9])"), dict(phrase_to_ids)


def _concepts_mentioned(text: str, matcher: tuple[re.Pattern | None, dict[str, set[str]]]) -> set[str]:
    pattern, phrase_to_ids = matcher
    if pattern is None:
        return set()
    found: set[str] = set()
    for m in pattern.finditer(normalize_ws(text).lower()):
        found |= phrase_to_ids.get(m.group(0), set())
    return found


# ---------------------------------------------------------------------------
# draft
# ---------------------------------------------------------------------------


def _candidate_step(concept: dict, unit_ids: list[str], inp: _Inputs) -> str | None:
    step = concept.get("learning_step")
    if step in LEARNING_STEPS:
        return step
    steps = Counter(
        (inp.qual.get(u) or {}).get("learning_step") for u in unit_ids
        if (inp.qual.get(u) or {}).get("learning_step") in LEARNING_STEPS
    )
    if not steps:
        return None
    return min(steps, key=lambda s: (-steps[s], LEARNING_STEPS.index(s)))


def _candidate_ids(inp: _Inputs) -> dict[str, str]:
    """concept id -> candidate id g:<slug of label>, de-duplicated with -2, -3 ..."""
    ids: dict[str, str] = {}
    used: set[str] = set()
    for concept in inp.concept_list:
        base = "g:" + slugify(concept.get("label") or concept["id"].split(":", 1)[-1])
        gid, n = base, 2
        while gid in used:
            gid, n = f"{base}-{n}", n + 1
        used.add(gid)
        ids[concept["id"]] = gid
    return ids


def build_draft(inp: _Inputs) -> dict:
    """Mechanical lesson candidates from qualified units. Pure function of the inputs."""
    kept = [uid for uid in inp.units_in_order() if inp.is_kept(uid)]
    primary: dict[str, list[str]] = defaultdict(list)
    related: dict[str, list[str]] = defaultdict(list)
    unassigned: list[dict] = []

    for uid in kept:
        cids = inp.concept_ids_of(uid)
        if not cids:
            unassigned.append({"unit_id": uid, "reason": "kept unit has no concept tags"})
            continue
        first = cids[0]
        if first not in inp.concepts:
            unassigned.append({"unit_id": uid, "reason": f"first concept {first} is not in concepts.json"})
            continue
        primary[first].append(uid)
        for cid in cids[1:]:
            if cid in inp.concepts:
                related[cid].append(uid)
    for uid in inp.units_in_order():
        if uid not in inp.qual:
            unassigned.append({"unit_id": uid, "reason": "no qualification entry"})

    candidate_ids = _candidate_ids(inp)
    candidates: list[dict] = []
    for concept in inp.concept_list:
        cid = concept["id"]
        unit_ids = primary.get(cid, [])
        candidates.append({
            "id": candidate_ids[cid],
            "concept_id": cid,
            "title": normalize_ws(concept.get("label") or cid),
            "unit_ids": unit_ids,
            "related_unit_ids": related.get(cid, []),
            "token_mass": sum(inp.token_count(u) for u in unit_ids),
            "unit_types": dict(sorted(Counter((inp.qual[u].get("unit_type") or "unknown") for u in unit_ids).items())),
            "knowledge_levels": dict(sorted(Counter((inp.qual[u].get("knowledge_level") or "unknown") for u in unit_ids).items())),
            "learning_step": _candidate_step(concept, unit_ids, inp),
        })

    order_idx = {c: i for i, c in enumerate(inp.ordering)}

    def sort_key(cand: dict) -> tuple:
        step = cand["learning_step"]
        return (
            LEARNING_STEPS.index(step) if step in LEARNING_STEPS else len(LEARNING_STEPS),
            order_idx.get(cand["concept_id"], len(order_idx)),
            min((inp.unit_order[u] for u in cand["unit_ids"]), default=sys.maxsize),
            cand["concept_id"],
        )

    candidates.sort(key=sort_key)
    rank = {cand["concept_id"]: i for i, cand in enumerate(candidates)}
    by_concept = {cand["concept_id"]: cand for cand in candidates}

    # Co-occurrence counts over every kept unit (any tag position).
    cooc: dict[str, Counter] = defaultdict(Counter)
    for uid in kept:
        cids = [c for c in inp.concept_ids_of(uid) if c in inp.concepts]
        for a in cids:
            for b in cids:
                if a != b:
                    cooc[a][b] += 1

    suggested_merges: list[dict] = []
    suggested_splits: list[dict] = []
    for cand in candidates:
        cid = cand["concept_id"]
        thin_reasons = []
        if cand["token_mass"] < GROUP_TOKENS[0]:
            thin_reasons.append(f"token mass {cand['token_mass']} < {GROUP_TOKENS[0]}")
        if len(cand["unit_ids"]) < 3:
            thin_reasons.append(f"{len(cand['unit_ids'])} kept unit(s) < 3")
        if thin_reasons:
            target = None
            if cooc[cid]:
                target = min(cooc[cid], key=lambda other: (-cooc[cid][other], rank.get(other, len(rank)), other))
            suggested_merges.append({
                "candidate_id": cand["id"],
                "concept_id": cid,
                "reason": "; ".join(thin_reasons),
                "target_candidate_id": by_concept[target]["id"] if target else None,
                "target_concept_id": target,
                "co_occurrence_units": cooc[cid][target] if target else 0,
            })

        if cand["token_mass"] > GROUP_TOKENS[1]:
            suggested_splits.append({
                "candidate_id": cand["id"], "concept_id": cid,
                "reason": f"token mass {cand['token_mass']} > {GROUP_TOKENS[1]}",
                "unit_ids": list(cand["unit_ids"]),
            })
        definitions: dict[str, list[str]] = defaultdict(list)
        for uid in cand["unit_ids"] + cand["related_unit_ids"]:
            if inp.qual[uid].get("unit_type") == "definition":
                definitions[inp.concept_ids_of(uid)[0]].append(uid)
        if len(definitions) > 1 and sum(len(v) for v in definitions.values()) > 1:
            listing = "; ".join(f"{c} ({', '.join(us)})" for c, us in sorted(definitions.items(), key=lambda kv: rank.get(kv[0], len(rank))))
            suggested_splits.append({
                "candidate_id": cand["id"], "concept_id": cid,
                "reason": f"definition units for distinct concepts: {listing}",
                "unit_ids": sorted((u for us in definitions.values() for u in us), key=inp.unit_order.get),
            })
        procedures = [u for u in cand["unit_ids"] if inp.qual[u].get("unit_type") == "procedure"]
        if len(procedures) > 1:
            suggested_splits.append({
                "candidate_id": cand["id"], "concept_id": cid,
                "reason": f"{len(procedures)} procedure units — one procedure per lesson",
                "unit_ids": procedures,
            })

    prerequisite_candidates = _prerequisite_candidates(inp, kept, primary, rank)
    return {
        "contract_version": CONTRACT_VERSION,
        "candidates": candidates,
        "suggested_merges": suggested_merges,
        "suggested_splits": suggested_splits,
        "prerequisite_candidates": prerequisite_candidates,
        "unassigned_units": unassigned,
    }


def _prerequisite_candidates(inp: _Inputs, kept: list[str], primary: dict[str, list[str]], rank: dict[str, int]) -> list[dict]:
    """Concept pairs sharing >= 1 evidence tag, oriented a (prerequisite) -> b (dependent)."""
    directed: dict[tuple[str, str], dict] = {}
    symmetric: dict[frozenset, set[str]] = defaultdict(set)

    def add(a: str, b: str, tag: str, unit_ids: tuple[str, ...] = ()) -> None:
        if a == b or a not in inp.concepts or b not in inp.concepts:
            return
        entry = directed.setdefault((a, b), {"tags": set(), "units": set()})
        entry["tags"].add(tag)
        entry["units"].update(unit_ids)

    for uid in kept:
        cids = [c for c in inp.concept_ids_of(uid) if c in inp.concepts]
        for i, a in enumerate(cids):
            for b in cids[i + 1:]:
                symmetric[frozenset((a, b))].add(uid)
        if cids:
            for dep in (inp.qual.get(uid) or {}).get("depends_on") or []:
                if isinstance(dep, str):
                    add(dep, cids[0], EVIDENCE_DEPENDS_ON, (uid,))
    for concept in inp.concept_list:
        for dep in concept.get("depends_on") or []:
            if isinstance(dep, str):
                add(dep, concept["id"], EVIDENCE_DEPENDS_ON)

    matcher = _concept_matcher(inp)
    for concept in inp.concept_list:
        for other in _concepts_mentioned(concept.get("description") or "", matcher):
            add(other, concept["id"], EVIDENCE_LABEL)

    # First primary unit of each concept per source -> adjacency pairs.
    first_in_source: dict[str, dict[str, str]] = defaultdict(dict)  # source -> concept -> first unit id
    for cid, unit_ids in primary.items():
        for uid in unit_ids:
            source = inp.units[uid]["source"]
            current = first_in_source[source].get(cid)
            if current is None or inp.unit_order[uid] < inp.unit_order[current]:
                first_in_source[source][cid] = uid
    for source, firsts in first_in_source.items():
        sequence = sorted(firsts, key=lambda c: inp.unit_order[firsts[c]])
        for a, b in zip(sequence, sequence[1:]):
            add(a, b, EVIDENCE_SOURCE_ORDER, (firsts[a], firsts[b]))
    # Source order also supports pairs that already have other evidence.
    for pair in {frozenset(k) for k in directed} | set(symmetric):
        x, y = sorted(pair)
        for firsts in first_in_source.values():
            if x in firsts and y in firsts:
                a, b = (x, y) if inp.unit_order[firsts[x]] < inp.unit_order[firsts[y]] else (y, x)
                add(a, b, EVIDENCE_SOURCE_ORDER, (firsts[a], firsts[b]))

    empty = {"tags": set(), "units": set()}
    out: list[dict] = []
    for pair in {frozenset(k) for k in directed} | set(symmetric):
        x, y = sorted(pair, key=lambda c: (rank.get(c, len(rank)), c))
        fwd, rev = directed.get((x, y), empty), directed.get((y, x), empty)
        a, b, ev, rev_ev = x, y, fwd, rev
        for tag in _DIRECTIONAL_PRIORITY:
            if tag in fwd["tags"] and tag not in rev["tags"]:
                break
            if tag in rev["tags"] and tag not in fwd["tags"]:
                a, b, ev, rev_ev = y, x, rev, fwd
                break
        tags, unit_ids = set(ev["tags"]), set(ev["units"])
        if pair in symmetric:
            tags.add(EVIDENCE_CO_OCCURRENCE)
            unit_ids |= symmetric[pair]
        out.append({
            "a": a, "b": b,
            "evidence": sorted(tags),
            "reverse_evidence": sorted(rev_ev["tags"]),
            "unit_ids": sorted(unit_ids, key=inp.unit_order.get),
        })
    out.sort(key=lambda e: (rank.get(e["a"], len(rank)), rank.get(e["b"], len(rank)), e["a"], e["b"]))
    return out


def draft(input_dir: Path) -> tuple[dict | None, list[Finding]]:
    """Write groups.draft.json. Returns (draft, findings); draft is None when inputs are unusable."""
    input_dir = Path(input_dir)
    inp = _load_inputs(input_dir)
    if any(f.severity == "error" for f in inp.findings):
        return None, inp.findings
    result = build_draft(inp)
    dump_json(input_dir / GROUPS_DRAFT_FILE, result)
    return result, inp.findings


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------


@dataclass
class _Ctx:
    inp: _Inputs
    data: dict
    groups: list[dict]
    group_ids: set[str]
    areas: dict[str, dict]            # area id -> area
    modules: dict[str, str]           # module id -> area id
    matcher: tuple[re.Pattern | None, dict[str, set[str]]]


def _h(value):
    """Hashable stand-in for arbitrary JSON values (lists/dicts become their repr)."""
    try:
        hash(value)
    except TypeError:
        return repr(value)
    return value


def _as_list(value, loc: str, name: str, findings: list[Finding]) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    findings.append(Finding("groups.shape", "error", loc, f"{name} must be a list, got {type(value).__name__}"))
    return []


def _dicts(items: list, loc: str, name: str, findings: list[Finding]) -> list[dict]:
    out = []
    for i, item in enumerate(items):
        if isinstance(item, dict):
            out.append(item)
        else:
            findings.append(Finding("groups.shape", "error", f"{loc} {name}[{i}]", "entry must be an object"))
    return out


def _load_groups(input_dir: Path) -> tuple[_Ctx | None, list[Finding]]:
    inp = _load_inputs(input_dir)
    findings = list(inp.findings)
    path = input_dir / GROUPS_FILE
    if not path.exists():
        findings.append(Finding("groups.file_missing", "error", GROUPS_FILE, f"{GROUPS_FILE} not found in {input_dir}"))
        return None, findings
    try:
        data = load_json(path)
    except (OSError, json.JSONDecodeError) as e:
        findings.append(Finding("groups.invalid_json", "error", GROUPS_FILE, str(e)))
        return None, findings
    if not isinstance(data, dict):
        findings.append(Finding("groups.shape", "error", GROUPS_FILE, "top level must be an object"))
        return None, findings
    if data.get("contract_version") != CONTRACT_VERSION:
        findings.append(Finding(
            "groups.contract_version", "warning", GROUPS_FILE,
            f"contract_version={data.get('contract_version')!r}, expected {CONTRACT_VERSION}",
        ))
    groups = _dicts(_as_list(data.get("groups"), GROUPS_FILE, "groups", findings), GROUPS_FILE, "groups", findings)
    areas: dict[str, dict] = {}
    modules: dict[str, str] = {}
    for area in _dicts(_as_list(data.get("areas"), GROUPS_FILE, "areas", findings), GROUPS_FILE, "areas", findings):
        aid = area.get("id")
        if isinstance(aid, str) and aid not in areas:
            areas[aid] = area
        for module in _dicts(_as_list(area.get("modules"), f"{GROUPS_FILE} areas[{aid}]", "modules", findings), f"{GROUPS_FILE} areas[{aid}]", "modules", findings):
            mid = module.get("id")
            if isinstance(mid, str) and mid not in modules:
                modules[mid] = aid
    ctx = _Ctx(
        inp=inp, data=data, groups=groups,
        group_ids={g["id"] for g in groups if isinstance(g.get("id"), str)},
        areas=areas, modules=modules, matcher=_concept_matcher(inp),
    )
    return ctx, findings


def _check_unit_ref(ctx: _Ctx, uid, loc: str, findings: list[Finding], *, kept_required: bool) -> None:
    if not isinstance(uid, str) or uid not in ctx.inp.units:
        findings.append(Finding("groups.unknown_unit", "error", loc, f"unit id {uid!r} does not exist in units/"))
        return
    if kept_required and not ctx.inp.is_kept(uid):
        findings.append(Finding(
            "groups.unit_not_kept", "warning", loc,
            f"unit {uid} is not keep=true in its qualification (drop it from unit_ids or re-qualify it)",
        ))


def _check_subitem_id(sid, gid: str, kind: str, loc: str, seen: set[str], findings: list[Finding]) -> None:
    m = SUBITEM_ID_RE.match(sid or "") if isinstance(sid, str) else None
    if not m or m.group("kind") != kind or m.group("variant") or sid.split("#", 1)[0] != gid:
        findings.append(Finding("groups.subitem_id", "error", loc, f"id {sid!r} must be {gid}#{kind}<n>"))
        return
    if sid in seen:
        findings.append(Finding("groups.subitem_id", "error", loc, f"duplicate sub-item id {sid}"))
    seen.add(sid)


def _objective_first_verb(text: str) -> str | None:
    tokens = _WORD_RE.findall(text.lower())
    i = 0
    while i < len(tokens) and tokens[i] in _OBJECTIVE_LEADIN:
        i += 1
    if i >= len(tokens):
        return None
    bigram = " ".join(tokens[i:i + 2])
    return bigram if bigram in OBSERVABLE_VERBS else tokens[i]


def _weak_objective_verb(text: str) -> str | None:
    """A WEAK_OBJECTIVE_VERBS entry inside the first 3 words (raw, or after the lead-in)."""
    tokens = _WORD_RE.findall(text.lower())
    i = 0
    while i < len(tokens) and tokens[i] in _OBJECTIVE_LEADIN:
        i += 1
    for window in (" ".join(tokens[:3]), " ".join(tokens[i:i + 3])):
        for weak in sorted(WEAK_OBJECTIVE_VERBS):
            if re.search(rf"(?<![a-z]){re.escape(weak)}(?![a-z])", window):
                return weak
    return None


def _check_group(ctx: _Ctx, g: dict) -> list[Finding]:
    """Checks that need only this group plus the shared inputs."""
    f: list[Finding] = []
    gid = g.get("id") if isinstance(g.get("id"), str) else ""
    loc = f"{GROUPS_FILE} groups[{gid or '?'}]"
    if not GROUP_ID_RE.match(gid):
        f.append(Finding("groups.id", "error", loc, f"group id {gid!r} does not match g:<slug>"))
    if not normalize_ws(str(g.get("title") or "")):
        f.append(Finding("groups.title", "error", loc, "title is empty"))

    area_id, module_id = g.get("area_id"), g.get("module_id")
    if not isinstance(area_id, str) or area_id not in ctx.areas:
        f.append(Finding("groups.area_ref", "error", loc, f"area_id {area_id!r} is not declared in areas"))
    if module_id is not None:
        if not isinstance(module_id, str) or module_id not in ctx.modules:
            f.append(Finding("groups.module_ref", "error", loc, f"module_id {module_id!r} is not declared in any area"))
        elif ctx.modules[module_id] != area_id:
            f.append(Finding("groups.module_ref", "error", loc, f"module {module_id} belongs to area {ctx.modules[module_id]}, not {area_id!r}"))
    level = g.get("suggested_level")
    if level is not None and level not in (1, 2, 3):
        f.append(Finding("groups.level", "warning", loc, f"suggested_level={level!r} must be 1, 2 or 3"))
    step = g.get("learning_step")
    if step not in LEARNING_STEPS:
        f.append(Finding("groups.learning_step", "error", loc, f"learning_step={step!r} not in {list(LEARNING_STEPS)}"))

    unit_ids = _as_list(g.get("unit_ids"), loc, "unit_ids", f)
    for uid in unit_ids:
        _check_unit_ref(ctx, uid, f"{loc} unit_ids", f, kept_required=True)
    for uid in _as_list(g.get("related_unit_ids"), loc, "related_unit_ids", f):
        _check_unit_ref(ctx, uid, f"{loc} related_unit_ids", f, kept_required=False)
    known_units = [u for u in unit_ids if isinstance(u, str) and u in ctx.inp.units]
    if not unit_ids:
        f.append(Finding("groups.units_empty", "error", loc, "group has no unit_ids"))
    mass = sum(ctx.inp.token_count(u) for u in known_units)
    if known_units and not GROUP_TOKENS[0] <= mass <= GROUP_TOKENS[1]:
        f.append(Finding("groups.mass", "warning", loc, f"token mass {mass} outside [{GROUP_TOKENS[0]}, {GROUP_TOKENS[1]}]"))

    concept_ids = _as_list(g.get("concept_ids"), loc, "concept_ids", f)
    if not concept_ids:
        f.append(Finding("groups.concepts_empty", "error", loc, "group names no concept_ids"))
    unknown_reported: set[str] = set()
    for cid in concept_ids:
        if not isinstance(cid, str) or cid not in ctx.inp.concepts:
            if cid not in unknown_reported:
                unknown_reported.add(cid)
                f.append(Finding("groups.unknown_concept", "error", f"{loc} concept_ids", f"concept id {cid!r} is not in concepts.json"))
    if len(concept_ids) > NEW_CONCEPTS_PER_GROUP_MAX:
        f.append(Finding("groups.concepts_max", "warning", loc, f"{len(concept_ids)} concepts > {NEW_CONCEPTS_PER_GROUP_MAX}"))

    subitem_ids: set[str] = set()
    kps = _dicts(_as_list(g.get("knowledge_points"), loc, "knowledge_points", f), loc, "knowledge_points", f)
    if not GROUP_KNOWLEDGE_POINTS[0] <= len(kps) <= GROUP_KNOWLEDGE_POINTS[1]:
        f.append(Finding("groups.knowledge_points", "error", loc, f"{len(kps)} knowledge points, expected {GROUP_KNOWLEDGE_POINTS[0]}-{GROUP_KNOWLEDGE_POINTS[1]}"))
    kp_ids: set[str] = set()
    for i, kp in enumerate(kps):
        kloc = f"{loc} knowledge_points[{i}]"
        _check_subitem_id(kp.get("id"), gid, "kp", kloc, subitem_ids, f)
        if isinstance(kp.get("id"), str):
            kp_ids.add(kp["id"])
        if not normalize_ws(str(kp.get("title") or "")):
            f.append(Finding("groups.kp_title", "error", kloc, "knowledge point has no title"))
        for uid in _as_list(kp.get("unit_ids"), kloc, "unit_ids", f):
            _check_unit_ref(ctx, uid, kloc, f, kept_required=False)

    objectives = _dicts(_as_list(g.get("objectives"), loc, "objectives", f), loc, "objectives", f)
    if not GROUP_OBJECTIVES[0] <= len(objectives) <= GROUP_OBJECTIVES[1]:
        f.append(Finding("groups.objectives", "error", loc, f"{len(objectives)} objectives, expected {GROUP_OBJECTIVES[0]}-{GROUP_OBJECTIVES[1]}"))
    for i, obj in enumerate(objectives):
        oloc = f"{loc} objectives[{i}]"
        _check_subitem_id(obj.get("id"), gid, "lo", oloc, subitem_ids, f)
        text = normalize_ws(str(obj.get("text") or ""))
        if not text:
            f.append(Finding("groups.objective_verb", "error", oloc, "objective text is empty"))
        else:
            verb = _objective_first_verb(text)
            if verb not in OBSERVABLE_VERBS:
                f.append(Finding("groups.objective_verb", "error", oloc, f"first verb {verb!r} is not an observable verb (OBSERVABLE_VERBS)"))
            weak = _weak_objective_verb(text)
            if weak:
                f.append(Finding("groups.objective_weak_verb", "error", oloc, f"weak verb {weak!r} in the first words of {text!r}"))
        bloom = obj.get("bloom_level")
        obj_units = _as_list(obj.get("unit_ids"), oloc, "unit_ids", f)
        for uid in obj_units:
            _check_unit_ref(ctx, uid, oloc, f, kept_required=False)
        if bloom not in BLOOM_ORDER:
            f.append(Finding("groups.objective_bloom", "error", oloc, f"bloom_level={bloom!r} not in {list(BLOOM_LEVELS)}"))
        else:
            basis = [u for u in (obj_units or known_units) if isinstance(u, str) and u in ctx.inp.units]
            ceilings = [c for c in (ctx.inp.ceiling(u) for u in basis) if c]
            if ceilings:
                ceiling = max(ceilings, key=BLOOM_ORDER.get)
                if BLOOM_ORDER[bloom] > BLOOM_ORDER[ceiling]:
                    f.append(Finding("groups.objective_above_ceiling", "error", oloc, f"bloom_level {bloom} exceeds the units' ceiling {ceiling}"))
        for cid in _as_list(obj.get("concept_ids"), oloc, "concept_ids", f):
            if (not isinstance(cid, str) or cid not in ctx.inp.concepts) and cid not in unknown_reported:
                unknown_reported.add(cid)
                f.append(Finding("groups.unknown_concept", "error", oloc, f"concept id {cid!r} is not in concepts.json"))
        for kid in _as_list(obj.get("knowledge_point_ids"), oloc, "knowledge_point_ids", f):
            if not isinstance(kid, str) or kid not in kp_ids:
                f.append(Finding("groups.kp_ref", "error", oloc, f"knowledge_point_id {kid!r} is not a knowledge point of this group"))

    watch = _dicts(_as_list(g.get("watch"), loc, "watch", f), loc, "watch", f)
    if len(watch) < GROUP_WATCH_MIN:
        f.append(Finding("groups.watch", "error", loc, f"{len(watch)} watch items < {GROUP_WATCH_MIN}"))
    for i, w in enumerate(watch):
        wloc = f"{loc} watch[{i}]"
        _check_subitem_id(w.get("id"), gid, "w", wloc, subitem_ids, f)
        if w.get("kind") not in WATCH_KINDS:
            f.append(Finding("groups.watch_kind", "error", wloc, f"kind={w.get('kind')!r} not in {list(WATCH_KINDS)}"))
        if not normalize_ws(str(w.get("text") or "")):
            f.append(Finding("groups.watch_text", "error", wloc, "watch item has no text"))
        for uid in _as_list(w.get("unit_ids"), wloc, "unit_ids", f):
            _check_unit_ref(ctx, uid, wloc, f, kept_required=False)

    generalization = normalize_ws(str(g.get("generalization") or ""))
    if not generalization:
        f.append(Finding("groups.generalization_missing", "error", loc, "generalization is empty"))
    else:
        mentioned = _concepts_mentioned(generalization, ctx.matcher)
        if len(mentioned) < 2:
            f.append(Finding(
                "groups.generalization_concepts", "error", loc,
                f"generalization names {len(mentioned)} concept label(s)/alias(es), needs >= 2: {generalization!r}",
            ))
        weak = sorted(set(_WORD_RE.findall(generalization.lower())) & WEAK_GENERALIZATION_VERBS)
        if weak:
            f.append(Finding("groups.generalization_weak_verb", "warning", loc, f"weak verb(s) {weak} in generalization — state a mechanism or consequence"))

    for i, dup in enumerate(_dicts(_as_list(g.get("duplicates"), loc, "duplicates", f), loc, "duplicates", f)):
        dloc = f"{loc} duplicates[{i}]"
        dup_units = _as_list(dup.get("unit_ids"), dloc, "unit_ids", f)
        for uid in dup_units:
            _check_unit_ref(ctx, uid, dloc, f, kept_required=False)
        if dup.get("prefer") not in dup_units:
            f.append(Finding("groups.duplicates_prefer", "error", dloc, f"prefer={dup.get('prefer')!r} must be one of the duplicate's unit_ids"))
    for i, conflict in enumerate(_dicts(_as_list(g.get("conflicts"), loc, "conflicts", f), loc, "conflicts", f)):
        for uid in _as_list(conflict.get("unit_ids"), f"{loc} conflicts[{i}]", "unit_ids", f):
            _check_unit_ref(ctx, uid, f"{loc} conflicts[{i}]", f, kept_required=False)

    seen_prereqs: set[str] = set()
    for i, p in enumerate(_dicts(_as_list(g.get("prerequisites"), loc, "prerequisites", f), loc, "prerequisites", f)):
        ploc = f"{loc} prerequisites[{i}]"
        target = p.get("group_id")
        if target == gid:
            f.append(Finding("groups.prereq_self", "error", ploc, "group lists itself as a prerequisite"))
        elif not isinstance(target, str) or target not in ctx.group_ids:
            f.append(Finding("groups.prereq_ref", "error", ploc, f"group_id {target!r} is not a group"))
        if isinstance(target, str):
            if target in seen_prereqs:
                f.append(Finding("groups.prereq_duplicate", "warning", ploc, f"prerequisite {target} is listed more than once"))
            seen_prereqs.add(target)
        if p.get("strength") not in PREREQ_STRENGTHS:
            f.append(Finding("groups.prereq_strength", "error", ploc, f"strength={p.get('strength')!r} not in {list(PREREQ_STRENGTHS)}"))
        status = p.get("status")
        if status is not None and status not in PREREQ_STATUSES:
            f.append(Finding("groups.prereq_status", "error", ploc, f"status={status!r} not in {list(PREREQ_STATUSES)}"))
        for uid in _as_list(p.get("evidence_unit_ids"), ploc, "evidence_unit_ids", f):
            _check_unit_ref(ctx, uid, ploc, f, kept_required=False)

    if g.get("budget") is not None and g["budget"] != _group_budget(g, ctx.inp):
        f.append(Finding("groups.budget_stale", "warning", loc, "budget differs from contracts.budget_for — rerun `regroup.py budget`"))
    return f


def _group_budget(g: dict, inp: _Inputs) -> dict:
    """Deterministic budget of a group (contracts.budget_for) — the same rule lessons.py
    uses for its page template: has_example is true when any primary/related unit has
    block_type "code" or a qualified unit_type of example/code."""
    prereqs = [p for p in (g.get("prerequisites") or []) if isinstance(p, dict)]
    return budget_for(
        len(g.get("knowledge_points") or []),
        len(g.get("objectives") or []),
        g.get("learning_step") or "",
        any(p.get("status") in _BUDGET_PREREQ_STATUSES for p in prereqs),
        has_example=group_has_example(g, inp.units, inp.qual),
    )


def _check_areas(ctx: _Ctx) -> list[Finding]:
    f: list[Finding] = []
    areas = [a for a in (ctx.data.get("areas") or []) if isinstance(a, dict)]
    if areas and not AREAS_PER_CURRICULUM[0] <= len(areas) <= AREAS_PER_CURRICULUM[1]:
        f.append(Finding("groups.areas_count", "warning", GROUPS_FILE, f"{len(areas)} areas, expected {AREAS_PER_CURRICULUM[0]}-{AREAS_PER_CURRICULUM[1]}"))
    if not areas:
        f.append(Finding("groups.areas_count", "error", GROUPS_FILE, "no areas declared"))
    seen_ids: set[str] = set()
    orders: Counter = Counter()
    for area in areas:
        aid = area.get("id")
        loc = f"{GROUPS_FILE} areas[{aid!r}]"
        if not isinstance(aid, str) or not AREA_ID_RE.match(aid):
            f.append(Finding("groups.area_id", "error", loc, f"area id {aid!r} does not match a:<slug>"))
        elif aid in seen_ids:
            f.append(Finding("groups.area_id", "error", loc, f"duplicate area id {aid}"))
        else:
            seen_ids.add(aid)
        if not normalize_ws(str(area.get("title") or "")):
            f.append(Finding("groups.title", "error", loc, "area title is empty"))
        if area.get("suggested_level") not in (None, 1, 2, 3):
            f.append(Finding("groups.level", "warning", loc, f"suggested_level={area.get('suggested_level')!r} must be 1, 2 or 3"))
        orders[_h(area.get("sort_order"))] += 1
        module_orders: Counter = Counter()
        for module in (area.get("modules") or []) if isinstance(area.get("modules"), list) else []:
            if not isinstance(module, dict):
                continue
            mid = module.get("id")
            mloc = f"{loc} modules[{mid!r}]"
            if not isinstance(mid, str) or not MODULE_ID_RE.match(mid):
                f.append(Finding("groups.module_id", "error", mloc, f"module id {mid!r} does not match m:<slug>"))
            if not normalize_ws(str(module.get("title") or "")):
                f.append(Finding("groups.title", "error", mloc, "module title is empty"))
            module_orders[_h(module.get("sort_order"))] += 1
        for order, n in module_orders.items():
            if n > 1:
                f.append(Finding("groups.sort_order", "error", loc, f"sort_order {order!r} is used by {n} modules"))
    for order, n in orders.items():
        if n > 1:
            f.append(Finding("groups.sort_order", "error", f"{GROUPS_FILE} areas", f"sort_order {order!r} is used by {n} areas"))
    return f


def _check_global(ctx: _Ctx) -> list[Finding]:
    """Cross-group checks: uniqueness, assignment, coverage, counts per area (DAG is separate)."""
    f = _check_areas(ctx)
    inp = ctx.inp

    id_counts = Counter(_h(g.get("id")) for g in ctx.groups)
    for gid, n in id_counts.items():
        if n > 1:
            f.append(Finding("groups.id", "error", f"{GROUPS_FILE} groups[{gid}]", f"group id appears {n} times"))
    title_locs: dict[str, list[str]] = defaultdict(list)
    for g in ctx.groups:
        title_locs[normalize_ws(str(g.get("title") or "")).lower()].append(str(g.get("id")))
    for title, gids in title_locs.items():
        if title and len(gids) > 1:
            f.append(Finding("groups.title_duplicate", "error", ", ".join(gids), f"title {title!r} is used by {len(gids)} groups — lesson titles must be unique"))

    order_counts: Counter = Counter((_h(g.get("area_id")), _h(g.get("module_id")), _h(g.get("sort_order"))) for g in ctx.groups)
    for (aid, mid, order), n in order_counts.items():
        if n > 1:
            parent = f"{aid}/{mid}" if mid else str(aid)
            f.append(Finding("groups.sort_order", "error", f"{GROUPS_FILE} groups under {parent}", f"sort_order {order!r} is used by {n} groups"))

    per_area = Counter(g.get("area_id") for g in ctx.groups if isinstance(g.get("area_id"), str) and g.get("area_id") in ctx.areas)
    for aid in ctx.areas:
        n = per_area.get(aid, 0)
        if not GROUPS_PER_AREA[0] <= n <= GROUPS_PER_AREA[1]:
            f.append(Finding("groups.per_area", "warning", f"{GROUPS_FILE} areas[{aid}]", f"{n} groups in area, expected {GROUPS_PER_AREA[0]}-{GROUPS_PER_AREA[1]}"))

    # Unit assignment: every kept unit is primary in exactly one group or dropped.
    assigned: dict[str, list[str]] = defaultdict(list)
    for g in ctx.groups:
        for uid in (g.get("unit_ids") or []) if isinstance(g.get("unit_ids"), list) else []:
            if isinstance(uid, str):
                assigned[uid].append(str(g.get("id")))
    dropped_ids: set[str] = set()
    for i, d in enumerate(_dicts(_as_list(ctx.data.get("dropped"), GROUPS_FILE, "dropped", f), GROUPS_FILE, "dropped", f)):
        uid = d.get("unit_id")
        _check_unit_ref(ctx, uid, f"{GROUPS_FILE} dropped[{i}]", f, kept_required=False)
        if isinstance(uid, str):
            dropped_ids.add(uid)
    for uid in inp.units_in_order():
        if not inp.is_kept(uid):
            continue
        owners = assigned.get(uid, [])
        n = len(owners) + (1 if uid in dropped_ids else 0)
        if n == 0:
            f.append(Finding("groups.unit_unassigned", "error", uid, "kept unit is primary in no group and not listed in dropped"))
        elif n > 1:
            where = owners + (["dropped"] if uid in dropped_ids else [])
            f.append(Finding("groups.unit_multi_assigned", "error", uid, f"kept unit is primary in {n} places: {', '.join(where)}"))

    covered = {_h(cid) for g in ctx.groups for cid in (g.get("concept_ids") or []) if isinstance(g.get("concept_ids"), list)}
    for concept in inp.concept_list:
        if concept.get("coverage") != "gap" and concept["id"] not in covered:
            f.append(Finding("groups.concept_uncovered", "warning", concept["id"], f"concept {concept.get('label')!r} (coverage={concept.get('coverage')!r}) appears in no group"))
    return f


def _dag_edges(ctx: _Ctx) -> tuple[list[tuple[str, str]], bool]:
    """(edges as (prerequisite, dependent), provisional) — committed edges, or all proposed ones before critique."""
    raw: list[tuple[str, str, str | None]] = []
    for g in ctx.groups:
        gid = g.get("id")
        for p in (g.get("prerequisites") or []) if isinstance(g.get("prerequisites"), list) else []:
            if isinstance(p, dict) and isinstance(gid, str) and isinstance(p.get("group_id"), str):
                raw.append((p["group_id"], gid, p.get("status")))
    critiqued = any(status in ("committed", "uncertain", "rejected") for _, _, status in raw)
    selected = [
        (a, b) for a, b, status in raw
        if (status == "committed" if critiqued else status in (None, "proposed"))
        and a != b and a in ctx.group_ids and b in ctx.group_ids
    ]
    return list(dict.fromkeys(selected)), not critiqued


def _reachable(start: str, succ: dict[str, list[str]], memo: dict[str, set[str]]) -> set[str]:
    if start in memo:
        return memo[start]
    memo[start] = set()  # guards against revisiting while on the stack (acyclic input expected)
    out: set[str] = set()
    for nxt in succ.get(start, []):
        out.add(nxt)
        out |= _reachable(nxt, succ, memo)
    memo[start] = out
    return out


def _find_cycle(succ: dict[str, list[str]], nodes: list[str]) -> list[str] | None:
    """First cycle found by DFS, as a node path closing on itself; None for a DAG."""
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = defaultdict(int)
    stack: list[str] = []

    def dfs(node: str) -> list[str] | None:
        color[node] = GRAY
        stack.append(node)
        for nxt in succ.get(node, []):
            if color[nxt] == GRAY:
                return stack[stack.index(nxt):] + [nxt]
            if color[nxt] == WHITE:
                found = dfs(nxt)
                if found:
                    return found
        stack.pop()
        color[node] = BLACK
        return None

    for node in nodes:
        if color[node] == WHITE:
            found = dfs(node)
            if found:
                return found
    return None


# Per-group prerequisite findings that make the edge set untrustworthy (no reduction is written).
_EDGE_SHAPE_CHECKS = {"groups.prereq_self", "groups.prereq_ref", "groups.prereq_strength", "groups.prereq_status"}


def _check_dag(ctx: _Ctx, edge_errors: bool) -> tuple[list[Finding], list[dict] | None]:
    """DAG hygiene over committed (or, before critique, proposed) edges.

    Returns (findings, transitive reduction) — the reduction is None when the
    edges carry errors (shape errors from the per-group pass, a cycle, fan-in fail).
    """
    f: list[Finding] = []
    edges, provisional = _dag_edges(ctx)
    note = " (provisional: no edge has a critique status yet, all proposed edges counted)" if provisional else ""
    nodes = sorted(ctx.group_ids)
    succ: dict[str, list[str]] = defaultdict(list)   # prerequisite -> dependents
    fan_in: Counter = Counter()                       # group -> number of prerequisites
    for a, b in edges:
        succ[a].append(b)
        fan_in[b] += 1

    cycle = _find_cycle(succ, nodes)
    if cycle:
        f.append(Finding("groups.prereq_cycle", "error", " -> ".join(cycle), f"prerequisite cycle{note}"))
        return f, None

    for gid, n in sorted(fan_in.items()):
        if n >= PREREQ_FANIN_FAIL:
            f.append(Finding("groups.prereq_fanin", "error", gid, f"{n} prerequisites >= {PREREQ_FANIN_FAIL}{note}"))
            edge_errors = True
        elif n > PREREQ_FANIN_WARN:
            f.append(Finding("groups.prereq_fanin", "warning", gid, f"{n} prerequisites > {PREREQ_FANIN_WARN}{note}"))

    if len(nodes) >= PREREQ_ROOTS_MIN_LESSONS:
        roots = [n for n in nodes if fan_in[n] == 0]
        if len(roots) < PREREQ_ROOTS_MIN:
            f.append(Finding("groups.prereq_roots", "warning", GROUPS_FILE, f"{len(roots)} root group(s) without prerequisites < {PREREQ_ROOTS_MIN} for {len(nodes)} groups{note}"))
        memo: dict[str, set[str]] = {}
        limit = PREREQ_BOTTLENECK_WARN * (len(nodes) - 1)
        for gid in nodes:
            dependents = _reachable(gid, succ, memo)
            if len(dependents) > limit:
                f.append(Finding("groups.prereq_bottleneck", "warning", gid, f"gates {len(dependents)} of {len(nodes) - 1} other groups (> {PREREQ_BOTTLENECK_WARN:.0%}){note}"))

    memo = {}
    reduced: list[dict] = []
    for a, b in edges:
        implied = any(b in _reachable(w, succ, memo) for w in succ[a] if w != b)
        if implied:
            f.append(Finding("groups.prereq_transitive", "info", f"{a} -> {b}", f"edge is implied by a longer path and left out of prerequisite_edges_reduced{note}"))
        else:
            reduced.append({"from": a, "to": b})
    reduced.sort(key=lambda e: (e["from"], e["to"]))
    return f, (None if edge_errors else reduced)


def check(input_dir: Path, write_reduced: bool = False, **_opts) -> list[Finding]:
    """Validate groups.json. With write_reduced, store the transitive reduction in groups.json."""
    input_dir = Path(input_dir)
    ctx, findings = _load_groups(input_dir)
    if ctx is None:
        return findings
    findings.extend(_check_global(ctx))
    for g in ctx.groups:
        findings.extend(_check_group(ctx, g))
    edge_errors = any(fd.check_id in _EDGE_SHAPE_CHECKS for fd in findings)
    dag_findings, reduced = _check_dag(ctx, edge_errors)
    findings.extend(dag_findings)
    if write_reduced and reduced is not None and ctx.data.get("prerequisite_edges_reduced") != reduced:
        ctx.data["prerequisite_edges_reduced"] = reduced
        dump_json(input_dir / GROUPS_FILE, ctx.data)
    return findings


def check_item(input_dir: Path, item_id: str, **_opts) -> list[Finding]:
    """Per-group checks for one group id (cross-group checks are left to check())."""
    ctx, findings = _load_groups(Path(input_dir))
    if ctx is None:
        return findings
    for g in ctx.groups:
        if g.get("id") == item_id:
            return findings + _check_group(ctx, g)
    return findings + [Finding("groups.unknown_group", "error", item_id, f"no group with id {item_id!r} in {GROUPS_FILE}")]


# ---------------------------------------------------------------------------
# budget
# ---------------------------------------------------------------------------


def apply_budget(input_dir: Path) -> tuple[list[dict], list[Finding]]:
    """Fill every group's script-owned "budget". Idempotent; returns table rows.

    Reads units/ and qualification/ so the budget's ``has_example`` (a worked_example page)
    follows the same rule as the lesson page template. Without any units nothing is written.
    """
    input_dir = Path(input_dir)
    path = input_dir / GROUPS_FILE
    if not path.exists():
        return [], [Finding("groups.file_missing", "error", GROUPS_FILE, f"{GROUPS_FILE} not found in {input_dir}")]
    try:
        data = load_json(path)
    except (OSError, json.JSONDecodeError) as e:
        return [], [Finding("groups.invalid_json", "error", GROUPS_FILE, str(e))]
    inp = _load_inputs(input_dir)
    findings = list(inp.findings)
    if not inp.units:
        findings.append(Finding("groups.missing_input", "error", f"{UNITS_DIR}/",
                                "budget needs units/ and qualification/ to decide has_example — nothing written"))
        return [], findings
    groups = [g for g in (data.get("groups") or []) if isinstance(g, dict)] if isinstance(data, dict) else []
    rows: list[dict] = []
    changed = False
    for g in groups:
        budget = _group_budget(g, inp)
        if g.get("budget") != budget:
            g["budget"] = budget
            changed = True
        rows.append({"group_id": g.get("id"), **budget})
    if changed:
        dump_json(path, data)
    return rows, findings


# ---------------------------------------------------------------------------
# commit-edges
# ---------------------------------------------------------------------------


def _edge_status(verdict: str | None, has_evidence: bool) -> str:
    if verdict == "confirmed":
        return "committed" if has_evidence else "uncertain"
    if verdict == "either":
        return "rejected"
    return "uncertain"


def commit_edges(input_dir: Path) -> tuple[list[dict], list[Finding]]:
    """Set prerequisites[].status from critique/groups/<item>.json edge verdicts. Idempotent."""
    input_dir = Path(input_dir)
    path = input_dir / GROUPS_FILE
    findings: list[Finding] = []
    if not path.exists():
        return [], [Finding("groups.file_missing", "error", GROUPS_FILE, f"{GROUPS_FILE} not found in {input_dir}")]
    try:
        data = load_json(path)
    except (OSError, json.JSONDecodeError) as e:
        return [], [Finding("groups.invalid_json", "error", GROUPS_FILE, str(e))]
    groups = [g for g in (data.get("groups") or []) if isinstance(g, dict)] if isinstance(data, dict) else []
    critique_dir = input_dir / CRITIQUE_DIR / CRITIQUE_STAGE_DIR
    rows: list[dict] = []
    changed = False
    for g in groups:
        gid = g.get("id")
        prereqs = [p for p in (g.get("prerequisites") or []) if isinstance(p, dict)]
        if not prereqs or not isinstance(gid, str):
            continue
        verdict_path = critique_dir / item_id_to_filename(gid)
        verdicts: dict[str, dict] = {}
        if verdict_path.exists():
            try:
                verdict_file = load_json(verdict_path)
                for e in (verdict_file.get("edges") or []) if isinstance(verdict_file, dict) else []:
                    if isinstance(e, dict) and isinstance(e.get("group_id"), str):
                        verdicts[e["group_id"]] = e
            except (OSError, json.JSONDecodeError) as e:
                findings.append(Finding("groups.invalid_json", "warning", str(verdict_path.relative_to(input_dir)), f"{e} — edges of {gid} set to uncertain"))
        else:
            findings.append(Finding("groups.verdict_missing", "warning", str(verdict_path.relative_to(input_dir)), f"no critique verdict for {gid} — its proposed edges are set to uncertain"))
        for p in prereqs:
            target = p.get("group_id")
            entry = verdicts.get(target) if isinstance(target, str) else None
            if entry is None and p.get("status") in ("committed", "rejected"):
                # An edge already decided in an earlier round is not re-listed in later critic work
                # files (critics only see proposed edges), so a missing verdict keeps its status.
                rows.append({"group_id": gid, "prerequisite": target, "verdict": "kept",
                             "evidence_units": len(set(p.get("evidence_unit_ids") or [])), "status": p.get("status")})
                continue
            verdict = entry.get("verdict") if entry else None
            if verdict is not None and verdict not in PREREQ_VALIDATOR_VERDICTS:
                findings.append(Finding("groups.verdict_invalid", "warning", f"{gid} <- {target}", f"verdict {verdict!r} not in {list(PREREQ_VALIDATOR_VERDICTS)} — treated as uncertain"))
                verdict = None
            evidence = [u for u in (p.get("evidence_unit_ids") or []) if isinstance(u, str)]
            critic_units = [u for u in ((entry or {}).get("unit_ids") or []) if isinstance(u, str)]
            status = _edge_status(verdict, bool(evidence or critic_units))
            if p.get("status") != status:
                p["status"] = status
                changed = True
            rows.append({
                "group_id": gid, "prerequisite": target, "verdict": verdict or "missing",
                "evidence_units": len(set(evidence) | set(critic_units)), "status": status,
            })
    if changed:
        dump_json(path, data)
    return rows, findings


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

_SEVERITY_TAG = {"error": "[ERROR]", "warning": "[WARN]", "info": "[INFO]"}
_SEVERITY_RANK = {"error": 0, "warning": 1, "info": 2}


def print_findings(findings: list[Finding]) -> tuple[int, int]:
    """Print findings (errors first) and the summary line; returns (errors, warnings)."""
    for fd in sorted(findings, key=lambda x: _SEVERITY_RANK.get(x.severity, 3)):
        print(f"{_SEVERITY_TAG.get(fd.severity, '[INFO]')} {fd.check_id} @ {fd.location}: {fd.message}")
    errors = sum(1 for fd in findings if fd.severity == "error")
    warnings = sum(1 for fd in findings if fd.severity == "warning")
    print(f"{errors} errors, {warnings} warnings")
    return errors, warnings


def _print_table(title: str, rows: list[dict]) -> None:
    from rich.console import Console
    from rich.table import Table

    console = Console()
    if not rows:
        console.print(f"{title}: nothing to show")
        return
    table = Table(title=title)
    for key in rows[0]:
        table.add_column(key)
    for row in rows:
        table.add_row(*(str(v) for v in row.values()))
    console.print(table)


def _cmd_draft(args: argparse.Namespace) -> int:
    result, findings = draft(args.input_dir)
    errors, _ = print_findings(findings)
    if result is None:
        return 1
    print(
        f"wrote {GROUPS_DRAFT_FILE}: {len(result['candidates'])} candidates, "
        f"{len(result['suggested_merges'])} merge / {len(result['suggested_splits'])} split suggestions, "
        f"{len(result['prerequisite_candidates'])} prerequisite candidates, "
        f"{len(result['unassigned_units'])} unassigned units"
    )
    return 1 if errors else 0


def _cmd_check(args: argparse.Namespace) -> int:
    if args.item:
        findings = check_item(args.input_dir, args.item)
    else:
        findings = check(args.input_dir, write_reduced=not args.no_write)
    errors, warnings = print_findings(findings)
    return 1 if errors or (args.strict and warnings) else 0


def _cmd_budget(args: argparse.Namespace) -> int:
    rows, findings = apply_budget(args.input_dir)
    errors, _ = print_findings(findings)
    _print_table("Budgets (ceilings, not quotas)", rows)
    return 1 if errors else 0


def _cmd_commit_edges(args: argparse.Namespace) -> int:
    rows, findings = commit_edges(args.input_dir)
    errors, _ = print_findings(findings)
    _print_table("Prerequisite edges", rows)
    return 1 if errors else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Lesson pipeline stage 3: lesson groups")
    sub = parser.add_subparsers(dest="command", required=True)

    p_draft = sub.add_parser("draft", help=f"write {GROUPS_DRAFT_FILE} from units + qualification + concepts")
    p_draft.add_argument("input_dir", type=Path)
    p_draft.set_defaults(func=_cmd_draft)

    p_check = sub.add_parser("check", help=f"gate {GROUPS_FILE}")
    p_check.add_argument("input_dir", type=Path)
    p_check.add_argument("--strict", action="store_true", help="warnings fail too")
    p_check.add_argument("--item", help="check one group id only (g:<slug>)")
    p_check.add_argument("--no-write", action="store_true", help="do not write prerequisite_edges_reduced")
    p_check.set_defaults(func=_cmd_check)

    p_budget = sub.add_parser("budget", help="fill each group's budget (script-owned)")
    p_budget.add_argument("input_dir", type=Path)
    p_budget.set_defaults(func=_cmd_budget)

    p_commit = sub.add_parser("commit-edges", help="set prerequisites[].status from critique verdicts")
    p_commit.add_argument("input_dir", type=Path)
    p_commit.set_defaults(func=_cmd_commit_edges)

    args = parser.parse_args(argv)
    if not args.input_dir.is_dir():
        print(f"error: {args.input_dir} is not a directory", file=sys.stderr)
        return 2
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
