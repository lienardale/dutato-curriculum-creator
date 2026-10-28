# Regrouping Rubric — from `groups.draft.json` to `groups.json`

Stage 3 of the lesson pipeline. One **group = one lesson**. The script drafts candidates
mechanically; you decide what a lesson is. Contract: `docs/artifacts.md` (Stage 3); thresholds:
`shared/contracts.py`. Nothing here overrides either.

## Workflow

```bash
python regroup.py draft output/<name>/          # groups.draft.json (mechanical candidates)
#   ... author groups.json from the draft (this rubric) ...
python regroup.py check output/<name>/          # gates; writes prerequisite_edges_reduced
python regroup.py budget output/<name>/         # fills each group's "budget" (script-owned)
python critique.py prepare groups output/<name>/ ; # independent critic writes critique/groups/<id>.json
python regroup.py commit-edges output/<name>/   # prerequisites[].status from the critic's edge verdicts
python regroup.py check output/<name>/          # re-run: DAG hygiene now uses committed edges only
```

Fix every `[ERROR]` before critique. Warnings need a one-line justification in `rationale`
or a fix. Never edit script-owned fields (`budget`, `prerequisites[].status`,
`prerequisite_edges_reduced`).

## Reading the draft

`candidates` = one per concept: the kept units whose **first** concept is it (`unit_ids`),
units that mention it secondarily (`related_unit_ids`), token mass, unit-type and
knowledge-level counts, learning step. Candidates are ordered foundation → building_block →
applied → advanced, then by `concepts.ordering`, then source order — a *starting* order, not
the lesson order.

- `suggested_merges`: thin candidates (< 300 tokens or < 3 kept units) with the concept they
  most often share a unit with. Merge only when both concepts serve **one** generalization;
  otherwise fold the thin concept into a neighbour's `concept_ids` as context, or drop its
  units with a reason.
- `suggested_splits`: > 8000 tokens, more than one `procedure` unit, or definition units for
  distinct concepts among the candidate's primary + related units. A lesson teaches one
  procedure; two procedures are two lessons (or one lesson plus a `related` reference).
- `prerequisite_candidates`: concept pairs with evidence tags (below). No tag, no pair — do
  not invent edges for pairs the draft never lists unless you can cite units.
- `unassigned_units`: kept units with no concept or an unregistered first concept. Every one
  of them must end up in a group's `unit_ids` or in `dropped` with a reason.

## Unit accounting (hard rule)

Every kept unit (`keep: true` in qualification) is **primary in exactly one group** or listed
in `dropped`. A unit may appear in any number of `related_unit_ids`. Drop reasons are short
and honest (`"meta: preface"`, `"duplicate of u:…"`); never drop to make a count fit.

## One generalization per group

`generalization` is the sentence the recap will repeat verbatim: a mechanism or consequence
("Recording the context of a decision lets a team revisit it without relitigating"), not a
label ("ADRs are important"). It must name **>= 2 concept labels or aliases** — add an alias
to `concepts.json` if the natural wording uses one — and avoid weak main verbs (`is`, `has`,
`affects`, `impacts`, `influences`). If you cannot write it in one sentence, the group holds
two lessons.

## Knowledge points (3–5)

Facets of the generalization, each adding **one twist** (why it is needed, how it works, where
it breaks, how it compares, how to apply it). Ids `g:<slug>#kp<n>`; each cites the units
that teach it. A knowledge point that spans two concepts, or that two groups both claim,
means the grouping is wrong, not the point. The budget derives pages from this count.

## Objectives (2–4)

- Start with an observable verb from `OBSERVABLE_VERBS` (`explain`, `compare`, `implement`,
  `debug`, `justify`, `design` …). `understand`, `know`, `appreciate`, `learn`, `be familiar`,
  `be aware`, `grasp`, `realize` anywhere in the first three words fail.
- `bloom_level` <= the highest `bloom_ceiling` of the objective's `unit_ids` (defaults:
  fact → understand, concept → analyze, procedure → apply, principle → evaluate). A unit
  cannot support `evaluate` unless its qualification says so with a reason.
- Each objective names its `concept_ids`, `unit_ids` and the `knowledge_point_ids` it is
  taught by. Promise only what the units teach.

## Watch items (>= 2)

Kinds: `misconception | boundary | contrast | failure_mode | example_seed`. A misconception is
something a learner would **plausibly believe** after a shallow read, grounded in a unit that
contradicts it — not a restatement of the generalization with "not". Boundaries say where the
idea stops applying; contrasts name the confusable neighbour; failure modes describe what
breaks. These seed the pitfalls page and the quiz distractors.

## Duplicates and conflicts

- `duplicates`: units from different sources saying the same thing. `prefer` must be one of
  the listed units; say why in `reason` (clearer, more complete, has the code).
- `conflicts`: sources that **disagree**. Keep both units, write the disagreement in `note`.
  The brief must surface it as a watch item — never silently pick a side.

## Prerequisites with evidence

An edge `prerequisites: [{group_id, strength, evidence_unit_ids, reason, status}]` says the
learner needs that lesson first. Rules:

- **Evidence or nothing.** `evidence_unit_ids` must cite units showing the dependency: a unit
  that uses the prerequisite's concept without defining it, a declared `depends_on`, a
  description that names it. Source order alone (chapter 3 comes after chapter 2) is **not**
  evidence — the draft tags it only as a supporting signal, never on its own.
