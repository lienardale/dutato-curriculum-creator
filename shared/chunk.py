"""
Semantic chunking: split extracted text into chunks aligned with topic boundaries.

Each chunk is 500-1500 tokens, split at paragraph boundaries, never mid-sentence
or mid-code-block.
"""

import json
import re
import sys
from pathlib import Path

import tiktoken


# Use cl100k_base encoding (compatible with Claude's tokenizer)
_encoder = tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    """Count tokens using tiktoken."""
    return len(_encoder.encode(text))


def is_code_block(text: str) -> bool:
    """Heuristic: detect if a paragraph looks like a code block."""
    lines = text.strip().split("\n")
    if len(lines) < 2:
        return False
    # Check for common code indicators
    code_indicators = 0
    for line in lines:
        stripped = line.strip()
        if any(stripped.startswith(s) for s in [
            "def ", "class ", "import ", "from ", "return ",
            "if ", "for ", "while ", "try:", "except",
            "public ", "private ", "void ", "int ", "String ",
            "{", "}", "//", "/*", "#include", "package ",
        ]):
            code_indicators += 1
        elif re.match(r"^\s{2,}", line) and stripped:
            code_indicators += 0.5
    return code_indicators / max(len(lines), 1) > 0.3


def split_into_paragraphs(text: str) -> list[str]:
    """Split text into paragraphs, keeping code blocks together."""
    # Split on double newlines
    raw_paragraphs = re.split(r"\n\s*\n", text)

    paragraphs = []
    code_buffer = []
    in_code = False

    for para in raw_paragraphs:
        para = para.strip()
        if not para:
            continue

        if is_code_block(para):
            code_buffer.append(para)
            in_code = True
        else:
            if in_code and code_buffer:
                # Flush code block as single paragraph
                paragraphs.append("\n\n".join(code_buffer))
                code_buffer = []
                in_code = False
            paragraphs.append(para)

    if code_buffer:
        paragraphs.append("\n\n".join(code_buffer))

    return paragraphs


def chunk_text(
    text: str,
    max_tokens: int = 1500,
    min_tokens: int = 200,
) -> list[dict]:
    """
    Split text into semantic chunks.

    Returns list of {content: str, token_count: int, has_code: bool}
    """
    total_tokens = count_tokens(text)
    if total_tokens <= max_tokens:
        return [{
            "content": text.strip(),
            "token_count": total_tokens,
            "has_code": is_code_block(text),
        }]

    paragraphs = split_into_paragraphs(text)
    chunks = []
    current_parts: list[str] = []
    current_tokens = 0
    current_has_code = False

    for para in paragraphs:
        para_tokens = count_tokens(para)
        para_is_code = is_code_block(para)

        # If single paragraph exceeds max, add it as its own chunk
        if para_tokens > max_tokens:
            # Flush current buffer first
            if current_parts:
                chunks.append({
                    "content": "\n\n".join(current_parts),
                    "token_count": current_tokens,
                    "has_code": current_has_code,
                })
                current_parts = []
                current_tokens = 0
                current_has_code = False

            # Add oversized paragraph as-is (don't split mid-code-block)
            chunks.append({
                "content": para,
                "token_count": para_tokens,
                "has_code": para_is_code,
            })
            continue

        # If adding this paragraph exceeds max, start new chunk
        if current_tokens + para_tokens > max_tokens and current_tokens >= min_tokens:
            chunks.append({
                "content": "\n\n".join(current_parts),
                "token_count": current_tokens,
                "has_code": current_has_code,
            })
            current_parts = []
            current_tokens = 0
            current_has_code = False

        current_parts.append(para)
        current_tokens += para_tokens
        if para_is_code:
            current_has_code = True

    # Flush remaining
    if current_parts:
        # If too small, merge with previous chunk
        if current_tokens < min_tokens and chunks:
            prev = chunks[-1]
            prev["content"] += "\n\n" + "\n\n".join(current_parts)
            prev["token_count"] += current_tokens
            prev["has_code"] = prev["has_code"] or current_has_code
        else:
            chunks.append({
                "content": "\n\n".join(current_parts),
                "token_count": current_tokens,
                "has_code": current_has_code,
            })

    return chunks


