"""Shared fixtures: a synthetic mini-curriculum that passes every validator check.

Tests inject violations by mutating the JSON files in the returned directory.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import _compat  # noqa: F401

from chunk import count_tokens

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
