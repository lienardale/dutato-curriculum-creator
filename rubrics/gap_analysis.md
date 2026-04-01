# Gap Analysis Rubric

After exploration, assess what's missing from the source materials.

## Process

### 1. Domain Completeness Check

For the stated domain, consider what a comprehensive curriculum should cover:
- **Core concepts**: The absolute minimum knowledge for the domain
- **Common techniques**: Methods and approaches practitioners use daily
- **Advanced topics**: What distinguishes an expert from a competent practitioner
- **Practical skills**: Hands-on abilities needed to apply the knowledge

### 2. Classify Each Gap

For each missing concept:

| Severity | Definition | Action |
|----------|-----------|--------|
| Critical | Curriculum is incomplete without it | Must add more sources or note limitation |
| Recommended | Significantly improves the curriculum | Suggest sources to the user |
| Nice-to-have | Adds depth but not essential | Note for potential future expansion |

### 3. Suggest Remedies

For each gap, suggest:
1. Specific search terms or book titles that would fill the gap
2. Whether the gap could be partially filled by reorganizing existing content
3. Whether the agent could fill the gap from its own knowledge (only for factual, well-established content — never for opinions or cutting-edge topics)

### 4. Report Format

Present to the user:

```
## Gap Analysis for [Curriculum Name]

### Critical Gaps
- **[Concept]**: Not covered by any source. This is essential because [reason].
  Suggested source: [book/URL/topic to search for]

### Recommended Additions
- **[Concept]**: Only briefly mentioned in [source]. A deeper treatment would
  strengthen the [topic area] section.
  Suggested source: [book/URL]

### Nice-to-have
- **[Concept]**: Not covered, but the curriculum is functional without it.

### Coverage Summary
- Core concepts: [X]% covered
- Techniques: [X]% covered
- Advanced topics: [X]% covered
- Practical skills: [X]% covered
```

## Important

- Never fabricate content to fill gaps. If a concept isn't in the sources, flag it — don't invent it.
- The user decides whether to add more sources or accept the gaps.
- Gaps are normal. A focused curriculum with acknowledged limitations is better than a padded one.
