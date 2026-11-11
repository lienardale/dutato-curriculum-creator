"""
Pipeline status checker — shows where a curriculum stands in the pipeline.

A new agent session should run this FIRST to understand what's already done
and where to resume.

Two pipelines are reported (``manifest.pipeline``, absent => raw):

* raw — the legacy stage list (STAGES below), output unchanged;
* lesson — ``contracts.LESSON_STAGES`` with progress counts per stage
  (sources with units, qualified units, concepts, groups and budgets,
  briefs, outline/full lessons with their critique status, build, questions,
  cards), a critique line summarizing ``critique/summary.json`` per stage,
  the FAILED items by id, and a RESUME marker at the first incomplete
  non-optional stage.

Usage:
  uv run python status.py output/my-curriculum/
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import _compat  # noqa: F401

from contracts import (
    CARDS_DIR,
    CONCEPTS_FILE,
    CRITIQUE_STATUSES,
    CRITIQUE_SUMMARY,
    CRITIQUED_STAGES,
    GROUP_ID_RE,
    GROUPS_FILE,
    LESSON_STAGES,
    LESSONS_DIR,
    PIPELINE_LESSON,
    QUALIFICATION_DIR,
    QUESTIONS_DIR,
    SCOPE_FILE,
    SYNTHESES_DIR,
    UNITS_DIR,
    filename_to_item_id,
    item_id_to_filename,
    list_item_files,
    normalize_ws,
    pipeline_mode,
    source_key,
)


# Pipeline stages in order
STAGES = [
    ("manifest", "manifest.json", "Manifest created"),
    ("extract", "extracted/", "Sources extracted"),
    ("analyze_images", "image_analysis.json", "Images analyzed (optional)"),
    ("explore", "exploration.json", "Content explored"),
    ("structure", "structure.json", "Topic hierarchy built"),
    ("chunk", "chunks.json", "Content chunked"),
    ("exercises", "exercises.json", "Practice exercises generated"),
    ("validate", "validation_report.json", "Validation report (optional)"),
    ("review", "review.json", "Quality reviewed"),
    ("upload", "upload_result.json", "Uploaded to Supabase"),
    ("condense", "condensation_plan.json", "Condensed variants planned (optional)"),
]

# Files inside units/ that are not per-source units files.
_NON_SOURCE_UNIT_FILES = {"index.json", "dedup.json"}
_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")


def check_status(output_dir: Path) -> dict:
    """Check pipeline status for a curriculum output directory."""
    if output_dir.exists() and pipeline_mode(output_dir) == PIPELINE_LESSON:
        return check_lesson_status(output_dir)

    status = {
        "directory": str(output_dir),
        "exists": output_dir.exists(),
        "stages": {},
        "current_stage": None,
        "next_stage": None,
    }

    if not output_dir.exists():
        status["next_stage"] = "manifest"
        return status

    for stage_name, artifact, description in STAGES:
        artifact_path = output_dir / artifact

        if stage_name == "extract":
            # Check for extracted files (directory with JSONs)
            exists = artifact_path.is_dir() and any(artifact_path.glob("*.json"))
            detail = {}
            if exists:
                files = list(artifact_path.glob("*.json"))
                detail["file_count"] = len(files)
                detail["files"] = [f.name for f in sorted(files)]
                # Count images
                images_dir = artifact_path / "images"
                if images_dir.is_dir():
                    img_files = [
                        f for f in images_dir.iterdir()
                        if f.suffix.lower() in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg")
                    ]
                    if img_files:
                        detail["image_count"] = len(img_files)
        else:
            exists = artifact_path.exists()
            detail = {}
            if exists and artifact_path.suffix == ".json":
                try:
                    with open(artifact_path) as f:
                        data = json.load(f)
                    if isinstance(data, dict):
                        detail["keys"] = list(data.keys())
                    elif isinstance(data, list):
                        detail["count"] = len(data)
                    if stage_name == "review" and isinstance(data, dict):
                        detail["approved"] = data.get("approved")
                    if stage_name == "validate" and isinstance(data, dict):
                        summary = data.get("summary", {})
                        detail["errors"] = summary.get("errors")
                        detail["warnings"] = summary.get("warnings")
                except (json.JSONDecodeError, OSError):
                    detail["error"] = "Could not read JSON"

        status["stages"][stage_name] = {
            "complete": exists,
            "artifact": str(artifact),
            "description": description,
            **detail,
        }

        if exists:
            status["current_stage"] = stage_name
        elif status["next_stage"] is None:
            # Optional stages don't block progression
            optional_stages = {"analyze_images", "exercises", "validate", "condense"}
            if stage_name not in optional_stages:
                status["next_stage"] = stage_name

    return status


# ---------------------------------------------------------------------------
# Lesson pipeline
# ---------------------------------------------------------------------------


def _read_json(path: Path):
    """JSON content or None when the file is absent or unreadable."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _extracted_sources(output_dir: Path) -> list[str]:
    """Source keys of extracted/*.json, narrowed by scope.json when it exists."""
    extracted = output_dir / "extracted"
    if not extracted.is_dir():
        return []
    sources = [source_key(p.name) for p in sorted(extracted.glob("*.json"))]
    scope = _read_json(output_dir / SCOPE_FILE)
    if not isinstance(scope, dict):
        return sources
    include = scope.get("include_sources")
    if isinstance(include, list):
        sources = [s for s in sources if s in include]
    sections = scope.get("include_sections")
    if isinstance(sections, dict):
        sources = [s for s in sources if s not in sections or sections[s]]
    return sources


