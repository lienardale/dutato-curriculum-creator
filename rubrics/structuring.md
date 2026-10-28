# Structuring Rubric — hierarchy and ordering

How the curriculum tree is shaped. In the lesson pipeline the tree is **not authored
directly**: `lessons.py build` assembles `structure.json` from `groups.json` (areas, modules,
groups and their committed prerequisite edges) and `lessons/<id>.json` (titles, descriptions,
objectives). The rules below are what you apply while writing `groups.json`
(`rubrics/regrouping.md`) and what `build` and `validate.py --stage structure` enforce.
Shapes: `docs/artifacts.md` (Stage 3 and Stage 5 "build").

## Hierarchy

| Depth | Node | Source | Count |
|---|---|---|---|
| 0 | **area** | `groups.json → areas[]` | 1–15 per curriculum (`groups.areas_count`) |
| 1 | **module** (optional) | `areas[].modules[]` | any; a lesson without a `module_id` sits directly under its area |
| 1 or 2 | **lesson** (leaf) | one per group, `lesson_id` = group id | 2–10 per area (`groups.per_area`) |

- Never deeper than 2 (`structure.invalid_depth`). Leaves never have children.
- A module exists only to group lessons; an area whose lessons all fit one sequence needs
  no modules. Mixing direct lessons and modules in one area is allowed; `build` places the
  direct lessons first, then the modules.
- Every group references an area that exists (`lessons.build.area_unknown`) and, when set, a
  module inside that area (`groups.module_ref`).

## Naming

- Titles are noun phrases a learner understands out of context ("Architecture Decision
  Records", "Bootstrapping a Cluster from Scratch"), or a question ("What Is Big-O
  Notation?").
- **No source branding** and no book structure: no author, book, site or channel names, no
  "Chapter 3", "Part II", "(Fowler)", "3.2.1". The learner sees a standalone curriculum.
- **Lesson titles are unique curriculum-wide** and must not collide with an area or module
  title: the uploader keys every topic by title (`lessons.title_duplicate`,
  `groups.title_duplicate`).
- Area and module titles name the theme, not the lesson list ("Recording Decisions", not
  "ADRs, templates and tooling").

## Ordering

`sort_order` is 0-based and unique per parent (`groups.sort_order`). `build` sorts areas by
their `sort_order` (then id), modules by theirs, and lessons by their group `sort_order`;
the `sort_order` written into `structure.json` is the resulting position.

Order by what builds on what, never by one source's table of contents:

1. **Committed prerequisite edges first.** A lesson comes after every lesson it requires;
   across areas, the area holding the prerequisites comes first. `validate.py` warns when a
   prerequisite has a higher `sort_order` than its dependent (`structure.prereq_order`).
2. **Then learning step**: foundation → building_block → applied → advanced.
3. **Then `concepts.ordering`** (the registry's foundation-to-advanced list) and, as a last
   tie-break, the order in which the sources introduce the concept.
4. Survey / orientation lessons open an area; synthesis and trade-off lessons close it.

Only **committed** edges (`prerequisites[].status == "committed"`, set by
`regroup.py commit-edges` from the critic's verdicts) reach `structure.json`; `uncertain` and
`rejected` edges do not order anything. `build` drops an edge whose prerequisite has no
built lesson (`lessons.build.prerequisite_unbuilt`) — write that lesson or remove the edge.
The transitive reduction (`prerequisite_edges_reduced`) is script-owned; do not prune implied
edges by hand.

## Levels

`suggested_level` 1–3 on every area and group:

| Level | Name | Content |
|---|---|---|
| 1 | Fundamentals | definitions, core concepts, basic examples |
| 2 | Intermediate | techniques, patterns, comparative analysis |
| 3 | Advanced | complex applications, trade-offs, synthesis |

- A group's level is set by its most advanced objective; an area's level is its own
  `suggested_level` or, when absent, the majority level of its groups (`build`).
- Depth-0 levels create `curriculum_levels` rows at upload and group areas into tiers in the
  app, so every area needs one (`structure.missing_level`, `structure.invalid_level`).
- Aim for roughly 30 % / 40 % / 30 % of lessons across levels; the distribution is a guideline
  in lesson mode, not a gate.

## What `build` copies into each leaf

`title`, `description` and `learning_objectives` (`{text, bloom_level}`) from the lesson file;
`prerequisites` (`{topic: <lesson title>, strength}`) from committed edges; `lesson_id`;
`source_sections: []` (always empty in lesson mode). Objectives follow
`rubrics/learning_objectives.md` and are gated at the group stage (observable verb, Bloom
level under the units' ceiling), not here.

## Raw pipeline (legacy)

A hand-authored `structure.json` with `source_sections` follows the same hierarchy, naming,
ordering and level rules, plus the section-mapping rules enforced by
`validate.py --stage structure` (`structure.stale_ref`, `structure.empty_leaf`,
`structure.orphan_section`): run `normalize_titles.py` first so no `source_sections` entry
carries a `Chapter N` / `Part I` prefix or a book-meta title, map every extracted section to
at least one leaf, and keep leaves under 10 sections. See the appendix of `AGENT.md`.
