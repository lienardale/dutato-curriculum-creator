"""Tests for the second-iteration pipeline improvements: multi-part prefix
stripping, balanced chunking, markdown de-linking/hash ids, merge-preserving
prepare, and acknowledged orphan exclusions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from normalize_titles import strip_prefix
from chunk import chunk_text, count_tokens

from conftest import make_text, read_json, write_json


# ---------------------------------------------------------------------------
# normalize_titles: multi-part Part prefixes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("Part V - 1 - Architecture", "Architecture"),
    ("Part V - 2 - Architecture", "Architecture"),
    ("Part 5.1 - Architecture", "Architecture"),
    ("Part I - Introduction", "Introduction"),
    ("Chapter 3 - Paradigm Overview", "Paradigm Overview"),
])
def test_multi_part_prefixes(raw, expected):
    assert strip_prefix(raw) == expected


# ---------------------------------------------------------------------------
# chunk_text: balanced sizes instead of max-fill + small tail
# ---------------------------------------------------------------------------

def test_chunk_text_balances_sizes():
    # ~1700 tokens must split; balanced chunking should avoid a tiny tail
    text = "\n\n".join(make_text(170, seed=f"p{i}") for i in range(10))
    total = count_tokens(text)
    assert total > 1500
    chunks = chunk_text(text, max_tokens=1500, min_tokens=200)
    assert len(chunks) >= 2
    sizes = [c["token_count"] for c in chunks]
    # No chunk should be dramatically smaller than the mean
    assert min(sizes) > 0.4 * (sum(sizes) / len(sizes))


# ---------------------------------------------------------------------------
# markdown extractor: de-linking, hash ids, dedup
# ---------------------------------------------------------------------------

def test_markdown_delinks_relative_links(tmp_path):
    from extractors.markdown import extract_markdown

    md = tmp_path / "doc.md"
    md.write_text(
        "# Title\n\nSee [the SRP](part-3.md#srp) and [intro](#intro) but "
        "keep [external](https://example.com/page).\n"
    )
    result = extract_markdown(str(md))
    content = result["sections"][0]["content"]
    assert "part-3.md" not in content
    assert "(#intro)" not in content
    assert "the SRP" in content and "intro" in content
    assert "https://example.com/page" in content


def test_markdown_hash_image_ids_stable_and_deduped(tmp_path):
    from extractors.markdown import extract_markdown

    img = tmp_path / "pic.png"
    img.write_bytes(b"\x89PNG fake image bytes")
    (tmp_path / "a.md").write_text("# A\n\n![one](pic.png)\n\nbody\n")
    (tmp_path / "b.md").write_text("# B\n\n![two](pic.png)\n\nbody\n")

    out1 = tmp_path / "out1"
    result1 = extract_markdown(str(tmp_path), images_dir=str(out1 / "images"))
    out2 = tmp_path / "out2"
    result2 = extract_markdown(str(tmp_path), images_dir=str(out2 / "images"))

    ids1 = [e["id"] for e in result1.get("images", [])]
    ids2 = [e["id"] for e in result2.get("images", [])]
    assert ids1 == ids2                    # stable across re-extraction
    assert len(ids1) == 1                  # identical bytes deduplicated


# ---------------------------------------------------------------------------
# analyze_images: prepare preserves existing analysis
# ---------------------------------------------------------------------------

def test_prepare_preserves_descriptions(tmp_path):
    from analyze_images import prepare_analysis

    extracted = tmp_path / "extracted"
    extracted.mkdir()
    write_json(extracted / "src.json", {
        "title": "Src",
        "sections": [],
        "images": [{"id": "md_src_abc123", "local_path": "images/x.png",
                    "mime_type": "image/png", "size_bytes": 42}],
    })
    write_json(tmp_path / "image_analysis.json", {
        "total_images": 1, "analyzed": 1,
        "images": [{"id": "md_src_abc123", "size_bytes": 42,
                    "description": "A diagram", "educational_value": "high",
                    "contains_diagram": True, "contains_code": False,
                    "contains_text": True, "ocr_text": ""}],
    })
    result = prepare_analysis(tmp_path)
    entry = result["images"][0]
    assert entry["description"] == "A diagram"
    assert entry["educational_value"] == "high"
    assert result["analyzed"] == 1


def test_prepare_requeues_changed_images(tmp_path):
    from analyze_images import prepare_analysis

    extracted = tmp_path / "extracted"
    extracted.mkdir()
    write_json(extracted / "src.json", {
        "title": "Src",
        "sections": [],
        "images": [{"id": "md_src_abc123", "local_path": "images/x.png",
                    "mime_type": "image/png", "size_bytes": 99}],  # size changed
    })
    write_json(tmp_path / "image_analysis.json", {
        "total_images": 1, "analyzed": 1,
        "images": [{"id": "md_src_abc123", "size_bytes": 42,
                    "description": "A diagram"}],
    })
    result = prepare_analysis(tmp_path)
    assert result["images"][0]["description"] == ""
    assert result["analyzed"] == 0


# ---------------------------------------------------------------------------
# validate: acknowledged orphans via manifest excluded_sections
# ---------------------------------------------------------------------------

def _curriculum_with_orphan(tmp_path, excluded_sections):
    write_json(tmp_path / "manifest.json", {
        "name": "T", "domain": "t", "domain_family": "t",
        "variant": "extensive", "description": "d",
        "sources": [], "created_at": "2026-01-01T00:00:00Z",
        "created_by": "agent",
        "excluded_sections": excluded_sections,
    })
    extracted = tmp_path / "extracted"
    extracted.mkdir(exist_ok=True)
    write_json(extracted / "src.json", {
        "title": "Src",
        "sections": [
            {"title": "Used Section", "content": make_text(60), "depth": 0},
            {"title": "Orphan Section", "content": make_text(60), "depth": 0},
        ],
    })
    write_json(tmp_path / "structure.json", [{
        "title": "Topic", "depth": 0, "sort_order": 0,
        "description": "d", "suggested_level": 1,
        "learning_objectives": [
            {"text": "Explain X.", "bloom_level": "understand"},
            {"text": "Apply X.", "bloom_level": "apply"},
        ],
        "source_sections": ["Used Section"],
    }])


def _findings(tmp_path):
    from validate import check_structure
    return {(f.check_id, f.message.split("'")[1] if "'" in f.message else "")
            for f in check_structure(tmp_path)}


def test_unacknowledged_orphan_warns(tmp_path):
    _curriculum_with_orphan(tmp_path, [])
    checks = {c for c, _ in _findings(tmp_path)}
    assert "structure.orphan_section" in checks


def test_acknowledged_orphan_is_silent(tmp_path):
    _curriculum_with_orphan(tmp_path, [
        {"match": "Orphan Section", "reason": "descoped"},
    ])
    checks = {c for c, _ in _findings(tmp_path)}
    assert "structure.orphan_section" not in checks
    assert "structure.stale_exclusion" not in checks


def test_stale_exclusion_warns(tmp_path):
    _curriculum_with_orphan(tmp_path, [
        {"match": "Orphan Section", "reason": "descoped"},
        {"match": "No Such Section", "reason": "typo"},
    ])
    checks = {c for c, _ in _findings(tmp_path)}
    assert "structure.stale_exclusion" in checks


def test_excluded_but_referenced_warns(tmp_path):
    _curriculum_with_orphan(tmp_path, [
        {"match": "Orphan Section", "reason": "descoped"},
        {"match": "Used Section", "reason": "conflict"},
    ])
    checks = {c for c, _ in _findings(tmp_path)}
    assert "structure.excluded_but_referenced" in checks


def test_file_scoped_exclusion(tmp_path):
    _curriculum_with_orphan(tmp_path, [
        {"match": "src.json::Orphan Section", "reason": "descoped"},
    ])
    checks = {c for c, _ in _findings(tmp_path)}
    assert "structure.orphan_section" not in checks

    # Wrong file scope leaves the orphan unacknowledged (and the pattern stale)
    _curriculum_with_orphan(tmp_path, [
        {"match": "other.json::Orphan Section", "reason": "descoped"},
    ])
    checks = {c for c, _ in _findings(tmp_path)}
    assert "structure.orphan_section" in checks
    assert "structure.stale_exclusion" in checks