def _unnormalized_titles(output_dir: Path) -> int | None:
    """Section titles that still carry a chapter prefix or look like book meta (None when
    normalize_titles is unavailable)."""
    try:
        from normalize_titles import PREFIX_PATTERNS, is_book_meta
    except ImportError:
        return None
    count = 0
    for path in sorted((output_dir / "extracted").glob("*.json")):
        data = _read_json(path)
        for section in (data.get("sections") or []) if isinstance(data, dict) else []:
            title = normalize_ws(str((section or {}).get("title") or "")) if isinstance(section, dict) else ""
            if title and (is_book_meta(title) or any(p.sub("", title) != title for p in PREFIX_PATTERNS)):
                count += 1
    return count


def _units_by_source(output_dir: Path) -> dict[str, list[str]]:
    """source -> unit ids from units/<source>.json."""
    out: dict[str, list[str]] = {}
    for path in list_item_files(output_dir / UNITS_DIR):
        if path.name in _NON_SOURCE_UNIT_FILES:
            continue
        data = _read_json(path)
        units = data.get("units") if isinstance(data, dict) else None
        out[source_key(path.name)] = [u["id"] for u in (units or []) if isinstance(u, dict) and isinstance(u.get("id"), str)]
    return out


def _group_ids(output_dir: Path) -> tuple[list[str], dict]:
    data = _read_json(output_dir / GROUPS_FILE)
    if not isinstance(data, dict):
        return [], {}
    groups = [g for g in (data.get("groups") or []) if isinstance(g, dict) and isinstance(g.get("id"), str)]
    return [g["id"] for g in groups], {g["id"]: g for g in groups}


def _item_ids(output_dir: Path, stage_dir: str) -> set[str]:
    """Authored per-item ids of a ``g:<slug>``-keyed stage directory (helper files such as
    ``lessons/anchor.json`` do not map back to an id and are ignored)."""
    return {lid for lid in (filename_to_item_id(p.name) for p in list_item_files(output_dir / stage_dir))
            if GROUP_ID_RE.match(lid)}


def _lesson_is_full(lesson) -> bool:
    pages = lesson.get("pages") if isinstance(lesson, dict) else None
    return bool(pages) and all(isinstance(p, dict) and isinstance(p.get("markdown"), str) and p["markdown"].strip() for p in pages)


def _critique_summary(output_dir: Path) -> dict:
    data = _read_json(output_dir / CRITIQUE_SUMMARY)
    stages = data.get("stages") if isinstance(data, dict) else None
    return stages if isinstance(stages, dict) else {}


def _critique_counts(summary: dict, stage: str) -> dict | None:
    """Status counts of one critiqued stage, derived from by_item (the per-stage counters
    in the summary are a cache of the same thing); None when the stage was never checked."""
    data = summary.get(stage)
    if not isinstance(data, dict):
        return None
    by_item = data.get("by_item")
    if not isinstance(by_item, dict):
        return {s: data.get(s, 0) for s in CRITIQUE_STATUSES} | {"items": data.get("items", 0)}
    counts = {s: 0 for s in CRITIQUE_STATUSES}
    for entry in by_item.values():
        status = entry.get("status") if isinstance(entry, dict) else None
        counts[status if status in counts else "fail"] += 1
    return counts | {"items": len(by_item)}


