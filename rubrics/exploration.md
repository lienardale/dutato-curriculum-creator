# Content Exploration Rubric

When analyzing extracted content to build a curriculum, follow this systematic process.

## Phase 1: Survey

For each extracted JSON file, read the section titles and skim the content (first ~200 words of each section).

Identify:
1. **Key concepts** — the named ideas, techniques, principles, or patterns
2. **Concept relationships** — which concepts depend on others (prerequisites)
3. **Domain boundaries** — what subject area(s) does this content cover
4. **Audience level** — is this introductory, intermediate, or advanced material

## Phase 2: Cross-Source Mapping

If there are multiple sources, create a concept map:

| Concept | Source A | Source B | Source C | Coverage Quality |
|---------|----------|----------|----------|------------------|
| Concept X | Sections 1.1, 1.3 | Section 4 | — | Strong |
| Concept Y | — | Section 2 | Section 7 | Moderate |
| Concept Z | — | — | — | Gap |

Coverage quality levels:
- **Strong**: Multiple sources, different perspectives, examples included
- **Moderate**: At least one source with substantial treatment
- **Thin**: Mentioned but not deeply covered
- **Gap**: Important for the domain but not covered by any source

## Phase 3: Gap Analysis

Based on common knowledge of the domain:
1. What fundamental concepts are missing?
2. What advanced concepts should be included for completeness?
3. Are there practical/applied topics that would strengthen the curriculum?

Classify gaps as:
- **Critical**: The curriculum would be incomplete without this
- **Recommended**: Would significantly improve the curriculum
- **Nice-to-have**: Would be good but not essential

Report gaps to the user with suggestions for additional sources.

## Phase 4: Concept Ordering

Order concepts by prerequisite chain:
1. **Foundation**: Concepts that require no prior knowledge from this curriculum
2. **Building blocks**: Concepts that depend on foundations
3. **Applied**: Concepts that combine multiple building blocks
4. **Advanced**: Concepts requiring mastery of applied topics

This ordering directly informs the topic hierarchy and level assignments.

## Output

Present to the user:
1. Summary of key concepts (10-30 concepts for a typical curriculum)
2. Coverage assessment (strong/moderate/thin/gap for each)
3. Identified gaps with severity
4. Suggested concept ordering (foundation → advanced)
5. Recommendation on whether the sources are sufficient or more are needed