- `strength`: `required` when the lesson is unreadable without it; `recommended` otherwise.
- Leave `status` unset or `"proposed"`. The critic judges each edge `confirmed | either |
  uncertain`; `commit-edges` sets `committed | rejected | uncertain`. Only committed edges
  reach `structure.json` and the DAG checks after critique.
- Shape: no cycles, no self-edges, <= 4 prerequisites per lesson (6 fails), >= 3 lessons with
  none when there are >= 10, no lesson gating > 85% of the others. The script writes the
  transitive reduction; you do not need to prune implied edges by hand, but fewer, stronger
  edges beat many weak ones.

## Areas and modules

`areas[]` (1–15) group lessons by theme; optional `modules[]` subdivide an area. 2–10 groups
per area. Titles are noun phrases for a learner: **no source branding** (book, author, site,
chapter numbers), no "Part II", no "(Fowler)". `sort_order` is unique per parent and follows
what builds on what — the committed prerequisite graph, then foundation → advanced — never
the table of contents of one source. `suggested_level` 1–3 on areas and groups; a group's
level is set by its most advanced objective. Lesson titles are unique curriculum-wide.

## Budget semantics (ceilings, not quotas)

`regroup.py budget` fills `budget` from `contracts.budget_for(knowledge_points, objectives,
learning_step, has_prerequisite)`:

| Field | Rule | Meaning downstream |
|---|---|---|
| `pages` | knowledge points + 2, clamped to 3–6 | exact page count of the lesson outline |
| `embedded_questions_min/max` | 1 / 2 × (pages − 1) | at least one embedded question; never more than the slots |
| `quiz_items` | max(2 × objectives, 3) | quiz size; must span `quiz_min_bloom_levels` (3) |
| `recap_item` | true when a proposed/committed prerequisite exists | one quiz item recaps the prerequisite |
| `cards_min/max` | 4 / min(7, 2 + knowledge points) | card count window (also <= 1 per 100 words) |
| `exercises_max` | 3 for applied/advanced, else 1 | upper bound, zero is fine |

A budget is a **ceiling**: later stages may write fewer items when the units do not support
more, never more. Re-run `budget` after changing knowledge points, objectives, the learning
step or prerequisites; `check` warns (`groups.budget_stale`) when it is out of date.

## What `regroup.py check` verifies

| check_id | severity | what |
|---|---|---|
| `groups.unit_unassigned` / `groups.unit_multi_assigned` | error | kept unit primary in 0 / > 1 places |
| `groups.unknown_unit` / `groups.unknown_concept` / `groups.unit_not_kept` | error / error / warning | dangling or non-kept references |
| `groups.id`, `groups.area_id`, `groups.module_id`, `groups.subitem_id` | error | id formats, uniqueness, `#kp/#lo/#w` prefix = group id |
| `groups.area_ref`, `groups.module_ref`, `groups.kp_ref`, `groups.prereq_ref` | error | references resolve (module inside its area) |
| `groups.sort_order`, `groups.title`, `groups.title_duplicate` | error | unique order per parent; non-empty, unique titles |
| `groups.mass` | warning | token mass outside 300–8000 |
| `groups.knowledge_points`, `groups.objectives`, `groups.watch` | error | 3–5 / 2–4 / >= 2 |
| `groups.objective_verb`, `groups.objective_weak_verb`, `groups.objective_bloom`, `groups.objective_above_ceiling` | error | observable verb, no weak verb, valid Bloom, <= units' ceiling |
| `groups.watch_kind`, `groups.watch_text`, `groups.kp_title` | error | enums and non-empty text |
| `groups.generalization_missing` / `groups.generalization_concepts` | error | present; names >= 2 concepts |
| `groups.generalization_weak_verb` | warning | `is/are/has/have/affects/impacts/influences` as a standalone word |
| `groups.concepts_empty` / `groups.concepts_max` | error / warning | >= 1 concept; <= 5 |
| `groups.concept_uncovered` | warning | non-gap concept in no group |
| `groups.per_area`, `groups.areas_count`, `groups.level`, `groups.learning_step` | warning / warning / warning / error | 2–10 groups per area, 1–15 areas, enums |
| `groups.duplicates_prefer` | error | `prefer` not among the duplicate's units |
| `groups.prereq_self`, `groups.prereq_strength`, `groups.prereq_status` | error | edge shape |
| `groups.prereq_duplicate` | warning | same prerequisite listed twice |
| `groups.prereq_cycle` | error | cycle over committed (or, before critique, proposed) edges |
| `groups.prereq_fanin` | warning > 4, error >= 6 | prerequisites per lesson |
| `groups.prereq_roots`, `groups.prereq_bottleneck` | warning | < 3 roots at >= 10 lessons; one lesson gating > 85% |
| `groups.prereq_transitive` | info | edge implied by a longer path (left out of the reduction) |
| `groups.budget_stale`, `groups.contract_version`, `groups.shape`, `groups.missing_input` | warning / warning / error / error | housekeeping |

## What the critic checks (`contracts.CRITERIA["groups"]`)

The critic sees the group, its units' text and `concepts.json` — not your rationale.
Critical (all must pass): **one generalization** (knowledge points are facets, not topics),
**knowledge points distinct** (one twist each, no concept-spanning point), **objectives
observable** at a Bloom level the units support. Minor: title clear and unbranded, watch items
real and grounded, ordering follows dependencies rather than the source TOC. Every proposed
prerequisite edge gets a 3-way verdict with the units that show the dependency; `uncertain`
is legitimate, `either` means the lessons can be read in any order (edge rejected). Write the
group so a reader with only the units could reach the same conclusions.
