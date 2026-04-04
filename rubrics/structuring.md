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
