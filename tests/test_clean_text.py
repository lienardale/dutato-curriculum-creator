"""Tests for shared/clean_text.py — recurring-furniture detection, every furniture kind,
de-hyphenation, hard-wrap rejoin, code left untouched, ligatures, footnote markers,
list-marker folding and the cleaned -> raw offset map."""

from __future__ import annotations

import _compat  # noqa: F401

from clean_text import clean_text, clean_text_with_map, find_recurring_lines
from contracts import normalize_ws


# ---------------------------------------------------------------------------
# find_recurring_lines
# ---------------------------------------------------------------------------

def test_bare_page_numbers_recur_on_recurrence_alone():
    texts = ["12\nSome prose here.", "13\nMore prose.", "12\nAgain prose.", "12\nLast one."]
    rec = find_recurring_lines(texts)
    assert "12" in rec
    assert "13" not in rec


def test_all_caps_running_header_recurs():
    rec = find_recurring_lines(["DOMAIN MODEL\nThe pattern text."] * 3)
    assert "DOMAIN MODEL" in rec


def test_title_case_line_needs_adjacency_to_page_numbers():
    not_adjacent = find_recurring_lines(["How It Works\nThe pattern works like this."] * 3)
    assert "How It Works" not in not_adjacent
    adjacent = find_recurring_lines([
        "42\nService Layer\nprose.", "43\nService Layer\nprose.", "Service Layer\nprose.",
    ])
    assert "Service Layer" in adjacent


def test_sentences_and_long_lines_never_recur():
    sentence = "This sentence recurs on every page but ends with a period."
    long_line = "one two three four five six seven eight nine ten"
    rec = find_recurring_lines([f"{sentence}\n{long_line}\nx"] * 4)
    assert sentence not in rec
    assert long_line not in rec


def test_code_like_lines_never_recur():
    for line in ['"SELECT " + COLUMNS +', "foo();", "x = 1", "{"]:
        rec = find_recurring_lines([f"{line}\nSome prose."] * 4)
        assert line not in rec, line


def test_min_occurrences_is_respected():
    texts = ["7\nprose.", "7\nprose."]
    assert "7" not in find_recurring_lines(texts)
    assert "7" in find_recurring_lines(texts, min_occurrences=2)


def test_watermark_chapter_and_pipe_lines_recur_on_recurrence_alone():
    texts = ["From the Library of Jane Doe\nChapter 12\n12 | Chapter 3\nprose."] * 3
    rec = find_recurring_lines(texts)
    assert {"From the Library of Jane Doe", "Chapter 12", "12 | Chapter 3"} <= rec


# ---------------------------------------------------------------------------
# clean_text — furniture
# ---------------------------------------------------------------------------

PDF_SECTION = """Chapter 3. Service Layer
Service Layer
From the Library of Jane Doe
A Service Layer defines an application's boundary with a layer of
services that establishes a set of available operations.
133
Patterns of Enterprise Application Architecture
It coordinates the application's response in each operation.
134 | Chapter 3
CHAPTER 4
Click here to view code image
"""


def test_every_furniture_kind_is_removed_and_lines_rejoined():
    cleaned, report = clean_text(
        PDF_SECTION,
        recurring={"Patterns of Enterprise Application Architecture"},
        source_type="pdf",
        section_title="Service Layer",
    )
    assert cleaned == (
        "A Service Layer defines an application's boundary with a layer of services "
        "that establishes a set of available operations.\n\n"
        "It coordinates the application's response in each operation."
    )
    # 2 title echoes, watermark, page number, running header, footer, CHAPTER 4, click-here
    assert report["removed_lines"] == 8
    assert report["rejoined"] == 1


def test_title_echo_with_parenthesised_disambiguation_is_dropped():
    cleaned, _ = clean_text("Architecture\nBody text here.", section_title="Architecture (PEAA)")
    assert cleaned == "Body text here."


def test_three_line_chapter_footer_is_removed_when_recurring():
    pages = [f"Prose of page {i}.\n{10 + i}\n|\nChapter 3: Patterns" for i in range(3)]
    recurring = find_recurring_lines(pages)
    assert "Chapter 3: Patterns" in recurring
    cleaned, report = clean_text(pages[0], recurring=recurring, source_type="pdf")
    assert cleaned == "Prose of page 0."
    assert report["removed_lines"] == 3


def test_recurring_lines_are_not_removed_from_code():
    text = "    HTML\n    DATA = 1\nSome prose."
    cleaned, report = clean_text(text, recurring={"HTML", "DATA = 1"}, source_type="pdf")
    assert "    HTML" in cleaned and "    DATA = 1" in cleaned
    assert report["removed_lines"] == 0


# ---------------------------------------------------------------------------
# clean_text — de-hyphenation and rejoin
# ---------------------------------------------------------------------------

def test_line_end_hyphen_break_is_removed():
    cleaned, report = clean_text("The two halves stay sepa-\nrate from the whole.", source_type="pdf")
    assert cleaned == "The two halves stay separate from the whole."
    assert report["dehyphenated"] == 1


def test_safe_compound_prefix_keeps_its_hyphen():
    cleaned, _ = clean_text("This is a well-\nknown pattern in the field.", source_type="pdf")
    assert cleaned == "This is a well-known pattern in the field."


def test_uppercase_second_half_keeps_the_hyphen():
    cleaned, _ = clean_text("We use Object-\nRelational mapping tools here.", source_type="pdf")
    assert cleaned == "We use Object-Relational mapping tools here."


