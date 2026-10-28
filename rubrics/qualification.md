# Qualification Rubric

How to annotate knowledge units (`qualification/<source>.json`) after `qualify.py prepare`
has segmented a source into units (`units/<source>.json`). One sub-agent per source; write the
file incrementally. Shapes: `docs/artifacts.md` (Stage 2); enums: `shared/contracts.py`.

## Per unit

- **`concepts`** — the registry concepts the unit teaches, most central first, each with a
  verbatim `quote` (copied exactly from the unit's `text`; the validator substring-matches it).
  One to three concepts; a unit that teaches nothing registry-worthy gets `[]` and `keep: false`.
- **`unit_type`** — what the unit *does*:
  `definition` (names and bounds a concept) · `mechanism` (explains how/why it works) ·
  `procedure` (steps to do something) · `example` (a concrete case) · `code` (a code block) ·
  `pitfall` (a mistake or failure mode) · `trade_off` (costs vs benefits) · `comparison` (X vs Y) ·
  `context` (history, motivation, scope) · `reference` (tables, option lists, bibliographies) ·
  `noise` (furniture, promos, navigation).
- **`knowledge_level`** — `fact` (a statement to remember) · `concept` (a category or idea to
  classify with) · `procedure` (a skill to apply) · `principle` (a rule with a rationale, to judge
  with). This selects the Bloom ceiling and later the card types.
- **`learning_step`** — `foundation` (needs nothing else in this curriculum) · `building_block` ·
  `applied` (combines building blocks) · `advanced` (needs mastery of applied material).
- **`bloom_ceiling`** — defaults by knowledge level (fact→understand, concept→analyze,
  procedure→apply, principle→evaluate). Override only with `bloom_ceiling_reason`.
- **`quality`** 1–5 — 5 = clear, complete, self-contained; 1 = fragmentary or garbled.
- **`keep` / `drop_reason`** — drop `meta` (preface, index, acknowledgements), `duplicate_of:<unit>`
  (same content as a kept unit, keep the clearer one), `off_topic`, `noise`. `reference` and
  `noise` units are normally dropped.
- **`depends_on`** — registry concepts a reader must already know to understand this unit.

## Concept granularity

A concept is something a learner can be *asked about*: "Service Layer", "Idempotent consumer",
"Consistent hashing". Too fine ("left subtree", a parameter name) and too broad ("software
architecture", "best practices") are both wrong. When unsure, prefer the level at which the
sources themselves name the idea. Reuse registry ids; create a new one only when no existing
label or alias fits, then add it to `concepts.json` in the same pass.

## Gleaning

After finishing a section, answer `gleaning[<section_index>].missed`: "Did this section contain
a teachable idea that none of its units carries a concept for?" If yes, re-read the section once,
annotate what was missed, and record a `note`.

## What the blind re-tag critique checks

A critic re-tags a 10 % sample of units without seeing your tags; more than 20 % disagreement on
`concepts` or `unit_type` fails the source. Be consistent: the same idea gets the same concept id
in every source.
