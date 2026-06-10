"""Tests for condense.py — assembly strategies, content enforcement, propagation."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import _compat  # noqa: F401

import condense
from condense import (
    _remap_prerequisites,
    assemble_tier,
    build_chunk_index,
    compute_stats,
    find_missing_content,
)

from conftest import read_json, write_json

REPO_ROOT = Path(__file__).resolve().parents[1]


def load_inputs(curriculum_dir: Path):
    manifest = read_json(curriculum_dir / "manifest.json")
    structure = read_json(curriculum_dir / "structure.json")
    chunks = read_json(curriculum_dir / "chunks.json")
    exercises = read_json(curriculum_dir / "exercises.json")
    plan = read_json(curriculum_dir / "condensation_plan.json")
    return manifest, structure, chunks, exercises, plan


def run_assemble(curriculum_dir: Path, plan_tier: list[dict]):
    manifest, structure, chunks, exercises, _ = load_inputs(curriculum_dir)
    stats = compute_stats(structure, chunks)
    assemble_tier(
        plan_tier, chunks, manifest, "detailed", stats, curriculum_dir,
        original_structure=structure, exercises=exercises,
    )
    variant = curriculum_dir / "variants" / "detailed"
    return read_json(variant / "structure.json"), read_json(variant / "chunks.json")


def test_keep_copies_chunks_and_merge_uses_inline_content(curriculum_dir: Path):
    _, _, chunks, _, plan = load_inputs(curriculum_dir)
    out_structure, out_chunks = run_assemble(curriculum_dir, plan["detailed"])

    by_topic = {tuple(c["topic_path"]): c for c in out_chunks}
    kept = by_topic[("Widget Essentials", "Intro to Widgets")]
    original = build_chunk_index(chunks)["Intro to Widgets"][0]
    assert kept["content"] == original["content"]

    merged = by_topic[("Widget Essentials", "Widget Practice")]
    plan_content = plan["detailed"][0]["children"][1]["content"]
    assert merged["content"] == plan_content


def test_objectives_propagate_from_original_structure(curriculum_dir: Path):
    _, _, _, _, plan = load_inputs(curriculum_dir)
    out_structure, _ = run_assemble(curriculum_dir, plan["detailed"])
    child = out_structure[0]["children"][0]
    assert child["title"] == "Intro to Widgets"
    assert any(
        "intro to widgets" in obj["text"].lower()
        for obj in child["learning_objectives"]
    )


def test_keep_preserves_images(curriculum_dir: Path):
    chunks_path = curriculum_dir / "chunks.json"
    chunks = read_json(chunks_path)
    img = {"id": "img-1", "local_path": "images/x.png", "alt_text": ""}
    for c in chunks:
        if c["topic_path"][-1] == "Intro to Widgets":
            c["images"] = [img]
    write_json(chunks_path, chunks)

    _, _, _, _, plan = load_inputs(curriculum_dir)
    _, out_chunks = run_assemble(curriculum_dir, plan["detailed"])
    kept = next(c for c in out_chunks
                if tuple(c["topic_path"]) == ("Widget Essentials", "Intro to Widgets"))
    assert kept["images"] == [img]


def test_remap_prerequisites_filters_to_kept_titles():
    prereqs = [
        {"topic": "Widget Foundations", "strength": "required"},
        {"topic": "Dropped Topic", "strength": "recommended"},
    ]
    assert _remap_prerequisites(prereqs, {"widget foundations".title()}) == [
        {"topic": "Widget Foundations", "strength": "required"},
    ]
    assert _remap_prerequisites(None, {"Anything"}) == []


def test_find_missing_content():
    plan = {
        "detailed": [
            {"title": "A", "condensation_strategy": "keep"},
            {"title": "B", "children": [
                {"title": "B1", "condensation_strategy": "merge",
                 "content": "fine"},
                {"title": "B2", "condensation_strategy": "synthesize"},
            ]},
        ],
        "core": [
            {"title": "C", "condensation_strategy": "merge"},
        ],
    }
    violations = find_missing_content(plan, ["detailed", "core"])
    assert violations == [
        "detailed > B > B2 (strategy: synthesize)",
        "core > C (strategy: merge)",
    ]


def test_cli_blocks_on_missing_content(curriculum_dir: Path):
    plan_path = curriculum_dir / "condensation_plan.json"
    plan = read_json(plan_path)
    del plan["detailed"][0]["children"][1]["content"]
    write_json(plan_path, plan)

    result = subprocess.run(
        [sys.executable, "condense.py",
         "--input", str(curriculum_dir), "--plan", str(plan_path)],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 1
    assert "missing their 'content' field" in result.stdout
    assert not (curriculum_dir / "variants").exists()


def test_cli_allow_concat_restores_fallback(curriculum_dir: Path):
    plan_path = curriculum_dir / "condensation_plan.json"
    plan = read_json(plan_path)
    del plan["detailed"][0]["children"][1]["content"]
    write_json(plan_path, plan)

    result = subprocess.run(
        [sys.executable, "condense.py",
         "--input", str(curriculum_dir), "--plan", str(plan_path),
         "--allow-concat"],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    out_chunks = read_json(
        curriculum_dir / "variants" / "detailed" / "chunks.json"
    )
    merged = next(c for c in out_chunks
                  if tuple(c["topic_path"]) == ("Widget Essentials", "Widget Practice"))
    # Fallback concatenates the source leaves' chunk contents
    assert "using widgets" in merged["content"]
    assert "widget patterns" in merged["content"]
