# Synthesis Rubric — writing a knowledge brief

You are writing the **brief** for one lesson group: the complete, source-faithful list of
what the lesson may teach. Lesson pages (Stage 5) are written from the brief alone; anything
you leave out cannot appear in the lesson, and anything you add without a source will be
caught. Input: the work file `syntheses/work/<id>.json` (group entry, units with full text,
concepts, neighbour titles, budget, this rubric, `lessons_learned`). Output:
`syntheses/<id>.json` in the shape of docs/artifacts.md "Stage 4". Then run
`python synthesize.py check output/<name>/ --group <id>` and fix every finding.

## 1. Read before writing

1. Read the group's `generalization`, `knowledge_points`, `objectives` and `watch` — the
   brief must give a page author everything needed to teach exactly those.
2. Read every unit in order. Units marked `duplicate_of_preferred: true` are near-copies:
   cite the preferred unit instead. `related` units are context; cite them when they add a
   fact the primary units lack.
3. Note the `neighbours`: do not re-teach a prerequisite; do not pre-empt the following lessons.

## 2. Items: molecular claims

Each item is **one claim, 4–40 words**, in your own words, that a page author can turn into a
paragraph. One idea per item — no "and also". Three kinds:

| kind | use for | example |
|---|---|---|
| `map` | definitions, relations, mechanism steps, procedures — what the lesson teaches as fact | "A service layer lists the operations a client may invoke and coordinates each response." |
| `watch` | misconceptions, boundaries, contrasts, failure modes, example seeds — what the pitfalls page needs | "Moving business rules into the service layer hollows the entities into an anemic domain model." |
| `inference` | a conclusion that joins two or more sourced facts but is stated by no unit | "Because services only coordinate while entities hold rules, a thin service layer and a rich domain model reinforce each other." |

Rules the checker enforces:

- ≤ 30 items; every objective (`lo_ids`) is covered by ≥ 1 `map` item; ≥ 2 `watch` items;
  `inference` items are ≤ 10% of all items and each cites ≥ 2 units.
- Each item carries `lo_ids` (objectives it serves), `knowledge_point_id` (or `null`) and
  `concept_ids`, all from this group.
- Aim to cite ≥ 70% of the group's kept units across items and examples; an uncited unit is
  either redundant (say so in a `watch`/`lessons_learned` note) or a gap in your brief.

## 3. Cite while writing

Write the claim, then immediately record `unit_ids` (**≤ 3**) — the units that literally
support it — and the `quote`:

- **Quote rule**: `quote` is a verbatim span (3–15 words) copied from **one of the cited
  units**; the checker matches it case- and whitespace-insensitively. For `map` and `watch`
  the quote is mandatory. For `inference` it may be `""`.
- Never cite a unit "for context"; cite only what supports the claim. If no unit supports
  it, the claim is an `inference` (if two units together imply it) or it does not belong.

## 4. Confidence

- `stated`: the lesson may assert this as fact — allowed only when the cited units come from
  **≥ 2 distinct sources**.
- `attributed`: a single source says it; pages must phrase it as "X describes…" or hedge.
  The checker warns on `stated` with one source.

## 5. Conflicts stay visible

When sources disagree (the group's `conflicts` list, or anything you notice), write a `watch`
item that names both positions and cites both units. Never pick a winner and never blend the
two into one `map` claim.

## 6. Code and examples

Code is **referenced, never copied**. For every code or example unit worth teaching add an
`examples` entry `{id: "<group>#ex<n>", unit_id, language, note}`; the page author embeds the
unit. Item text must not contain code, identifiers in backticks aside.

## 7. Terminology

List every term the lesson must define: `{term, definition, unit_id}`. The definition is
your prose (one sentence) and `unit_id` is the unit where the term is introduced. Terms you
list here are whitelisted by the verbatim gates (see §9), so name them precisely.

## 8. What the fidelity critic will do

An independent critic receives each item **with only the text of its cited units** and labels
it `supported | not_supported | irrelevant`. Precision must reach 0.95: an item is
`not_supported` when the cited units do not literally say it, even if it is true. Then, with
the whole brief, the critic lists knowledge points no `map` item covers (recall ≥ 0.90) and
`watch` items that silently merge conflicting sources. Repairs are limited to 3 named spans
per round and 2 rounds, so get the citations right the first time.

## 9. Verbatim gates (synthesize.py check)

The brief prose (overview + item texts + terminology definitions) is compared against the
union of every cited unit, after removing fenced/inline code and whitelisting concept labels,
aliases, `terminology[].term` and any span you put in double quotes:

- **fragment** (error): no copied run of **≥ 8 consecutive words**. Rephrase; do not shuffle
  a sentence's clauses.
- **jaccard5** (error): the 5-gram Jaccard similarity brief vs. units must be **≤ 0.03**.
- **density** (error): mean squared copied-run length per word must be **≤ 1.5** — many
  4–7-word copies fail even when no single run reaches 8.
- **ungrounded** (warning): copied-run coverage **< 0.02** on a brief over 40 words means the
  prose shares almost no wording with its citations — re-check that the cited units say it.

Write from understanding, not by editing the source sentence. Technical labels are free;
sentences are not.

## 10. Before you finish

- `python synthesize.py check output/<name>/ --group <id>` → 0 errors; read every warning.
- `overview`: two or three sentences a page author can open the lesson with.
- `revision`: 0 on first write; +1 on every repair. Carry forward `lessons_learned` (1–3
  lines on what the critic caught) so the next brief starts smarter.
