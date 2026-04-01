# Curriculum Condensation Rubric

Rules for generating condensed curriculum variants from a full (extensive) curriculum.

## Variant Tiers

| Variant | Token Budget | Depth-0 Topics | Total Chunks | Learning Time |
|---------|-------------|----------------|--------------|---------------|
| **Detailed** | ~10% of extensive | 10-12 | ~40 | 5-8 hours |
| **Classic** | ~1% of extensive | 4-6 | ~8 | 30-50 minutes |
| **Core** | ~0.1% of extensive | 2-3 | ~3 | 3-5 minutes |

## Selection Criteria

### What to always keep (highest priority)
1. **Foundational concepts** that everything else builds on
2. **Most-used practical skills** (the 20% that covers 80% of real usage)
3. **Mental models** that help the learner reason about the domain
4. **Vocabulary and terminology** needed to read further on their own

### What to drop first (lowest priority)
1. Edge cases, rare scenarios, historical context
2. Advanced optimizations and performance tuning
3. Platform-specific details and configuration options
4. Reference material (syntax listings, option catalogs)
5. Detailed examples when one example suffices

### Merging rules
- Two or more related subtopics can be merged into one topic when their core ideas can be expressed together without confusion
- When merging, keep the most general title or create a new encompassing title
- Merged content should read as a coherent whole, not a collage of fragments

## Per-Tier Guidelines

### Detailed (~10x compression)
- Keep all depth-0 topics that cover fundamental or widely-used areas
- Drop depth-0 topics that are purely advanced/niche
- Within kept topics: keep 2-4 most important subtopics, merge the rest if related, drop the remainder
- Chunks can be kept as-is or lightly trimmed
- The result should be a **complete but brisk** tour of the subject

### Classic (~100x compression)
- Keep only the 4-6 most essential depth-0 topics
- Each topic gets 1-2 subtopics maximum
- Content is synthesized: write concise summaries covering the key concepts
- Each chunk should be self-contained and teach exactly one core concept
- The result should be a **crash course** — enough to be productive

### Core (~1000x compression)
- Keep only 2-3 depth-0 topics covering the absolute fundamentals
- Each topic gets at most 1 subtopic
- Content is heavily synthesized: one chunk per topic, densely packed with core concepts
- The result should be a **cheat sheet** — the minimum viable knowledge to get started

## Condensation Strategies

Each leaf topic in the plan must declare its strategy:

| Strategy | When to Use | Content Handling |
|----------|-------------|-----------------|
| `keep` | Topic and its chunks are essential as-is | `condense.py` copies chunks from extensive version |
| `merge` | Multiple topics can be combined | Agent writes merged `content` inline in the plan |
| `synthesize` | Content needs significant compression | Agent writes synthesized `content` inline in the plan |

For `merge` and `synthesize`, you **must** include a `content` field with the text you wrote. If omitted, `condense.py` falls back to raw concatenation (which defeats the purpose).

## condensation_plan.json Format

The agent writes this file. It is a JSON object keyed by tier name, where each value is an array of condensed depth-0 topics:

```json
{
  "detailed": [
    {
      "title": "Topic Title",
      "description": "One-line description",
      "suggested_level": 1,
      "condensation_strategy": "keep",
      "source_topics": ["Original Topic Title"],
      "children": [
        {
          "title": "Subtopic Title",
          "description": "...",
          "condensation_strategy": "keep",
          "source_children": ["Original Child 1", "Original Child 2"]
        },
        {
          "title": "Merged Subtopic",
          "description": "...",
          "condensation_strategy": "merge",
          "source_children": ["Original Child A", "Original Child B"],
          "content": "Agent-written merged text covering both children..."
        }
      ]
    },
    {
      "title": "Synthesized Overview",
      "description": "...",
      "suggested_level": 1,
      "condensation_strategy": "synthesize",
      "source_topics": ["Topic A", "Topic B"],
      "content": "Agent-written synthesis of both topics..."
    }
  ],
  "classic": [ ... ],
  "core": [ ... ]
}
```

### Field reference

**Depth-0 topics:**
- `title` — display name (new name if merging multiple originals)
- `description` — one-line summary
- `suggested_level` — 1 (Fundamentals), 2 (Intermediate), or 3 (Advanced)
- `condensation_strategy` — "keep", "merge", or "synthesize"
- `source_topics` — list of original depth-0 topic titles this derives from
- `content` (optional) — agent-written text; required for "merge"/"synthesize" leaf topics
- `children` (optional) — array of depth-1 subtopics

**Children (depth-1 topics):**
- `title`, `description` — same as above
- `condensation_strategy` — same as above
- `source_children` — list of original child titles this derives from
- `content` (optional) — agent-written text; required for "merge"/"synthesize"

### How condense.py processes the plan

```
strategy = "keep"      → copies original chunks from source_children/source_topics
strategy = "merge"     → uses "content" field (falls back to concatenation if missing)
strategy = "synthesize" → uses "content" field (falls back to concatenation if missing)
```

Run after writing the plan:
```bash
python condense.py --input output/<name>/ --plan output/<name>/condensation_plan.json
```

## Quality Constraints

1. **Pedagogical ordering preserved**: foundational topics still come before topics that depend on them
2. **Self-contained**: the condensed curriculum must make sense without the extensive version
3. **No dangling references**: don't reference concepts that were dropped
4. **Token bounds**: each chunk stays within 200-2000 tokens
5. **Accurate**: condensed content must not introduce errors or oversimplifications that are technically wrong
