# Lesson Distillation Rubric

This rubric defines how to transform curated book topics into structured lessons for the DuTaTo learning app.

## Lesson Format

Each lesson is a markdown document rendered by `flutter_markdown`. Every lesson teaches **one concept**.

```markdown
## What is [Concept]?

**Definition**: 1-2 sentences. Precise, no filler.

**Analogy**: Concrete comparison from everyday life or another domain.

### Key Points
- **Point A**: concise explanation
- **Point B**: concise explanation
- **Point C**: concise explanation

### Why It Matters
One short paragraph connecting the concept to real-world impact.

### Example _(optional)_
A concrete worked example. Code in fenced blocks with language tags.
```

## Rules

1. **One concept per lesson.** If a topic covers 3 concepts, produce 3 lessons.
2. **200-600 words** per lesson. Never exceed 800.
3. **No paragraph longer than 3 sentences.**
4. **DIGEST the content.** Understand and rewrite. Don't just reformat or copy prose.
5. **NEVER invent content** not present in or directly implied by the source material.
6. **Preserve all code examples** — wrap in fenced blocks with language tag (```java, ```python, etc.).
7. **Use the question form** when natural: "What is X?", "How does X work?", "When should you use X?"
8. **Bold key terms** on first mention: `**Single Responsibility Principle**`, `**Big-O notation**`, etc.
9. **Skip** topics with `curation_action: "removed"` or empty content.

## Difficulty Levels

Assign one per lesson:
- `awareness` — Can recognize/define the concept (Bloom's: remember)
- `understanding` — Can explain in own words, compare (Bloom's: understand)
- `application` — Can use in new situations (Bloom's: apply)
- `evaluation` — Can critique, improve, judge trade-offs (Bloom's: evaluate)

## Domain-Specific Guidance

### Algorithms & Data Structures
- Always include **Big-O complexity** for time and space
- Include **when to use** vs alternatives
- Show **pseudocode or code** for key operations
- For sorting/searching: show input → output example

### SOLID / Design Principles
- Structure as: **Principle statement** → **Violation example** → **Correct example**
- Include the "smell" that tells you the principle is being violated
- Reference related principles when they interact

### Architecture & Patterns
- Include **trade-offs** (what you gain vs what you pay)
- Include **when to apply** and when NOT to apply
- Draw clear boundaries: "this pattern is for X, not for Y"

### PM / Product Management
- Include **real-world scenarios** and **framework applications**
- For interview frameworks (CIRCLES, etc.): show the framework applied to a concrete case
- For estimation: show the math/reasoning chain

### Testing / TDD
- Include the **Red-Green-Refactor** context where applicable
- Show **before and after** code when discussing refactoring
- Connect test strategy to the specific type of code being tested

## Split / Merge Rules

### Split a topic into multiple lessons when:
- Content > 2000 words AND covers more than one distinct concept
- Content has multiple `##` sections that are independently learnable
- Content mixes a definition with an extended worked example (split into concept + example)

### Merge adjacent topics into one lesson when:
- Both topics are < 300 words
- Both cover the same concept from slightly different angles
- One topic is a continuation of the previous (check topic_path for sequential sections)

### When merging:
- Read both `.synth.json` files
- Combine their content into a single lesson
- Use the first topic's file for the `.lesson.json` output
- Write an empty lessons array for the second topic's `.lesson.json`

## Output Schema

Each `topic_NNNN.lesson.json` file:

```json
{
  "lessons": [
    {
      "concept": "Single Responsibility Principle",
      "content": "## What is the Single Responsibility Principle?\n\n**Definition**: ...",
      "difficulty": "understanding",
      "word_count": 412
    }
  ],
  "topic_path": ["Chapter 3", "SOLID Principles", "SRP"],
  "source_synth": "topic_0015.synth.json",
  "distilled": true,
  "distill_version": 1
}
```

For removed/empty/merged-away topics:
```json
{
  "lessons": [],
  "topic_path": ["..."],
  "source_synth": "topic_NNNN.synth.json",
  "distilled": true,
  "distill_version": 1
}
```

## Agent Prompt Template

```
You are distilling technical book content into structured lessons for the DuTaTo learning app. Each lesson teaches ONE concept.

BOOK: {title} by {author}
DOMAIN: {domain}

For each topic, read the .synth.json file and produce a .lesson.json file.

LESSON FORMAT:
## [Concept as question or title]
**Definition**: 1-2 sentences, precise
**Analogy**: concrete everyday comparison
### Key Points (3-5 bullets with **bold** labels)
### Why It Matters (1 short paragraph)
### Example (optional — code in fenced blocks)

RULES:
- ONE concept per lesson, 200-600 words, never exceed 800
- DIGEST the content — understand and rewrite, don't copy prose
- NEVER invent content not in the source
- Preserve all code examples in fenced blocks
- Assign difficulty: awareness / understanding / application / evaluation
- If a topic has no teachable substance, write {"lessons": []}
- If a topic covers multiple concepts, write multiple lesson entries
```
