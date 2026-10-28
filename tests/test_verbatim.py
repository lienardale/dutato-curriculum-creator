"""Tests for shared/verbatim.py — tokenizer, Newsroom fragments, report numbers, gates."""

from __future__ import annotations

import pytest

import _compat  # noqa: F401

from contracts import (
    VERBATIM_COVERAGE_UNGROUNDED,
    VERBATIM_DENSITY_MAX,
    VERBATIM_FRAGMENT_MAX,
    VERBATIM_JACCARD5_MAX,
)
from verbatim import (
    GATE_SEVERITY,
    UNGROUNDED_MIN_TOKENS,
    extractive_fragments,
    gate,
    tokenize_words,
    verbatim_report,
)


# ---------------------------------------------------------------------------
# tokenize_words
# ---------------------------------------------------------------------------

def test_tokenize_lowercases_and_strips_markdown_and_punctuation():
    text = "## The **Service Layer**: an _application's_ boundary, (see [docs](http://x.io/a)) — v2.0!"
    assert tokenize_words(text) == [
        "the", "service", "layer", "an", "application's", "boundary", "see", "docs", "v2", "0",
    ]


def test_tokenize_removes_fenced_and_inline_code():
    text = (
        "Intro words here.\n"
        "```python\nthe quick brown fox jumps over the lazy dog\n```\n"
        "Then `inline code span` and tail."
    )
    assert tokenize_words(text) == ["intro", "words", "here", "then", "and", "tail"]


def test_tokenize_unclosed_fence_drops_to_end():
    assert tokenize_words("before\n```\ncode until the end") == ["before"]


def test_tokenize_normalizes_curly_apostrophes():
    assert tokenize_words("don’t") == ["don't"]


# ---------------------------------------------------------------------------
# extractive_fragments (Newsroom greedy matching)
# ---------------------------------------------------------------------------

def test_fragments_on_toy_pair():
    summary = tokenize_words("the quick brown fox jumps over the lazy dog today")
    source = tokenize_words("yesterday the quick brown fox leapt; later it ran over the lazy dog")
    frags = extractive_fragments(summary, source)
    # "the quick brown fox" (summary 0..3, source 1..4) and "over the lazy dog" (summary 5..8, source 9..12)
    assert frags == [(0, 1, 4), (5, 9, 4)]


def test_fragments_pick_longest_match_not_first():
    summary = ["a", "b", "c", "d"]
    source = ["a", "b", "x", "a", "b", "c", "d"]
    assert extractive_fragments(summary, source) == [(0, 3, 4)]


def test_fragments_ignore_single_shared_words():
    assert extractive_fragments(["the", "cat"], ["the", "dog", "cat"]) == []


def test_fragments_empty_inputs():
    assert extractive_fragments([], ["a", "b"]) == []
    assert extractive_fragments(["a", "b"], []) == []


# ---------------------------------------------------------------------------
# verbatim_report numbers (hand-computed)
# ---------------------------------------------------------------------------

def test_report_identical_text_is_fully_extractive():
    text = "alpha beta gamma delta epsilon zeta eta theta iota kappa"
    report = verbatim_report(text, [text])
    assert report["summary_tokens"] == 10
    assert report["source_tokens"] == 10
    assert report["coverage"] == 1.0
    assert report["density"] == 10.0          # 10^2 / 10
    assert report["max_fragment"] == 10
    assert report["jaccard5"] == 1.0
    assert report["fragments"] == [{"length": 10, "text": text}]


def test_report_partial_overlap_hand_computed():
    summary = "alpha beta gamma delta epsilon zeta"          # 6 tokens, 5-grams: {a..e, b..f}
    source = "alpha beta gamma delta epsilon xi omega"        # 5-grams: {a..e, b..xi, g..omega}
    report = verbatim_report(summary, [source])
    assert report["max_fragment"] == 5
    assert report["coverage"] == pytest.approx(5 / 6)
    assert report["density"] == pytest.approx(25 / 6)
    assert report["jaccard5"] == pytest.approx(1 / 4)        # 1 shared of 4 distinct 5-grams
    assert report["fragments"] == [{"length": 5, "text": "alpha beta gamma delta epsilon"}]


def test_report_disjoint_texts_have_zero_overlap():
    report = verbatim_report("one two three four five six", ["seven eight nine ten eleven twelve"])
    assert report["coverage"] == 0.0
    assert report["density"] == 0.0
    assert report["max_fragment"] == 0
    assert report["jaccard5"] == 0.0
    assert report["fragments"] == []


def test_report_empty_text_has_no_division_error():
    report = verbatim_report("", ["some source text here"])
    assert report["summary_tokens"] == 0
    assert report["coverage"] == 0.0 and report["density"] == 0.0 and report["jaccard5"] == 0.0


def test_report_short_fragments_not_listed_but_counted():
    report = verbatim_report("the service layer here", ["we use the service layer daily"])
    assert report["max_fragment"] == 3
    assert report["coverage"] == pytest.approx(3 / 4)
    assert report["fragments"] == []  # only fragments >= 5 words are listed


