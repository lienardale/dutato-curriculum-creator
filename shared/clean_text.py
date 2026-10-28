"""
Extraction cleanup for the lesson pipeline (Stage 2, ``qualify.py prepare``).

PURE DATA — no AI calls. Turns raw extractor output (PDF text with page
furniture and hard-wrapped lines, web text with one line per paragraph) into
paragraph-structured text the unit segmenter can work on, while keeping every
cleaned character mappable back to its raw offset so units can carry a
``raw_span`` into the untouched section text.

What it does, per line of a section:

* removes page furniture — bare page numbers, lines recurring across the pages
  of one source (running headers / footers, ``find_recurring_lines``),
  ``From the Library of …`` watermarks, ``N | Chapter …`` footers (also the
  three-line ``N`` / ``|`` / ``Chapter N: …`` form), standalone ``CHAPTER N``
  lines, the section title echoed as the first heading, and the phrase
  patterns listed in ``contracts.PAGE_FURNITURE_PATTERNS``;
* maps ligature glyphs (ﬁ ﬂ ﬀ ﬃ ﬄ ﬅ ﬆ) to ASCII and strips stray footnote
  markers glued to sentence punctuation (``word.3`` → ``word.``);
* for hard-wrapped sources (pdf, video transcripts) de-hyphenates line-end
  breaks (``sepa-\\nrate`` → ``separate``, ``well-\\nknown`` stays hyphenated)
  and rejoins wrapped lines into paragraphs; a page break that falls inside a
  sentence (blank run between a line without terminal punctuation and a line
  starting lowercase) is bridged too;
* emits blank lines between paragraphs and between typed blocks (code, list,
  table, caption) so every source type reaches the segmenter in the same
  shape; fenced code is copied verbatim, indented / code-looking lines, list
  items and table rows are never joined. In pre-paragraphed sources (web,
  office, notion) a lowercase line that follows a line without terminal
  punctuation is a sentence the extractor split and is rejoined.

``source_type == "code"`` only gets the ligature mapping.

Usage::

    from clean_text import clean_text, clean_text_with_map, find_recurring_lines

    recurring = find_recurring_lines([s["content"] for s in sections])
    cleaned, report = clean_text(raw, recurring=recurring, source_type="pdf")
    cleaned, report, to_raw = clean_text_with_map(raw, recurring=recurring,
                                                   section_title="Service Layer")
    raw_start = to_raw(cleaned_offset)

``report`` is ``{"removed_lines", "dehyphenated", "rejoined", "ligatures"}``
(counts) plus ``"anchors"``: ``[(cleaned_start, raw_start), …]`` for every
contiguous run of the cleaned text; ``clean_text_with_map`` additionally
returns ``report["events"]`` — the raw offsets of each cleaning event — so a
caller can attribute cleaning statistics to sub-spans.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from collections import Counter
from typing import Callable

from contracts import PAGE_FURNITURE_PATTERNS, normalize_ws

# ---------------------------------------------------------------------------
# Lexical gates
# ---------------------------------------------------------------------------

LIGATURES = {"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st"}
_LIGATURE_RE = re.compile("[" + "".join(LIGATURES) + "]")

# Source types whose text arrives hard-wrapped (one visual line per text line).
HARD_WRAPPED_TYPES = frozenset({"pdf", "video", "video_playlist"})

# A line is "furniture-looking" only when it is short and not a sentence.
RECURRING_MAX_WORDS = 8
REJOIN_LONG_LINE_CHARS = 60

_BARE_NUMBER_RE = re.compile(r"^\d{1,4}$")
_BARE_PIPE_RE = re.compile(r"^\|$")
_CHAPTER_PART_RE = re.compile(r"^(chapter|part)\s+([0-9]+|[ivxlc]+)\b", re.IGNORECASE)
_STANDALONE_CHAPTER_RE = re.compile(r"^chapter\s+\d+[.:]?$", re.IGNORECASE)
_CHAPTER_FOOTER_RE = re.compile(
    r"^(\d{1,4}\s*\|\s*chapter\b.*|chapter\b.*\|\s*\d{1,4})$", re.IGNORECASE
)
_WATERMARK_RE = re.compile(r"From the Library of", re.IGNORECASE)
_NO_ALNUM_RE = re.compile(r"^[^\w]+$")
_TITLE_PREFIX_RE = re.compile(r"^(chapter|part)\s+([0-9]+|[ivxlc]+)\s*[.:\-–—]?\s*", re.IGNORECASE)
_TITLE_PAREN_SUFFIX_RE = re.compile(r"\s*\([^()]*\)\s*$")

_TERMINAL_RE = re.compile(r"[.!?:][\"”’'\)\]]*$")
_LOWER_START_RE = re.compile(r"^[(\"“‘'\[]*[a-z]")
_LIST_ITEM_RE = re.compile(r"^(?:[-*•+–]|\d{1,3}[.)])\s+\S")
_BARE_MARKER_RE = re.compile(r"^(?:[•–-]|\d{1,3}[.)])$")
_TABLE_ROW_RE = re.compile(r"^\|.*\|$")
_CAPTION_RE = re.compile(r"^(Figure|Fig\.|Table)\s+\d")
_HEADING_RE = re.compile(r"^#{1,6}\s")
_FENCE_RE = re.compile(r"^```")
_INDENT_CODE_RE = re.compile(r"^(?: {4,}|\t)")
_CODE_TOKEN_RE = re.compile(
    r"[{}]|\(\);|=>|->|==|!=|</|/>|;$|^\s*(?:def |class |import |from \S+ import|return\b|"
    r"public |private |protected |void |#include|package |var |let |const |function |"
    r"if \(|for \(|while \(|@\w+\()"
)
# Code-looking per the recurring-lines contract: never treated as furniture.
# A straight double-quoted string literal marks concatenated SQL / string code
# such as ``"SELECT " + COLUMNS +`` (all-caps, no brackets) that recurs across
# PEAA-style code samples; typeset running headers use curly quotes.
_CODE_CHARS_RE = re.compile(r"[;{}()=<>]")
_STRING_LITERAL_RE = re.compile(r'"[^"\n]*"')
_WORD_RE = re.compile(r"[A-Za-z]+(?:-[A-Za-z]+)*")
_HYPHEN_BREAK_RE = re.compile(r"([A-Za-z]+)-$")
_FIRST_WORD_RE = re.compile(r"^[A-Za-z]+")
# Footnote marker glued to sentence punctuation: "word.3" / "wrong.”12"
_FOOTNOTE_RE = re.compile(r"(?<=[A-Za-z)\]][.!?,;:])([\"”’']?)(\d{1,2})(?=\s|$)")

# Compound first halves that keep their hyphen when the second half is a
# plain lowercase word and neither form is attested elsewhere in the text.
SAFE_COMPOUND_PREFIXES = frozenset({
    "self", "well", "non", "anti", "cross", "multi", "long", "high", "low", "real",
    "open", "half", "full", "semi", "ultra", "quasi", "pseudo", "so", "all", "ever", "e",
})


def _is_code_like(line: str) -> bool:
    """Lines that must never be joined, de-hyphenated or treated as furniture."""
    return bool(_INDENT_CODE_RE.match(line) or _CODE_TOKEN_RE.search(line.strip()))


def _is_all_caps(line: str) -> bool:
    letters = [c for c in line if c.isalpha()]
    return bool(letters) and all(c.isupper() for c in letters)


def _is_title_case(line: str) -> bool:
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z'’-]*", line)]
    if not words:
        return False
    capitalized = sum(1 for w in words if w[0].isupper())
    # Allow small connecting words in lowercase ("Where to Use It").
    return capitalized >= max(1, len(words) - 2) and words[0][0].isupper()


def _recurring_candidate(line: str) -> bool:
    if not line or len(line.split()) > RECURRING_MAX_WORDS:
        return False
    if line[-1] in ".!?:;":
        return False
    if _CODE_CHARS_RE.search(line) or _STRING_LITERAL_RE.search(line) or _INDENT_CODE_RE.match(line):
        return False
    return True


def find_recurring_lines(texts: list[str], min_occurrences: int = 3) -> set[str]:
    """Short lines recurring across the texts of one source that look like page furniture.

    Bare numbers, ALL CAPS lines, lines containing `` | ``, ``Chapter N`` /
    ``Part N`` lines and ``From the Library of`` watermarks qualify on
    recurrence alone. Title Case lines (which are also how real sub-headings
    look — ``How It Works`` recurs in every PEAA pattern) qualify only when at
    least half of their occurrences sit next to a bare page number or a lone
    ``|`` line, i.e. when they behave like running headers. Code-looking lines
    (brackets, ``;``, ``=``, indentation, or a double-quoted string literal)
    are never returned.
    """
    counts: Counter[str] = Counter()
    adjacent: Counter[str] = Counter()
    for text in texts:
        lines = [normalize_ws(_LIGATURE_RE.sub(lambda m: LIGATURES[m.group(0)], ln)) for ln in text.split("\n")]
        non_blank = [(i, ln) for i, ln in enumerate(lines) if ln]
        for pos, (_, line) in enumerate(non_blank):
            if not _recurring_candidate(line):
                continue
            counts[line] += 1
            neighbours = []
            if pos > 0:
                neighbours.append(non_blank[pos - 1][1])
            if pos + 1 < len(non_blank):
                neighbours.append(non_blank[pos + 1][1])
            if any(_BARE_NUMBER_RE.match(n) or _BARE_PIPE_RE.match(n) for n in neighbours):
                adjacent[line] += 1

    result: set[str] = set()
    for line, n in counts.items():
        if n < min_occurrences:
            continue
        if (
            _BARE_NUMBER_RE.match(line)
            or " | " in line
            or _CHAPTER_PART_RE.match(line)
            or _WATERMARK_RE.search(line)
            or _is_all_caps(line)
        ):
            result.add(line)
        elif _is_title_case(line) and adjacent[line] * 2 >= n:
            result.add(line)
    return result


# ---------------------------------------------------------------------------
# Offset-preserving text builder
# ---------------------------------------------------------------------------


class _Builder:
    """Accumulates cleaned text as segments that remember their raw offsets.

    A *mapped* segment is a 1:1 copy of raw characters starting at
    ``raw_start``; a *fixed* segment (inserted separators, ligature
    expansions) maps every character to the single raw offset it stands for.
    """

    def __init__(self) -> None:
        self.parts: list[str] = []
        self.starts: list[int] = []      # cleaned start of each segment
        self.raw_starts: list[int] = []
        self.lengths: list[int] = []
        self.mapped: list[bool] = []
        self.length = 0

    def add(self, s: str, raw_start: int, mapped: bool = True) -> None:
        if not s:
            return
        self.parts.append(s)
        self.starts.append(self.length)
        self.raw_starts.append(raw_start)
        self.lengths.append(len(s))
        self.mapped.append(mapped)
        self.length += len(s)

    def text(self) -> str:
        return "".join(self.parts)

    def anchors(self) -> list[tuple[int, int]]:
        return [(c, r) for c, r, m in zip(self.starts, self.raw_starts, self.mapped) if m]

    def mapper(self) -> Callable[[int], int]:
        starts, raw_starts, lengths, mapped = self.starts, self.raw_starts, self.lengths, self.mapped
        total = self.length

        def to_raw(cleaned_offset: int) -> int:
            if not starts:
                return 0
            if cleaned_offset >= total:
                i = len(starts) - 1
                return raw_starts[i] + (lengths[i] if mapped[i] else 1)
            i = bisect_right(starts, max(cleaned_offset, 0)) - 1
            return raw_starts[i] + (cleaned_offset - starts[i] if mapped[i] else 0)

        return to_raw


# ---------------------------------------------------------------------------
# Line classification
# ---------------------------------------------------------------------------

_KIND_BLANK, _KIND_FENCE, _KIND_CODE, _KIND_LIST, _KIND_TABLE = "blank", "fence", "code", "list", "table"
_KIND_CAPTION, _KIND_HEADING, _KIND_PROSE = "caption", "heading", "prose"


class _Line:
    __slots__ = ("raw_start", "text", "kind", "prefix")

    def __init__(self, raw_start: int, text: str, kind: str) -> None:
        self.raw_start = raw_start
        self.text = text            # rstripped raw text (ligatures NOT yet mapped)
        self.kind = kind
        self.prefix: _Line | None = None  # bare list marker folded into this item

    def stripped(self) -> str:
        return self.text.strip()


def _is_furniture(stripped: str, recurring: set[str] | frozenset[str], hard_wrapped: bool) -> bool:
    mapped = normalize_ws(_LIGATURE_RE.sub(lambda m: LIGATURES[m.group(0)], stripped))
    if mapped in recurring:
        return True
    if _WATERMARK_RE.search(mapped) or _STANDALONE_CHAPTER_RE.match(mapped) or _CHAPTER_FOOTER_RE.match(mapped):
        return True
    if any(p.search(mapped) for p in PAGE_FURNITURE_PATTERNS):
        return True
    if hard_wrapped and (_BARE_PIPE_RE.match(mapped) or _NO_ALNUM_RE.match(mapped)):
        return True
    return False


def _classify(stripped: str, raw_line: str) -> str:
    if _HEADING_RE.match(stripped):
        return _KIND_HEADING
    if _is_code_like(raw_line):
        return _KIND_CODE
    if _TABLE_ROW_RE.match(stripped):
        return _KIND_TABLE
    if _LIST_ITEM_RE.match(stripped):
        return _KIND_LIST
    if _CAPTION_RE.match(stripped):
        return _KIND_CAPTION
    return _KIND_PROSE


def _title_key(title: str) -> str:
    return normalize_ws(_TITLE_PAREN_SUFFIX_RE.sub("", title)).lower()


def _echo_key(text: str) -> str:
    return normalize_ws(_TITLE_PREFIX_RE.sub("", _LIGATURE_RE.sub(lambda m: LIGATURES[m.group(0)], text))).lower()


def _drop_title_echoes(lines: list[_Line], section_title: str, events: dict[str, list[int]]) -> None:
    """Remove the section title re-stated as the first heading line(s), repeatedly.

    PDF extractors emit the chapter heading (possibly split over two or three
    lines and prefixed with ``Chapter N.``) at the top of the section text,
    sometimes several times (running header, big title, repeated title).
    """
    key = _title_key(section_title)
    if not key:
        return
    for _ in range(4):
        content = [ln for ln in lines if ln.kind not in (_KIND_BLANK,) and ln.kind != "removed"]
        if not content:
            return
        head = content[:3]
        matched = 0
        for k in range(1, len(head) + 1):
            if head[k - 1].kind == _KIND_FENCE:
                break
            if _echo_key(" ".join(ln.stripped() for ln in head[:k])) == key:
                matched = k
                break
        if not matched:
            return
        for ln in head[:matched]:
            ln.kind = "removed"
            events["removed_lines"].append(ln.raw_start)


def _parse_lines(
    text: str,
    recurring: set[str] | frozenset[str],
    hard_wrapped: bool,
    events: dict[str, list[int]],
) -> list[_Line]:
    lines: list[_Line] = []
    in_fence = False
    offset = 0
    for raw in text.split("\n"):
        start = offset
        offset += len(raw) + 1
        rstripped = raw.rstrip()
        stripped = rstripped.strip()
        if in_fence:
            lines.append(_Line(start, rstripped, _KIND_FENCE))
            if _FENCE_RE.match(stripped):
                in_fence = False
            continue
        if _FENCE_RE.match(stripped):
            in_fence = True
            lines.append(_Line(start, rstripped, _KIND_FENCE))
            continue
        if not stripped:
            lines.append(_Line(start, "", _KIND_BLANK))
            continue
        kind = _classify(stripped, rstripped)
        if kind == _KIND_PROSE and _BARE_MARKER_RE.match(stripped):
            lines.append(_Line(start, rstripped, kind))  # fold candidate; furniture is decided after folding
            continue
        if kind != _KIND_CODE and _is_furniture(stripped, recurring, hard_wrapped):
            events["removed_lines"].append(start)
            lines.append(_Line(start, rstripped, "removed"))
            continue
        lines.append(_Line(start, rstripped, kind))

    # Fold a bare list marker line ("•", "1.") into the following line; a marker
    # that has nothing to fold into is page furniture in hard-wrapped sources.
    for i, ln in enumerate(lines):
        if ln.kind != _KIND_PROSE or not _BARE_MARKER_RE.match(ln.stripped()):
            continue
        for nxt in lines[i + 1:]:
            if nxt.kind == _KIND_BLANK or nxt.kind == "removed":
                continue
            if nxt.kind in (_KIND_PROSE, _KIND_LIST, _KIND_CAPTION) and nxt.prefix is None:
                nxt.prefix = ln
                nxt.kind = _KIND_LIST
                ln.kind = "removed"
            break
        if ln.kind == _KIND_PROSE and hard_wrapped and _NO_ALNUM_RE.match(ln.stripped()):
            ln.kind = "removed"
            events["removed_lines"].append(ln.raw_start)
    return lines


# ---------------------------------------------------------------------------
# Join decisions
# ---------------------------------------------------------------------------

_JOIN_SPACE, _JOIN_DEHYPHEN_DROP, _JOIN_DEHYPHEN_KEEP, _SEP_LINE, _SEP_PARA = range(5)


def _is_terminal(stripped: str) -> bool:
    return bool(_TERMINAL_RE.search(stripped))


def _dehyphenation(prev: str, cur: str, words: set[str]) -> int | None:
    """Return the join kind for a line ending in a hyphen, or None when not a break."""
    m = _HYPHEN_BREAK_RE.search(prev)
    n = _FIRST_WORD_RE.match(cur)
    if not m or not n:
        return None
    part1, part2 = m.group(1), n.group(0)
    if part2[0].isupper():
        return _JOIN_DEHYPHEN_KEEP
    joined, hyphenated = (part1 + part2).lower(), (part1 + "-" + part2).lower()
    if joined in words:
        return _JOIN_DEHYPHEN_DROP
    if hyphenated in words:
        return _JOIN_DEHYPHEN_KEEP
    if part1.lower() in SAFE_COMPOUND_PREFIXES:
        return _JOIN_DEHYPHEN_KEEP
    return _JOIN_DEHYPHEN_DROP


def _decide(prev: _Line, cur: _Line, blank_between: bool, hard_wrapped: bool, words: set[str]) -> int:
    pk, ck = prev.kind, cur.kind
    ps, cs = prev.stripped(), cur.stripped()
    if pk == _KIND_FENCE or ck == _KIND_FENCE:
        return _SEP_LINE if pk == ck else _SEP_PARA
    if blank_between:
        if hard_wrapped and pk == _KIND_PROSE and ck == _KIND_PROSE and not _is_terminal(ps) and _LOWER_START_RE.match(cs):
            return _JOIN_SPACE  # page break inside a sentence
        return _SEP_PARA
    if pk == ck and pk in (_KIND_CODE, _KIND_TABLE, _KIND_LIST):
        return _SEP_LINE
    if pk == _KIND_HEADING or ck == _KIND_HEADING or ck in (_KIND_CODE, _KIND_TABLE, _KIND_LIST, _KIND_CAPTION):
        return _SEP_PARA
    if not hard_wrapped:
        # Pre-paragraphed source: a lowercase line right after a line without
        # terminal punctuation is a sentence the extractor split (web list items
        # often arrive as "- Work on what matters," / "particularly as ...").
        if pk in (_KIND_PROSE, _KIND_LIST) and ck == _KIND_PROSE and not _is_terminal(ps) and _LOWER_START_RE.match(cs):
            return _JOIN_SPACE
        return _SEP_PARA
    # Hard-wrapped source: prose/list/caption line followed by a prose line.
    if pk in (_KIND_PROSE, _KIND_LIST, _KIND_CAPTION) and ck == _KIND_PROSE:
        dehyphen = _dehyphenation(ps, cs, words)
        if dehyphen is not None:
            return dehyphen
        if _is_terminal(ps):
            return _SEP_PARA
        if _LOWER_START_RE.match(cs):
            return _JOIN_SPACE
        if pk == _KIND_PROSE and len(ps) > REJOIN_LONG_LINE_CHARS:
            return _JOIN_SPACE
    return _SEP_PARA


# ---------------------------------------------------------------------------
# Emission
# ---------------------------------------------------------------------------


def _add_with_ligatures(b: _Builder, text: str, base: int, events: dict[str, list[int]]) -> None:
    """Append ``text`` (raw offset ``base``), expanding ligature glyphs to ASCII."""
    pos = 0
    for m in _LIGATURE_RE.finditer(text):
        b.add(text[pos:m.start()], base + pos)
        b.add(LIGATURES[m.group(0)], base + m.start(), mapped=False)
        events["ligatures"].append(base + m.start())
        pos = m.end()
    b.add(text[pos:], base + pos)


def _emit_line(b: _Builder, ln: _Line, *, drop_trailing_hyphen: bool, events: dict[str, list[int]]) -> None:
    """Append one line's content.

    Fenced lines are copied verbatim; code-looking lines keep their indentation
    and only get the ligature mapping; everything else is stripped, ligature-
    mapped and loses footnote markers glued to sentence punctuation.
    """
    if ln.prefix is not None:
        _emit_line(b, ln.prefix, drop_trailing_hyphen=False, events=events)
        b.add(" ", ln.prefix.raw_start + len(ln.prefix.text), mapped=False)
    if ln.kind == _KIND_FENCE:
        b.add(ln.text, ln.raw_start)
        return
    if ln.kind == _KIND_CODE:
        _add_with_ligatures(b, ln.text, ln.raw_start, events)
        return
    text = ln.text.strip()
    base = ln.raw_start + len(ln.text) - len(ln.text.lstrip())
    if drop_trailing_hyphen and text.endswith("-"):
        text = text[:-1]
    pos = 0
    for m in _FOOTNOTE_RE.finditer(text):
        digits_start = m.start(2)
        _add_with_ligatures(b, text[pos:digits_start], base + pos, events)
        events["footnotes"].append(base + digits_start)
        pos = m.end()
    _add_with_ligatures(b, text[pos:], base + pos, events)


def _new_events() -> dict[str, list[int]]:
    return {"removed_lines": [], "dehyphenated": [], "rejoined": [], "ligatures": [], "footnotes": []}


def clean_text_with_map(
    text: str,
    *,
    recurring: set[str] | frozenset[str] = frozenset(),
    source_type: str = "pdf",
    section_title: str | None = None,
) -> tuple[str, dict, Callable[[int], int]]:
    """Clean ``text`` and return ``(cleaned, report, to_raw)``.

    ``to_raw(cleaned_offset)`` gives the raw offset of the character at
    ``cleaned_offset`` (for an exclusive end offset use ``to_raw(end - 1) + 1``).
    ``report`` carries the counts described in the module docstring plus
    ``"anchors"`` and ``"events"`` (raw offsets per event kind).
    """
    events = _new_events()
    b = _Builder()
    if source_type == "code":
        pos = 0
        for m in _LIGATURE_RE.finditer(text):
            b.add(text[pos:m.start()], pos)
            b.add(LIGATURES[m.group(0)], m.start(), mapped=False)
            events["ligatures"].append(m.start())
            pos = m.end()
        b.add(text[pos:], pos)
        return b.text(), _report(events, b), b.mapper()

    hard_wrapped = source_type in HARD_WRAPPED_TYPES
    lines = _parse_lines(text, recurring, hard_wrapped, events)
    if section_title:
        _drop_title_echoes(lines, section_title, events)
    words: set[str] = set()
    if hard_wrapped:
        words = set(_WORD_RE.findall(_LIGATURE_RE.sub(lambda m: LIGATURES[m.group(0)], text).lower()))

    prev: _Line | None = None
    blank_between = False
    for ln in lines:
        if ln.kind == "removed":
            continue
        if ln.kind == _KIND_BLANK:
            blank_between = True
            continue
        if prev is None:
            prev = ln
            blank_between = False
            continue
        decision = _decide(prev, ln, blank_between, hard_wrapped, words)
        if decision in (_JOIN_SPACE, _JOIN_DEHYPHEN_DROP, _JOIN_DEHYPHEN_KEEP) and prev.kind in (_KIND_LIST, _KIND_CAPTION):
            ln.kind = prev.kind  # a joined continuation is still part of the list item / caption
        prev_end = prev.raw_start + len(prev.text)
        _emit_line(b, prev, drop_trailing_hyphen=decision == _JOIN_DEHYPHEN_DROP, events=events)
        if decision == _JOIN_SPACE:
            b.add(" ", prev_end, mapped=False)
            events["rejoined"].append(prev_end)
        elif decision in (_JOIN_DEHYPHEN_DROP, _JOIN_DEHYPHEN_KEEP):
            events["dehyphenated"].append(prev_end - 1)
        elif decision == _SEP_LINE:
            b.add("\n", prev_end, mapped=False)
        else:
            b.add("\n\n", prev_end, mapped=False)
        prev = ln
        blank_between = False
    if prev is not None:
        _emit_line(b, prev, drop_trailing_hyphen=False, events=events)
    return b.text(), _report(events, b), b.mapper()


def _report(events: dict[str, list[int]], b: _Builder) -> dict:
    return {
        "removed_lines": len(events["removed_lines"]),
        "dehyphenated": len(events["dehyphenated"]),
        "rejoined": len(events["rejoined"]),
        "ligatures": len(events["ligatures"]),
        "footnotes": len(events["footnotes"]),
        "anchors": b.anchors(),
        "events": events,
    }


def clean_text(
    text: str,
    *,
    recurring: set[str] | frozenset[str] = frozenset(),
    source_type: str = "pdf",
    section_title: str | None = None,
) -> tuple[str, dict]:
    """Clean ``text``; return ``(cleaned, report)`` — see the module docstring."""
    cleaned, report, _ = clean_text_with_map(
        text, recurring=recurring, source_type=source_type, section_title=section_title
    )
    report.pop("events")
    return cleaned, report
