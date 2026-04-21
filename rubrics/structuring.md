# Topic Structuring Rubric

Rules for building the topic hierarchy (structure.json) from exploration findings.

## Hierarchy Rules

### Depth limits
- **Depth 0**: Major topic areas (5-15 per curriculum)
- **Depth 1**: Subtopics within each area (2-8 per parent)
- **Depth 2**: Specific concepts (1-5 per parent, optional)
- Never go beyond depth 2

### Naming conventions
- Use clear, descriptive titles (not chapter numbers)
- Use noun phrases or "What is X?" form
- Bad: "Chapter 3", "Section 2.1", "3.2.1"
- Good: "Sorting Algorithms", "Binary Search Trees", "What is Big-O Notation?"
- **No source branding** in topic titles. Strip parentheticals that name a book, author, publisher, or video channel. The learning app shows standalone topics; users don't need to know which book a chapter came from.
  - Bad: "Bootstrapping a Cluster (The Hard Way)", "Production Chart Patterns (Bitnami examples)", "Docker Internals (Poulton)".
  - Good: "Bootstrapping a Cluster from Scratch", "Production Chart Patterns — NGINX".

### Ordering
- Within each parent: foundational → specific → advanced
- Across depth-0 topics: survey/overview → fundamentals → applied → advanced
- Use `sort_order` (0-indexed) to enforce the order

## Level Assignment

Assign `suggested_level` (1, 2, or 3) to each depth-0 topic:

| Level | Name | Content Type |
|-------|------|-------------|
| 1 | Fundamentals | Definitions, core concepts, basic examples |
| 2 | Intermediate | Techniques, patterns, comparative analysis |
| 3 | Advanced | Complex applications, trade-offs, synthesis |

Guidelines:
- Level 1 should cover ~30% of the curriculum
- Level 2 should cover ~40%
- Level 3 should cover ~30%
- A topic's level is determined by the _most advanced_ concept it contains

**Database effect**: During upload, `suggested_level` creates `curriculum_levels` rows (Fundamentals / Intermediate / Advanced) and links depth-0 topics via `curriculum_level_id`. The app uses these tiers to group topics and guide learning progression from basic to advanced.

## Section Mapping

Each leaf topic (no children) must have `source_sections` listing which extracted sections contain its content:

```json
{
  "title": "Binary Search",
  "source_sections": ["Searching Algorithms", "Binary Search Implementation"]
}
```

Rules:
- Every extracted section must appear in at least one topic's `source_sections`
- A section can appear in multiple topics if it covers multiple concepts
- If a section doesn't fit any topic, either create a topic for it or confirm with the user it should be excluded

### source_sections must be clean of book-meta

Extraction often captures TOC entries, preamble, end-matter, and "Chapter N" chapter markers. These leak book structure into the curriculum and have zero educational value. Before authoring `source_sections`, **always run the normalizer:**

```bash
python normalize_titles.py output/<name>/
```

This strips chapter-number prefixes (`Chapter N:`, `N.`, `N:`, `Part I`, `Section N`, etc.) and drops book-meta sections (Cover, Credits, Foreword, Dedication, Index, "Who Should Read This Book?", "How This Book Is Organized", "Technical Requirements", end-of-chapter "Questions"/"Summary", Acknowledgements, etc.) directly in the extracted JSONs.

For low-fidelity PDF extractions that collapsed chapters into bare "Chapter N" titles (no trailing semantic name), provide an explicit per-file remap so the normalizer can rewrite them using the book's TOC:

```bash
python normalize_titles.py output/<name>/ --remap remap.json
```

```json
{
  "Docker_up_and_running.json": {
    "Chapter 1": "Introduction to Docker",
    "Chapter 4": "Working with Docker Images"
  }
}
```

After normalization, `source_sections` strings must:
- **Not** contain `Chapter N`, `Part I`, `Section N`, or `N: / N. / N.N` prefixes.
- **Not** include book preamble / end-matter (see deny list in `normalize_titles.py :: BOOK_META_RE`).
- Match an actual section title in an extracted JSON (otherwise the chunker pulls nothing).

A **stale-ref audit** after structure authoring:

```python
import json, pathlib
titles = set()
for p in pathlib.Path('output/<name>/extracted').glob('*.json'):
    titles.update((s.get('title') or '').strip() for s in json.loads(p.read_text()).get('sections',[]))
with open('output/<name>/structure.json') as f:
    structure = json.load(f)
def check(items, path=''):
    for it in items:
        p = path + '/' + it['title']
        for s in it.get('source_sections', []):
            if s not in titles and s.lower() not in {t.lower() for t in titles}:
                print(f'STALE: {p} references {s!r}')
        check(it.get('children', []), p)
check(structure)
```

Any `STALE:` line means that topic will get zero content from that entry — either fix the title to match a real extracted section or drop it.

## Balance Checks

After building the structure, verify:
1. **No empty topics**: Every topic should map to at least one section
2. **No giant topics**: No single topic should have >10 sections mapped
3. **Balanced children**: Siblings should be roughly similar in scope
4. **Complete coverage**: All extracted sections are mapped somewhere
5. **No orphan paths**: Every depth-2 topic has a depth-1 parent, every depth-1 has a depth-0 parent

## Learning Objectives

After building the hierarchy, add learning objectives to each topic.
See `rubrics/learning_objectives.md` for the full rubric.

Each topic gains a `learning_objectives` array:
```json
"learning_objectives": [
  {"text": "Implement binary search on a sorted array", "bloom_level": "apply"},
  {"text": "Analyze the time complexity of binary search", "bloom_level": "analyze"}
]
```

- Leaf topics: 2-5 objectives (required)
- Parent topics: 1-3 broader objectives (optional)
- Use Bloom's action verbs matching the topic's level

## Prerequisite Links

After building the hierarchy, add prerequisite links using the prerequisite chains
from `exploration.json`:

```json
"prerequisites": [
  {"topic": "Arrays", "strength": "required"},
  {"topic": "Sorting Algorithms", "strength": "recommended"}
]
```

Rules:
1. **Intra-curriculum only**: Only reference topics within this structure
2. **Granularity**: Link at the most specific level possible
3. **No cycles**: The prerequisite graph must be a DAG
4. **No transitive links**: If A→B→C, don't also add A→C
5. **Valid references**: Every `topic` value must match a title in this structure (case-insensitive)
6. **Ordering consistency**: Prerequisites should have a lower `sort_order` than their dependents

## Split Hints (Optional)

If a source section contains distinct conceptual segments, add `split_after_headings`
to force chunk breaks at specific headings:

```json
"split_after_headings": ["Pseudocode", "Complexity Analysis"]
```

Only use when default heading-based splitting would combine concepts that should be
separate chunks.

## structure.json Format

```json
[
  {
    "title": "Topic Name",
    "depth": 0,
    "sort_order": 0,
    "description": "One-line description",
    "suggested_level": 1,
    "learning_objectives": [
      {"text": "Explain the fundamentals of X", "bloom_level": "understand"}
    ],
    "prerequisites": [
      {"topic": "Prerequisite Topic", "strength": "recommended"}
    ],
    "children": [
      {
        "title": "Subtopic Name",
        "depth": 1,
        "sort_order": 0,
        "description": "...",
        "source_sections": ["Extracted Section Title 1", "Extracted Section Title 2"],
        "learning_objectives": [
          {"text": "Define key terms of X", "bloom_level": "remember"}
        ],
        "children": []
      }
    ]
  }
]
```