def _critique_line(counts: dict | None) -> str:
    if counts is None:
        return "no verdicts checked"
    parts = [f"{counts[s]} {s}" for s in CRITIQUE_STATUSES if counts.get(s)]
    return ", ".join(parts) or "no items"


def check_lesson_status(output_dir: Path) -> dict:
    """Lesson-pipeline status: contracts.LESSON_STAGES plus a critique pseudo-stage, each with
    a progress detail string; next_stage is the first incomplete non-optional stage."""
    status = {
        "directory": str(output_dir),
        "exists": output_dir.exists(),
        "mode": PIPELINE_LESSON,
        "stages": {},
        "current_stage": None,
        "next_stage": None,
        "failed_items": [],
    }
    if not output_dir.exists():
        status["next_stage"] = "manifest"
        return status

    sources = _extracted_sources(output_dir)
    units = _units_by_source(output_dir)
    group_ids, groups = _group_ids(output_dir)
    lesson_ids = _item_ids(output_dir, LESSONS_DIR)
    critique = _critique_summary(output_dir)
    for stage, data in critique.items():
        for item_id, entry in sorted(((data or {}).get("by_item") or {}).items()):
            if isinstance(entry, dict) and entry.get("status") == "FAILED":
                status["failed_items"].append({"stage": stage, "item_id": item_id, "round": entry.get("round")})

    entries: list[tuple[str, str, str, bool, bool, str]] = []  # (stage, artifact, description, optional, complete, detail)

    def add(stage: str, artifact: str, description: str, optional: bool, complete: bool, detail: str = "") -> None:
        entries.append((stage, artifact, description, optional, complete, detail))

    for stage, artifact, description, optional in LESSON_STAGES:
        path = output_dir / artifact
        if stage == "extract":
            files = sorted((output_dir / "extracted").glob("*.json")) if path.is_dir() else []
            images_dir = output_dir / "extracted" / "images"
            n_images = sum(1 for f in images_dir.iterdir() if f.suffix.lower() in _IMAGE_SUFFIXES) if images_dir.is_dir() else 0
            detail = f"{len(files)} files" + (f", {n_images} images" if n_images else "") if files else ""
            add(stage, artifact, description, optional, bool(files), detail)
        elif stage == "normalize":
            pending = _unnormalized_titles(output_dir) if (output_dir / "extracted").is_dir() else None
            complete = bool(sources) and pending == 0
            detail = "" if pending is None else (f"{pending} title(s) still carry a prefix / book-meta" if pending else "titles clean")
            add(stage, artifact, description, optional, complete, detail)
        elif stage == "qualify":
            n_units = sum(len(v) for v in units.values())
            complete = bool(units) and set(sources) <= set(units)
            detail = f"{len(units)}/{len(sources)} sources, {n_units} units" if units or sources else ""
            add(stage, artifact, description, optional, complete, detail)
        elif stage == "qualification":
            done = 0
            qualified = 0
            total_units = sum(len(v) for v in units.values())
            for source, ids in units.items():
                data = _read_json(output_dir / QUALIFICATION_DIR / f"{source}.json")
                entries_map = data.get("units") if isinstance(data, dict) and isinstance(data.get("units"), dict) else None
                if entries_map is None:
                    continue
                done += 1
                qualified += sum(1 for uid in ids if isinstance(entries_map.get(uid), dict))
            complete = bool(units) and done == len(units) and qualified == total_units
            detail = f"{done}/{len(units)} sources, {qualified}/{total_units} units" if units else ""
            add(stage, artifact, description, optional, complete, detail)
        elif stage == "concepts":
            data = _read_json(path)
            n = len([c for c in (data.get("concepts") or []) if isinstance(c, dict)]) if isinstance(data, dict) else 0
            add(stage, artifact, description, optional, n > 0, f"{n} concepts" if path.exists() else "")
        elif stage == "regroup":
            budgets = sum(1 for g in groups.values() if isinstance(g.get("budget"), dict) and g["budget"])
            prereqs = [p for g in groups.values() for p in (g.get("prerequisites") or []) if isinstance(p, dict)]
            committed = sum(1 for p in prereqs if p.get("status") == "committed")
            complete = bool(group_ids) and budgets == len(group_ids)
            detail = ""
            if group_ids:
                detail = f"{len(group_ids)} groups, budgets {'filled' if budgets == len(group_ids) else f'{budgets}/{len(group_ids)}'}"
                if prereqs:
                    detail += f", {committed}/{len(prereqs)} prerequisites committed"
            add(stage, artifact, description, optional, complete, detail)
        elif stage == "synthesize":
            written = _item_ids(output_dir, SYNTHESES_DIR)
            complete = bool(group_ids) and set(group_ids) <= written
            add(stage, artifact, description, optional, complete, f"{len(written & set(group_ids))}/{len(group_ids)} groups" if group_ids else "")
        elif stage == "lessons":
            full = outline = 0
            for lid in lesson_ids:
                if _lesson_is_full(_read_json(output_dir / LESSONS_DIR / item_id_to_filename(lid))):
                    full += 1
                else:
                    outline += 1
            complete = bool(group_ids) and full == len(group_ids)
            detail = f"{full}/{len(group_ids)} full, {outline} outline" if group_ids or lesson_ids else ""
            counts = _critique_counts(critique, "lessons")
            if counts is not None:
                detail += f"; critique {_critique_line(counts)}"
            add(stage, artifact, description, optional, complete, detail)
        elif stage == "build":
            structure_ok = (output_dir / "structure.json").exists()
            chunks = _read_json(path)
            complete = structure_ok and isinstance(chunks, list)
            detail = f"{len(chunks)} chunks" if isinstance(chunks, list) else ("structure.json only" if structure_ok else "")
            add(stage, artifact, description, optional, complete, detail)
        elif stage in ("questions", "cards"):
            written = _item_ids(output_dir, QUESTIONS_DIR if stage == "questions" else CARDS_DIR)
            complete = bool(lesson_ids) and lesson_ids <= written
            add(stage, artifact, description, optional, complete, f"{len(written & lesson_ids)}/{len(lesson_ids)} lessons" if lesson_ids else "")
        elif stage == "validate":
            data = _read_json(path)
            summary = data.get("summary") if isinstance(data, dict) else None
            detail = f"{summary.get('errors')} errors, {summary.get('warnings')} warnings" if isinstance(summary, dict) else ""
            add(stage, artifact, description, optional, path.exists(), detail)
        elif stage == "review":
            data = _read_json(path)
            approved = data.get("approved") if isinstance(data, dict) else None
            add(stage, artifact, description, optional, path.exists(), ("approved" if approved else "NOT approved") if path.exists() else "")
        else:
            add(stage, artifact, description, optional, path.exists())
        if stage == "cards":
            # Critique pseudo-stage: complete when every critiqued stage that has author
            # artifacts was checked and holds no pending / failing item.
            due = [s for s in CRITIQUED_STAGES if _critiqued_artifacts_present(output_dir, s, group_ids)]
            parts = []
            complete = bool(due)
            for s in due:
                counts = _critique_counts(critique, s)
                parts.append(f"{s}: {_critique_line(counts)}")
                if counts is None or counts["pending"] or counts["fail"] or counts["FAILED"] or counts["verdict_error"]:
                    complete = False
            add("critique", CRITIQUE_SUMMARY, "Critiqued by independent critics", False, complete, "; ".join(parts))

    for stage, artifact, description, optional, complete, detail in entries:
        status["stages"][stage] = {
            "complete": complete,
            "artifact": artifact,
            "description": description,
            "optional": optional,
            "detail": detail,
        }
        if complete:
            status["current_stage"] = stage
        elif status["next_stage"] is None and not optional:
            status["next_stage"] = stage
    return status


