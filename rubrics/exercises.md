# Practice Exercises Rubric

Rules for generating exercises during the EXERCISES stage.

## Exercise Format

Each exercise in `exercises.json`:

```json
{
  "title": "Filter with WHERE Clause",
  "problem_statement": "Write a SQL query that selects all rows from the 'weather' table where the temperature is above 30 degrees.",
  "hints": [
    "Start with SELECT * FROM weather",
    "Add a WHERE clause to filter rows",
    "Use the > operator to compare the temp column"
  ],
  "expected_solution": "SELECT * FROM weather WHERE temp > 30;",
  "common_mistakes": [
    "Forgetting the semicolon",
    "Using = instead of > for 'above'"
  ],
  "bloom_level": "apply",
  "difficulty": 1
}
```

## Exercise Types by Bloom's Level

| Level | Exercise Type | Example |
|-------|--------------|---------|
| `remember` | Fill-in-the-blank, terminology matching, true/false | "What does ACID stand for?" |
| `understand` | Explain in your own words, compare/contrast, predict outcome | "Explain why a hash table has O(1) average lookup" |
| `apply` | Write code, solve a specific problem, use a technique | "Write a function that reverses a linked list" |
| `analyze` | Debug broken code, identify issues, trace execution | "Find and fix the bug in this sorting implementation" |
| `evaluate` | Choose between approaches, justify trade-offs | "Given these requirements, would you use SQL or NoSQL? Justify." |
| `create` | Design from requirements, build a system, extend a pattern | "Design a caching layer for this API" |

## Hint Design

Each exercise needs exactly 3 progressive hints:

1. **Context hint**: Points to the relevant concept without giving the approach ("Think about what data structure supports O(1) lookup")
2. **Approach hint**: Describes the strategy ("Use a hash map to store seen values")
3. **Near-solution hint**: Almost gives the answer ("Iterate the array, check if complement exists in the map")

## Common Mistakes

Include 2-4 realistic mistakes per exercise. Good common mistakes:
- Off-by-one errors in loops
- Confusing similar operators or keywords
- Forgetting edge cases (empty input, null values)
- Using the wrong data structure
- Syntax errors specific to the language/domain

Bad common mistakes (avoid):
- Trivially wrong answers that no learner would produce
- Mistakes unrelated to the concept being tested

## Difficulty Alignment

| Topic Level | Exercise Difficulty | Bloom's Focus |
|-------------|-------------------|---------------|
| 1 (Fundamentals) | 1 | `remember`, `understand`, `apply` |
| 2 (Intermediate) | 2 | `apply`, `analyze` |
| 3 (Advanced) | 3 | `analyze`, `evaluate`, `create` |

## Coverage Targets

- At least 1 exercise per depth-0 topic group
- 1-3 exercises per leaf topic (focus on the most important)
- Skip purely definitional or reference-style topics
- Prioritize topics where hands-on practice adds the most value
- For code-heavy curricula: at least 50% of exercises should involve writing code
- For conceptual curricula: mix of explain, compare, and short-answer exercises

## Quality Checklist

- [ ] Problem statement is clear and unambiguous
- [ ] Expected solution is correct and complete
- [ ] Hints are progressive (not all at the same level)
- [ ] Common mistakes are realistic
- [ ] Exercise is answerable from the curriculum content alone (no external knowledge required)
- [ ] Bloom's level matches the cognitive demand of the exercise
