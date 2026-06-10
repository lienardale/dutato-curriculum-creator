"""Tests for chunk_bridge.py — matching, coverage reporting, image placement."""

from __future__ import annotations

import json
from pathlib import Path

import _compat  # noqa: F401

from chunk_bridge import bridge_and_chunk

from conftest import write_json


def make_section(
    title: str,
    n_paras: int = 4,
    images: list[dict] | None = None,
    meta: dict | None = None,
) -> dict:
    paras = [
        f"Paragraph {i} about {title}. " + ("It explains the concept in detail. " * 8)
        for i in range(n_paras)
    ]
    section = {"title": title, "content": "\n\n".join(paras)}
    if images:
        section["images"] = images
    if meta:
        section["metadata"] = meta
    return section


def setup_curriculum(tmp_path: Path, sections: list[dict], leaves: list[dict]):
    """Write extracted/source.json + structure.json; return their paths."""
    extracted = tmp_path / "extracted"
    extracted.mkdir()
    write_json(extracted / "source.json", {
        "source_type": "pdf",
        "source_path": "/src/source.pdf",
        "sections": sections,
    })
    structure = [{
        "title": "Area",
        "depth": 0,
        "sort_order": 0,
        "children": [
            {"title": leaf["title"], "depth": 1, "sort_order": i,
             **{k: v for k, v in leaf.items() if k != "title"},
             "children": []}
            for i, leaf in enumerate(leaves)
        ],
    }]
    structure_path = tmp_path / "structure.json"
    write_json(structure_path, structure)
    return structure_path, extracted


def test_source_section_matching_is_nbsp_and_case_insensitive(tmp_path):
    structure_path, extracted = setup_curriculum(
        tmp_path,
        sections=[make_section("Intro to Widgets")],
        leaves=[{"title": "Widget Basics",
                 "source_sections": ["intro\xa0to widgets"]}],
    )
    chunks, report = bridge_and_chunk(structure_path, extracted)
    assert len(chunks) == 1
    assert chunks[0]["topic_path"] == ["Area", "Widget Basics"]
    assert report["unmatched_source_sections"] == []


def test_unmatched_source_section_is_reported_not_silent(tmp_path):
    structure_path, extracted = setup_curriculum(
        tmp_path,
        sections=[make_section("Intro to Widgets")],
        leaves=[{"title": "Widget Basics",
                 "source_sections": ["Intro to Widgets", "Ghost Section"]}],
    )
    chunks, report = bridge_and_chunk(structure_path, extracted)
    assert len(chunks) == 1  # matched section still chunks
    assert report["unmatched_source_sections"] == [{
        "topic_path": ["Area", "Widget Basics"],
        "missing": ["Ghost Section"],
    }]


def test_empty_topic_is_reported(tmp_path):
    structure_path, extracted = setup_curriculum(
        tmp_path,
        sections=[make_section("Intro to Widgets")],
        leaves=[
            {"title": "Widget Basics", "source_sections": ["Intro to Widgets"]},
            {"title": "Nothing Here", "source_sections": ["Ghost Section"]},
        ],
    )
    chunks, report = bridge_and_chunk(structure_path, extracted)
    assert ["Area", "Nothing Here"] in report["empty_topics"]
    assert all(c["topic_path"] != ["Area", "Nothing Here"] for c in chunks)


def test_leaf_title_fallback(tmp_path):
    # The fallback applies in source_sections mode, so at least one leaf in
    # the structure must carry explicit source_sections.
    structure_path, extracted = setup_curriculum(
        tmp_path,
        sections=[make_section("Widget Basics"), make_section("Other")],
        leaves=[
            {"title": "Widget Basics", "source_sections": []},
            {"title": "Other Topic", "source_sections": ["Other"]},
        ],
    )
    chunks, report = bridge_and_chunk(structure_path, extracted)
    assert any(c["topic_path"] == ["Area", "Widget Basics"] for c in chunks)
    assert report["empty_topics"] == []


