"""Tests for extractors/web.py — markdown extraction, heading split, bullets, seam."""

from __future__ import annotations

import _compat  # noqa: F401

import extractors.web as web
from extractors.web import _repair_inline_formatting, _split_into_sections, extract_html

URL = "https://example.com/guides/service-layer"


def paragraphs(n: int, seed: str = "idea") -> str:
    return "".join(
        f"<p>Paragraph {i} about the {seed} explains it in enough detail to count as "
        f"real article content that the extractor keeps around.</p>"
        for i in range(n)
    )


PAGE = f"""<!doctype html><html><head><title>Guide Title</title>
<meta name="author" content="Jane Doe"></head><body>
<nav><a href="/">Home</a> <a href="/about">About</a></nav>
<article>
<h1>Service Layer</h1>
{paragraphs(4)}
<h2>Why use it</h2>
<p>Several reasons apply:</p>
<ul>
  <li>Transactions are coordinated in one place.</li>
  <li>Clients see a <b>coarse-grained</b> API.</li>
  <li>Plain third bullet.</li>
</ul>
<p>Text with <code>inline_code()</code> and more words after it.</p>
<h3>Caveats</h3>
<p>It can become a dumping ground.</p>
<pre><code># not a heading
def f():
    return 1</code></pre>
<table><tr><th>Pattern</th><th>Use</th></tr><tr><td>Service Layer</td><td>Boundary</td></tr></table>
{paragraphs(4, seed="caveat")}
</article></body></html>"""


def test_extract_html_splits_headings_into_sections_with_depths():
    result = extract_html(PAGE, URL)
    titles = [(s["title"], s["depth"]) for s in result["sections"]]
    assert titles == [("Service Layer", 0), ("Why use it", 1), ("Caveats", 2)]
    assert result["source_type"] == "url"
    assert result["source_path"] == URL
    assert result["metadata"]["total_sections"] == 3
    assert result["metadata"]["url"] == URL


def test_extract_html_reads_title_and_author_metadata():
    result = extract_html(PAGE, URL)
    assert result["title"] == "Service Layer"
    assert result["author"] == "Jane Doe"


def test_extract_html_falls_back_to_url_title():
    html = f"<html><body><article>{paragraphs(5)}</article></body></html>"
    result = extract_html(html, URL)
    assert result["title"] == "example.com/guides/service-layer"
    assert result["sections"], "heading-less pages still produce paragraph-group sections"


def test_bullets_are_preserved_as_markdown_list_items():
    why = {s["title"]: s for s in extract_html(PAGE, URL)["sections"]}["Why use it"]
    lines = [ln.strip() for ln in why["content"].split("\n")]
    assert "- Transactions are coordinated in one place." in lines
    # inline markup inside items is flattened so the bullet structure survives
    assert "- Clients see a coarse-grained API." in lines
    assert "- Plain third bullet." in lines
    # the inline-code sentence survives in one piece
    assert "Text with `inline_code()` and more words after it." in why["content"]


def test_loose_and_nested_list_items_keep_their_bullets():
    html = (
        "<html><head><title>Lists</title></head><body><article><h1>Lists</h1>"
        + paragraphs(4)
        + "<ul><li><p>Loose item one with <b>bold</b> text.</p></li><li><p>Loose item two.</p></li></ul>"
        + "<ul><li>Outer one<ul><li>Inner a</li><li>Inner <code>b()</code> item</li></ul></li>"
        + "<li>Outer two</li></ul>"
        + paragraphs(4, seed="tail")
        + "</article></body></html>"
    )
    content = extract_html(html, URL)["sections"][0]["content"]
    lines = [ln.strip() for ln in content.split("\n") if ln.strip()]
    for expected in ("- Loose item one with bold text.", "- Loose item two.",
                     "- Outer one", "- Inner a", "- Inner b() item", "- Outer two"):
        assert expected in lines, f"{expected!r} missing from {lines}"


def test_tables_and_fenced_code_stay_inside_their_section():
    caveats = {s["title"]: s for s in extract_html(PAGE, URL)["sections"]}["Caveats"]
    assert "| Pattern | Use |" in caveats["content"]
    assert "```" in caveats["content"]
    assert "# not a heading" in caveats["content"], "code comment must not become a section"