def test_report_fragments_do_not_span_sources():
    report = verbatim_report("alpha beta gamma delta", ["alpha beta", "gamma delta"])
    assert report["max_fragment"] == 2


# ---------------------------------------------------------------------------
# whitelist
# ---------------------------------------------------------------------------

def test_whitelist_removes_concept_label_from_fragments():
    text = "the service layer defines an application boundary"
    source = [text]
    assert verbatim_report(text, source)["max_fragment"] == 7
    report = verbatim_report(text, source, whitelist={"Service Layer"})
    # "the" | <label> | "defines an application boundary"
    assert report["max_fragment"] == 4
    assert report["coverage"] == pytest.approx(4 / 7)
    assert report["fragments"] == []


def test_whitelist_quoted_definition_is_exempt():
    definition = "a layer of services that establishes a set of available operations"
    text = f'PEAA calls it "{definition}" in the pattern catalogue.'
    source = [f"A Service Layer defines an application's boundary with {definition} and coordinates the response."]
    assert verbatim_report(text, source)["max_fragment"] == 11
    report = verbatim_report(text, source, whitelist={definition})
    assert report["max_fragment"] == 0


def test_whitelist_lowers_jaccard_too():
    text = "alpha beta gamma delta epsilon zeta eta"
    plain = verbatim_report(text, [text])
    masked = verbatim_report(text, [text], whitelist={"gamma delta epsilon"})
    assert plain["jaccard5"] == 1.0
    assert masked["jaccard5"] < plain["jaccard5"]


# ---------------------------------------------------------------------------
# code exemption
# ---------------------------------------------------------------------------

def test_code_blocks_are_exempt_from_fragments():
    code = "public class RecognitionService { void calculate(long contractNumber) { } }"
    text = f"Here is the service:\n```java\n{code}\n```\nand `{code}` inline."
    report = verbatim_report(text, [code])
    assert report["summary_tokens"] == 6  # "here is the service" + "and" + "inline"
    assert report["max_fragment"] == 0


# ---------------------------------------------------------------------------
# gate thresholds
# ---------------------------------------------------------------------------

def _report(**overrides) -> dict:
    base = {
        "coverage": 0.2, "density": 0.5, "max_fragment": 3, "fragments": [],
        "jaccard5": 0.0, "summary_tokens": 200, "source_tokens": 2000,
    }
    base.update(overrides)
    return base


def _ids(violations):
    return [check_id for check_id, _ in violations]


def test_gate_clean_report_has_no_violations():
    assert gate(_report(), "brief") == []
    assert gate(_report(), "page") == []


def test_gate_fragment_threshold_is_inclusive():
    assert "verbatim.fragment" in _ids(gate(_report(max_fragment=VERBATIM_FRAGMENT_MAX), "brief"))
    assert "verbatim.fragment" not in _ids(gate(_report(max_fragment=VERBATIM_FRAGMENT_MAX - 1), "brief"))


def test_gate_fragment_message_names_worst_run():
    fragments = [{"length": 9, "text": "a copied run of nine words from the unit"}]
    (check_id, message), = gate(_report(max_fragment=9, fragments=fragments), "brief")
    assert check_id == "verbatim.fragment"
    assert fragments[0]["text"] in message


def test_gate_jaccard_threshold_per_kind():
    j = VERBATIM_JACCARD5_MAX["brief"] + 0.001
    assert "verbatim.jaccard5" in _ids(gate(_report(jaccard5=j), "brief"))
    assert "verbatim.jaccard5" not in _ids(gate(_report(jaccard5=j), "page"))
    assert "verbatim.jaccard5" not in _ids(gate(_report(jaccard5=VERBATIM_JACCARD5_MAX["brief"]), "brief"))


def test_gate_density_threshold_per_kind():
    d = VERBATIM_DENSITY_MAX["brief"] + 0.01
    assert "verbatim.density" in _ids(gate(_report(density=d), "brief"))
    assert "verbatim.density" not in _ids(gate(_report(density=d), "page"))


def test_gate_ungrounded_only_on_long_texts():
    low = VERBATIM_COVERAGE_UNGROUNDED / 2
    assert "verbatim.ungrounded" in _ids(gate(_report(coverage=low, summary_tokens=UNGROUNDED_MIN_TOKENS + 1), "brief"))
    assert "verbatim.ungrounded" not in _ids(gate(_report(coverage=low, summary_tokens=UNGROUNDED_MIN_TOKENS), "brief"))
    assert GATE_SEVERITY["verbatim.ungrounded"] == "warning"


def test_gate_rejects_unknown_kind():
    with pytest.raises(ValueError):
        gate(_report(), "essay")


def test_gate_every_check_id_has_a_severity():
    report = _report(max_fragment=20, jaccard5=0.5, density=9.0, coverage=0.0, summary_tokens=500)
    # coverage 0 with fragments is contradictory but exercises every branch at once
    for check_id, _ in gate(report, "brief"):
        assert check_id in GATE_SEVERITY
