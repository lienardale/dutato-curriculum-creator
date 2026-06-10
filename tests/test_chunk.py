"""Tests for shared/chunk.py — paragraph splitting and semantic chunking."""

from __future__ import annotations

import _compat  # noqa: F401

from chunk import chunk_text, count_tokens, split_into_paragraphs


def para(label: str, words: int = 60) -> str:
    return f"Paragraph {label}. " + (f"Detail about {label} follows here. " * (words // 5))


# ---------------------------------------------------------------------------
# split_into_paragraphs
# ---------------------------------------------------------------------------

def test_split_on_blank_lines():
    text = "First paragraph.\n\nSecond paragraph.\n\n\nThird."
    assert split_into_paragraphs(text) == [
        "First paragraph.", "Second paragraph.", "Third.",
    ]


def test_fenced_code_block_with_blank_lines_stays_atomic():
    code = "```python\ndef f():\n    return 1\n\n\ndef g():\n    return 2\n```"
    text = f"Intro text.\n\n{code}\n\nOutro text."
    paragraphs = split_into_paragraphs(text)
    assert paragraphs == ["Intro text.", code, "Outro text."]


def test_text_before_fence_is_flushed():
    text = "Lead-in line.\n```\ncode here\n```"
    paragraphs = split_into_paragraphs(text)
    assert paragraphs[0] == "Lead-in line."
    assert paragraphs[1].startswith("```")


# ---------------------------------------------------------------------------
# chunk_text
# ---------------------------------------------------------------------------

def test_short_text_is_single_chunk():
    text = para("only", words=50)
    chunks = chunk_text(text, max_tokens=1500, min_tokens=200)
    assert len(chunks) == 1
    assert chunks[0]["content"] == text.strip()
    assert chunks[0]["token_count"] == count_tokens(text)


def test_long_text_splits_within_bounds():
    text = "\n\n".join(para(str(i)) for i in range(30))
    max_tokens = 300
    chunks = chunk_text(text, max_tokens=max_tokens, min_tokens=50)
    assert len(chunks) > 1
    paragraph_tokens = count_tokens(para("0"))
    for c in chunks:
        # No chunk may exceed max by more than one paragraph's worth
        # (paragraphs are atomic units).
        assert c["token_count"] <= max_tokens + paragraph_tokens


def test_no_content_is_lost_when_splitting():
    paras = [para(str(i)) for i in range(20)]
    chunks = chunk_text("\n\n".join(paras), max_tokens=300, min_tokens=50)
    joined = "\n\n".join(c["content"] for c in chunks)
    for p in paras:
        assert p.strip() in joined


def test_heading_kept_with_body():
    text = "\n\n".join([
        para("one", words=120),
        "## Second Topic",
        para("two", words=120),
        "## Third Topic",
        para("three", words=120),
    ])
    chunks = chunk_text(text, max_tokens=200, min_tokens=50)
    assert len(chunks) > 1
    for c in chunks:
        last_para = c["content"].split("\n\n")[-1].strip()
        assert not last_para.startswith("##"), (
            f"chunk ends with an orphaned heading: {last_para!r}"
        )


def test_split_after_headings_forces_break():
    text = "\n\n".join([
        para("alpha", words=80),
        "## Beta Section",
        para("beta", words=80),
    ])
    # Without the hint, this would fit in fewer chunks
    chunks = chunk_text(
        text, max_tokens=count_tokens(text) - 1, min_tokens=10,
        split_after_headings=["Beta Section"],
    )
    boundary_chunks = [c for c in chunks if c["content"].startswith("## Beta Section")]
    assert len(boundary_chunks) == 1


def test_small_tail_merges_into_previous_chunk():
    paras = [para(str(i), words=100) for i in range(4)] + ["Tiny tail."]
    chunks = chunk_text("\n\n".join(paras), max_tokens=300, min_tokens=100)
    assert chunks[-1]["content"].endswith("Tiny tail.")
    assert count_tokens("Tiny tail.") < 100  # the tail alone is below min


def test_oversized_paragraph_becomes_own_chunk():
    big = para("huge", words=600)
    text = "\n\n".join([para("before", words=50), big, para("after", words=50)])
    chunks = chunk_text(text, max_tokens=300, min_tokens=50)
    own = [c for c in chunks if c["content"] == big.strip()]
    assert len(own) == 1
    assert own[0]["token_count"] > 300


def test_code_chunk_flagged():
    code = "```python\nfor i in range(10):\n    print(i)\n```"
    chunks = chunk_text(f"Some text.\n\n{code}", max_tokens=1500, min_tokens=10)
    assert chunks[0]["has_code"] is True