def test_orphan_sections_reported(tmp_path):
    structure_path, extracted = setup_curriculum(
        tmp_path,
        sections=[make_section("Intro to Widgets"), make_section("Spare Notes")],
        leaves=[{"title": "Widget Basics",
                 "source_sections": ["Intro to Widgets"]}],
    )
    _, report = bridge_and_chunk(structure_path, extracted)
    assert [o["title"] for o in report["orphan_sections"]] == ["Spare Notes"]
    assert report["totals"]["sections"] == 2
    assert report["totals"]["sections_used"] == 1


def test_multi_section_concatenation(tmp_path):
    structure_path, extracted = setup_curriculum(
        tmp_path,
        sections=[make_section("Part One", n_paras=2),
                  make_section("Part Two", n_paras=2)],
        leaves=[{"title": "Both Parts",
                 "source_sections": ["Part One", "Part Two"]}],
    )
    chunks, _ = bridge_and_chunk(structure_path, extracted)
    text = "\n\n".join(c["content"] for c in chunks)
    assert "Part One" in text and "Part Two" in text


def test_pdf_images_placed_by_page_position(tmp_path):
    img_first = {"id": "img-first", "local_path": "images/a.png",
                 "alt_text": "", "context": "page:1"}
    img_last = {"id": "img-last", "local_path": "images/b.png",
                "alt_text": "", "context": "page:10"}
    structure_path, extracted = setup_curriculum(
        tmp_path,
        sections=[make_section(
            "Long Section", n_paras=24,
            images=[img_first, img_last],
            meta={"page_start": 1, "page_end": 10},
        )],
        leaves=[{"title": "Long Topic", "source_sections": ["Long Section"]}],
    )
    chunks, _ = bridge_and_chunk(
        structure_path, extracted, max_tokens=300, min_tokens=50,
    )
    assert len(chunks) >= 3, "fixture must split into multiple chunks"
    by_id = {
        img["id"]: i
        for i, c in enumerate(chunks)
        for img in c.get("images", [])
    }
    assert by_id["img-first"] == 0
    assert by_id["img-last"] == len(chunks) - 1


def test_positionless_image_anchors_to_owning_section(tmp_path):
    img = {"id": "img-b", "local_path": "images/b.png", "alt_text": ""}
    structure_path, extracted = setup_curriculum(
        tmp_path,
        sections=[
            make_section("Section A", n_paras=12),
            make_section("Section B", n_paras=12, images=[img]),
        ],
        leaves=[{"title": "Combined",
                 "source_sections": ["Section A", "Section B"]}],
    )
    chunks, _ = bridge_and_chunk(
        structure_path, extracted, max_tokens=300, min_tokens=50,
    )
    assert len(chunks) >= 3
    img_chunks = [i for i, c in enumerate(chunks)
                  for im in c.get("images", []) if im["id"] == "img-b"]
    assert len(img_chunks) == 1
    # Section B starts halfway through the text — its image must not land
    # on the leaf's first chunk (the old behavior).
    assert img_chunks[0] > 0


def test_cli_writes_chunk_report(tmp_path, monkeypatch, capsys):
    structure_path, extracted = setup_curriculum(
        tmp_path,
        sections=[make_section("Intro to Widgets")],
        leaves=[{"title": "Widget Basics",
                 "source_sections": ["Intro to Widgets", "Ghost Section"]}],
    )
    out = tmp_path / "chunks.json"
    import chunk_bridge
    monkeypatch.setattr("sys.argv", [
        "chunk_bridge.py",
        "--structure", str(structure_path),
        "--extracted", str(extracted),
        "-o", str(out),
    ])
    chunk_bridge.main()
    report = json.loads((tmp_path / "chunk_report.json").read_text())
    assert report["unmatched_source_sections"][0]["missing"] == ["Ghost Section"]
    assert out.exists()