def _critiqued_artifacts_present(output_dir: Path, stage: str, group_ids: list[str]) -> bool:
    if stage == "groups":
        return bool(group_ids)
    if stage == "qualification":  # keyed by source, not by g:<slug>
        return bool(list_item_files(output_dir / QUALIFICATION_DIR))
    stage_dir = {"syntheses": SYNTHESES_DIR, "lessons": LESSONS_DIR, "questions": QUESTIONS_DIR, "cards": CARDS_DIR}[stage]
    return bool(_item_ids(output_dir, stage_dir))


def print_lesson_status(status: dict, manifest: dict | None) -> None:
    if manifest is not None:
        print(f"Curriculum: {manifest.get('name', '?')}")
        print(f"Domain:     {manifest.get('domain', '?')}")
        print(f"Pipeline:   {PIPELINE_LESSON}")
        print(f"Sources:    {len(manifest.get('sources', []))}")
        print()
    print("Pipeline Status (lesson):")
    print("-" * 50)
    for stage_name, stage in status["stages"].items():
        icon = "[DONE]" if stage["complete"] else "[    ]"
        line = f"  {icon} {stage['description']}"
        if stage["detail"]:
            line += f" ({stage['detail']})"
        if stage_name == status["next_stage"]:
            line += "  <-- RESUME HERE"
        print(line)
    print("-" * 50)
    if status["failed_items"]:
        print("\nFAILED items (still failing after the repair rounds — list them in review.json):")
        for item in status["failed_items"]:
            print(f"  {item['stage']}: {item['item_id']} (round {item['round']})")
    if status["next_stage"]:
        print(f"\nNext step: {status['next_stage']}")
    else:
        print("\nAll stages complete!")


