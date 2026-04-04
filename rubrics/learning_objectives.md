# Learning Objectives Rubric

Rules for writing per-topic learning objectives in structure.json.

## Format

Each topic (any depth) may have a `learning_objectives` array:

```json
{
  "text": "Implement binary search on a sorted array",
  "bloom_level": "apply"
}
```

## Bloom's Taxonomy Levels

Use the correct action verbs for each level:

| Level | Verbs | Example |
|-------|-------|---------|
| `remember` | list, define, name, identify, recall | "List the four properties of ACID transactions" |
| `understand` | explain, compare, describe, summarize | "Explain the difference between TCP and UDP" |
| `apply` | implement, use, solve, demonstrate | "Implement a binary search on a sorted array" |
| `analyze` | debug, differentiate, examine, classify | "Analyze the time complexity of nested loops" |
| `evaluate` | justify, choose, critique, assess | "Evaluate trade-offs between normalization and denormalization" |
| `create` | design, construct, plan, produce | "Design a REST API for a booking system" |

## Quantity

- **Leaf topics**: 2-5 objectives each (required)
- **Parent topics**: 1-3 broader objectives (optional, summarize children)
- **Depth-0 topics**: at least 1 objective that frames the overall learning goal

## Quality Rules

1. **Measurable**: Every objective must describe an observable action — avoid "understand X" unless the verb is paired with a visible output ("explain X to a peer")
2. **Source-faithful**: Only promise knowledge that the source material actually covers. Never write objectives for content that doesn't exist in the extracted sections
3. **Specific**: "Implement binary search" not "Understand searching"
4. **Progressive**: Within a topic, order objectives from lower Bloom's levels to higher
5. **Non-redundant**: Sibling topics should not repeat the same objective

## Bloom's Level Guidelines

- Level 1 (Fundamentals) topics: objectives should be mostly `remember` and `understand`
- Level 2 (Intermediate) topics: mix of `understand`, `apply`, and `analyze`
- Level 3 (Advanced) topics: `analyze`, `evaluate`, and `create`
