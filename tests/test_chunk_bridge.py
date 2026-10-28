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


# ---------------------------------------------------------------------------
# Multi-file matching: ambiguity, disambiguated form, _source attribution
# ---------------------------------------------------------------------------

def setup_multi(tmp_path: Path, files: dict[str, list[dict]], leaves: list[dict]):
    """Write several extracted files + a structure.json; return their paths."""
    extracted = tmp_path / "extracted"
    extracted.mkdir()
    for fname, sections in files.items():
        write_json(extracted / fname, {
            "source_type": "pdf",
            "source_path": f"/src/{fname.replace('.json', '.pdf')}",
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


def test_ambiguous_title_uses_all_matches_in_file_order_and_is_reported(tmp_path):
    structure_path, extracted = setup_multi(
        tmp_path,
        files={
            "b_book.json": [make_section("Other", n_paras=2), make_section("Summary", n_paras=2)],
            "a_book.json": [make_section("Summary", n_paras=2)],
        },
        leaves=[{"title": "Wrap Up", "source_sections": ["Summary"]}],
    )
    chunks, report = bridge_and_chunk(structure_path, extracted)
    text = "\n\n".join(c["content"] for c in chunks)
    # both sections are pulled in, a_book (sorted first) before b_book
    assert text.count("Paragraph 0 about Summary") == 2
    assert report["ambiguous_titles"] == [{
        "topic_path": ["Area", "Wrap Up"],
        "title": "Summary",
        "matches": [
            {"file": "a_book.json", "section_index": 0},
            {"file": "b_book.json", "section_index": 1},
        ],
    }]
    assert report["unmatched_source_sections"] == []
    assert report["totals"]["sections_used"] == 2


def test_disambiguated_file_title_form_selects_one_file(tmp_path):
    structure_path, extracted = setup_multi(
        tmp_path,
        files={
            "a_book.json": [make_section("Summary", n_paras=2)],
            "b_book.json": [make_section("Summary", n_paras=2)],
        },
        leaves=[
            {"title": "B Wrap Up", "source_sections": ["b_book.json::Summary"]},
            {"title": "A Wrap Up", "source_sections": ["a_book::summary"]},  # .json optional, case-insensitive
        ],
    )
    chunks, report = bridge_and_chunk(structure_path, extracted)
    by_topic = {c["topic_path"][-1]: c for c in chunks}
    assert len(chunks) == 2
    assert by_topic["B Wrap Up"]["_source"] == "/src/b_book.pdf"
    assert by_topic["A Wrap Up"]["_source"] == "/src/a_book.pdf"
    assert report["ambiguous_titles"] == []
    assert report["orphan_sections"] == []


def test_unknown_file_prefix_is_treated_as_a_plain_title(tmp_path):
    structure_path, extracted = setup_multi(
        tmp_path,
        files={"a_book.json": [make_section("std::vector", n_paras=2)]},
        leaves=[{"title": "Vectors", "source_sections": ["std::vector"]}],
    )
    chunks, report = bridge_and_chunk(structure_path, extracted)
    assert len(chunks) == 1
    assert report["unmatched_source_sections"] == []


def test_chunk_source_follows_the_section_covering_its_start(tmp_path):
    structure_path, extracted = setup_multi(
        tmp_path,
        files={
            "a_book.json": [make_section("Part One", n_paras=12)],
            "b_book.json": [make_section("Part Two", n_paras=12)],
        },
        leaves=[{"title": "Both", "source_sections": ["Part One", "Part Two"]}],
    )
    chunks, _ = bridge_and_chunk(structure_path, extracted, max_tokens=300, min_tokens=50)
    assert len(chunks) >= 4
    sources = [c["_source"] for c in chunks]
    assert sources[0] == "/src/a_book.pdf"
    assert sources[-1] == "/src/b_book.pdf"
    # the switch happens exactly once, where Part Two's text begins
    assert sources == sorted(sources)
    first_b = sources.index("/src/b_book.pdf")
    assert chunks[first_b]["content"].startswith("Paragraph 0 about Part Two")
    assert "Part Two" not in chunks[first_b - 1]["content"]


def test_duplicate_titles_within_one_file_are_reported_and_both_used(tmp_path):
    structure_path, extracted = setup_multi(
        tmp_path,
        files={"book.json": [
            make_section("Intro", n_paras=2),
            make_section("Summary", n_paras=2),
            make_section("Summary", n_paras=2),
        ]},
        leaves=[
            {"title": "Start", "source_sections": ["Intro"]},
            {"title": "Recap", "source_sections": ["book.json::Summary"]},
        ],
    )
    chunks, report = bridge_and_chunk(structure_path, extracted)
    assert report["duplicate_titles_in_file"] == [
        {"file": "book.json", "title": "Summary", "section_indices": [1, 2]},
    ]
    recap = "\n\n".join(c["content"] for c in chunks if c["topic_path"][-1] == "Recap")
    assert recap.count("Paragraph 0 about Summary") == 2
    # even the file-qualified form is ambiguous inside that file
    assert report["ambiguous_titles"][0]["matches"] == [
        {"file": "book.json", "section_index": 1},
        {"file": "book.json", "section_index": 2},
    ]


def test_empty_sections_never_match_and_are_counted(tmp_path):
    empty = {"title": "Chapter Page", "content": ""}
    structure_path, extracted = setup_multi(
        tmp_path,
        files={"site.json": [empty, make_section("Chapter Page", n_paras=2)]},
        leaves=[{"title": "Chapter", "source_sections": ["Chapter Page"]}],
    )
    chunks, report = bridge_and_chunk(structure_path, extracted)
    assert len(chunks) == 1
    assert report["ambiguous_titles"] == []
    assert report["duplicate_titles_in_file"] == []
    assert report["totals"] == {
        "sections": 1, "sections_used": 1, "empty_sections": 1,
        "chunks": 1, "tokens": chunks[0]["token_count"],
    }


def test_orphan_entries_carry_file_and_index(tmp_path):
    structure_path, extracted = setup_multi(
        tmp_path,
        files={"book.json": [make_section("Used"), make_section("Spare")]},
        leaves=[{"title": "Topic", "source_sections": ["Used"]}],
    )
    _, report = bridge_and_chunk(structure_path, extracted)
    assert report["orphan_sections"] == [{
        "source": "/src/book.pdf", "file": "book.json", "section_index": 1, "title": "Spare",
    }]
