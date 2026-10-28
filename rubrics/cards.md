# Cards Rubric — designing spaced-repetition cards

You are designing the review cards for one lesson. Input: `cards/work/<id>.json` (pages,
objectives, knowledge points, generalization, terminology and map items from the brief,
budget, knowledge-level counts with suggested card types, the lens checklist, the lesson's
question prompts, a do-not-duplicate list from neighbouring lessons, `existing`,
`lessons_learned`, this rubric under `instructions`). Output: `cards/<id>.json` in the shape
of docs/artifacts.md "Stage 7". Then run `python cards.py check output/<name>/ --lesson <id>`
and fix every finding before the critic sees the file.

## 1. Minimum information, one proposition

A card is reviewed many times over years; every extra word costs on every review. Each card
tests **one proposition** — one fact, one relationship, one step, one distinction — and has
**one intended answer** that a learner who read the lesson can produce from the front alone.

- Split "X and Y" into two cards. A front that joins two verb phrases is flagged
  (`cards.compound_front`).
- The front names its subject: "Why this matters?" is unanswerable out of context
  (`cards.pronoun_front`).
- No yes/no fronts (`cards.yes_no_front`) and no "give an example" fronts
  (`cards.give_example_front`): both admit many right answers.
- The back never appears on the front (`cards.answer_on_front`), and the front shares
  ≤ 50% of the back's content words (`cards.overlap`) — a front that restates the back is
  a reading check, not a retrieval cue.
- Cards test takeaways and relationships, not wording or trivia (the critic's
  `card.tests_takeaway`). A card is not a question re-asked: the work file lists the
  lesson's `question_prompts` so you do not restate them.

## 2. Card type by knowledge level

The work file counts the lesson's units by `knowledge_level` and suggests types
(`contracts.CARD_TYPES_BY_KNOWLEDGE`; first = primary):

| knowledge level | preferred types |
|---|---|
| fact | `concept`, `cloze` |
| concept | `discrimination`, `comparison`, `concept` |
| procedure | `application`, `code`, `ordering` |
| principle | `concept`, `application` |

Use ≥ 3 different types per lesson (`cards.types`). Stay within the budget:
`cards_min` ≤ count ≤ `cards_ceiling`, where the ceiling is `cards_max` capped at one card
per 100 lesson words (`cards.count`). Ceilings are not quotas — four sharp cards beat seven.

## 3. The five lenses

At least two cards view the lesson's **generalization** through different lenses (`lens`
differs; `cards.generalization_angles` warns otherwise). Walk the `lens_checklist`:

| lens | ask |
|---|---|
| `attributes` | What defines it? Which defining attribute is most often dropped? |
| `similarities_differences` | What is it confused with, and which single feature separates them? |
| `parts_wholes` | What are its parts, or what larger whole is it part of? |
| `causes_effects` | What brings it about, and what changes when it is present or missing? |
| `significance` | Why does it matter — what breaks or becomes possible because of it? |

`recall` (facts) and `procedure` (steps) are the lenses for cards that are not about the
generalization itself. Every card carries exactly one `lens` from `contracts.CARD_LENSES`.

## 4. Shapes, caps and `card_data`

Front ≤ 25 words except `code` (`cards.front_length`). Back caps (`cards.back_length`):

| type | back ≤ | shape | `card_data` |
|---|---|---|---|
| `concept` | 15 | a question about one attribute, part, cause or significance; back is a noun phrase or one short sentence | `{}` |
| `application` | 25 | a one- or two-sentence scenario, then "what happens / which applies"; back names the outcome and its one-line reason | `{}` |
| `comparison` | 25 | front names **both** terms ("X vs Y", "X and Y", "between X and Y"; `cards.comparison_terms`); back states the separating feature | `{}` |
| `code` | 40 | front holds a short fenced snippet and asks what it prints / returns or which line is wrong; back is the result | `{}` |
| `cloze` | 15 | one sentence with exactly one `___` (`cards.cloze_blank`), under 25 words (`cards.cloze_length`) | `{"blank_answer": "…", "hint": "…"}` (hint optional) |
| `ordering` | 40 | front names the sequence; back is the correct order as text ("a → b → c") | `{"items": [...], "correct_order": [...]}` |
| `discrimination` | 25 | "Which of these …" over 3–4 options; back is the correct option's text | `{"options": [...], "correct_index": n}` |

