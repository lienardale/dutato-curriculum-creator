"""Tests for normalize_titles.py — prefix stripping, book-meta detection, file processing."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from normalize_titles import is_book_meta, process_file, strip_prefix

from conftest import read_json, write_json


# ---------------------------------------------------------------------------
# strip_prefix (one case per PREFIX_PATTERNS entry, plus passthroughs)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("Chapter 4: Working with Images", "Working with Images"),
    ("Chapter 4. Working with Images", "Working with Images"),
    ("Chapter 12.5: Edge Cases", "Edge Cases"),
    ("Ch. 4 The Docker Engine", "The Docker Engine"),
    ("1.1.1 Deeply Nested Topic", "Deeply Nested Topic"),
    ("2.3 Subsection Title", "Subsection Title"),
    ("1. Meet Kafka", "Meet Kafka"),
    ("5: The Docker Engine", "The Docker Engine"),
    ("3 Pods: Running Containers", "Pods: Running Containers"),
    ("Part I. Setting the Stage", "Setting the Stage"),
    ("Part II: Designing Systems", "Designing Systems"),
    ("Section 1: Basics", "Basics"),
    # Passthroughs — no prefix to strip
    ("Binary Search", "Binary Search"),
    ("Top 10 Mistakes", "Top 10 Mistakes"),
    ("HTTP/2 in Practice", "HTTP/2 in Practice"),
])
def test_strip_prefix(raw, expected):
    assert strip_prefix(raw) == expected


def test_strip_prefix_normalizes_whitespace():
    assert strip_prefix("Chapter\xa01:  Intro ") == "Intro"


# ---------------------------------------------------------------------------
# is_book_meta
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("title", [
    "Cover", "Foreword", "Index", "Dedication", "Acknowledgements",
    "Table of Contents", "About the Author", "About Packt",
    # D5 additions
    "Preface", "Copyright", "About the Reviewers", "Other Books You May Enjoy",
    # Phrase matches
    "Who Should Read This Book?", "How This Book Is Organized",
    "Conventions Used in This Book", "Technical Requirements",
    "What This Book Covers", "Share Your Thoughts",
    "Downloading the example code files", "Download the color images",
    # End-of-chapter standalone markers
    "Questions", "Summary", "Exercises", "Summary.",
])
def test_is_book_meta_positive(title):
    assert is_book_meta(title) is True


@pytest.mark.parametrize("title", [
    # Embedded EOC words must survive
    "Questions About Recursion", "Summary Statistics in Pandas",
    "Exercises for the Reader: Generics",
    # Deliberately kept (often real content)
    "Appendix", "Glossary",
    # Lookalikes
    "Coverage Analysis", "Indexing Strategies", "Copyright Law Basics",
    "",
])
def test_is_book_meta_negative(title):
    assert is_book_meta(title) is False


# ---------------------------------------------------------------------------
# process_file
# ---------------------------------------------------------------------------

def make_extracted(tmp_path: Path) -> Path:
    path = tmp_path / "book.json"
    write_json(path, {
        "source_type": "pdf",
        "sections": [
            {"title": "Cover", "content": "cover art"},
            {"title": "Chapter 1: Meet Kafka", "content": "kafka intro"},
            {"title": "Chapter 2", "content": "bare chapter"},
            {"title": "Summary", "content": "end of chapter recap"},
            {"title": "Plain Topic", "content": "untouched"},
        ],
    })
    return path


def test_process_file_renames_and_drops(tmp_path):
    path = make_extracted(tmp_path)
    result = process_file(path)
    assert ("Chapter 1: Meet Kafka", "Meet Kafka") in result["renamed"]
    assert "Cover" in result["dropped"]
    assert "Summary" in result["dropped"]

    titles = [s["title"] for s in read_json(path)["sections"]]
    assert titles == ["Meet Kafka", "Chapter 2", "Plain Topic"]


def test_process_file_remap_takes_precedence(tmp_path):
    path = make_extracted(tmp_path)
    result = process_file(path, remap={"Chapter 2": "Kafka Producers"})
    assert ("Chapter 2", "Kafka Producers") in result["renamed"]
    titles = [s["title"] for s in read_json(path)["sections"]]
    assert "Kafka Producers" in titles


def test_process_file_is_idempotent(tmp_path):
    path = make_extracted(tmp_path)
    process_file(path)
    second = process_file(path)
    assert second["renamed"] == []
    assert second["dropped"] == []


def test_process_file_dry_run_writes_nothing(tmp_path):
    path = make_extracted(tmp_path)
    before = path.read_text()
    result = process_file(path, dry_run=True)
    assert result["renamed"]  # changes were detected...
    assert path.read_text() == before  # ...but not written
