"""
Verbatim-copy detection for authored text against its cited source units.

PURE DATA — no AI calls. Used by `synthesize.py check` (brief text vs. the
union of cited units) and by `lessons.py check` (page markdown vs. cited
units). The thresholds live in `shared/contracts.py`; this module only
measures and compares.

Measures follow Grusky, Naaman & Artzi (2018), "Newsroom": a summary is split
into word tokens, then greedily matched against the source to find the
longest contiguous runs ("extractive fragments"). From those fragments:

- coverage  = share of summary tokens that sit inside a fragment (>= 2 words)
- density   = mean squared fragment length per summary token (long copied
              runs weigh quadratically more than many short overlaps)
- max_fragment = the longest copied run, in words
- jaccard5  = Jaccard similarity of the 5-gram sets (summary vs. sources)

Whitelisted phrases (concept labels, aliases, terminology terms, quoted
definitions) are exempt: they are masked in the summary before matching so a
label like "service layer" never contributes to a copied run, and any
fragment whose text equals a whitelist phrase outright is dropped.

Fenced code blocks and inline code spans are removed before tokenizing on
both sides — code is referenced through `examples`, never paraphrased, so it
is exempt from the copy gates.

Usage::

    from verbatim import verbatim_report, gate
    report = verbatim_report(brief_text, [unit["text"] for unit in cited], whitelist)
    for check_id, message in gate(report, "brief"):
        ...  # map to Finding("<stage>." + check_id, GATE_SEVERITY[check_id], ...)
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from contracts import (
    VERBATIM_COVERAGE_UNGROUNDED,
    VERBATIM_DENSITY_MAX,
    VERBATIM_FRAGMENT_MAX,
    VERBATIM_JACCARD5_MAX,
)

# Texts at or below this many tokens are too short for the "ungrounded"
# coverage warning to mean anything (a 20-word overview rarely shares a bigram).
UNGROUNDED_MIN_TOKENS = 40

# Severity each gate check_id carries when mapped to a Finding.
GATE_SEVERITY = {
    "verbatim.fragment": "error",
    "verbatim.jaccard5": "error",
    "verbatim.density": "error",
    "verbatim.ungrounded": "warning",
}

NGRAM_SIZE = 5
MIN_FRAGMENT_LEN = 2          # single shared words are not fragments
REPORTED_FRAGMENT_MIN_LEN = 5  # fragments listed in the report
REPORTED_FRAGMENTS_MAX = 20

# Placeholders can never be produced by tokenize_words (NUL is not a word char),
# so a masked summary token never matches a source token and a source separator
# never joins two sources into one run.
_MASK = "\x00wl"
_SOURCE_SEP = "\x00sep"

_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
_LINK_TARGET_RE = re.compile(r"\]\([^)]*\)")           # [text](url) -> [text]
_WORD_RE = re.compile(r"[^\W_]+(?:'[^\W_]+)*")         # letters/digits, inner apostrophes


def strip_code(text: str) -> str:
    """Remove fenced code blocks (``` ... ```, unclosed fence runs to the end)
    and inline code spans. Markdown link/image targets are dropped too."""
    parts = (text or "").split("```")
    prose = " ".join(parts[0::2])  # odd-indexed parts are inside fences
    prose = _INLINE_CODE_RE.sub(" ", prose)
    return _LINK_TARGET_RE.sub("]", prose)


def tokenize_words(text: str) -> list[str]:
    """Lowercase word tokens (letters/digits/apostrophes); markdown, punctuation,
    fenced code blocks and inline code removed."""
    prose = strip_code(text).replace("’", "'").replace("‘", "'")
    return _WORD_RE.findall(prose.lower())


def extractive_fragments(
    summary_tokens: list[str], source_tokens: list[str],
) -> list[tuple[int, int, int]]:
    """Newsroom greedy longest-fragment matching.

    For each summary position, find the longest run of tokens that appears
    contiguously anywhere in the source, record it, and skip past it. Returns
    (summary_start, source_start, length) for fragments of length >= 2, in
    summary order. Ties keep the earliest source occurrence.
    """
    positions: dict[str, list[int]] = {}
    for j, tok in enumerate(source_tokens):
        positions.setdefault(tok, []).append(j)

    fragments: list[tuple[int, int, int]] = []
    n, m = len(summary_tokens), len(source_tokens)
    i = 0
    while i < n:
        best_len, best_start = 0, -1
        for j in positions.get(summary_tokens[i], ()):
            k = 0
            while i + k < n and j + k < m and summary_tokens[i + k] == source_tokens[j + k]:
                k += 1
            if k > best_len:
                best_len, best_start = k, j
        if best_len >= MIN_FRAGMENT_LEN:
            fragments.append((i, best_start, best_len))
        i += max(best_len, 1)
    return fragments


def _whitelist_phrases(whitelist: Iterable[str]) -> list[tuple[str, ...]]:
    """Tokenized whitelist phrases, longest first so nested phrases mask once."""
    phrases = {tuple(tokenize_words(p)) for p in whitelist}
    phrases.discard(())
    return sorted(phrases, key=len, reverse=True)


def _mask_whitelist(tokens: list[str], phrases: list[tuple[str, ...]]) -> list[str]:
    """Replace every occurrence of a whitelist phrase with placeholder tokens
    (one per word, so indices stay aligned with the original token list)."""
    masked = list(tokens)
    n = len(tokens)
    for phrase in phrases:
        k = len(phrase)
        i = 0
        while i + k <= n:
            if tuple(tokens[i:i + k]) == phrase and all(t != _MASK for t in masked[i:i + k]):
                masked[i:i + k] = [_MASK] * k
                i += k
            else:
                i += 1
    return masked


def _ngrams(tokens: list[str], n: int = NGRAM_SIZE) -> set[tuple[str, ...]]:
    return {tuple(tokens[i:i + n]) for i in range(len(tokens) - n + 1)}


def verbatim_report(
    text: str, sources: list[str], whitelist: Iterable[str] = frozenset(),
) -> dict:
    """Measure how much of `text` is copied from `sources`.

    Returns a dict with: coverage, density, max_fragment, fragments
    ([{"length", "text"}] for non-whitelisted fragments >= 5 words, longest
    first, at most 20), jaccard5, summary_tokens, source_tokens.
    """
    summary = tokenize_words(text)
    phrases = _whitelist_phrases(whitelist)
    masked = _mask_whitelist(summary, phrases)

    source_lists = [tokenize_words(s) for s in sources]
    source_tokens: list[str] = []
    for toks in source_lists:
        if source_tokens:
            source_tokens.append(_SOURCE_SEP)
        source_tokens.extend(toks)

    phrase_set = set(phrases)
    fragments = [
        f for f in extractive_fragments(masked, source_tokens)
        if tuple(summary[f[0]:f[0] + f[2]]) not in phrase_set
    ]

    n = len(summary)
    covered = sum(length for _, _, length in fragments)
    squared = sum(length * length for _, _, length in fragments)
    max_fragment = max((length for _, _, length in fragments), default=0)

    summary_grams = _ngrams(masked)
    source_grams: set[tuple[str, ...]] = set()
    for toks in source_lists:
        source_grams |= _ngrams(toks)
    union = summary_grams | source_grams
    jaccard5 = len(summary_grams & source_grams) / len(union) if union else 0.0

    reported = sorted(
        (f for f in fragments if f[2] >= REPORTED_FRAGMENT_MIN_LEN),
        key=lambda f: (-f[2], f[0]),
    )[:REPORTED_FRAGMENTS_MAX]

    return {
        "coverage": covered / n if n else 0.0,
        "density": squared / n if n else 0.0,
        "max_fragment": max_fragment,
        "fragments": [
            {"length": length, "text": " ".join(summary[start:start + length])}
            for start, _, length in reported
        ],
        "jaccard5": jaccard5,
        "summary_tokens": n,
        "source_tokens": sum(len(toks) for toks in source_lists),
    }


def gate(report: dict, kind: str) -> list[tuple[str, str]]:
    """Compare a report with the thresholds for `kind` ("brief" | "page").

    Returns (check_id, message) pairs for every violation; severities are in
    GATE_SEVERITY. Raises ValueError for an unknown kind.
    """
    if kind not in VERBATIM_JACCARD5_MAX or kind not in VERBATIM_DENSITY_MAX:
        raise ValueError(f"unknown verbatim kind {kind!r}; expected one of {sorted(VERBATIM_JACCARD5_MAX)}")

    violations: list[tuple[str, str]] = []
    max_fragment = report.get("max_fragment", 0)
    if max_fragment >= VERBATIM_FRAGMENT_MAX:
        fragments = report.get("fragments") or []
        worst = f': "{fragments[0]["text"]}"' if fragments else ""
        violations.append((
            "verbatim.fragment",
            f"copied run of {max_fragment} words from a cited unit "
            f"(limit < {VERBATIM_FRAGMENT_MAX}){worst}",
        ))
    jaccard5 = report.get("jaccard5", 0.0)
    if jaccard5 > VERBATIM_JACCARD5_MAX[kind]:
        violations.append((
            "verbatim.jaccard5",
            f"5-gram Jaccard {jaccard5:.3f} > {VERBATIM_JACCARD5_MAX[kind]} ({kind}) — too much text shared with the sources",
        ))
    density = report.get("density", 0.0)
    if density > VERBATIM_DENSITY_MAX[kind]:
        violations.append((
            "verbatim.density",
            f"extractive density {density:.2f} > {VERBATIM_DENSITY_MAX[kind]} ({kind}) — copied runs dominate the text",
        ))
    coverage = report.get("coverage", 0.0)
    if coverage < VERBATIM_COVERAGE_UNGROUNDED and report.get("summary_tokens", 0) > UNGROUNDED_MIN_TOKENS:
        violations.append((
            "verbatim.ungrounded",
            f"extractive coverage {coverage:.3f} < {VERBATIM_COVERAGE_UNGROUNDED} — the text shares almost no "
            f"wording with its cited units; check that the citations support it",
        ))
    return violations
