"""Tests for qualify.py — Stage 2 of the lesson pipeline.

``prepare``: segmentation of synthetic extracted JSON (typed blocks atomic, headings,
min/max token grouping, ids, heading_path, page, raw_span, images, index, scope,
idempotence); ``scope-from-structure``; every ``check`` gate with a planted defect and a
clean pass; ``check_item``; CLI exit codes; regression tests for the plain-heading and
comma-continuation fixes found on real data.

Every fixture is built in tmp_path. Token counts are measured with ``chunk.count_tokens``
at test time (never hard-coded) so the tests hold whichever tokenizer is active.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import _compat  # noqa: F401

import qualify
from chunk import count_tokens
from clean_text import clean_text
from contracts import (
    BLOOM_CEILING_DEFAULT,
    CONTRACT_VERSION,
    UNIT_DEDUP_BAND,
    UNIT_DEDUP_RATIO,
    UNIT_ID_RE,
    UNIT_MAX_TOKENS,
    UNIT_MIN_TOKENS,
    normalize_ws,
)
from qualify import check, check_item, main, prepare, scope_from_structure, segment_text, stats

from conftest import read_json, write_json


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def ids(findings, severity=None):
    return [f.check_id for f in findings if severity is None or f.severity == severity]


def errors(findings):
    return ids(findings, "error")


def warnings(findings):
    return ids(findings, "warning")


def infos(findings):
    return ids(findings, "info")


def messages(findings, check_id):
    return [f.message for f in findings if f.check_id == check_id]


def locations(findings, check_id):
    return [f.location for f in findings if f.check_id == check_id]


def para(min_tokens: int, seed: str) -> str:
    """One-line prose paragraph of at least ``min_tokens`` tokens (sentences end with '.')."""
    sentence = f"The {seed} is explained here with enough detail that a learner can follow each step. "
    text = sentence
    while count_tokens(text) < min_tokens:
        text += sentence
    return text.strip()


def short(seed: str) -> str:
    """A prose paragraph under UNIT_MIN_TOKENS."""
    text = f"The {seed} is explained here briefly."
    assert count_tokens(text) < UNIT_MIN_TOKENS
    return text


def big_para(seed: str, tail: str) -> str:
    """A paragraph that fits UNIT_MAX_TOKENS alone but not together with ``tail``."""
    text = para(UNIT_MAX_TOKENS - count_tokens(tail), seed)
    assert count_tokens(text) <= UNIT_MAX_TOKENS < count_tokens(text + "\n\n" + tail)
    return text


def pdf_lines(n_lines: int, seed: str) -> list[str]:
    """Hard-wrapped paragraph: capitalised first line, lowercase continuation lines without
    terminal punctuation, a final line ending the sentence."""
    first = f"The {seed} is explained here with enough detail that a learner can"
    body = f"and the {seed} is explained here with enough detail that a learner can"
    return [first] + [body] * (n_lines - 1) + ["follow each step and connect it to practice."]


def sec(title: str, content: str, depth: int = 1, metadata: dict | None = None, images: list | None = None) -> dict:
    section = {"title": title, "content": content, "depth": depth}
    if metadata is not None:
        section["metadata"] = metadata
    if images is not None:
        section["images"] = images
    return section


def write_source(cur: Path, key: str, sections: list[dict], source_type: str = "web", images: list | None = None) -> Path:
    data = {"source_type": source_type, "title": f"Title of {key}", "sections": sections}
    if images is not None:
        data["images"] = images
    path = cur / "extracted" / f"{key}.json"
    write_json(path, data)
    return path


def units_of(cur: Path, key: str) -> list[dict]:
    return read_json(cur / "units" / f"{key}.json")["units"]


@pytest.fixture
def cur(tmp_path: Path) -> Path:
    """An output/<name>/ directory with manifest.json and an empty extracted/."""
    root = tmp_path / "cur"
    (root / "extracted").mkdir(parents=True)
    write_json(root / "manifest.json", {"name": "Mini", "pipeline": "lesson", "sources": []})
    return root


# ---------------------------------------------------------------------------
# prepare — segmentation
# ---------------------------------------------------------------------------

def test_code_fence_is_atomic_even_with_blank_lines_inside(cur):
    code = "```python\ndef f():\n    return 1\n\n\nx = f()\n```"
    write_source(cur, "a", [sec("Intro", para(60, "widget") + "\n\n" + code + "\n\n" + para(60, "gadget"))])
    assert errors(prepare(cur)) == []
    units = units_of(cur, "a")
    assert [u["block_type"] for u in units] == ["prose", "code", "prose"]
    assert units[1]["text"].startswith("```python") and units[1]["text"].endswith("```")
    assert "x = f()" in units[1]["text"]
    assert "```" not in units[0]["text"] and "```" not in units[2]["text"]


def test_pipe_table_is_atomic(cur):
    table = "| a | b |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |"
    write_source(cur, "a", [sec("Intro", para(60, "widget") + "\n\n" + table + "\n\n" + para(60, "gadget"))])
    prepare(cur)
    units = units_of(cur, "a")
    assert [u["block_type"] for u in units] == ["prose", "table", "prose"]
    assert units[1]["text"] == table


def test_bullet_list_is_atomic_and_takes_its_short_lead_in(cur):
    lead = "Common problems include:"
    items = "- first item here\n- second item here\n- third item here"
    write_source(cur, "a", [sec("Intro", lead + "\n\n" + items + "\n\n" + para(60, "gadget"))])
    prepare(cur)
    units = units_of(cur, "a")
    assert [u["block_type"] for u in units] == ["list", "prose"]
    assert units[0]["text"].startswith(lead)
    assert units[0]["text"].endswith("- third item here")


def test_numbered_list_and_figure_caption_are_atomic(cur):
    numbered = "1. first step\n2. second step\n3. third step"
    caption = "Figure 3-1. The service layer sits above the domain."
    write_source(cur, "a", [sec("Intro", para(60, "widget") + "\n\n" + numbered + "\n\n" + para(60, "gadget") + "\n\n" + caption + "\n\n" + para(60, "sprocket"))])
    prepare(cur)
    units = units_of(cur, "a")
    assert [u["block_type"] for u in units] == ["prose", "list", "prose", "figure_caption", "prose"]
    assert units[1]["text"] == numbered
    assert units[3]["text"] == caption


def test_hash_heading_starts_a_unit(cur):
    write_source(cur, "a", [sec("Intro", "# First Part\n\n" + para(60, "widget") + "\n\n# Second Part\n\n" + para(60, "gadget"))])
    prepare(cur)
    units = units_of(cur, "a")
    assert len(units) == 2
    assert units[0]["text"].startswith("# First Part")
    assert units[1]["text"].startswith("# Second Part")
    assert "# Second Part" not in units[0]["text"]


def test_hash_heading_never_merges_backwards_even_when_short(cur):
    # A heading-started buffer under UNIT_MIN_TOKENS at the end of the section stays its own unit.
    write_source(cur, "a", [sec("Intro", para(60, "widget") + "\n\n# Closing\n\n" + short("epilogue"))])
    prepare(cur)
    units = units_of(cur, "a")
    assert len(units) == 2
    assert units[1]["text"].startswith("# Closing")
    assert units[1]["token_count"] < UNIT_MIN_TOKENS


def test_short_trailing_prose_merges_into_previous_prose_unit(cur):
    tail = short("tail")
    big = big_para("widget", tail)
    write_source(cur, "a", [sec("Intro", big + "\n\n" + tail)])
    prepare(cur)
    units = units_of(cur, "a")
    assert len(units) == 1
    assert units[0]["text"].endswith(tail)
    assert units[0]["token_count"] > UNIT_MAX_TOKENS  # merged backwards despite the maximum


def test_short_prose_before_a_typed_block_becomes_its_lead_in(cur):
    lead = short("lead")
    big = big_para("widget", lead)
    write_source(cur, "a", [sec("Intro", big + "\n\n" + lead + "\n\n- one\n- two")])
    prepare(cur)
    units = units_of(cur, "a")
    assert [u["block_type"] for u in units] == ["prose", "list"]
    assert units[0]["text"] == big
    assert units[1]["text"].startswith(lead) and units[1]["text"].endswith("- two")


def test_short_prose_after_a_typed_block_stays_alone(cur):
    # Nothing to merge into (previous unit is a list) and nothing follows.
    write_source(cur, "a", [sec("Intro", "- one\n- two\n\n" + short("coda"))])
    prepare(cur)
    units = units_of(cur, "a")
    assert [u["block_type"] for u in units] == ["list", "prose"]
    assert units[1]["token_count"] < UNIT_MIN_TOKENS


def test_prose_over_max_tokens_splits_at_blank_lines(cur):
    p1, p2, p3 = para(150, "widget"), para(150, "gadget"), para(150, "sprocket")
    assert count_tokens(p1 + "\n\n" + p2) <= UNIT_MAX_TOKENS < count_tokens(p1 + "\n\n" + p2 + "\n\n" + p3)
    write_source(cur, "a", [sec("Intro", p1 + "\n\n" + p2 + "\n\n" + p3)])
    prepare(cur)
    units = units_of(cur, "a")
    assert [u["text"] for u in units] == [p1 + "\n\n" + p2, p3]
    assert all(u["token_count"] <= UNIT_MAX_TOKENS for u in units)


def test_single_paragraph_over_max_tokens_splits_at_sentence_boundaries():
    big = para(500, "widget")
    assert count_tokens(big) > UNIT_MAX_TOKENS
    segs = segment_text(big)
    assert len(segs) >= 2
    assert all(s.block_type == "prose" and s.token_count <= UNIT_MAX_TOKENS for s in segs)
    assert all(s.text.endswith(".") for s in segs)
    assert normalize_ws(" ".join(s.text for s in segs)) == normalize_ws(big)
    assert all(big[s.start:s.end] == s.text for s in segs)


def test_unit_ids_are_well_formed_and_sequential_per_section(cur):
    write_source(cur, "src-1.v2", [
        sec("One", para(60, "a") + "\n\n- x\n- y\n\n" + para(60, "b")),
        sec("Two", para(60, "c")),
    ])
    prepare(cur)
    units = units_of(cur, "src-1.v2")
    assert [u["id"] for u in units] == ["u:src-1.v2:0:0", "u:src-1.v2:0:1", "u:src-1.v2:0:2", "u:src-1.v2:1:0"]
    assert all(UNIT_ID_RE.match(u["id"]) for u in units)
    assert [u["section_index"] for u in units] == [0, 0, 0, 1]
    assert [u["section_title"] for u in units] == ["One", "One", "One", "Two"]


def test_heading_path_follows_section_depths(cur):
    write_source(cur, "a", [
        sec("Part A", para(60, "a"), depth=0),
        sec("Chapter 1", para(60, "b"), depth=1),
        sec("Section 1.1", para(60, "c"), depth=2),
        sec("Chapter 2", para(60, "d"), depth=1),
        {"content": para(60, "e")},  # no title, no depth
    ])
    prepare(cur)
    paths = [u["heading_path"] for u in units_of(cur, "a")]
    assert paths == [
        ["Part A"], ["Part A", "Chapter 1"], ["Part A", "Chapter 1", "Section 1.1"], ["Part A", "Chapter 2"], ["Section 4"],
    ]


def test_page_is_interpolated_from_section_metadata(cur):
    lines = pdf_lines(16, "widget") + pdf_lines(16, "gadget") + pdf_lines(16, "sprocket")
    write_source(cur, "a", [
        sec("Paged", "\n".join(lines), metadata={"page_start": 10, "page_end": 19}),
        sec("Start only", "\n".join(pdf_lines(4, "x")), metadata={"page_start": 33}),
        sec("No meta", "\n".join(pdf_lines(4, "y"))),
    ], source_type="pdf")
    prepare(cur)
    units = units_of(cur, "a")
    paged = [u for u in units if u["section_index"] == 0]
    assert len(paged) >= 2
    pages = [u["page"] for u in paged]
    assert pages[0] == 10
    assert pages == sorted(pages) and all(10 <= p <= 19 for p in pages)
    assert pages[-1] > 10
    assert [u["page"] for u in units if u["section_index"] == 1] == [33]
    assert [u["page"] for u in units if u["section_index"] == 2] == [None]


def test_raw_span_resolves_into_the_raw_section_text(cur):
    furniture = ["12", "From the Library of Jane Doe", "CHAPTER 3"]
    p1 = pdf_lines(6, "widget")
    p1[2] = "the widget is explained here so that a learner can sepa-"
    p1[3] = "rate the ﬁrst concern from the second without any trouble"
    p2 = pdf_lines(6, "gadget")
    raw = "\n".join(p1[:3] + furniture[:1] + p1[3:] + furniture[1:] + p2)
    write_source(cur, "a", [sec("Spans", raw)], source_type="pdf")
    findings = prepare(cur)
    assert "units.raw_span_unresolved" not in ids(findings)
    units = units_of(cur, "a")
    assert units, "expected at least one unit"
    for u in units:
        a, b = u["raw_span"]
        assert isinstance(a, int) and isinstance(b, int) and 0 <= a < b <= len(raw)
        head, tail = normalize_ws(u["text"])[:25], normalize_ws(u["text"])[-25:]
        assert normalize_ws(raw[a:b]).lower().startswith(head.lower()[:10])
        assert normalize_ws(raw[a:b]).lower().endswith(tail.lower()[-10:])
    joined = "\n".join(u["text"] for u in units)
    assert "separate" in joined and "sepa-" not in joined
    assert "first" in joined and "ﬁ" not in joined
    assert "From the Library" not in joined and "CHAPTER 3" not in joined
    assert not any(line.strip() == "12" for line in joined.split("\n"))
    cleaning = {k: sum(u["cleaning"][k] for u in units) for k in units[0]["cleaning"]}
    assert cleaning["removed_lines"] == 3
    assert cleaning["dehyphenated"] == 1
    assert cleaning["ligatures"] == 1
    assert cleaning["rejoined"] > 0
    assert set(cleaning) == {"removed_lines", "dehyphenated", "rejoined", "ligatures", "footnotes"}


def test_images_attach_to_the_unit_covering_their_page(cur):
    lines = pdf_lines(18, "widget") + pdf_lines(18, "gadget")
    images = [
        {"id": "p10_img0", "local_path": "images/p10_img0.png", "context": "page:10"},
        {"id": "p19_img0", "local_path": "images/p19_img0.png", "context": "page:19", "alt_text": "late"},
        {"id": "noctx", "local_path": "images/noctx.jpg"},
    ]
    write_source(cur, "a", [sec("Paged", "\n".join(lines), metadata={"page_start": 10, "page_end": 19}, images=images)],
                 source_type="pdf", images=[{"id": "p10_img0", "mime_type": "image/png"}])
    findings = prepare(cur)
    assert "units.image_unattached" not in ids(findings)
    units = units_of(cur, "a")
    assert len(units) == 2
    assert [img["id"] for img in units[0]["images"]] == ["p10_img0", "noctx"]
    assert [img["id"] for img in units[1]["images"]] == ["p19_img0"]
    first = units[0]["images"][0]
    assert first == {"id": "p10_img0", "local_path": "images/p10_img0.png", "alt_text": "", "mime_type": "image/png"}
    assert units[0]["images"][1]["mime_type"] == "image/jpeg"   # guessed from the extension
    assert units[1]["images"][0]["alt_text"] == "late"


def test_units_file_header_and_index_counts(cur):
    write_source(cur, "a", [sec("One", para(60, "a")), sec("Empty", "   "), sec("Two", para(60, "b") + "\n\n- x\n- y")], source_type="pdf")
    write_source(cur, "b", [sec("Only", para(60, "c"))])
    assert errors(prepare(cur)) == []
    file_a = read_json(cur / "units" / "a.json")
    assert file_a["contract_version"] == CONTRACT_VERSION
    assert file_a["source"] == "a" and file_a["source_file"] == "a.json"
    assert file_a["source_type"] == "pdf" and file_a["title"] == "Title of a"
    assert file_a["scope_applied"] is False
    index = read_json(cur / "units" / "index.json")
    assert index["contract_version"] == CONTRACT_VERSION
    assert index["scope"] is None
    assert index["sources"]["a"] == {
        "units": 3, "tokens": sum(u["token_count"] for u in file_a["units"]), "sections": 3, "skipped_sections": 1,
    }
    assert index["sources"]["b"]["units"] == 1 and index["sources"]["b"]["skipped_sections"] == 0
    assert index["total_units"] == 4
    assert index["total_tokens"] == index["sources"]["a"]["tokens"] + index["sources"]["b"]["tokens"]
    assert list(index["sources"]) == ["a", "b"]


def test_prepare_is_idempotent(cur):
    write_source(cur, "a", [sec("One", "\n".join(pdf_lines(20, "widget") + ["7"] + pdf_lines(20, "gadget")))], source_type="pdf")
    write_source(cur, "b", [sec("Two", para(60, "c") + "\n\n```\ncode\n```")])
    prepare(cur)
    first = {p.name: p.read_bytes() for p in (cur / "units").glob("*.json")}
    prepare(cur)
    second = {p.name: p.read_bytes() for p in (cur / "units").glob("*.json")}
    assert first == second
    assert set(first) == {"a.json", "b.json", "index.json"}


# ---------------------------------------------------------------------------
# prepare — scope / sources / input errors
# ---------------------------------------------------------------------------

def _two_sources(cur: Path) -> None:
    write_source(cur, "a", [sec("First", para(60, "a1")), sec("Second", para(60, "a2")), sec("Third", para(60, "a3"))])
    write_source(cur, "b", [sec("Only", para(60, "b1"))])


def test_scope_include_sources_limits_the_units_files(cur):
    _two_sources(cur)
    scope = {"include_sources": ["a"], "include_sections": None}
    write_json(cur / "my_scope.json", scope)
    assert errors(prepare(cur, scope_path=cur / "my_scope.json")) == []
    assert (cur / "units" / "a.json").exists() and not (cur / "units" / "b.json").exists()
    index = read_json(cur / "units" / "index.json")
    assert list(index["sources"]) == ["a"]
    assert index["scope"] == scope
    assert read_json(cur / "units" / "a.json")["scope_applied"] is True


def test_scope_include_sections_filters_by_normalized_title(cur):
    _two_sources(cur)
    write_json(cur / "scope.json", {"include_sources": None, "include_sections": {"a": ["  second "]}})
    prepare(cur)   # default scope.json in input_dir is picked up without --scope
    units = units_of(cur, "a")
    assert [u["section_title"] for u in units] == ["Second"]
    index = read_json(cur / "units" / "index.json")
    assert index["sources"]["a"] == {"units": 1, "tokens": units[0]["token_count"], "sections": 3, "skipped_sections": 2}
    assert index["sources"]["b"]["units"] == 1   # no section filter for b


def test_scope_errors(cur):
    _two_sources(cur)
    f = prepare(cur, scope_path=cur / "nope.json")
    assert errors(f) == ["units.scope_missing"]
    (cur / "bad.json").write_text("{not json", encoding="utf-8")
    assert errors(prepare(cur, scope_path=cur / "bad.json")) == ["units.scope_invalid_json"]
    for bad in ([], {"include_sources": "a"}, {"include_sections": ["a"]}, {"include_sections": {"a": "First"}}):
        write_json(cur / "shape.json", bad)
        assert errors(prepare(cur, scope_path=cur / "shape.json")) == ["units.scope_shape"], bad
    assert not (cur / "units").exists()


def test_stale_units_files_are_removed_when_the_scope_narrows(cur):
    _two_sources(cur)
    prepare(cur)
    assert (cur / "units" / "b.json").exists()
    write_json(cur / "scope.json", {"include_sources": ["a"]})
    f = prepare(cur)
    assert infos(f) == ["units.stale_removed"]
    assert locations(f, "units.stale_removed") == ["units/b.json"]
    assert not (cur / "units" / "b.json").exists()
    assert list(read_json(cur / "units" / "index.json")["sources"]) == ["a"]


def test_source_option_reprepares_one_source_and_keeps_the_other_index_entry(cur):
    _two_sources(cur)
    prepare(cur)
    before = read_json(cur / "units" / "index.json")["sources"]
    write_source(cur, "b", [sec("Only", para(60, "b1")), sec("More", para(60, "b2"))])
    f = prepare(cur, sources=["b"])
    assert errors(f) == [] and infos(f) == []
    after = read_json(cur / "units" / "index.json")["sources"]
    assert after["a"] == before["a"]
    assert after["b"]["units"] == 2 and after["b"]["sections"] == 2
    assert (cur / "units" / "a.json").exists()


def test_unknown_source_option_is_an_error(cur):
    _two_sources(cur)
    f = prepare(cur, sources=["zzz"])
    assert errors(f) == ["units.unknown_source"]
    assert "a, b" in messages(f, "units.unknown_source")[0]
    assert not (cur / "units").exists()


def test_missing_or_empty_extracted_dir(cur, tmp_path):
    assert errors(prepare(cur)) == ["units.missing_extracted"]
    empty = tmp_path / "empty"
    empty.mkdir()
    assert errors(prepare(empty)) == ["units.missing_extracted"]


def test_invalid_extracted_files(cur):
    (cur / "extracted" / "broken.json").write_text("{", encoding="utf-8")
    write_json(cur / "extracted" / "nosections.json", {"source_type": "web", "title": "x"})
    write_source(cur, "mixed", [sec("Ok", para(60, "a")), "not a section", sec("Blank", "")])
    f = prepare(cur)
    assert sorted(errors(f)) == ["units.invalid_extracted", "units.invalid_extracted"]
    assert warnings(f) == ["units.invalid_extracted"]
    assert [x.location for x in f if x.severity == "warning"] == ["extracted/mixed.json sections[1]"]
    assert sorted(locations(f, "units.invalid_extracted")) == ["extracted/broken.json", "extracted/mixed.json sections[1]", "extracted/nosections.json"]
    index = read_json(cur / "units" / "index.json")
    assert list(index["sources"]) == ["mixed"]
    assert index["sources"]["mixed"] == {"units": 1, "tokens": units_of(cur, "mixed")[0]["token_count"], "sections": 3, "skipped_sections": 2}


# ---------------------------------------------------------------------------
# prepare — regressions found on real data (plain sub-headings, comma continuations)
# ---------------------------------------------------------------------------

def test_plain_heading_starts_a_unit_instead_of_ending_the_previous_one(cur):
    # PDF / web extractors emit sub-headings as plain lines ("Making Change Possible");
    # they used to land at the tail of the preceding unit.
    heading = "Making Change Possible"
    web = para(60, "widget") + "\n\n" + heading + "\n\n" + para(60, "gadget")
    pdf = "\n".join(pdf_lines(5, "widget") + [heading] + pdf_lines(5, "gadget"))
    write_source(cur, "web", [sec("Intro", web)])
    write_source(cur, "pdf", [sec("Intro", pdf)], source_type="pdf")
    prepare(cur)
    for key in ("web", "pdf"):
        units = units_of(cur, key)
        assert len(units) == 2, key
        assert not units[0]["text"].endswith(heading), key
        assert units[1]["text"].startswith(heading + "\n\n"), key


def test_plain_heading_waits_until_the_buffer_holds_min_tokens(cur):
    # A chapter summary of one-line terms each followed by a sentence stays grouped.
    text = "\n\n".join(["Vision", short("vision"), "Empathy", short("empathy"), "Collaboration", short("collaboration")])
    write_source(cur, "a", [sec("Summary", text)])
    prepare(cur)
    units = units_of(cur, "a")
    assert len(units) == 1
    assert units[0]["text"] == text


def test_plain_heading_detection_rules():
    assert qualify._is_plain_heading("Making Change Possible")
    assert qualify._is_plain_heading("Hot spots")
    assert qualify._is_plain_heading("1.2 Overview of the model")
    assert not qualify._is_plain_heading("Common problems include:")        # lead-in keeps its colon
    assert not qualify._is_plain_heading("It can be invisible to the people living with it.")
    assert not qualify._is_plain_heading("—Inigo Montoya, from The Princess Bride")
    assert not qualify._is_plain_heading("because the line starts lowercase")
    assert not qualify._is_plain_heading("One two three four five six seven eight nine ten eleven")
    assert not qualify._is_plain_heading("- a list item")
    assert not qualify._is_plain_heading("| a | b |")
    assert not qualify._is_plain_heading("Figure 3-1 the diagram")
    assert not qualify._is_plain_heading("Two\nlines")
    assert not qualify._is_plain_heading("")


def test_lead_in_before_a_list_is_not_treated_as_a_heading(cur):
    big = para(60, "widget")
    write_source(cur, "a", [sec("Intro", big + "\n\nCommon problems include:\n\n- one\n- two")])
    prepare(cur)
    units = units_of(cur, "a")
    assert [u["block_type"] for u in units] == ["prose", "list"]
    assert units[0]["text"].endswith("Common problems include:")   # grouped into the prose buffer (>= min), as before


def test_comma_continuation_lines_are_rejoined():
    # adr.github.io's Y-statement arrives one fragment per line; ", facing <concern>" is never a paragraph.
    raw = "In short, the Y-statement is as follows:\nIn the context of\n<use case>\n, facing <concern>\nwe decided for <option>\n; accepting <downside>"
    cleaned, report = clean_text(raw, source_type="web")
    assert cleaned == "In short, the Y-statement is as follows:\n\nIn the context of\n\n<use case> , facing <concern> we decided for <option> ; accepting <downside>"
    assert report["rejoined"] == 3
    # the same in a hard-wrapped source, across a page break
    cleaned_pdf, _ = clean_text("we decided for the option\n\n, accepting the downside.", source_type="pdf")
    assert cleaned_pdf == "we decided for the option , accepting the downside."


# ---------------------------------------------------------------------------
# scope-from-structure
# ---------------------------------------------------------------------------

def _structure(cur: Path, *, wrap: bool = False) -> Path:
    topics = [
        {"title": "Area One", "depth": 0, "children": [
            {"title": "Leaf A", "depth": 1, "source_sections": ["Alpha", "Beta"], "children": []},
            {"title": "Mid", "depth": 1, "children": [
                {"title": "Leaf B", "depth": 2, "source_sections": ["beta", "Missing Title"], "children": []},
            ]},
        ]},
        {"title": "Area Two", "depth": 0, "children": [
            {"title": "Leaf C", "depth": 1, "source_sections": ["Gamma"], "children": []},
        ]},
        {"title": "Area Empty", "depth": 0, "children": [{"title": "Leaf D", "depth": 1, "source_sections": [], "children": []}]},
    ]
    path = cur / "legacy" / "structure.json"
    path.parent.mkdir(exist_ok=True)
    write_json(path, {"topics": topics} if wrap else topics)
    return path


def _scoped_sources(cur: Path) -> None:
    write_source(cur, "a", [sec("Alpha", para(60, "a1")), sec("Beta", para(60, "a2")), sec("Delta", para(60, "a3"))])
    write_source(cur, "b", [sec("Gamma", para(60, "b1")), sec("Alpha", para(60, "b2"))])


def test_scope_from_structure_resolves_titles_to_extracted_files(cur):
    _scoped_sources(cur)
    structure = _structure(cur)
    out = cur / "scope.json"
    scope, f = scope_from_structure(structure, ["Area One"], cur / "extracted", out)
    assert scope == {"include_sources": ["a", "b"], "include_sections": {"a": ["Alpha", "Beta"], "b": ["Alpha"]}}
    assert read_json(out) == scope
    assert errors(f) == []
    assert warnings(f) == ["scope.unresolved_section"]
    assert locations(f, "scope.unresolved_section") == ["Missing Title"]
    assert infos(f) == ["scope.ambiguous_section"]
    assert locations(f, "scope.ambiguous_section") == ["Alpha"]
    # the scope then drives prepare
    assert errors(prepare(cur, scope_path=out)) == []
    assert [u["section_title"] for u in units_of(cur, "a")] == ["Alpha", "Beta"]
    assert [u["section_title"] for u in units_of(cur, "b")] == ["Alpha"]


def test_scope_from_structure_accepts_topics_wrapper_and_several_areas(cur):
    _scoped_sources(cur)
    structure = _structure(cur, wrap=True)
    scope, f = scope_from_structure(structure, ["area one", "Area Two"], cur / "extracted", None)
    # areas resolve in structure order, so b sees "Alpha" (Area One) before "Gamma" (Area Two)
    assert scope["include_sections"] == {"a": ["Alpha", "Beta"], "b": ["Alpha", "Gamma"]}
    assert not (cur / "scope.json").exists()


def test_scope_from_structure_errors_and_warnings(cur):
    _scoped_sources(cur)
    structure = _structure(cur)
    scope, f = scope_from_structure(structure, ["Area One", "Nope"], cur / "extracted", cur / "scope.json")
    assert scope is None and errors(f) == ["scope.unknown_area"]
    assert "Nope" in messages(f, "scope.unknown_area")[0] and "Area One" in messages(f, "scope.unknown_area")[0]
    assert not (cur / "scope.json").exists()

    scope, f = scope_from_structure(structure, ["Area Empty"], cur / "extracted", None)
    assert scope == {"include_sources": [], "include_sections": {}}
    assert warnings(f) == ["scope.empty"]

    scope, f = scope_from_structure(structure, ["Area One"], cur / "missing", None)
    assert scope is None and errors(f) == ["scope.missing_extracted"]

    scope, f = scope_from_structure(cur / "nope.json", ["Area One"], cur / "extracted", None)
    assert scope is None and errors(f) == ["scope.invalid_structure"]
    write_json(cur / "legacy" / "bad.json", {"not": "topics"})
    scope, f = scope_from_structure(cur / "legacy" / "bad.json", ["Area One"], cur / "extracted", None)
    assert scope is None and errors(f) == ["scope.invalid_structure"]

    (cur / "extracted" / "broken.json").write_text("{", encoding="utf-8")
    scope, f = scope_from_structure(structure, ["Area Two"], cur / "extracted", None)
    assert scope["include_sections"] == {"b": ["Gamma"]}
    assert "scope.invalid_extracted" in warnings(f)


# ---------------------------------------------------------------------------
# check — fixture: a clean units/ + qualification/ + concepts.json
# ---------------------------------------------------------------------------

TEXTS = {
    "u:alpha:0:0": "Service layers define the boundary of an application and coordinate the domain operations that respond to each request from the presentation layer. They also manage transactions and security for every operation.",
    "u:alpha:0:1": "A domain model is an object model of the domain that incorporates both behavior and data. Each object carries the rules that apply to it, so validation lives next to the state it protects.",
    "u:alpha:1:0": "A unit of work keeps track of everything you do during a business transaction that can affect the database. When you are done, it figures out what needs to be written out.",
    "u:beta:0:0": "The service layer is the entry point for the user interface and other clients. It scripts the use case by calling the domain objects and returning data transfer objects to the caller.",
    "u:beta:0:1": "Domain models grow richer as business rules accumulate, which is why teams with complex logic favor them over transaction scripts despite the mapping cost.",
    "u:beta:2:0": "Units of work are usually created at the start of a request and committed at its end, so every change made while handling the request is persisted together or not at all.",
}
TAGS = {
    "u:alpha:0:0": ("c:service-layer", "coordinate the domain operations"),
    "u:alpha:0:1": ("c:domain-model", "object model of the domain"),
    "u:alpha:1:0": ("c:unit-of-work", "keeps track of everything you do"),
    "u:beta:0:0": ("c:service-layer", "entry point for the user interface"),
    "u:beta:0:1": ("c:domain-model", "grow richer as business rules accumulate"),
    "u:beta:2:0": ("c:unit-of-work", "committed at its end"),
}
# Near-duplicates of u:alpha:0:0 (ratios measured with SequenceMatcher on the lowercased texts).
DUP_HIGH = TEXTS["u:alpha:0:0"].replace("security", "logging")
DUP_BAND = ("Service layers define the boundary of an application and coordinate the domain operations that respond "
            "to each request from the presentation layer. They also schedule batch jobs and send email notifications to users.")


def _unit(uid: str, text: str, block_type: str = "prose", **overrides) -> dict:
    _, _, section, _ = uid.split(":")
    unit = {
        "id": uid, "section_index": int(section), "section_title": f"Section {section}",
        "heading_path": [f"Section {section}"], "block_type": block_type, "text": text,
        "token_count": count_tokens(text), "raw_span": [0, len(text)], "page": None, "images": [],
        "cleaning": {"removed_lines": 0, "dehyphenated": 0, "rejoined": 0, "ligatures": 0, "footnotes": 0},
    }
    unit.update(overrides)
    return unit


def _entry(tags, unit_type="mechanism", knowledge_level="concept", learning_step="building_block",
           bloom_ceiling=None, keep=True, drop_reason=None, depends_on=(), quality=4, **extra) -> dict:
    entry = {
        "concepts": [{"concept_id": cid, "quote": quote} for cid, quote in tags],
        "unit_type": unit_type, "knowledge_level": knowledge_level, "learning_step": learning_step,
        "bloom_ceiling": bloom_ceiling or BLOOM_CEILING_DEFAULT[knowledge_level], "bloom_ceiling_reason": None,
        "quality": quality, "keep": keep, "drop_reason": drop_reason, "depends_on": list(depends_on), "notes": "",
    }
    entry.update(extra)
    return entry


def _concept(cid, label, description="What it is.", aliases=(), depends_on=(), learning_step="building_block",
             coverage="strong", sources=("alpha", "beta")) -> dict:
    return {"id": cid, "label": label, "description": description, "aliases": list(aliases),
            "learning_step": learning_step, "depends_on": list(depends_on), "coverage": coverage, "sources": list(sources)}


def write_units(cur: Path, source: str, units: list[dict], **header) -> None:
    data = {"contract_version": CONTRACT_VERSION, "source": source, "source_file": f"{source}.json",
            "source_type": "pdf", "title": source, "scope_applied": False, "units": units}
    data.update(header)
    write_json(cur / "units" / f"{source}.json", data)


def write_qual(cur: Path, source: str, entries: dict, gleaning: dict | None = None, **header) -> None:
    if gleaning is None:
        gleaning = {str(s): {"missed": False, "note": ""} for s in sorted({int(uid.split(":")[2]) for uid in entries})}
    data = {"contract_version": CONTRACT_VERSION, "source": source, "units": entries, "gleaning": gleaning}
    data.update(header)
    write_json(cur / "qualification" / f"{source}.json", data)


def write_concepts(cur: Path, concepts: list[dict], **extra) -> None:
    data = {"contract_version": CONTRACT_VERSION, "concepts": concepts,
            "gaps": [], "ordering": [c["id"] for c in concepts], "merges": []}
    data.update(extra)
    write_json(cur / "concepts.json", data)


CONCEPTS = [
    _concept("c:domain-model", "Domain Model", learning_step="foundation"),
    _concept("c:service-layer", "Service Layer", depends_on=["c:domain-model"]),
    _concept("c:unit-of-work", "Unit of Work", depends_on=["c:domain-model"]),
]


@pytest.fixture
def lesson(tmp_path: Path) -> Path:
    """units/ for alpha + beta, a complete qualification/ and concepts.json that pass check() cleanly."""
    root = tmp_path / "lesson"
    (root / "units").mkdir(parents=True)
    (root / "qualification").mkdir()
    write_json(root / "manifest.json", {"name": "Mini", "pipeline": "lesson"})
    for source in ("alpha", "beta"):
        uids = [uid for uid in TEXTS if uid.startswith(f"u:{source}:")]
        write_units(root, source, [_unit(uid, TEXTS[uid]) for uid in uids])
        write_qual(root, source, {uid: _entry([TAGS[uid]]) for uid in uids})
    write_concepts(root, CONCEPTS)
    return root


def qual_path(lesson: Path, source: str = "alpha") -> Path:
    return lesson / "qualification" / f"{source}.json"


def edit_qual(lesson: Path, source: str, uid: str, **changes) -> None:
    data = read_json(qual_path(lesson, source))
    data["units"][uid].update(changes)
    write_json(qual_path(lesson, source), data)


def edit_concepts(lesson: Path, **changes) -> None:
    data = read_json(lesson / "concepts.json")
    data.update(changes)
    write_json(lesson / "concepts.json", data)


def edit_concept(lesson: Path, cid: str, **changes) -> None:
    data = read_json(lesson / "concepts.json")
    for concept in data["concepts"]:
        if concept["id"] == cid:
            concept.update(changes)
    write_json(lesson / "concepts.json", data)


def edit_unit(lesson: Path, source: str, uid: str, **changes) -> None:
    data = read_json(lesson / "units" / f"{source}.json")
    for unit in data["units"]:
        if unit["id"] == uid:
            unit.update(changes)
    write_json(lesson / "units" / f"{source}.json", data)


# ---------------------------------------------------------------------------
# check — clean pass, qualification gates
# ---------------------------------------------------------------------------

def test_clean_fixture_passes_every_gate(lesson):
    findings = check(lesson)
    assert ids(findings) == []
    assert read_json(lesson / "units" / "dedup.json") == []
    summary = stats(lesson)
    assert summary["sources"]["alpha"]["units"] == 3 and summary["sources"]["alpha"]["kept"] == 3
    assert summary["sources"]["beta"]["unqualified"] == 0
    assert [(r["concept_id"], r["kept_units"], r["primary_units"]) for r in summary["concepts"]] == [
        ("c:domain-model", 2, 2), ("c:service-layer", 2, 2), ("c:unit-of-work", 2, 2),
    ]


def test_missing_qualification_file(lesson):
    qual_path(lesson, "beta").unlink()
    f = check(lesson)
    assert errors(f) == ["qualification.missing"]
    assert locations(f, "qualification.missing") == ["qualification/beta.json"]


def test_missing_unit_entry(lesson):
    data = read_json(qual_path(lesson))
    del data["units"]["u:alpha:0:1"]
    write_json(qual_path(lesson), data)
    f = check(lesson)
    assert errors(f) == ["qualification.missing_unit"]
    assert "u:alpha:0:1" in messages(f, "qualification.missing_unit")[0]
    assert "concepts.thin" in warnings(f)   # c:domain-model now has one kept unit


def test_unknown_unit_entry(lesson):
    data = read_json(qual_path(lesson))
    data["units"]["u:alpha:9:9"] = _entry([("c:domain-model", "x")])
    data["units"]["u:beta:0:0"] = _entry([TAGS["u:beta:0:0"]])   # a real unit, but of another source
    write_json(qual_path(lesson), data)
    f = check(lesson)
    assert errors(f) == ["qualification.unknown_unit", "qualification.unknown_unit"]
    assert locations(f, "qualification.unknown_unit") == ["qualification/alpha.json units[u:alpha:9:9]", "qualification/alpha.json units[u:beta:0:0]"]


def test_quote_must_be_verbatim_in_the_unit(lesson):
    edit_qual(lesson, "alpha", "u:alpha:0:0", concepts=[{"concept_id": "c:service-layer", "quote": "coordinate the sprocket operations"}])
    f = check(lesson)
    assert errors(f) == ["qualification.quote_mismatch"]
    assert locations(f, "qualification.quote_mismatch") == ["u:alpha:0:0 concepts[0]"]
    # whitespace / case differences are tolerated
    edit_qual(lesson, "alpha", "u:alpha:0:0", concepts=[{"concept_id": "c:service-layer", "quote": "  COORDINATE the\n domain   operations "}])
    assert ids(check(lesson)) == []
    # a missing or empty quote is a mismatch too
    edit_qual(lesson, "alpha", "u:alpha:0:0", concepts=[{"concept_id": "c:service-layer", "quote": ""}])
    assert errors(check(lesson)) == ["qualification.quote_mismatch"]


@pytest.mark.parametrize("key", ["unit_type", "knowledge_level", "learning_step", "bloom_ceiling"])
def test_enum_fields_are_validated(lesson, key):
    edit_qual(lesson, "alpha", "u:alpha:0:0", **{key: "bogus"})
    f = check(lesson)
    assert errors(f) == ["qualification.enum"]
    assert key in messages(f, "qualification.enum")[0]


def test_bloom_ceiling_override_needs_a_reason(lesson):
    edit_qual(lesson, "alpha", "u:alpha:0:0", knowledge_level="fact", bloom_ceiling="evaluate")
    f = check(lesson)
    assert warnings(f) == ["qualification.bloom_ceiling_reason"]
    assert "understand" in messages(f, "qualification.bloom_ceiling_reason")[0]
    edit_qual(lesson, "alpha", "u:alpha:0:0", bloom_ceiling_reason="the unit argues a trade-off")
    assert ids(check(lesson)) == []
    # the default ceiling for the level needs no reason
    edit_qual(lesson, "alpha", "u:alpha:0:0", bloom_ceiling="understand", bloom_ceiling_reason=None)
    assert ids(check(lesson)) == []


def test_drop_reason_format(lesson):
    uid = "u:alpha:0:0"
    edit_qual(lesson, "alpha", uid, keep=False, drop_reason="bogus")
    assert errors(check(lesson)) == ["qualification.drop_reason"]
    edit_qual(lesson, "alpha", uid, keep=False, drop_reason=None)
    assert errors(check(lesson)) == ["qualification.drop_reason"]
    edit_qual(lesson, "alpha", uid, keep=False, drop_reason=f"duplicate_of:{uid}")
    assert "names the unit itself" in messages(check(lesson), "qualification.drop_reason")[0]
    edit_qual(lesson, "alpha", uid, keep=False, drop_reason="duplicate_of:u:nope:0:0")
    assert "is not a unit" in messages(check(lesson), "qualification.drop_reason")[0]
    for ok in ("meta", "off_topic", "noise", "duplicate_of:u:beta:0:0"):
        edit_qual(lesson, "alpha", uid, keep=False, drop_reason=ok)
        f = check(lesson)
        assert errors(f) == [], ok
        assert "qualification.drop_reason" not in ids(f), ok
    # a reason on a kept unit is a warning
    edit_qual(lesson, "alpha", uid, keep=True, drop_reason="meta")
    f = check(lesson)
    assert warnings(f) == ["qualification.drop_reason"] and errors(f) == []
    # keep must be a bool
    edit_qual(lesson, "alpha", uid, keep="yes", drop_reason=None)
    assert errors(check(lesson)) == ["qualification.keep"]


def test_kept_unit_warnings_no_concepts_and_noise_kept(lesson):
    edit_qual(lesson, "alpha", "u:alpha:0:0", concepts=[])
    f = check(lesson)
    assert "qualification.no_concepts" in warnings(f)
    edit_qual(lesson, "alpha", "u:alpha:0:0", concepts=[{"concept_id": "c:service-layer", "quote": TAGS["u:alpha:0:0"][1]}], unit_type="noise")
    assert warnings(check(lesson)) == ["qualification.noise_kept"]
    edit_qual(lesson, "alpha", "u:alpha:0:0", unit_type="reference", keep=False, drop_reason="noise")
    assert "qualification.noise_kept" not in ids(check(lesson))


def test_quality_must_be_an_integer_1_to_5(lesson):
    for bad in (0, 6, "4", 4.0, True, None):
        edit_qual(lesson, "alpha", "u:alpha:0:0", quality=bad)
        assert errors(check(lesson)) == ["qualification.quality"], bad
    edit_qual(lesson, "alpha", "u:alpha:0:0", quality=1)
    assert ids(check(lesson)) == []


def test_concept_tags_shape_and_unknown_concepts(lesson):
    edit_qual(lesson, "alpha", "u:alpha:0:0", concepts="c:service-layer")
    assert errors(check(lesson)) == ["qualification.concepts"]
    edit_qual(lesson, "alpha", "u:alpha:0:0", concepts=["c:service-layer"])
    assert errors(check(lesson)) == ["qualification.concepts"]
    edit_qual(lesson, "alpha", "u:alpha:0:0", concepts=[{"concept_id": "Service Layer", "quote": "boundary"}])
    assert errors(check(lesson)) == ["qualification.concepts"]
    edit_qual(lesson, "alpha", "u:alpha:0:0", concepts=[{"concept_id": "c:nope", "quote": "boundary"}])
    f = check(lesson)
    assert errors(f) == ["concepts.unknown_concept"]
    assert "not in concepts.json" in messages(f, "concepts.unknown_concept")[0]
    # a merged-away id gets a pointer to the kept one
    edit_concepts(lesson, merges=[{"kept": "c:service-layer", "merged": ["c:nope"], "reason": "same thing"}])
    assert "was merged into c:service-layer" in messages(check(lesson), "concepts.unknown_concept")[0]
    # without concepts.json the tag cannot be resolved and is not reported as unknown
    (lesson / "concepts.json").unlink()
    f = check(lesson)
    assert errors(f) == ["concepts.missing"]


def test_depends_on_entries(lesson):
    edit_qual(lesson, "alpha", "u:alpha:0:0", depends_on="c:domain-model")
    assert errors(check(lesson)) == ["qualification.depends_on"]
    edit_qual(lesson, "alpha", "u:alpha:0:0", depends_on=["domain model"])
    assert errors(check(lesson)) == ["qualification.depends_on"]
    edit_qual(lesson, "alpha", "u:alpha:0:0", depends_on=["c:nope"])
    f = check(lesson)
    assert errors(f) == ["concepts.unknown_concept"]
    assert locations(f, "concepts.unknown_concept") == ["u:alpha:0:0 depends_on"]
    edit_qual(lesson, "alpha", "u:alpha:0:0", depends_on=["c:domain-model"])
    assert ids(check(lesson)) == []
    edit_qual(lesson, "alpha", "u:alpha:0:0", depends_on=None)
    assert ids(check(lesson)) == []


def test_gleaning_entries(lesson):
    data = read_json(qual_path(lesson))
    del data["gleaning"]["1"]
    write_json(qual_path(lesson), data)
    f = check(lesson)
    assert warnings(f) == ["qualification.gleaning"]
    assert "section index(es) 1" in messages(f, "qualification.gleaning")[0]
    data["gleaning"] = {"0": {"missed": False, "note": ""}, "1": {"missed": "no"}, "7": {"missed": True, "note": "x"}, "abc": {"missed": False}}
    write_json(qual_path(lesson), data)
    f = check(lesson)
    assert warnings(f) == ["qualification.gleaning"] * 3
    data["gleaning"] = []
    write_json(qual_path(lesson), data)
    assert warnings(check(lesson)) == ["qualification.gleaning"]
    del data["gleaning"]
    write_json(qual_path(lesson), data)
    assert "no gleaning entry" in messages(check(lesson), "qualification.gleaning")[0]


def test_qualification_file_shape_and_headers(lesson):
    good = read_json(qual_path(lesson))
    write_json(qual_path(lesson), {"contract_version": CONTRACT_VERSION, "source": "alpha", "units": []})
    assert errors(check(lesson)) == ["qualification.shape"]
    write_json(qual_path(lesson), {**good, "units": {**good["units"], "u:alpha:0:0": "entry"}})
    f = check(lesson)
    assert errors(f) == ["qualification.shape"] and locations(f, "qualification.shape") == ["u:alpha:0:0"]
    write_json(qual_path(lesson), {**good, "contract_version": 2, "source": "beta"})
    f = check(lesson)
    assert errors(f) == ["qualification.source_mismatch"]
    assert warnings(f) == ["qualification.contract_version"]
    qual_path(lesson).write_text("{", encoding="utf-8")
    assert errors(check(lesson)) == ["qualification.invalid_json"]


def test_qualification_for_a_source_without_units_is_flagged(lesson):
    write_qual(lesson, "gamma", {})
    f = check(lesson)
    assert warnings(f) == ["qualification.unknown_source"] and errors(f) == []
    assert locations(f, "qualification.unknown_source") == ["qualification/gamma.json"]


# ---------------------------------------------------------------------------
# check — units files
# ---------------------------------------------------------------------------

def test_units_missing_and_invalid(lesson, tmp_path):
    empty = tmp_path / "empty"
    (empty / "units").mkdir(parents=True)
    f = check(empty)
    assert errors(f) == ["units.missing"]
    assert not (empty / "units" / "dedup.json").exists()
    f = check(tmp_path / "nothing")
    assert errors(f) == ["units.missing"]
    (lesson / "units" / "alpha.json").write_text("{", encoding="utf-8")
    assert "units.invalid_json" in errors(check(lesson))
    write_json(lesson / "units" / "alpha.json", {"units": {}})
    assert "units.shape" in errors(check(lesson))


def test_units_file_header_checks(lesson):
    data = read_json(lesson / "units" / "alpha.json")
    write_json(lesson / "units" / "alpha.json", {**data, "contract_version": 0, "source": "zeta"})
    f = check(lesson)
    assert "units.source_mismatch" in errors(f)
    assert "units.contract_version" in warnings(f)


def test_unit_field_checks(lesson):
    edit_unit(lesson, "alpha", "u:alpha:0:0", id="alpha-0-0")
    f = check(lesson)
    assert "units.id" in errors(f)
    assert "alpha-0-0" in messages(f, "units.id")[0]
    edit_unit(lesson, "alpha", "alpha-0-0", id="u:alpha:0:0", section_index=5)
    f = check(lesson)
    assert "units.id" in errors(f) and "disagrees" in messages(f, "units.id")[0]
    edit_unit(lesson, "alpha", "u:alpha:0:0", section_index=0, block_type="paragraph", token_count=0, text="  ", raw_span=[0, "x"], heading_path="Section 0", images=None)
    f = check(lesson)
    for check_id in ("units.block_type", "units.token_count", "units.empty_text", "units.shape"):
        assert check_id in errors(f), check_id
    assert errors(f).count("units.shape") == 2   # raw_span + heading_path/images
    # a duplicate id is reported once and the second copy ignored
    data = read_json(lesson / "units" / "beta.json")
    data["units"].append(dict(data["units"][0]))
    write_json(lesson / "units" / "beta.json", data)
    assert errors(check(lesson)).count("units.duplicate_id") == 1


def test_unit_that_is_not_an_object(lesson):
    data = read_json(lesson / "units" / "alpha.json")
    data["units"].append("text")
    write_json(lesson / "units" / "alpha.json", data)
    f = check(lesson)
    assert errors(f) == ["units.shape"]
    assert locations(f, "units.shape") == ["units/alpha.json units[3]"]


# ---------------------------------------------------------------------------
# check — concepts.json
# ---------------------------------------------------------------------------

def test_concepts_missing_invalid_or_misshapen(lesson):
    (lesson / "concepts.json").unlink()
    f = check(lesson)
    assert errors(f) == ["concepts.missing"]
    (lesson / "concepts.json").write_text("{", encoding="utf-8")
    assert errors(check(lesson)) == ["concepts.invalid_json"]
    write_json(lesson / "concepts.json", {"concepts": {}})
    assert errors(check(lesson)) == ["concepts.shape"]


def test_concept_id_and_label_rules(lesson):
    edit_concept(lesson, "c:unit-of-work", id="unit-of-work")
    f = check(lesson)
    assert "concepts.id" in errors(f)
    assert "concepts.unknown_concept" in errors(f)   # the units still tag c:unit-of-work
    edit_concept(lesson, "unit-of-work", id="c:unit-of-work", label="A unit of work for every single request")
    f = check(lesson)
    assert errors(f) == ["concepts.label"]
    assert "8 words" in messages(f, "concepts.label")[0]
    edit_concept(lesson, "c:unit-of-work", label="")
    assert errors(check(lesson)) == ["concepts.label"]
    edit_concept(lesson, "c:unit-of-work", label="Unit of Work Pattern Core")   # 5 words: allowed
    assert ids(check(lesson)) == []


def test_too_broad_label_on_the_stoplist(lesson):
    edit_concept(lesson, "c:unit-of-work", label="Architecture")
    f = check(lesson)
    assert warnings(f) == ["concepts.too_broad"]
    assert "stoplist" in messages(f, "concepts.too_broad")[0]


def test_too_broad_concept_by_share_of_kept_units(lesson):
    # tag c:domain-model on every kept unit as a secondary concept: 6/6 > 40%
    for source in ("alpha", "beta"):
        data = read_json(qual_path(lesson, source))
        for uid, entry in data["units"].items():
            if entry["concepts"][0]["concept_id"] != "c:domain-model":
                entry["concepts"].append({"concept_id": "c:domain-model", "quote": TAGS[uid][1]})
        write_json(qual_path(lesson, source), data)
    f = check(lesson)
    assert warnings(f) == ["concepts.too_broad"]
    assert locations(f, "concepts.too_broad") == ["c:domain-model"]
    assert "6/6" in messages(f, "concepts.too_broad")[0]


def test_thin_concept(lesson):
    edit_qual(lesson, "beta", "u:beta:2:0", keep=False, drop_reason="off_topic")
    f = check(lesson)
    assert warnings(f) == ["concepts.thin"]
    assert locations(f, "concepts.thin") == ["c:unit-of-work"]
    edit_concept(lesson, "c:unit-of-work", coverage="gap")
    assert ids(check(lesson)) == []


def test_concept_description_enum_shape_and_sources(lesson):
    edit_concept(lesson, "c:service-layer", description="  ")
    assert warnings(check(lesson)) == ["concepts.description"]
    edit_concept(lesson, "c:service-layer", description="ok", aliases="alias", sources=[1])
    assert errors(check(lesson)) == ["concepts.shape", "concepts.shape"]
    edit_concept(lesson, "c:service-layer", aliases=[], sources=["gamma"])
    f = check(lesson)
    assert warnings(f) == ["concepts.unknown_source"]
    edit_concept(lesson, "c:service-layer", sources=["alpha"], learning_step="basic", coverage="full")
    f = check(lesson)
    assert errors(f) == ["concepts.enum", "concepts.enum"]


def test_duplicate_concept_ids_and_labels(lesson):
    data = read_json(lesson / "concepts.json")
    data["concepts"].append(_concept("c:service-layer", "Service Layer Again"))
    data["concepts"].append(_concept("c:boundary", "service layer"))
    write_json(lesson / "concepts.json", data)
    f = check(lesson)
    assert "concepts.duplicate_id" in errors(f)
    assert "concepts.duplicate_label" in errors(f)
    assert "c:service-layer, c:boundary" in locations(f, "concepts.duplicate_label")


def test_depends_on_cycle_and_unknown_dependency(lesson):
    edit_concept(lesson, "c:domain-model", depends_on=["c:service-layer"])
    f = check(lesson)
    assert errors(f) == ["concepts.depends_on_cycle"]
    loc = locations(f, "concepts.depends_on_cycle")[0]
    assert loc.startswith("c:domain-model -> c:service-layer -> c:domain-model") or loc.startswith("c:service-layer -> c:domain-model -> c:service-layer")
    edit_concept(lesson, "c:domain-model", depends_on=["c:nope"])
    f = check(lesson)
    assert errors(f) == ["concepts.unknown_concept"]
    assert locations(f, "concepts.unknown_concept") == ["c:domain-model depends_on"]


def test_merge_candidate_from_similar_labels_or_aliases(lesson):
    edit_concept(lesson, "c:domain-model", aliases=["service layers"])
    f = check(lesson)
    assert infos(f) == ["concepts.merge_candidate"]
    assert locations(f, "concepts.merge_candidate") == ["c:domain-model, c:service-layer"]
    assert "'service layers' ~ 'service layer'" in messages(f, "concepts.merge_candidate")[0]
    assert errors(f) == [] and warnings(f) == []


def test_gap_schema_and_resolution(lesson):
    edit_concepts(lesson, gaps={"concept": "x"})
    assert errors(check(lesson)) == ["concepts.gap"]
    edit_concepts(lesson, gaps=["text", {"concept": " ", "severity": "urgent", "suggestion": ""}])
    f = check(lesson)
    assert errors(f) == ["concepts.gap"] * 4
    edit_concepts(lesson, gaps=[{"concept": "Fitness functions", "severity": "recommended", "suggestion": "add a source", "resolution": "later"}])
    f = check(lesson)
    assert errors(f) == [] and warnings(f) == ["concepts.gap_resolution"]


def test_unresolved_critical_gap(lesson):
    gap = {"concept": "Fitness functions", "severity": "critical", "suggestion": "Add Building Evolutionary Architectures"}
    edit_concepts(lesson, gaps=[gap])
    f = check(lesson)
    assert warnings(f) == ["concepts.gap_unresolved"]
    assert "Fitness functions" in messages(f, "concepts.gap_unresolved")[0]
    for resolution in ("user_accepted", "descoped", "source_added: evolutionary-architectures"):
        edit_concepts(lesson, gaps=[{**gap, "resolution": resolution}])
        assert ids(check(lesson)) == [], resolution
    edit_concepts(lesson, gaps=[{**gap, "severity": "nice-to-have"}])
    assert ids(check(lesson)) == []


def test_ordering_checks(lesson):
    edit_concepts(lesson, ordering="c:domain-model")
    assert errors(check(lesson)) == ["concepts.ordering"]
    edit_concepts(lesson, ordering=["c:domain-model", "c:nope", "c:domain-model"])
    f = check(lesson)
    assert errors(f) == ["concepts.ordering", "concepts.ordering"]
    assert "concepts.ordering" in warnings(f) and "2 concept(s) missing" in " ".join(messages(f, "concepts.ordering"))
    edit_concepts(lesson, ordering=["c:service-layer", "c:unit-of-work", "c:domain-model"])
    f = check(lesson)
    assert errors(f) == []
    assert warnings(f) == ["concepts.ordering", "concepts.ordering"]
    assert "before its dependency c:domain-model" in messages(f, "concepts.ordering")[0]
    edit_concepts(lesson, ordering=None)
    assert ids(check(lesson)) == []


def test_merges_checks(lesson):
    edit_concepts(lesson, merges={"kept": "c:service-layer"})
    assert errors(check(lesson)) == ["concepts.merge"]
    edit_concepts(lesson, merges=["x", {"kept": "c:nope", "merged": [], "reason": ""}])
    f = check(lesson)
    assert errors(f) == ["concepts.merge"] * 3
    assert warnings(f) == ["concepts.merge"]   # no reason
    edit_concepts(lesson, merges=[{"kept": "c:service-layer", "merged": ["c:unit-of-work"], "reason": "same"}])
    f = check(lesson)
    assert errors(f) == [] and warnings(f) == ["concepts.merge"]
    assert "still a concept" in messages(f, "concepts.merge")[0]
    edit_concepts(lesson, merges=[{"kept": "c:service-layer", "merged": ["c:application-service"], "reason": "same pattern"}])
    assert ids(check(lesson)) == []


def test_concepts_contract_version_warning(lesson):
    edit_concepts(lesson, contract_version="1")
    assert warnings(check(lesson)) == ["concepts.contract_version"]


# ---------------------------------------------------------------------------
# check — duplicate units + units/dedup.json
# ---------------------------------------------------------------------------

def _add_unit(lesson: Path, source: str, uid: str, text: str, block_type: str = "prose", cid: str = "c:domain-model") -> None:
    """Append a kept unit + its qualification entry (tagging ``cid`` so no concept crosses the too-broad share)."""
    data = read_json(lesson / "units" / f"{source}.json")
    data["units"].append(_unit(uid, text, block_type=block_type))
    write_json(lesson / "units" / f"{source}.json", data)
    qual = read_json(qual_path(lesson, source))
    qual["units"][uid] = _entry([(cid, normalize_ws(text)[:30])])
    qual["gleaning"][uid.split(":")[2]] = {"missed": False, "note": ""}
    write_json(qual_path(lesson, source), qual)


def test_near_duplicate_units_are_flagged_and_alternates_written_to_dedup_json(lesson):
    _add_unit(lesson, "beta", "u:beta:5:0", DUP_HIGH, cid="c:domain-model")
    _add_unit(lesson, "beta", "u:beta:6:0", DUP_BAND, cid="c:unit-of-work")
    f = check(lesson)
    assert errors(f) == []
    assert infos(f) == ["units.alternate_of"]
    assert locations(f, "units.alternate_of") == ["u:beta:5:0"]
    assert "alternate of u:alpha:0:0" in messages(f, "units.alternate_of")[0]
    # the band-ratio unit is near both u:alpha:0:0 and its high-ratio twin u:beta:5:0
    assert warnings(f) == ["units.near_duplicate"] * 2
    assert locations(f, "units.near_duplicate") == ["u:beta:6:0", "u:beta:6:0"]
    assert sorted(m.split(" to ")[1].split(" ")[0] for m in messages(f, "units.near_duplicate")) == ["u:alpha:0:0", "u:beta:5:0"]
    dedup = read_json(lesson / "units" / "dedup.json")
    assert len(dedup) == 1
    assert dedup[0]["unit_id"] == "u:beta:5:0" and dedup[0]["alternate_of"] == "u:alpha:0:0"
    assert UNIT_DEDUP_RATIO <= dedup[0]["ratio"] <= 1.0
    assert set(dedup[0]) == {"unit_id", "alternate_of", "ratio"}


def test_duplicates_in_the_same_section_or_non_prose_are_ignored(lesson):
    _add_unit(lesson, "alpha", "u:alpha:0:2", DUP_HIGH, cid="c:domain-model")                    # same section as u:alpha:0:0
    _add_unit(lesson, "beta", "u:beta:7:0", DUP_HIGH, block_type="list", cid="c:unit-of-work")   # not prose
    f = check(lesson)
    assert ids(f) == []
    assert read_json(lesson / "units" / "dedup.json") == []


def test_dedup_thresholds_match_the_contract():
    from difflib import SequenceMatcher
    base = TEXTS["u:alpha:0:0"].lower()
    assert SequenceMatcher(None, base, DUP_HIGH.lower(), autojunk=False).ratio() >= UNIT_DEDUP_RATIO
    assert UNIT_DEDUP_BAND <= SequenceMatcher(None, base, DUP_BAND.lower(), autojunk=False).ratio() < UNIT_DEDUP_RATIO


# ---------------------------------------------------------------------------
# check_item, stats, CLI
# ---------------------------------------------------------------------------

def test_check_item_covers_one_source_only(lesson):
    edit_qual(lesson, "beta", "u:beta:0:0", quality=9)                     # defect in beta
    edit_qual(lesson, "alpha", "u:alpha:0:0", concepts=[{"concept_id": "c:service-layer", "quote": "wrong"}])
    _add_unit(lesson, "beta", "u:beta:5:0", DUP_HIGH)
    (lesson / "units" / "dedup.json").unlink(missing_ok=True)
    f = check_item(lesson, "alpha")
    assert errors(f) == ["qualification.quote_mismatch"]
    assert "units.alternate_of" not in ids(f)
    assert not (lesson / "units" / "dedup.json").exists()                 # no dedup in a per-item run
    assert ids(check(lesson, source="beta")) == ["qualification.quality"]
    assert ids(check_item(lesson, "gamma")) == ["units.missing"]
    assert ids(check(lesson, source="gamma")) == ["units.missing"]


def test_check_item_without_concepts_still_gates_the_map(lesson):
    (lesson / "concepts.json").unlink()
    f = check_item(lesson, "alpha")
    assert errors(f) == ["concepts.missing"]


def test_stats_counts_drops_and_unqualified_units(lesson):
    edit_qual(lesson, "alpha", "u:alpha:0:0", keep=False, drop_reason="meta")
    edit_qual(lesson, "alpha", "u:alpha:0:1", keep=False, drop_reason="duplicate_of:u:beta:0:1")
    edit_qual(lesson, "beta", "u:beta:0:0", keep=False, drop_reason="???")
    data = read_json(qual_path(lesson, "beta"))
    del data["units"]["u:beta:2:0"]
    write_json(qual_path(lesson, "beta"), data)
    s = stats(lesson)
    alpha, beta = s["sources"]["alpha"], s["sources"]["beta"]
    assert (alpha["kept"], alpha["drop_meta"], alpha["drop_duplicate"], alpha["unqualified"]) == (1, 1, 1, 0)
    assert (beta["kept"], beta["drop_other"], beta["qualified"], beta["unqualified"]) == (1, 1, 2, 1)
    rows = {r["concept_id"]: r for r in s["concepts"]}
    assert rows["c:unit-of-work"]["kept_units"] == 1 and rows["c:domain-model"]["kept_units"] == 1
    assert rows["c:service-layer"]["kept_units"] == 0


def test_cli_prepare_exit_codes(cur, tmp_path, capsys):
    _two_sources(cur)
    assert main(["prepare", str(cur)]) == 0
    out = capsys.readouterr().out
    assert "wrote units/index.json: 2 sources" in out and "0 errors, 0 warnings" in out
    assert main(["prepare", str(cur), "--scope", str(cur / "nope.json")]) == 1
    assert "[ERROR] units.scope_missing" in capsys.readouterr().out
    assert main(["prepare", str(tmp_path / "missing")]) == 2
    assert main(["prepare", str(cur), "--source", "a", "--source", "zzz"]) == 1


def test_cli_check_exit_codes(lesson, tmp_path, capsys):
    assert main(["check", str(lesson)]) == 0
    out = capsys.readouterr().out
    assert "0 errors, 0 warnings" in out
    assert "Units and drops per source" in out and "Kept units per concept" in out
    assert (lesson / "units" / "dedup.json").exists()
    edit_concept(lesson, "c:service-layer", description="")
    assert main(["check", str(lesson)]) == 0
    assert "[WARN] concepts.description" in capsys.readouterr().out
    assert main(["check", str(lesson), "--strict"]) == 1
    capsys.readouterr()
    assert main(["check", str(lesson), "--source", "alpha"]) == 0
    assert "Units and drops" not in capsys.readouterr().out   # no tables in a per-source run
    edit_qual(lesson, "alpha", "u:alpha:0:0", quality=0)
    assert main(["check", str(lesson)]) == 1
    assert main(["check", str(lesson), "--source", "alpha"]) == 1
    assert main(["check", str(lesson), "--source", "beta"]) == 0
    assert main(["check", str(tmp_path / "missing")]) == 2


def test_cli_scope_from_structure_exit_codes(cur, capsys):
    _scoped_sources(cur)
    structure = _structure(cur)
    out = cur / "scope.json"
    assert main(["scope-from-structure", str(structure), "--area", "Area One", "--extracted", str(cur / "extracted"), "-o", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "[WARN] scope.unresolved_section @ Missing Title" in printed
    assert f"wrote {out}: 2 sources, 3 sections" in printed
    assert read_json(out)["include_sources"] == ["a", "b"]
    assert main(["scope-from-structure", str(structure), "--area", "Nope", "--extracted", str(cur / "extracted"), "-o", str(out)]) == 1
    with pytest.raises(SystemExit):
        main(["scope-from-structure", str(structure), "--extracted", str(cur / "extracted"), "-o", str(out)])   # --area is required


def test_end_to_end_prepare_then_author_then_check(cur):
    """prepare on extracted JSON, author a qualification from the produced units, check passes."""
    for source, prefix in (("a", "u:alpha:"), ("b", "u:beta:")):
        write_source(cur, source, [sec(f"Section {i}", TEXTS[uid]) for i, uid in enumerate(uid for uid in TEXTS if uid.startswith(prefix))])
    assert errors(prepare(cur)) == []
    assert sum(len(units_of(cur, s)) for s in ("a", "b")) == 6
    (cur / "qualification").mkdir()
    for source in ("a", "b"):
        entries = {}
        for unit in units_of(cur, source):
            tags = [(cid, quote) for uid, (cid, quote) in TAGS.items() if normalize_ws(quote).lower() in normalize_ws(unit["text"]).lower()]
            entries[unit["id"]] = _entry(tags)
        write_qual(cur, source, entries)
    write_concepts(cur, [_concept(c["id"], c["label"], depends_on=c["depends_on"], learning_step=c["learning_step"], sources=["a", "b"]) for c in CONCEPTS])
    f = check(cur)
    assert errors(f) == [] and warnings(f) == []
    assert main(["check", str(cur)]) == 0
