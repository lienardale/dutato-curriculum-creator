# Review Checklist

Before uploading, verify all quality gates pass.

## Structure Quality

- [ ] **Hierarchy depth**: No topic deeper than depth 2
- [ ] **Balanced breadth**: 5-15 depth-0 topics (not 2, not 50)
- [ ] **Balanced children**: Each parent has 2-8 children (not 1, not 20)
- [ ] **No empty topics**: Every leaf topic maps to at least one source section
- [ ] **No giant topics**: No topic has more than 10 mapped source sections
- [ ] **Clear naming**: Titles are descriptive noun phrases, not chapter numbers
- [ ] **Ordering**: Topics flow from foundational to advanced
- [ ] **Level assignment**: Levels 1-3 are distributed roughly 30/40/30

## Content Quality

- [ ] **Full coverage**: Every extracted section appears in at least one topic
- [ ] **No orphan chunks**: After chunking, no chunks have unmatched topic paths
- [ ] **Token range**: Most chunks are 500-1500 tokens
- [ ] **No empty chunks**: No chunks with empty or whitespace-only content
- [ ] **Readable**: Spot-check 5-10 chunks — content should be coherent and self-contained

## Metadata Quality

- [ ] **manifest.json exists**: Has name, domain, description, sources list
- [ ] **structure.json valid**: Valid JSON, matches expected format
- [ ] **chunks.json valid**: Valid JSON, array of chunk objects

## Statistics to Report

Present these to the user before upload:

| Metric | Value |
|--------|-------|
| Total depth-0 topics | N |
| Total leaf topics | N |
| Total chunks | N |
| Total tokens | N |
| Avg tokens/chunk | N |
| Sources represented | N/M |
| Coverage % | N% |

### Per-Topic Distribution

Show chunk count per depth-0 topic to reveal imbalances:

| Topic | Chunks | Tokens | Level |
|-------|--------|--------|-------|
| Topic A | 15 | 12,000 | 1 |
| Topic B | 8 | 6,500 | 2 |
| ... | ... | ... | ... |

Flag any topic with <3 chunks or >30 chunks for user review.
