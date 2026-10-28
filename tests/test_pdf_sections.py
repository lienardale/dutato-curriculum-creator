"""Tests for extractors/pdf.py — heading-bounded section content.

PDF fixtures are built in tmp_path with PyMuPDF: headings are inserted in a
larger font and registered as TOC bookmarks so build_topic_tree uses them.
"""

from __future__ import annotations

import os
from pathlib import Path

import fitz
import pytest

import _compat  # noqa: F401

from extractors.pdf import _find_heading, _normalize_heading, extract_pdf


def build_pdf(path: Path, pages: list[list[tuple[str, int]]], toc: list[list]) -> Path:
    """Write a PDF whose pages hold the given (text, fontsize) blocks, top to bottom."""
    doc = fitz.open()
    for blocks in pages:
        page = doc.new_page()
        y = 72
        for text, size in blocks:
            page.insert_text((72, y), text, fontsize=size)
            y += (text.count("\n") + 1) * (size + 6) + 18
    doc.set_toc(toc)
    doc.save(str(path))
    doc.close()
    return path


def sections_by_title(result: dict) -> dict[str, dict]:
    return {s["title"]: s for s in result["sections"]}


# ---------------------------------------------------------------------------
# Heading normalization / matching
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("Chapter 4: Working with Images", "working with images"),
    ("CHAPTER 4\nWorking with Images", "working with images"),
    ("3.2 Data Mapper", "data mapper"),
    ("Part II. Setting the Stage", "setting the stage"),
    ("Appendix A: Tools", "tools"),
    ("  Service\xa0Layer  ", "service layer"),
    ("Introduction.", "introduction"),
])
def test_normalize_heading(raw, expected):
    assert _normalize_heading(raw) == expected


def test_find_heading_matches_title_split_over_two_lines():
    lines = ["CHAPTER 2", "Working with Images", "Body text follows."]
    assert _find_heading(lines, "Chapter 2: Working with Images") == (0, 2)


def test_find_heading_strips_numbering_prefix_on_page_line():
    lines = ["Intro body.", "3.2 Data Mapper", "Mapper body."]
    assert _find_heading(lines, "Data Mapper") == (1, 2)


def test_find_heading_does_not_swallow_a_bare_page_number_line():
    lines = ["5", "Kinds of Enterprise Application", "body"]
    assert _find_heading(lines, "Kinds of Enterprise Application") == (1, 2)
    # a chapter label is part of the heading and stays in the window
    assert _find_heading(["Chapter 1", "Layering", "body"], "Chapter 1: Layering") == (0, 2)


def test_find_heading_is_fuzzy_but_bounded():
    lines = ["Intro", "Service Layers", "body"]
    assert _find_heading(lines, "Service Layer") == (1, 2)
    assert _find_heading(["Intro", "Completely different", "body"], "Service Layer") is None


def test_find_heading_skips_running_header_when_title_repeats_lower():
    lines = ["Service Layer", "page 142", "Some tail of the previous section.", "Service Layer", "body"]
    assert _find_heading(lines, "Service Layer") == (3, 4)


def test_find_heading_respects_start_offset():
    lines = ["Summary", "chapter one recap", "Summary", "chapter two recap"]
    assert _find_heading(lines, "Summary", start=1) == (2, 3)


# ---------------------------------------------------------------------------
# extract_pdf: boundaries, flags, fallback
# ---------------------------------------------------------------------------

def test_two_sections_on_one_page_do_not_share_text(tmp_path):
    pdf = build_pdf(tmp_path / "two.pdf", pages=[
        [("Introduction", 16), ("Intro body line one.\nIntro body line two.", 11),
         ("1.1 Service Layer", 14), ("Service body alpha.\nService body beta.", 11)],
        [("Service body gamma on page two.", 11), ("1.2 Data Mapper", 14), ("Mapper body one.", 11)],
    ], toc=[[1, "Introduction", 1], [2, "Service Layer", 1], [2, "Data Mapper", 2]])

    secs = sections_by_title(extract_pdf(str(pdf), ocr=False))
    intro, service, mapper = secs["Introduction"], secs["Service Layer"], secs["Data Mapper"]

    assert "Intro body line two." in intro["content"]
    assert "Service body" not in intro["content"]
    assert intro["metadata"]["cut_start"] is True
    assert intro["metadata"]["cut_end"] is True
    assert intro["metadata"]["page_end"] == 1

    # Service Layer runs from its heading, across the page break, up to the
    # next heading on page 2 (its TOC range alone ended on page 1).
    assert service["content"].startswith("1.1 Service Layer")
    assert "Intro body" not in service["content"]
    assert "Service body gamma on page two." in service["content"]
    assert "Mapper body" not in service["content"]
    assert service["metadata"] == {
        "page_start": 1, "page_end": 2, "cut_start": True, "cut_end": True,
    }

    assert mapper["content"].startswith("1.2 Data Mapper")
    assert "Service body gamma" not in mapper["content"]
    assert mapper["metadata"]["cut_start"] is True
    assert mapper["metadata"]["cut_end"] is False  # last section: nothing to stop at


