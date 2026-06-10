# Review Checklist

Before uploading, verify all quality gates pass. The gates split into two
groups: **machine-checked** gates enforced by `validate.py`, and
**judgment** gates that only the reviewing agent can assess.

## Part 1: Machine-Checked Gates

Run the validator first and write the report:

```bash
python validate.py output/<name>/ --json
```

- **All errors must be fixed** before review can be approved. Errors mean
  broken artifacts: content that will be silently dropped, references that
  resolve to nothing, or data the app cannot render.
- **Every warning must be either fixed or individually acknowledged** in
  `review.json` → `quality_concerns` (one entry per acknowledged warning,
  with a short reason it is acceptable).

The former manual checkboxes map to validator check IDs:

| Former checkbox | Check ID(s) | Severity |
|-----------------|-------------|----------|
| Hierarchy depth ≤ 2 | `structure.invalid_depth` | error |
| Balanced breadth (5-15 depth-0) | `structure.breadth` | warning |
| Balanced children (2-8 per parent) | `structure.children_count` | warning |
| No empty topics | `structure.empty_leaf`, `chunks.empty_topic` | error |
| No giant topics (≤10 sections) | `structure.giant_topic` | warning |
| Level assignment 30/40/30 | `structure.missing_level`, `structure.invalid_level`, `structure.level_distribution` | mixed |
| Full coverage of extracted sections | `structure.orphan_section` | warning |
| No stale section references | `structure.stale_ref` | error |
| No orphan chunks | `chunks.unknown_topic_path` | error |
| Token range 500-1500 | `chunks.token_range` | warning (error outside 50-2500) |
| No empty chunks | `chunks.empty_content` | error |
| Objectives coverage (2-5 per leaf) | `structure.objective_count` | warning |
| Bloom's enum valid | `structure.invalid_bloom`, `exercises.invalid_bloom` | error |
| Prerequisites: no cycles | `structure.prereq_cycle` | error |
| Prerequisites: valid references | `structure.prereq_unknown_topic` | error |
| Prerequisites: no transitive redundancy | `structure.prereq_transitive` | warning |
| Prerequisites: ordering consistency | `structure.prereq_order` | warning |
| Exercise hints (3 progressive) | `exercises.few_hints` | warning |
| Exercises grounded to topics | `exercises.unknown_topic_path` | error |
| manifest.json valid | `manifest.*` | mixed |
| JSON files well-formed | `*.invalid_json`, `*.file_missing` | error |

## Part 2: Judgment Gates (agent review)

The validator cannot assess meaning. Check these yourself:

- [ ] **Readable**: Spot-check 5-10 chunks — content is coherent,
  self-contained, and doesn't start or end mid-thought
- [ ] **Clear naming**: Titles are descriptive noun phrases; no chapter
  numbers, no source branding
- [ ] **Ordering**: Topics flow foundational → advanced; siblings are
  similar in scope
- [ ] **Bloom's alignment**: Objective verbs actually match their declared
  level (the validator only checks the enum)
- [ ] **Solution correctness**: Exercise expected solutions are accurate
- [ ] **Common mistakes**: Realistic and pedagogically valuable
- [ ] **Exercise grounding**: Problems are answerable from curriculum
  content alone

### Objective Answerability Protocol

For **every leaf topic**, read its learning objectives against its chunks
(in `chunks.json`) and verify a learner could actually achieve each
objective from that content:

1. For each objective, find the chunk(s) that teach it.
2. If no chunk enables the objective, it is **unanswerable** — either
   rewrite the objective to match what the content covers, or fix the
   topic's `source_sections` so the supporting content is included.
3. List any objectives you could not ground (and what you did about them)
   in `review.json` → `quality_concerns`.

## review.json Contract

`review.json` must embed the validator's summary under the `"validation"`
key (copy the `summary` block from `validation_report.json`):

```json
{
  "total_topics": 25,
  "total_chunks": 142,
  "total_exercises": 35,
  "total_tokens": 95000,
  "avg_tokens_per_chunk": 669,
  "chunks_per_topic": {"min": 1, "max": 15, "avg": 5.7},
  "objectives_coverage": "24/25 topics have objectives",
  "prerequisites_count": 12,
  "exercises_coverage": "18/25 leaf topics have exercises",
  "validation": {
    "checked_at": "2026-03-31T14:00:00+00:00",
    "stage": "all",
    "errors": 0,
    "warnings": 2,
    "by_check": {"structure.breadth": 1, "exercises.few_hints": 1}
  },
  "quality_concerns": [
    "structure.breadth: 16 depth-0 topics — accepted, the domain genuinely has 16 major areas",
    "exercises.few_hints: 'Filter with WHERE' has 2 hints — third hint would give away the answer"
  ],
  "approved": false,
  "reviewed_at": "2026-03-31T14:00:00Z"
}
```

`upload.py` re-runs the validator automatically and **blocks on errors**
(override with `--skip-validation` only when the user explicitly asks).

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
| Validation | N errors / N warnings |

### Per-Topic Distribution

Show chunk count per depth-0 topic to reveal imbalances:

| Topic | Chunks | Tokens | Level |
|-------|--------|--------|-------|
| Topic A | 15 | 12,000 | 1 |
| Topic B | 8 | 6,500 | 2 |
| ... | ... | ... | ... |

Flag any topic with <3 chunks or >30 chunks for user review.