Common fields: `id` (`<lesson id>#k<n>`), `card_type`, `front`, `back`, `card_data`,
`lo_id` (one of the lesson's objectives), `unit_ids` (units the fact rests on),
`page_index` (the page that teaches it), `source_reference` (source key or short title shown
to the learner), `lens`.

### Cloze rules

- Blank the **concept**, not a function word: `blank_answer` is never a stopword
  (`cards.cloze_answer`) and never a proper name the learner cannot infer from the sentence.
- The sentence must determine the blank: if two words fit, add the discriminating condition
  to the sentence or choose another card type (the critic's `card.single_filler`).
- `hint` is optional, ≤ 6 words, and shares no 5-letter stem with the answer
  (`cards.cloze_hint`): "status word" for "superseded", never "super…".
- `back` equals `blank_answer`.

### Ordering rules

- 2–5 `items`, listed **shuffled**; `correct_order` is the permutation of item indices that
  sorts them (`cards.ordering_items`, `cards.ordering_permutation`; items already in order
  are flagged `cards.ordering_unshuffled`).
- Long sequences: never a nine-step card. Cut the sequence into overlapping windows of ≤ 5
  consecutive steps (1–5, 4–8, …), one card per window, and name the window on the front
  ("From receiving the request to writing the log, order these steps").

### Discrimination and comparison rules

- A discrimination card's options are the real confusables of the knowledge point — the
  `watch` misconceptions, sibling terms, adjacent lifecycle states — never random nouns, and
  exactly one option is defensible.
- A comparison card names both confusables on the front and asks for the one feature that
  separates them; the back states that feature, not two definitions.

## 5. Interference and duplication

Cards that look alike at review time interfere: the learner recalls the wrong one. Within
the lesson and against the neighbouring lessons' fronts in `do_not_duplicate` (accepted
prerequisite edges in either direction plus the same module):

- near-identical fronts (`SequenceMatcher` ≥ 0.85) are rejected (`cards.duplicate`);
  0.75–0.85 is flagged (`cards.near_duplicate`) — reword or merge;
- no two cards share a back or answer (`cards.shared_answer`): if two fronts lead to the
  same answer, one of them is redundant.

Do not re-card a prerequisite lesson's content; name it in the front when the card needs it.

## 6. Coverage

- Every objective has ≥ 1 card (`cards.objective_uncovered`).
- Every knowledge point the pages teach has a card unless the lesson's questions already
  drill it; prefer the knowledge points a learner is most likely to lose.
- `page_index` points at the page that teaches the proposition and `unit_ids` at the units
  it rests on; the critic checks `card.correct` against those units.

## 7. What is checked

Deterministic (`cards.py check`, prefix `cards.`): `id_format`, `duplicate_id`, `enum`,
`unknown_lo`, `unknown_unit`, `page_index`, `missing_field`, `count`, `types`,
`generalization_angles`, `objective_uncovered`, `front_length`, `back_length`,
`compound_front`, `pronoun_front`, `yes_no_front`, `give_example_front`, `overlap`,
`answer_on_front`, `shared_answer`, `cloze_blank`, `cloze_length`, `cloze_answer`,
`cloze_hint`, `ordering_items`, `ordering_permutation`, `ordering_unshuffled`,
`discrimination_options`, `correct_index`, `comparison_terms`, `duplicate`, `near_duplicate`.

Critic (`contracts.PER_ITEM_CRITERIA["cards"]`): `card.correct` (every claim true per the
cited units), `card.answerable_from_front_alone`, `card.single_filler` (the critic lists
every plausible alternative for cloze and discrimination cards) — all critical — and
`card.tests_takeaway` (minor). Verdicts are `keep | drop`; a dropped card is rewritten by
you, never by the critic.