def test_parent_keeps_only_text_before_first_child(tmp_path):
    pdf = build_pdf(tmp_path / "tree.pdf", pages=[
        [("Chapter One", 16), ("Chapter intro text.", 11), ("Section A", 14), ("A body.", 11)],
        [("A body continues.", 11), ("Section B", 14), ("B body.", 11)],
        [("Chapter Two", 16), ("Two body.", 11)],
    ], toc=[[1, "Chapter One", 1], [2, "Section A", 1], [2, "Section B", 2], [1, "Chapter Two", 3]])

    secs = sections_by_title(extract_pdf(str(pdf), ocr=False))
    parent = secs["Chapter One"]
    assert parent["content"].strip() == "Chapter One\nChapter intro text."
    assert parent["metadata"]["parent_trimmed"] is True
    assert parent["metadata"]["page_end"] == 1  # effective, not the TOC's page 2

    a, b, two = secs["Section A"], secs["Section B"], secs["Chapter Two"]
    assert "A body continues." in a["content"] and "B body" not in a["content"]
    assert "Chapter intro" not in a["content"]
    assert b["content"].strip() == "Section B\nB body."
    assert "parent_trimmed" not in a["metadata"] and "parent_trimmed" not in b["metadata"]
    assert two["content"].strip() == "Chapter Two\nTwo body."
    assert two["metadata"]["cut_start"] is True and two["metadata"]["cut_end"] is False


def test_fuzzy_heading_with_chapter_prefix_split_over_two_lines(tmp_path):
    pdf = build_pdf(tmp_path / "fuzzy.pdf", pages=[
        [("Front matter noise.", 11), ("CHAPTER 1\nGetting Started", 16), ("Start body.", 11)],
        [("CHAPTER 2\nWorking with Images", 16), ("Image body.", 11)],
        [("3.1 Advanced Topics", 14), ("Advanced body.", 11)],
    ], toc=[[1, "Chapter 1: Getting Started", 1], [1, "Chapter 2: Working with Images", 2],
            [1, "Advanced Topics", 3]])

    secs = sections_by_title(extract_pdf(str(pdf), ocr=False))
    start = secs["Chapter 1: Getting Started"]
    assert start["content"].startswith("CHAPTER 1\nGetting Started")
    assert "Front matter noise" not in start["content"]
    assert start["metadata"]["cut_start"] is True
    assert secs["Advanced Topics"]["content"].startswith("3.1 Advanced Topics")


def test_missing_heading_falls_back_to_full_page(tmp_path):
    pdf = build_pdf(tmp_path / "fallback.pdf", pages=[
        [("Prologue", 16), ("Prologue body.", 11), ("Something Else Entirely", 14), ("Else body.", 11)],
        [("Closing", 16), ("Closing body.", 11)],
    ], toc=[[1, "Overview", 1], [1, "Ghost Title", 1], [1, "Closing", 2]])

    secs = sections_by_title(extract_pdf(str(pdf), ocr=False))
    overview, ghost = secs["Overview"], secs["Ghost Title"]
    # Neither heading is on the page: both sections fall back to the whole page.
    assert overview["content"].strip() == ghost["content"].strip()
    assert "Prologue body." in overview["content"] and "Else body." in overview["content"]
    assert overview["metadata"]["cut_start"] is False
    assert overview["metadata"]["cut_end"] is False
    assert ghost["metadata"]["cut_start"] is False
    # The next section's heading IS found on page 2, but its page is outside
    # Ghost Title's TOC range only through that heading, so nothing of page 2 leaks.
    assert "Closing body" not in ghost["content"]
    assert ghost["metadata"]["page_end"] == 2 and ghost["metadata"]["cut_end"] is True
    assert "Closing" in secs["Closing"]["content"]


def test_next_section_on_later_page_without_heading_match_does_not_pull_that_page(tmp_path):
    pdf = build_pdf(tmp_path / "later.pdf", pages=[
        [("Alpha", 16), ("Alpha body.", 11)],
        [("Unrelated heading text", 16), ("Beta body.", 11)],
        [("Gamma", 16), ("Gamma body.", 11)],
    ], toc=[[1, "Alpha", 1], [1, "Beta", 2], [1, "Gamma", 3]])

    secs = sections_by_title(extract_pdf(str(pdf), ocr=False))
    alpha = secs["Alpha"]
    assert "Beta body" not in alpha["content"]
    assert alpha["metadata"] == {"page_start": 1, "page_end": 1, "cut_start": True, "cut_end": False}
    beta = secs["Beta"]
    assert "Beta body." in beta["content"]  # full-page fallback on its own page
    assert beta["metadata"]["cut_start"] is False and beta["metadata"]["cut_end"] is True


def test_images_follow_the_effective_page_range(tmp_path):
    pdf_path = tmp_path / "img.pdf"
    doc = fitz.open()
    p1 = doc.new_page()
    p1.insert_text((72, 72), "Chapter One", fontsize=16)
    p1.insert_text((72, 110), "Chapter intro.", fontsize=11)
    p1.insert_text((72, 150), "Section A", fontsize=14)
    p1.insert_text((72, 180), "A body.", fontsize=11)
    p2 = doc.new_page()
    p2.insert_text((72, 72), "A body continues.", fontsize=11)
    # Incompressible noise so the image survives extract_pdf_images' size filter.
    pix = fitz.Pixmap(fitz.csRGB, 160, 160, os.urandom(160 * 160 * 3), False)
    p2.insert_image(fitz.Rect(72, 120, 232, 280), pixmap=pix)
    p3 = doc.new_page()
    p3.insert_text((72, 72), "Chapter Two", fontsize=16)
    p3.insert_text((72, 110), "Two body.", fontsize=11)
    doc.set_toc([[1, "Chapter One", 1], [2, "Section A", 1], [1, "Chapter Two", 3]])
    doc.save(str(pdf_path))
    doc.close()

    result = extract_pdf(str(pdf_path), images_dir=str(tmp_path / "images"), ocr=False)
    assert result["metadata"]["total_images"] == 1
    secs = sections_by_title(result)
    assert "images" not in secs["Chapter One"], "trimmed parent must not carry its child's page image"
    assert [img["context"] for img in secs["Section A"]["images"]] == ["page:2"]
    assert "images" not in secs["Chapter Two"]