def get_text_for_page_range(
    pages: list[dict], page_start: int, page_end: int
) -> str:
    """Get concatenated text for a page range."""
    texts = []
    for p in pages:
        if page_start <= p["page"] <= page_end:
            texts.append(p["text"])
    return "\n\n".join(texts)


def _find_heading_position(text: str, title: str) -> int | None:
    """
    Find where a topic title appears as a heading in text.

    Looks for the title on its own line (possibly with leading/trailing whitespace
    or a page number prefix). Returns the character offset of the start of the
    heading line, or None if not found.
    """
    # Normalize the title for matching
    title_clean = title.strip()

    # Try exact match on its own line (most common case)
    # Allow optional leading page number and whitespace
    pattern = re.compile(
        r"^[\s\d]{0,6}" + re.escape(title_clean) + r"\s*$",
        re.MULTILINE,
    )
    m = pattern.search(text)
    if m:
        return m.start()

    # Try case-insensitive match
    pattern_ci = re.compile(
        r"^[\s\d]{0,6}" + re.escape(title_clean) + r"\s*$",
        re.MULTILINE | re.IGNORECASE,
    )
    m = pattern_ci.search(text)
    if m:
        return m.start()

    # Try without leading articles or chapter markers (e.g., "Chapter 1:" prefix removed)
    # and also try matching just the last part after ">"
    for variant in [
        title_clean.split(":")[-1].strip(),
        title_clean.split(">")[-1].strip(),
    ]:
        if variant and variant != title_clean and len(variant) > 3:
            pattern_v = re.compile(
                r"^[\s\d]{0,6}" + re.escape(variant) + r"\s*$",
                re.MULTILINE,
            )
            m = pattern_v.search(text)
            if m:
                return m.start()

    return None


def _split_text_by_headings(
    full_text: str,
    topics: list[dict],
) -> dict[str, str]:
    """
    Split shared page text among sibling topics using heading detection.

    Returns {topic_title: text_segment} for each topic.
    Topics whose heading is not found get empty string.
    """
    # Find heading positions for all topics
    positions: list[tuple[int, str]] = []
    unmatched: list[str] = []

    for topic in topics:
        title = topic["title"]
        pos = _find_heading_position(full_text, title)
        if pos is not None:
            positions.append((pos, title))
        else:
            unmatched.append(title)

    # Sort by position in text
    positions.sort(key=lambda x: x[0])

    # Split text at heading boundaries
    result: dict[str, str] = {}
    for idx, (pos, title) in enumerate(positions):
        if idx + 1 < len(positions):
            next_pos = positions[idx + 1][0]
            result[title] = full_text[pos:next_pos].strip()
        else:
            result[title] = full_text[pos:].strip()

    # Unmatched topics: if only one and it's the first in the sibling list,
    # give it text before the first found heading. Otherwise empty.
    if unmatched and positions:
        first_heading_pos = positions[0][0]
        preamble = full_text[:first_heading_pos].strip()
        if len(unmatched) == 1 and topics[0]["title"] == unmatched[0] and preamble:
            result[unmatched[0]] = preamble
        else:
            for title in unmatched:
                result[title] = ""
    elif not positions:
        # No headings found at all — give full text to first topic, empty to rest
        for i, topic in enumerate(topics):
            result[topic["title"]] = full_text.strip() if i == 0 else ""

    return result