def print_status(output_dir: Path):
    """Print a human-readable pipeline status."""
    status = check_status(output_dir)

    if not status["exists"]:
        print(f"Directory not found: {output_dir}")
        print("Pipeline has not started. Begin with extraction.")
        return

    # Read manifest for context
    manifest_path = output_dir / "manifest.json"
    manifest = None
    if manifest_path.exists():
        with open(manifest_path) as f:
            manifest = json.load(f)

    if status.get("mode") == PIPELINE_LESSON:
        print_lesson_status(status, manifest)
        return

    if manifest is not None:
        print(f"Curriculum: {manifest.get('name', '?')}")
        print(f"Domain:     {manifest.get('domain', '?')}")
        print(f"Sources:    {len(manifest.get('sources', []))}")
        print()

    print("Pipeline Status:")
    print("-" * 50)

    for stage_name, artifact, description in STAGES:
        stage = status["stages"][stage_name]
        icon = "[DONE]" if stage["complete"] else "[    ]"
        line = f"  {icon} {description}"

        # Add detail
        if stage["complete"]:
            if "file_count" in stage:
                line += f" ({stage['file_count']} files"
                if "image_count" in stage:
                    line += f", {stage['image_count']} images"
                line += ")"
            elif "count" in stage:
                line += f" ({stage['count']} items)"
            if stage_name == "review" and "approved" in stage:
                line += " — approved" if stage["approved"] else " — NOT approved"
            if stage_name == "validate" and stage.get("errors") is not None:
                line += f" ({stage['errors']} errors, {stage['warnings']} warnings)"

        # Mark current/next
        if stage_name == status["next_stage"]:
            line += "  <-- RESUME HERE"

        print(line)

    print("-" * 50)

    if status["next_stage"]:
        print(f"\nNext step: {status['next_stage']}")
    else:
        print("\nAll stages complete!")


USAGE = "Usage: uv run python status.py output/<curriculum_name>/"


def main():
    if len(sys.argv) > 1 and sys.argv[1] in ("-h", "--help"):
        print(USAGE)
        print()
        print("Shows where a curriculum stands in the pipeline (raw or lesson, from manifest.pipeline)")
        print("and where to resume. Without an argument, lists every curriculum under output/.")
        sys.exit(0)
    if len(sys.argv) < 2:
        print(USAGE)
        print()
        # List all curricula in output/
        output_root = Path(__file__).parent / "output"
        if output_root.exists():
            dirs = [d for d in output_root.iterdir() if d.is_dir() and d.name != ".gitkeep"]
            if dirs:
                print("Available curricula:")
                for d in sorted(dirs):
                    manifest = d / "manifest.json"
                    name = d.name
                    if manifest.exists():
                        with open(manifest) as f:
                            name = json.load(f).get("name", d.name)
                    status = check_status(d)
                    next_stage = status["next_stage"] or "complete"
                    print(f"  {d.name}/  ({name}) — next: {next_stage}")
            else:
                print("No curricula found in output/")
        sys.exit(0)

    output_dir = Path(sys.argv[1])
    print_status(output_dir)


if __name__ == "__main__":
    main()
