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

## structure.json Format

```json
[
  {
    "title": "Topic Name",
    "depth": 0,
    "sort_order": 0,
    "description": "One-line description",
    "suggested_level": 1,
    "children": [
      {
        "title": "Subtopic Name",
        "depth": 1,
        "sort_order": 0,
        "description": "...",
        "source_sections": ["Extracted Section Title 1", "Extracted Section Title 2"],
        "children": []
      }
    ]
  }
]
```