def chunk_by_topics(
    pages: list[dict],
    topic_tree: list[dict],
    parent_path: list[str] | None = None,
) -> list[dict]:
    """
    Chunk text aligned to topic boundaries.

    Returns flat list of chunks with topic_path for each.
    """
    if parent_path is None:
        parent_path = []

    all_chunks = []

    # Pre-compute page-sharing groups among leaf siblings.
    # Group consecutive leaf topics that share the exact same page range.
    leaf_page_groups: dict[tuple[int, int], list[int]] = {}
    for idx, topic in enumerate(topic_tree):
        if not topic.get("children"):
            key = (topic["page_start"], topic["page_end"])
            leaf_page_groups.setdefault(key, []).append(idx)

    # Track which leaf topics have already been handled via group splitting
    handled_indices: set[int] = set()

    # Process page-sharing groups first (only groups with >1 topic)
    for (pg_start, pg_end), indices in leaf_page_groups.items():
        if len(indices) <= 1:
            continue

        # Get shared text
        full_text = get_text_for_page_range(pages, pg_start, pg_end)
        if not full_text.strip():
            handled_indices.update(indices)
            continue

        # Split text among the group using headings
        group_topics = [topic_tree[i] for i in indices]
        split_texts = _split_text_by_headings(full_text, group_topics)

        for i_idx in indices:
            topic = topic_tree[i_idx]
            topic_path = parent_path + [topic["title"]]
            text = split_texts.get(topic["title"], "")

            if text.strip():
                for ci, chunk in enumerate(chunk_text(text)):
                    chunk["topic_path"] = topic_path
                    chunk["chunk_index"] = ci
                    chunk["page_number"] = topic["page_start"]
                    all_chunks.append(chunk)

            handled_indices.add(i_idx)

    # Process remaining topics normally (in original order for deterministic output)
    for idx, topic in enumerate(topic_tree):
        if idx in handled_indices:
            continue

        topic_path = parent_path + [topic["title"]]
        page_start = topic["page_start"]
        page_end = topic["page_end"]

        children = topic.get("children", [])

        if children:
            # Chunk only the "intro" text before first child
            first_child_start = children[0]["page_start"]
            if first_child_start > page_start:
                intro_text = get_text_for_page_range(
                    pages, page_start, first_child_start - 1
                )
                if intro_text.strip():
                    for i, chunk in enumerate(chunk_text(intro_text)):
                        chunk["topic_path"] = topic_path
                        chunk["chunk_index"] = i
                        chunk["page_number"] = page_start
                        all_chunks.append(chunk)

            # Recurse into children
            child_chunks = chunk_by_topics(pages, children, topic_path)
            all_chunks.extend(child_chunks)
        else:
            # Leaf topic: chunk all text in page range
            text = get_text_for_page_range(pages, page_start, page_end)
            if text.strip():
                for i, chunk in enumerate(chunk_text(text)):
                    chunk["topic_path"] = topic_path
                    chunk["chunk_index"] = i
                    chunk["page_number"] = page_start
                    all_chunks.append(chunk)

    return all_chunks


def _dedup_chunks(chunks: list[dict]) -> list[dict]:
    """
    Post-process: when chunks from different topics have identical content
    (cross-level page sharing), split the shared text using heading detection.
    """
    # Group chunks by content identity
    content_groups: dict[str, list[int]] = {}
    for i, chunk in enumerate(chunks):
        content_groups.setdefault(chunk["content"], []).append(i)

    for content, indices in content_groups.items():
        if len(indices) <= 1:
            continue

        # Build pseudo-topic list from chunk topic_paths (use last element as title)
        topics_for_split = [
            {"title": chunks[i]["topic_path"][-1]} for i in indices
        ]

        split_texts = _split_text_by_headings(content, topics_for_split)

        for i in indices:
            title = chunks[i]["topic_path"][-1]
            new_text = split_texts.get(title, "")
            if new_text and new_text != content:
                chunks[i]["content"] = new_text
                chunks[i]["token_count"] = count_tokens(new_text)
                chunks[i]["has_code"] = is_code_block(new_text)

    # Remove chunks that ended up empty after dedup
    return [c for c in chunks if c["content"].strip()]


def main():
    if len(sys.argv) < 3:
        print("Usage: python chunk.py <extracted.json> <topics.json> [output.json]")
        sys.exit(1)

    extracted_path = Path(sys.argv[1])
    topics_path = Path(sys.argv[2])
    output_path = (
        Path(sys.argv[3]) if len(sys.argv) > 3
        else extracted_path.with_suffix(".chunks.json")
    )

    from rich.console import Console
    console = Console()

    with open(extracted_path, encoding="utf-8") as f:
        extracted = json.load(f)

    with open(topics_path, encoding="utf-8") as f:
        topics = json.load(f)

    console.print(f"[bold blue]Chunking:[/] {extracted_path.name}")
    chunks = chunk_by_topics(extracted["pages"], topics)
    chunks = _dedup_chunks(chunks)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(chunks, f, ensure_ascii=False, indent=2)

    total_tokens = sum(c["token_count"] for c in chunks)
    console.print(
        f"  [green]✓[/] {len(chunks)} chunks, "
        f"{total_tokens:,} total tokens → {output_path}"
    )


if __name__ == "__main__":
    main()