def test_attested_forms_decide_the_hyphen():
    kept, _ = clean_text("Services are co-located in one process; the co-\nlocated ones share memory.", source_type="pdf")
    assert "co-located ones" in kept
    dropped, _ = clean_text("The database is shared. Each data-\nbase row is a record.", source_type="pdf")
    assert "database row" in dropped


def test_hard_wrapped_lines_rejoin_and_terminal_lines_split_paragraphs():
    text = (
        "A long first line of hard-wrapped prose that clearly exceeds sixty characters\n"
        "continues on the next line and ends here.\n"
        "A new paragraph starts after the period."
    )
    cleaned, report = clean_text(text, source_type="pdf")
    assert cleaned == (
        "A long first line of hard-wrapped prose that clearly exceeds sixty characters "
        "continues on the next line and ends here.\n\nA new paragraph starts after the period."
    )
    assert report["rejoined"] == 1


def test_page_break_inside_a_sentence_is_bridged():
    text = "The service layer defines the boundary of\n\n17\n\nthe application and nothing more."
    cleaned, _ = clean_text(text, source_type="pdf")
    assert cleaned == "The service layer defines the boundary of the application and nothing more."


def test_web_source_lines_are_paragraphs_not_wrapped_lines():
    cleaned, report = clean_text("Paragraph one is short\nParagraph two is also short.", source_type="url")
    assert cleaned == "Paragraph one is short\n\nParagraph two is also short."
    assert report["rejoined"] == 0


def test_web_source_rejoins_a_split_sentence_after_a_list_item():
    text = "Intro line.\n- Work on what matters to make the most of your hours,\nparticularly as commitments expand.\n- Second item."
    cleaned, report = clean_text(text, source_type="url")
    assert cleaned == (
        "Intro line.\n\n- Work on what matters to make the most of your hours, particularly as commitments expand.\n- Second item."
    )
    assert report["rejoined"] == 1


# ---------------------------------------------------------------------------
# clean_text — code, ligatures, footnotes, list markers
# ---------------------------------------------------------------------------

def test_fenced_code_is_copied_verbatim():
    code = "```python\ndef f(x):\n    return x-\n\n\n    # blank lines above stay\n```"
    text = f"Intro sentence here.\n{code}\nOutro sentence here."
    cleaned, report = clean_text(text, source_type="pdf")
    assert code in cleaned
    assert cleaned == f"Intro sentence here.\n\n{code}\n\nOutro sentence here."
    assert report["dehyphenated"] == 0


def test_code_looking_lines_keep_indentation_and_are_not_joined():
    text = "    int x = 1;\n    int y = 2;\nProse follows the snippet."
    cleaned, _ = clean_text(text, source_type="pdf")
    assert cleaned == "    int x = 1;\n    int y = 2;\n\nProse follows the snippet."


def test_ligatures_are_expanded_and_counted():
    cleaned, report = clean_text("The ﬁrst ﬂow is eﬃcient.", source_type="url")
    assert cleaned == "The first flow is efficient."
    assert report["ligatures"] == 3


def test_footnote_markers_glued_to_punctuation_are_stripped():
    cleaned, report = clean_text("This claim is wrong.3 The next sentence stands.", source_type="pdf")
    assert cleaned == "This claim is wrong. The next sentence stands."
    assert report["footnotes"] == 1


def test_bare_list_markers_fold_into_the_following_line():
    cleaned, _ = clean_text("•\nFirst item text\n•\nSecond item text", source_type="pdf")
    assert cleaned == "• First item text\n• Second item text"


def test_code_source_type_only_maps_ligatures():
    text = "12\nFrom the Library of X\nx = ﬁnd()\n\n\nfoo-\nbar"
    cleaned, report = clean_text(text, source_type="code")
    assert cleaned == "12\nFrom the Library of X\nx = find()\n\n\nfoo-\nbar"
    assert report["removed_lines"] == 0 and report["ligatures"] == 1


def test_empty_input_is_fine():
    assert clean_text("", source_type="pdf")[0] == ""
    assert clean_text("\n\n  \n", source_type="pdf")[0] == ""


# ---------------------------------------------------------------------------
# clean_text_with_map
# ---------------------------------------------------------------------------

def test_offset_map_points_back_into_the_raw_text():
    raw = "Chapter 1. Intro\n5\nThe ﬁrst line of prose that is long enough to be rejoined with\nthe second line.\nAnother paragraph."
    cleaned, report, to_raw = clean_text_with_map(raw, source_type="pdf", section_title="Intro")
    assert cleaned.startswith("The first line")
    start = cleaned.index("the second line.")
    end = start + len("the second line.")
    raw_start, raw_end = to_raw(start), to_raw(end - 1) + 1
    assert raw[raw_start:raw_end] == "the second line."
    # a span covering a ligature expansion still lands on the raw glyph
    s = cleaned.index("first")
    assert raw[to_raw(s):to_raw(s) + 1] == "ﬁ"
    assert set(report["events"]) == {"removed_lines", "dehyphenated", "rejoined", "ligatures", "footnotes"}
    assert report["anchors"] and report["anchors"][0][0] == 0
    assert normalize_ws(raw[to_raw(0):to_raw(len(cleaned) - 1) + 1]).endswith("Another paragraph.")


def test_wrapped_list_items_stay_one_list_in_pdf_sources():
    text = "- First item that wraps onto the next line because it is long and\ncontinues here\n- Second item."
    cleaned, _ = clean_text(text, source_type="pdf")
    assert cleaned == "- First item that wraps onto the next line because it is long and continues here\n- Second item."


def test_unfoldable_bare_marker_is_furniture_in_pdf_sources():
    cleaned, report = clean_text("Prose line one.\n•", source_type="pdf")
    assert cleaned == "Prose line one."
    assert report["removed_lines"] == 1
