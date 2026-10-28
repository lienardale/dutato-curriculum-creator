# Review Checklist (lesson pipeline)

Before uploading, every quality gate must pass. Gates come in three groups: **machine gates**
(deterministic `check_id`s from the stage scripts and `validate.py`), **critique gates**
(`critique/summary.json`, computed by `critique.py check` from independent critics' verdicts)
and **judgment gates** that only the reviewing agent can assess. The review is written to
`review.json`; `upload.py` re-runs the validator and blocks on errors, and
`validate.py --stage upload` additionally requires `approved: true` and no failing critique
item. Shapes of the artifacts under review: `docs/artifacts.md`.

## Part 1: Machine gates

```bash
python validate.py output/<name>/ --json      # every family whose checkpoint exists → validation_report.json
python critique.py status output/<name>/      # per-stage pass / fail / FAILED / pending
python status.py output/<name>/               # FAILED items by id
```

- **All errors must be fixed** — they mean broken artifacts: dangling ids, quotes that do not
  match a unit, pages that will not render, practice rows that cannot be attached.
- **Every remaining warning is fixed or individually acknowledged** in `review.json` →
  `quality_concerns` (one entry per acknowledged warning, with the reason it is acceptable).
- In lesson mode `validate.py` delegates each family to its stage module (findings 1:1) and
  reads the critique summary; the families and the `check_id` prefixes to expect:

| Family (`--stage`) | Prefix(es) | Produced by | What it covers |
|---|---|---|---|
| manifest | `manifest.*` | `validate.py` | required keys, `pipeline`, `domain_family` / `variant` |
| extracted | `extracted.*` | `validate.py` | source JSONs well-formed, non-empty sections |
| units | `units.*`, `scope.*` | `qualify.py` | unit ids, block types, token counts, cross-source duplicates (`units.alternate_of`, `units.near_duplicate`), scope resolution |
| qualification | `qualification.*` | `qualify.py` | every unit tagged, verbatim `quote`s, enums, drop reasons, Bloom-ceiling overrides, gleaning |
| concepts | `concepts.*` | `qualify.py` | ids, labels, `depends_on` DAG, thin / too-broad / merge candidates, gap resolutions, ordering |
| groups | `groups.*` | `regroup.py` | unit accounting, knowledge points, objectives (observable verb, ceiling), watch, generalization, areas, prerequisite DAG hygiene, stale budgets |
| syntheses | `syntheses.*`, `syntheses.verbatim.*` | `synthesize.py` | ids in group, quotes, citation limits, coverage, inference share, terminology, verbatim-copy gates |
| lessons | `lessons.outline.*`, `lessons.*` (`lesson_words`, `new_terms`, `undefined_terms`, `term_density`, `sentence_*`, `paragraph_long`, `example_missing`, `verbatim.*`, `furniture`, `dangling_reference`, `mid_sentence_start`, `unknown_image`, `pitfalls_count`, `recap_*`, `estimated_minutes`, `title_duplicate`), `lessons.build.*` | `lessons.py` | outline shape, page metrics, verbatim gates, image placeholders, titles, build preconditions |
| structure | `structure.*` | `validate.py` | depth, levels, prerequisite references / cycles / order, duplicate titles (section coverage skipped in lesson mode) |
| chunks | `chunks.*` | `validate.py` | topic paths resolve, non-empty content, token band 150–900 (warn) / 50–2500 (error) |
| questions | `questions.*` | `questions.py` | ids, provenance quotes, embedded placement, prompt hygiene, answer leaks, MCQ flaws, coverage, budgets, variants, exercises |
| cards | `cards.*` | `cards.py` | ids, counts and types, front/back caps and hygiene, cloze / ordering / discrimination shapes, duplicates across neighbours |
| critique | `critique.*`, `repair.*` | `validate.py` ← `critique/summary.json` | failing items (`critique.item_failed`: warning here, error at `--stage upload`), stages with author artifacts but no verdict (`critique.pending`), verdict shape, repair bounds |
| upload | `upload.*` | `upload.py check` | every lesson resolves to a structure leaf, enums, `page_index` ↔ chunk, image files present, lessons without practice content |
| review | `review.*` | `validate.py` | `approved` boolean present, `validation` block embedded |

Inside critique verdicts the criterion ids are `group.*`, `fidelity.precision` /
`fidelity.recall`, `brief.*`, `depth.*`, `structure.one_knowledge_point_per_page`,
`examples.*`, `pages.standalone`, `objectives.aligned`, `coherence.*`, `pedagogy.*`,
`register.*`, `bloom.*`, `tags.*`, `q.*`, `card.*` (`contracts.CRITERIA` /
`PER_ITEM_CRITERIA`); `critique.py check` reports which critical ones failed per item.

## Part 2: Critique gates

Every critiqued stage (qualification, groups, syntheses, lessons, questions, cards) must show
0 `pending` and 0 `fail` in `critique.py status`. A `FAILED` item (still failing after two
repair rounds) is never passed silently: it is listed in `review.json` → `failed_items` with
the decision taken (regrouped, rewritten with the user's OK, or dropped) and the user sees it
before approving. `critique.pending` on a stage means a critic never ran — run
`critique.py prepare <stage>`, the critic, `critique.py check <stage>`.

## Part 3: Judgment gates (agent review)

Scripts and critics cannot assess the whole. Read the curriculum as its learner would
(`manifest.audience`), across lessons:

- [ ] **Walk 3–5 lessons end to end** (pilot + the longest + one from each level): pages read
      in order, every page starts and ends on a whole thought, the recap matches what was
      taught, the orientation hook is concrete.
- [ ] **Prerequisite flow**: for two committed edges, read the prerequisite's recap and then
      the dependent's orientation — does the hand-off hold without re-teaching?
- [ ] **Naming and ordering** of `structure.json`: noun phrases, no source branding, areas
      flow foundational → advanced, levels sensible (`rubrics/structuring.md`).
- [ ] **Questions** (one lesson per level): stems are neutral and open-book-proof, the
      `acceptance.causal_reason` is a relationship, distractors are real misconceptions, the
      recap item really re-tests the prerequisite.
- [ ] **Exercises**: expected solutions are correct; hints progress; mistakes are realistic.
- [ ] **Cards** (one lesson per level): one proposition each, answerable from the front, no two
      cards with the same answer, no interference with neighbouring lessons.
- [ ] **Gaps**: every `critical` gap in `concepts.json` carries a `resolution` the user made.
- [ ] **Source balance**: no source dominates every area unless the sources warrant it; whisper
      / auto-caption transcripts did not win conflicts over written sources.
- [ ] **Metrics sanity** (`lesson_metrics.json`): estimated minutes per lesson are in the
      4–10 range, no lesson sits at a band edge only because it was padded or trimmed.

Record anything you could not confirm, and what you did about it, in `quality_concerns`.

## review.json contract

`validate.py --stage review` requires `approved` (boolean) and warns without `validation`.
Embed the validator's `summary` block from `validation_report.json`, the per-stage counts
from `critique/summary.json`, and a summary of `lesson_metrics.json`; do not restate the
artifacts themselves.

```json
{
  "total_lessons": 24,
  "total_areas": 5,
  "total_pages": 118,
  "total_questions": {"embedded": 61, "quiz": 104, "exercises": 19},
  "total_cards": 131,
  "total_tokens": 71000,
  "prerequisites_committed": 17,
  "validation": {
    "checked_at": "2026-10-28T14:00:00+00:00",
    "stage": "all",
    "errors": 0,
    "warnings": 2,
    "by_check": {"lessons.term_density": 1, "cards.near_duplicate": 1}
  },
  "critique": {
    "qualification": {"items": 5, "pass": 5, "fail": 0, "FAILED": 0, "pending": 0},
    "groups": {"items": 24, "pass": 24, "fail": 0, "FAILED": 0, "pending": 0},
    "syntheses": {"items": 24, "pass": 24, "fail": 0, "FAILED": 0, "pending": 0},
    "lessons": {"items": 24, "pass": 23, "fail": 0, "FAILED": 1, "pending": 0},
    "questions": {"items": 23, "pass": 23, "fail": 0, "FAILED": 0, "pending": 0},
    "cards": {"items": 23, "pass": 23, "fail": 0, "FAILED": 0, "pending": 0}
  },
  "lesson_metrics": {
    "lessons": 24,
    "words": {"min": 940, "max": 2310, "mean": 1480},
    "pages": {"min": 3, "max": 6},
    "estimated_minutes": {"min": 4, "max": 10, "total": 150},
    "lessons_with_warnings": ["g:fitness-functions"]
  },
  "failed_items": [
    {"stage": "lessons", "item_id": "g:evolutionary-architecture", "round": 2, "decision": "dropped from this release; sources too thin — user agreed to add a source later"}
  ],
  "quality_concerns": [
    "lessons.term_density: g:fitness-functions page 2 at 0.16 — four registry terms are the knowledge point; accepted",
    "cards.near_duplicate: g:adr#k3 vs g:decision-context#k1 — different lens, reworded front; accepted"
  ],
  "approved": false,
  "reviewed_at": "2026-10-28T14:00:00Z"
}
```

Set `approved: true` only after the user approves the summary. Re-run
`validate.py output/<name>/ --json` after any fix so the embedded summary is current.

## Statistics to report

Present to the user before asking for approval:

| Metric | Value |
|--------|-------|
| Areas / modules / lessons | N / N / N |
| Pages (chunks) and total tokens | N, N |
| Lesson length | min / mean / max words; min / max minutes |
| Embedded questions / quiz items / exercises / cards | N / N / N / N |
| Prerequisite edges committed / uncertain / rejected | N / N / N |
| Sources represented, units kept / dropped | N/M, N / N |
| Critique | per stage: pass / fail / FAILED / pending |
| Validation | N errors / N warnings |
| FAILED items | list with decision |

### Per-area distribution

| Area | Lessons | Pages | Tokens | Level |
|------|---------|-------|--------|-------|
| Area A | 6 | 29 | 17,000 | 1 |
| … | … | … | … | … |

Flag any area with a single lesson or more than ten, and any lesson whose metrics sit at a
band edge, for the user's attention.