def test_paragraphs_are_blank_line_separated():
    first = extract_html(PAGE, URL)["sections"][0]
    assert first["content"].count("\n\n") >= 3


def test_extract_html_raises_on_empty_page():
    import pytest
    with pytest.raises(RuntimeError):
        extract_html("<html><body></body></html>", URL)


# ---------------------------------------------------------------------------
# _split_into_sections (pure)
# ---------------------------------------------------------------------------

def test_split_into_sections_keeps_bullets_and_ignores_fenced_hashes():
    text = (
        "Preamble paragraph.\n\n# Top\n\nIntro.\n\n- one\n- two\n\n"
        "```python\n# comment, not a heading\nx = 1\n```\n\n"
        "## Sub\n\nBody.\n\n#### Too deep stays in content\n\nTail."
    )
    sections = _split_into_sections(text, "Fallback")
    assert [(s["title"], s["depth"]) for s in sections] == [
        ("Fallback", 0), ("Top", 0), ("Sub", 1),
    ]
    assert sections[0]["content"] == "Preamble paragraph."
    assert "- one\n- two" in sections[1]["content"]
    assert "# comment, not a heading" in sections[1]["content"]
    assert "#### Too deep stays in content" in sections[2]["content"]


def test_split_into_sections_without_headings_groups_paragraphs():
    text = "\n\n".join(f"Para {i} text." for i in range(12))
    sections = _split_into_sections(text, "Fallback")
    assert len(sections) == 3
    assert sections[0]["title"] == "Para 0 text."


# ---------------------------------------------------------------------------
# inline-formatting repair
# ---------------------------------------------------------------------------

def test_repair_inline_formatting_rejoins_bold_and_code_breaks():
    broken = (
        "- Clients see a\n**coarse-grained**API.\n"
        "- Bullet with\n`code()`\n\nin it\n\n"
        "Text with `inline_code()`\n\nand more words.\n\n"
        "**Note:** a paragraph that legitimately starts bold.\n\n"
        "```\n`not touched`\n```"
    )
    fixed = _repair_inline_formatting(broken)
    assert "- Clients see a **coarse-grained** API." in fixed
    assert "- Bullet with `code()` in it" in fixed
    assert "Text with `inline_code()` and more words." in fixed
    assert "\n\n**Note:** a paragraph" in fixed
    assert "```\n`not touched`\n```" in fixed


# ---------------------------------------------------------------------------
# extract_web uses the seam (fetch is the only network step)
# ---------------------------------------------------------------------------

def test_extract_web_delegates_to_extract_html(monkeypatch):
    monkeypatch.setattr(web, "_fetch_page", lambda url: PAGE)
    result = web.extract_web(URL)
    assert [s["title"] for s in result["sections"]] == ["Service Layer", "Why use it", "Caveats"]
    assert "pages_crawled" not in result["metadata"]


def test_extract_web_crawl_appends_subpages(monkeypatch):
    entry = (
        "<html><head><title>Docs</title></head><body><article><h1>Docs</h1>"
        + paragraphs(4)
        + f'<p><a href="{URL}/sub-page">Sub page</a></p></article></body></html>'
    )
    sub = (
        "<html><head><title>Sub Page</title></head><body><article><h1>Sub Page</h1>"
        + paragraphs(4, seed="sub")
        + "<h2>Detail</h2><ul><li>sub bullet</li></ul>" + paragraphs(2, seed="detail")
        + "</article></body></html>"
    )
    pages = {URL: entry, f"{URL}/sub-page": sub}
    monkeypatch.setattr(web, "_fetch_page", lambda url: pages.get(url))
    result = web.extract_web(URL, crawl=True)
    titles = [(s["title"], s["depth"]) for s in result["sections"]]
    assert titles[0] == ("Docs", 0)
    assert ("Sub Page", 0) in titles  # chapter heading inserted for the subpage
    assert ("Detail", 2) in titles  # subpage sections shifted one level down
    detail = next(s for s in result["sections"] if s["title"] == "Detail")
    assert "- sub bullet" in detail["content"]
    assert detail["metadata"]["source_url"] == f"{URL}/sub-page"
    assert result["metadata"]["pages_crawled"] == 2
    assert result["metadata"]["crawl_limited_by"] == "exhausted"
