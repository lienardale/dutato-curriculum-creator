"""
Stage 4 of the lesson pipeline — knowledge briefs (syntheses/).

PURE DATA UTILITY — no AI calls. The operating agent writes one brief per
group (`syntheses/<item_file>.json`, shape in docs/artifacts.md "Stage 4");
this script prepares the work files the agent reads and machine-checks the
briefs it writes.

Inputs (output/<name>/): groups.json, units/<source>.json,
qualification/<source>.json, concepts.json. Output: syntheses/.

Usage:
    python synthesize.py prepare output/<name>/ [--group g:ID ...] [--force]
        Write syntheses/work/<item_file>.json per group: the group entry, its
        units' full text (primary then related, ordered by source then id,
        duplicates marked), concept entries, neighbour titles, the writing
        rubric and any lessons_learned from an existing brief. Existing work
        files are skipped unless --force. Work files stay under ~12k tokens;
        related units are dropped first and the dropped ids recorded.

    python synthesize.py check output/<name>/ [--group g:ID] [--strict]
        Validate every brief: schema and enums, ids in-group, quotes match
        their cited units, citation/item/inference limits, objective and
        unit coverage, terminology and examples, and the verbatim gates of
        shared/verbatim.py on the brief prose vs. the cited units.

Exit codes: 0 = no errors, 1 = errors (or warnings with --strict), 2 = usage.

Module API (used by validate.py / critique.py):
    check(input_dir, group_id=None) -> list[Finding]
    check_item(input_dir, group_id) -> list[Finding]
    prepare(input_dir, group_ids=None, force=False, max_tokens=...) -> list[Finding]
    main(argv) -> int
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import _compat  # noqa: F401

from chunk import count_tokens
from contracts import (
    BRIEF_CITATIONS_PER_ITEM_MAX,
    BRIEF_COVERAGE_WARN,
    BRIEF_INFERENCE_SHARE_MAX,
    BRIEF_ITEM_KINDS,
    BRIEF_ITEM_WORDS,
    BRIEF_ITEMS_MAX,
    CONCEPTS_FILE,
    CONFIDENCE_LEVELS,
    CONTRACT_VERSION,
    GROUP_WATCH_MIN,
    GROUPS_FILE,
    QUALIFICATION_DIR,
    SUBITEM_ID_RE,
    SYNTHESES_DIR,
    UNITS_DIR,
    WORK_SUBDIR,
    Finding,
    dump_json,
    filename_to_item_id,
    item_id_to_filename,
    list_item_files,
    load_json,
    parse_unit_id,
    quote_in_unit,
    word_count,
)
from verbatim import GATE_SEVERITY, gate, verbatim_report

STAGE = "syntheses"
RUBRIC_PATH = Path(__file__).resolve().parent / "rubrics" / "synthesis.md"

# Work files are read whole by a sub-agent; keep them well inside its context.
WORK_FILE_MAX_TOKENS = 12_000
NEIGHBOURS_FOLLOWING = 2

# Units a brief may reference through `examples` (code is cited, never copied).
EXAMPLE_UNIT_TYPES = {"code", "example"}
EXAMPLE_BLOCK_TYPES = {"code"}

# Files in units/ that are not per-source unit files.
UNITS_NON_SOURCE_FILES = {"index.json", "dedup.json"}

# Double-quoted spans in brief prose are quoted definitions -> whitelisted.
_QUOTED_SPAN_RE = re.compile(r'["“]([^"“”]{3,}?)["”]')

BRIEF_REQUIRED_KEYS = ("group_id", "overview", "items")
ITEM_REQUIRED_KEYS = ("id", "kind", "text", "unit_ids")


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


@dataclass
class Context:
    """Everything a check or prepare needs from output/<name>/, loaded once."""

    input_dir: Path
    groups: dict[str, dict] = field(default_factory=dict)
    group_order: list[str] = field(default_factory=list)   # group ids in curriculum sort order
    units: dict[str, dict] = field(default_factory=dict)   # unit id -> unit (+ "source")
    qualification: dict[str, dict] = field(default_factory=dict)  # unit id -> qualification entry
    concepts: dict[str, dict] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)  # load-time problems

    @property
    def fatal(self) -> bool:
        return any(f.severity == "error" for f in self.findings)


def _read(path: Path, location: str, findings: list[Finding], check_id: str):
    """Load JSON or append a finding; returns None on failure."""
    try:
        return load_json(path)
    except FileNotFoundError:
        findings.append(Finding(check_id, "error", location, f"{path.name} not found"))
    except json.JSONDecodeError as e:
        findings.append(Finding(check_id, "error", location, f"invalid JSON: {e}"))
    except OSError as e:
        findings.append(Finding(check_id, "error", location, str(e)))
    return None


def _unit_sort_key(uid: str) -> tuple:
    parsed = parse_unit_id(uid)
    return (parsed[0], parsed[1], parsed[2]) if parsed else ("￿", 0, 0, uid)


_UNORDERED = 10**6  # sort position for entries without a usable sort_order


def _order(entry: dict, default: int = _UNORDERED) -> int:
    value = entry.get("sort_order")
    return value if isinstance(value, int) and not isinstance(value, bool) else default


def _group_sort_key(ctx_groups: dict, areas: list[dict]) -> Callable[[str], tuple]:
    """Curriculum order: area sort_order, module sort_order, group sort_order, then id."""
    area_order = {a.get("id"): _order(a, i) for i, a in enumerate(areas)}
    module_order = {
        m.get("id"): (_order(a, i), _order(m, j))
        for i, a in enumerate(areas)
        for j, m in enumerate(a.get("modules") or [])
    }

    def key(gid: str) -> tuple:
        g = ctx_groups[gid]
        return (
            area_order.get(g.get("area_id"), _UNORDERED),
            module_order.get(g.get("module_id"), (_UNORDERED, _UNORDERED)),
            _order(g),
            gid,
        )

    return key


def load_context(input_dir: Path, *, need_qualification: bool = True) -> Context:
    """Load groups, units, qualification and concepts. Missing groups/units are
    errors (nothing can be checked); missing qualification/concepts degrade
    with a warning."""
    input_dir = Path(input_dir)
    ctx = Context(input_dir=input_dir)

    groups_data = _read(input_dir / GROUPS_FILE, GROUPS_FILE, ctx.findings, f"{STAGE}.groups_missing")
    if isinstance(groups_data, dict):
        for i, g in enumerate(groups_data.get("groups") or []):
            gid = g.get("id")
            if not gid:
                ctx.findings.append(Finding(f"{STAGE}.groups_invalid", "error", f"{GROUPS_FILE} groups[{i}]", "group has no id"))
                continue
            ctx.groups[gid] = g
        ctx.group_order = sorted(ctx.groups, key=_group_sort_key(ctx.groups, groups_data.get("areas") or []))
    elif groups_data is not None:
        ctx.findings.append(Finding(f"{STAGE}.groups_invalid", "error", GROUPS_FILE, "expected an object with a 'groups' list"))

    units_dir = input_dir / UNITS_DIR
    if not units_dir.is_dir():
        ctx.findings.append(Finding(f"{STAGE}.units_missing", "error", f"{UNITS_DIR}/", "units directory not found — run `qualify.py prepare` first"))
    else:
        for path in sorted(units_dir.glob("*.json")):
            if path.name in UNITS_NON_SOURCE_FILES:
                continue
            data = _read(path, f"{UNITS_DIR}/{path.name}", ctx.findings, f"{STAGE}.units_invalid")
            if not isinstance(data, dict) or not isinstance(data.get("units"), list):
                if data is not None:
                    ctx.findings.append(Finding(f"{STAGE}.units_invalid", "warning", f"{UNITS_DIR}/{path.name}", "not a units file (no 'units' list) — skipped"))
                continue
            source = data.get("source") or path.stem
            for unit in data["units"]:
                uid = unit.get("id")
                if uid:
                    ctx.units[uid] = {**unit, "source": source}

    qual_dir = input_dir / QUALIFICATION_DIR
    if qual_dir.is_dir():
        for path in sorted(qual_dir.glob("*.json")):
            data = _read(path, f"{QUALIFICATION_DIR}/{path.name}", ctx.findings, f"{STAGE}.qualification_invalid")
            if isinstance(data, dict) and isinstance(data.get("units"), dict):
                ctx.qualification.update(data["units"])
    elif need_qualification:
        ctx.findings.append(Finding(f"{STAGE}.qualification_missing", "warning", f"{QUALIFICATION_DIR}/", "qualification directory not found — every unit is treated as kept"))

    concepts_path = input_dir / CONCEPTS_FILE
    if concepts_path.exists():
        data = _read(concepts_path, CONCEPTS_FILE, ctx.findings, f"{STAGE}.concepts_invalid")
        if isinstance(data, dict):
            for c in data.get("concepts") or []:
                if c.get("id"):
                    ctx.concepts[c["id"]] = c
    else:
        ctx.findings.append(Finding(f"{STAGE}.concepts_missing", "warning", CONCEPTS_FILE, "concepts.json not found — concept labels cannot be whitelisted or resolved"))

    return ctx


# ---------------------------------------------------------------------------
# Group helpers
# ---------------------------------------------------------------------------


def _group_unit_ids(group: dict) -> tuple[list[str], list[str]]:
    primary = [u for u in (group.get("unit_ids") or []) if isinstance(u, str)]
    related = [u for u in (group.get("related_unit_ids") or []) if isinstance(u, str) and u not in primary]
    return primary, related


def _non_preferred_duplicates(group: dict) -> dict[str, str]:
    """unit id -> preferred unit id, for every duplicate the brief should not cite."""
    out: dict[str, str] = {}
    for dup in group.get("duplicates") or []:
        prefer = dup.get("prefer")
        for uid in dup.get("unit_ids") or []:
            if prefer and uid != prefer:
                out[uid] = prefer
    return out


def _is_kept(ctx: Context, uid: str) -> bool:
    entry = ctx.qualification.get(uid)
    return entry.get("keep", True) is not False if isinstance(entry, dict) else True


def _is_example_unit(ctx: Context, uid: str) -> bool:
    unit = ctx.units.get(uid) or {}
    entry = ctx.qualification.get(uid) or {}
    return unit.get("block_type") in EXAMPLE_BLOCK_TYPES or entry.get("unit_type") in EXAMPLE_UNIT_TYPES


def _select_groups(ctx: Context, group_ids: list[str] | None) -> tuple[list[str], list[Finding]]:
    if not group_ids:
        return list(ctx.group_order), []
    findings = [
        Finding(f"{STAGE}.unknown_group", "error", GROUPS_FILE, f"group {gid!r} is not in groups.json")
        for gid in group_ids if gid not in ctx.groups
    ]
    return [gid for gid in ctx.group_order if gid in set(group_ids)], findings


# ---------------------------------------------------------------------------
# prepare
# ---------------------------------------------------------------------------


def _work_unit(ctx: Context, uid: str, role: str, duplicates: dict[str, str]) -> dict:
    unit = ctx.units[uid]
    entry = ctx.qualification.get(uid)
    qualification = None
    if isinstance(entry, dict):
        qualification = {
            "unit_type": entry.get("unit_type"),
            "knowledge_level": entry.get("knowledge_level"),
            "concepts": [c.get("concept_id") for c in (entry.get("concepts") or []) if isinstance(c, dict)],
            "quality": entry.get("quality"),
        }
    out = {
        "id": uid,
        "role": role,
        "block_type": unit.get("block_type"),
        "text": unit.get("text", ""),
        "token_count": unit.get("token_count") if unit.get("token_count") is not None else count_tokens(unit.get("text", "")),
        "heading_path": unit.get("heading_path") or [],
        "source": unit.get("source"),
        "images": unit.get("images") or [],
        "qualification": qualification,
    }
    if uid in duplicates:
        out["duplicate_of_preferred"] = True
        out["preferred_unit_id"] = duplicates[uid]
    return out


def _neighbours(ctx: Context, gid: str) -> dict:
    group = ctx.groups[gid]
    prereq_titles = [
        ctx.groups[p["group_id"]].get("title", p["group_id"])
        for p in (group.get("prerequisites") or [])
        if isinstance(p, dict) and p.get("group_id") in ctx.groups
    ]
    pos = ctx.group_order.index(gid)
    following = [
        ctx.groups[g].get("title", g)
        for g in ctx.group_order[pos + 1:pos + 1 + NEIGHBOURS_FOLLOWING]
    ]
    return {"prerequisites": prereq_titles, "following": following}


def _rubric_text(findings: list[Finding]) -> str:
    try:
        return RUBRIC_PATH.read_text(encoding="utf-8")
    except OSError:
        findings.append(Finding(f"{STAGE}.prepare.rubric_missing", "warning", str(RUBRIC_PATH), "rubric file not found — work files carry an empty rubric"))
        return ""


def build_work_file(ctx: Context, gid: str, rubric: str, max_tokens: int, findings: list[Finding]) -> dict:
    """Assemble the work file for one group (pure function of the context)."""
    group = ctx.groups[gid]
    loc = f"{GROUPS_FILE} {gid}"
    primary, related = _group_unit_ids(group)
    duplicates = _non_preferred_duplicates(group)

    units: list[dict] = []
    for role, ids in (("primary", primary), ("related", related)):
        for uid in sorted(ids, key=_unit_sort_key):
            if uid not in ctx.units:
                findings.append(Finding(f"{STAGE}.prepare.unit_missing", "warning", loc, f"{role} unit {uid} is not in units/ — omitted from the work file"))
                continue
            if uid not in ctx.qualification and ctx.qualification:
                findings.append(Finding(f"{STAGE}.prepare.unqualified", "warning", loc, f"unit {uid} has no qualification entry"))
            units.append(_work_unit(ctx, uid, role, duplicates))

    concepts = []
    for cid in group.get("concept_ids") or []:
        if cid in ctx.concepts:
            concepts.append(ctx.concepts[cid])
        else:
            findings.append(Finding(f"{STAGE}.prepare.concept_missing", "warning", loc, f"concept {cid} is not in concepts.json"))

    lessons_learned: list = []
    existing = ctx.input_dir / SYNTHESES_DIR / item_id_to_filename(gid)
    if existing.exists():
        try:
            prior = load_json(existing)
            lessons_learned = prior.get("lessons_learned") or [] if isinstance(prior, dict) else []
        except (OSError, json.JSONDecodeError) as e:
            findings.append(Finding(f"{STAGE}.prepare.existing_unreadable", "warning", f"{SYNTHESES_DIR}/{existing.name}", f"existing brief unreadable ({e}); lessons_learned not carried over"))

    work = {
        "contract_version": CONTRACT_VERSION,
        "group_id": gid,
        "group": group,
        "units": units,
        "concepts": concepts,
        "neighbours": _neighbours(ctx, gid),
        "rubric": rubric,
        "lessons_learned": lessons_learned,
        "truncated": False,
        "dropped_unit_ids": [],
    }

    # Token budget: drop from the end (related units first, then trailing primary).
    base_tokens = count_tokens(json.dumps({**work, "units": []}, ensure_ascii=False))
    unit_tokens = [count_tokens(json.dumps(u, ensure_ascii=False)) for u in units]
    total = base_tokens + sum(unit_tokens)
    dropped: list[str] = []
    while total > max_tokens and units:
        dropped.append(units.pop()["id"])
        total -= unit_tokens.pop()
    if dropped:
        work["truncated"] = True
        work["dropped_unit_ids"] = dropped
        findings.append(Finding(f"{STAGE}.prepare.truncated", "warning", loc, f"work file over {max_tokens} tokens — dropped {len(dropped)} unit(s): {', '.join(dropped)}"))
    return work


def prepare(
    input_dir: Path,
    group_ids: list[str] | None = None,
    force: bool = False,
    max_tokens: int = WORK_FILE_MAX_TOKENS,
) -> list[Finding]:
    """Write syntheses/work/<item_file>.json for the selected groups."""
    input_dir = Path(input_dir)
    ctx = load_context(input_dir)
    findings = list(ctx.findings)
    if ctx.fatal:
        return findings

    selected, select_findings = _select_groups(ctx, group_ids)
    findings.extend(select_findings)
    rubric = _rubric_text(findings)
    work_dir = input_dir / SYNTHESES_DIR / WORK_SUBDIR

    for gid in selected:
        path = work_dir / item_id_to_filename(gid)
        rel = f"{SYNTHESES_DIR}/{WORK_SUBDIR}/{path.name}"
        if path.exists() and not force:
            findings.append(Finding(f"{STAGE}.prepare.skipped", "info", rel, "work file exists (use --force to rewrite)"))
            continue
        work = build_work_file(ctx, gid, rubric, max_tokens, findings)
        dump_json(path, work)
        findings.append(Finding(f"{STAGE}.prepare.written", "info", rel, f"{len(work['units'])} unit(s), {len(work['concepts'])} concept(s)"))
    return findings


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------


def _check_brief(ctx: Context, gid: str, brief: dict, rel: str) -> list[Finding]:
    """All deterministic gates for one brief against its group."""
    findings: list[Finding] = []
    group = ctx.groups[gid]
    primary, related = _group_unit_ids(group)
    in_group = set(primary) | set(related)
    objective_ids = {o.get("id") for o in (group.get("objectives") or []) if isinstance(o, dict)}
    kp_ids = {k.get("id") for k in (group.get("knowledge_points") or []) if isinstance(k, dict)}
    concept_ids = set(group.get("concept_ids") or [])
    def err(cid: str, loc: str, msg: str) -> None:
        findings.append(Finding(f"{STAGE}.{cid}", "error", loc, msg))

    def warn(cid: str, loc: str, msg: str) -> None:
        findings.append(Finding(f"{STAGE}.{cid}", "warning", loc, msg))

    # --- top-level schema ---
    for key in BRIEF_REQUIRED_KEYS:
        if key not in brief:
            err("schema", rel, f"missing required key {key!r}")
    if brief.get("contract_version") != CONTRACT_VERSION:
        warn("contract_version", rel, f"contract_version={brief.get('contract_version')!r}, expected {CONTRACT_VERSION}")
    revision = brief.get("revision", 0)
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        err("schema", rel, f"revision must be an integer >= 0, got {revision!r}")
    if not isinstance(brief.get("overview"), str) or not brief.get("overview", "").strip():
        warn("overview_empty", rel, "overview is empty")
    for key in ("items", "examples", "terminology", "lessons_learned"):
        if key in brief and not isinstance(brief[key], list):
            err("schema", rel, f"{key!r} must be a list")

    items = brief.get("items") if isinstance(brief.get("items"), list) else []
    examples = brief.get("examples") if isinstance(brief.get("examples"), list) else []
    terminology = brief.get("terminology") if isinstance(brief.get("terminology"), list) else []

    if len(items) > BRIEF_ITEMS_MAX:
        err("items_max", rel, f"{len(items)} items > {BRIEF_ITEMS_MAX}")

    # --- items ---
    seen_ids: set[str] = set()
    cited_units: set[str] = set()
    lo_covered_by_map: set[str] = set()
    n_watch = n_inference = 0
    summary_parts: list[str] = [brief.get("overview") or ""]

    def unit_ok(uid: object, loc: str) -> bool:
        if not isinstance(uid, str) or uid not in in_group:
            err("unit_out_of_group", loc, f"unit {uid!r} is not in the group's unit_ids or related_unit_ids")
            return False
        if uid not in ctx.units:
            err("unit_unknown", loc, f"unit {uid} is in groups.json but not in units/")
            return False
        return True

    for i, item in enumerate(items):
        iid = item.get("id") if isinstance(item, dict) else None
        loc = f"{rel} items[{i}]" + (f" ({iid})" if iid else "")
        if not isinstance(item, dict):
            err("schema", loc, "item must be an object")
            continue
        for key in ITEM_REQUIRED_KEYS:
            if key not in item:
                err("schema", loc, f"item missing required key {key!r}")

        m = SUBITEM_ID_RE.match(iid or "")
        if not m or m.group("kind") != "b" or m.group("variant") or not (iid or "").startswith(f"{gid}#"):
            err("item_id", loc, f"item id {iid!r} must look like {gid}#b<n>")
        elif iid in seen_ids:
            err("item_id_duplicate", loc, f"item id {iid} appears more than once")
        else:
            seen_ids.add(iid)

        kind = item.get("kind")
        if kind not in BRIEF_ITEM_KINDS:
            err("item_kind", loc, f"kind={kind!r} not in {list(BRIEF_ITEM_KINDS)}")
        confidence = item.get("confidence")
        if confidence not in CONFIDENCE_LEVELS:
            err("confidence", loc, f"confidence={confidence!r} not in {list(CONFIDENCE_LEVELS)}")

        text = item.get("text") if isinstance(item.get("text"), str) else ""
        summary_parts.append(text)
        words = word_count(text)
        if not (BRIEF_ITEM_WORDS[0] <= words <= BRIEF_ITEM_WORDS[1]):
            warn("item_words", loc, f"{words} words, expected {BRIEF_ITEM_WORDS[0]}-{BRIEF_ITEM_WORDS[1]}")

        unit_ids = item.get("unit_ids") if isinstance(item.get("unit_ids"), list) else []
        if not unit_ids:
            err("item_no_units", loc, "item cites no unit")
        if len(unit_ids) > BRIEF_CITATIONS_PER_ITEM_MAX:
            err("citations", loc, f"{len(unit_ids)} citations > {BRIEF_CITATIONS_PER_ITEM_MAX}")
        valid_units = [u for u in unit_ids if unit_ok(u, loc)]
        cited_units.update(valid_units)

        quote = item.get("quote") if isinstance(item.get("quote"), str) else ""
        if kind == "inference":
            n_inference += 1
            if len(set(unit_ids)) < 2:
                err("inference_single_source", loc, "inference items must join >= 2 cited units")
            if quote.strip() and valid_units and not any(quote_in_unit(quote, ctx.units[u].get("text", "")) for u in valid_units):
                err("quote_mismatch", loc, f"quote {quote!r} is not found in any cited unit")
        elif kind in ("map", "watch"):
            if kind == "watch":
                n_watch += 1
            if not quote.strip():
                err("quote_missing", loc, f"{kind} items need a verbatim quote from a cited unit")
            elif valid_units and not any(quote_in_unit(quote, ctx.units[u].get("text", "")) for u in valid_units):
                err("quote_mismatch", loc, f"quote {quote!r} is not found in any cited unit")

        if confidence == "stated":
            sources = {ctx.units[u].get("source") for u in valid_units}
            if len(sources) < 2:
                warn("confidence_overstated", loc, "confidence 'stated' needs cited units from >= 2 distinct sources; use 'attributed'")

        lo_ids = item.get("lo_ids") if isinstance(item.get("lo_ids"), list) else []
        for lo in lo_ids:
            if lo not in objective_ids:
                err("lo_unknown", loc, f"lo_id {lo!r} is not an objective of {gid}")
            elif kind == "map":
                lo_covered_by_map.add(lo)
        kp = item.get("knowledge_point_id")
        if kp is not None and kp not in kp_ids:
            err("kp_unknown", loc, f"knowledge_point_id {kp!r} is not a knowledge point of {gid}")
        for cid in item.get("concept_ids") or []:
            if cid not in concept_ids:
                err("concept_unknown", loc, f"concept_id {cid!r} is not in the group's concept_ids")

    if items and n_inference / len(items) > BRIEF_INFERENCE_SHARE_MAX:
        err("inference_share", rel, f"{n_inference}/{len(items)} items are inferences ({n_inference / len(items):.0%} > {BRIEF_INFERENCE_SHARE_MAX:.0%})")
    if n_watch < GROUP_WATCH_MIN:
        warn("watch_few", rel, f"{n_watch} watch item(s), expected >= {GROUP_WATCH_MIN}")
    for lo in sorted(objective_ids - lo_covered_by_map, key=str):
        err("objective_uncovered", rel, f"objective {lo} has no map item (lo_ids)")

    # --- examples ---
    for i, ex in enumerate(examples):
        eid = ex.get("id") if isinstance(ex, dict) else None
        loc = f"{rel} examples[{i}]" + (f" ({eid})" if eid else "")
        if not isinstance(ex, dict):
            err("schema", loc, "example must be an object")
            continue
        m = SUBITEM_ID_RE.match(eid or "")
        if not m or m.group("kind") != "ex" or not (eid or "").startswith(f"{gid}#"):
            err("item_id", loc, f"example id {eid!r} must look like {gid}#ex<n>")
        elif eid in seen_ids:
            err("item_id_duplicate", loc, f"id {eid} appears more than once")
        else:
            seen_ids.add(eid)
        uid = ex.get("unit_id")
        if unit_ok(uid, loc):
            cited_units.add(uid)
            if not _is_example_unit(ctx, uid):
                warn("example_not_code", loc, f"unit {uid} is not a code/example unit")

    # --- terminology ---
    for i, term in enumerate(terminology):
        loc = f"{rel} terminology[{i}]"
        if not isinstance(term, dict):
            err("schema", loc, "terminology entry must be an object")
            continue
        if not isinstance(term.get("term"), str) or not term["term"].strip():
            err("terminology_term", loc, "terminology entry has no term")
        definition = term.get("definition") if isinstance(term.get("definition"), str) else ""
        if not definition.strip():
            warn("terminology_definition", loc, f"term {term.get('term')!r} has no definition")
        summary_parts.append(definition)
        uid = term.get("unit_id")
        if uid is None:
            err("terminology_uncited", loc, f"term {term.get('term')!r} cites no unit")
        elif unit_ok(uid, loc):
            cited_units.add(uid)

    # --- coverage of kept primary units (non-preferred duplicates excluded) ---
    duplicates = _non_preferred_duplicates(group)
    kept = [u for u in primary if _is_kept(ctx, u) and u not in duplicates]
    if kept:
        coverage = sum(1 for u in kept if u in cited_units) / len(kept)
        if coverage < BRIEF_COVERAGE_WARN:
            uncited = [u for u in kept if u not in cited_units]
            warn("coverage", rel, f"{coverage:.0%} of kept units cited (< {BRIEF_COVERAGE_WARN:.0%}); uncited: {', '.join(uncited)}")

    # --- verbatim gates on the prose vs. the cited units ---
    summary = "\n".join(p for p in summary_parts if p)
    whitelist: set[str] = set()
    for cid in concept_ids:
        c = ctx.concepts.get(cid) or {}
        if c.get("label"):
            whitelist.add(c["label"])
        whitelist.update(a for a in (c.get("aliases") or []) if isinstance(a, str))
    whitelist.update(t["term"] for t in terminology if isinstance(t, dict) and isinstance(t.get("term"), str))
    whitelist.update(_QUOTED_SPAN_RE.findall(summary))
    sources = [ctx.units[u].get("text", "") for u in sorted(cited_units, key=_unit_sort_key) if u in ctx.units]
    if summary.strip() and sources:
        report = verbatim_report(summary, sources, whitelist)
        for check_id, message in gate(report, "brief"):
            findings.append(Finding(f"{STAGE}.{check_id}", GATE_SEVERITY[check_id], rel, message))

    return findings


def _check_group(ctx: Context, gid: str) -> list[Finding]:
    """Load and check the brief for one known group; missing brief is an error."""
    path = ctx.input_dir / SYNTHESES_DIR / item_id_to_filename(gid)
    rel = f"{SYNTHESES_DIR}/{path.name}"
    if not path.exists():
        return [Finding(f"{STAGE}.missing", "error", rel, f"no brief for group {gid}")]
    findings: list[Finding] = []
    brief = _read(path, rel, findings, f"{STAGE}.invalid_json")
    if brief is None:
        return findings
    if not isinstance(brief, dict):
        return [Finding(f"{STAGE}.schema", "error", rel, "brief must be a JSON object")]
    if brief.get("group_id") != gid:
        findings.append(Finding(f"{STAGE}.group_id_mismatch", "error", rel, f"group_id={brief.get('group_id')!r} does not match the file name ({gid})"))
    findings.extend(_check_brief(ctx, gid, brief, rel))
    return findings


def check(input_dir: Path, group_id: str | None = None, **_opts) -> list[Finding]:
    """Validate every brief in syntheses/ (or one group with group_id)."""
    input_dir = Path(input_dir)
    ctx = load_context(input_dir)
    findings = list(ctx.findings)
    if ctx.fatal:
        return findings

    selected, select_findings = _select_groups(ctx, [group_id] if group_id else None)
    findings.extend(select_findings)
    for gid in selected:
        findings.extend(_check_group(ctx, gid))

    if group_id is None:
        for path in list_item_files(input_dir / SYNTHESES_DIR):
            iid = filename_to_item_id(path.name)
            if iid not in ctx.groups:
                findings.append(Finding(f"{STAGE}.unknown_group", "error", f"{SYNTHESES_DIR}/{path.name}", f"brief for {iid!r}, which is not a group in groups.json"))
    return findings


def check_item(input_dir: Path, item_id: str, **_opts) -> list[Finding]:
    """Validate the brief of one group."""
    return check(input_dir, group_id=item_id)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

_LABEL = {"error": "ERROR", "warning": "WARN", "info": "INFO"}


def print_findings(findings: list[Finding]) -> tuple[int, int]:
    order = {"error": 0, "warning": 1, "info": 2}
    for f in sorted(findings, key=lambda f: (order.get(f.severity, 3), f.location, f.check_id)):
        print(f"[{_LABEL.get(f.severity, f.severity.upper())}] {f.check_id} @ {f.location}: {f.message}")
    n_err = sum(1 for f in findings if f.severity == "error")
    n_warn = sum(1 for f in findings if f.severity == "warning")
    print(f"{n_err} errors, {n_warn} warnings")
    return n_err, n_warn


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stage 4: knowledge briefs (prepare work files / check briefs)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_prep = sub.add_parser("prepare", help="write syntheses/work/<item>.json per group")
    p_prep.add_argument("input_dir", type=Path, help="output/<name>/ directory")
    p_prep.add_argument("--group", action="append", metavar="g:ID", help="only this group (repeatable)")
    p_prep.add_argument("--force", action="store_true", help="rewrite existing work files")
    p_prep.add_argument("--max-tokens", type=int, default=WORK_FILE_MAX_TOKENS, help=f"work file token budget (default {WORK_FILE_MAX_TOKENS})")

    p_check = sub.add_parser("check", help="validate syntheses/<item>.json briefs")
    p_check.add_argument("input_dir", type=Path, help="output/<name>/ directory")
    p_check.add_argument("--group", metavar="g:ID", help="only this group")
    p_check.add_argument("--strict", action="store_true", help="warnings fail too")

    args = parser.parse_args(argv)
    if not args.input_dir.is_dir():
        print(f"error: {args.input_dir} is not a directory", file=sys.stderr)
        return 2

    if args.command == "prepare":
        findings = prepare(args.input_dir, group_ids=args.group, force=args.force, max_tokens=args.max_tokens)
        n_err, _ = print_findings(findings)
        return 1 if n_err else 0

    findings = check(args.input_dir, group_id=args.group)
    n_err, n_warn = print_findings(findings)
    return 1 if n_err or (args.strict and n_warn) else 0


if __name__ == "__main__":
    sys.exit(main())
