# Questions Rubric — designing embedded questions, quiz items and exercises

You are designing the questions for one lesson. Input: `questions/work/<id>.json` (pages,
objectives, knowledge points, watch items, generalization, budget, concepts, brief items,
units, prerequisite recaps, `allowed_slots`, this rubric under `instructions`, `anchor` = the
approved pilot's questions once it exists, `existing`, `lessons_learned`). Output:
`questions/<id>.json` in the shape of docs/artifacts.md
"Stage 6". Then run `python questions.py check output/<name>/ --lesson <id>` and fix every
finding before the critic sees the file.

## 1. What a question is for

A question makes the learner **retrieve or apply** something the lesson taught, and shows
from the answer alone whether the knowledge point landed. Assume an **open book**: the
learner can scroll back to the page. A question answered by locating one sentence on the
page is therefore a defect, not an easy item — it measures reading comprehension. Ask
instead for the outcome of a situation the page did not literally describe, the reason
behind a rule, the feature that separates two confusables, or the step that follows a state.

One bounded outcome per question: one scenario, one thing to predict or explain, one `?`.
"What happens to X, and why?" is one outcome with its reason; "What is X? Why does it
matter?" is two questions (`questions.multiple_interrogatives`).

## 2. Embedded questions (`embedded[]`)

Kinds (`kind` is the viewer's `question_type`):

| kind | position | use when |
|---|---|---|
| `predict` | `before` the page | the page explains a mechanism or consequence — make the learner commit to a prediction first |
| `pre_question` | `before` the page | the page introduces a distinction — ask what the learner expects the difference to be |
| `check` | `after` the page | the page taught a rule or procedure — ask for its application to a case the page did not show |

Placement rules the viewer imposes (`sort_order` = page index):

- `before` questions are shown only on pages `1..N-1`; `after` questions only on `0..N-2`.
  `allowed_slots` in the work file lists exactly these indices (`--allow-edge-questions`
  widens both to `0..N-1` for a viewer that supports it).
- At most one question per position per page (`questions.slot_overflow`).
- Every lesson needs ≥ 1 reachable embedded question (`questions.none_reachable`);
  `embedded_questions_max` is a ceiling, not a quota. Two or three well-placed questions
  beat one per page.

## 3. The two-layer key

Every embedded and quiz item carries its key in two layers. Graders and the critic's probe
matrix use **layer 1 only**; layer 2 feeds feedback and review.

**Layer 1 — `acceptance`**: `{"prediction": …, "causal_reason": …}`, each a short sentence
that states a **relationship**, so any wording expressing that relationship passes:

- `prediction`: what happens / which option / what the answer is — "The old ADR stays and
  is marked superseded."
- `causal_reason`: *why*, written as "X happens because Y" or "Y, so X" — "The record exists
  to preserve the reasoning history, so deleting it destroys the context later readers need."

A `causal_reason` that is a term or a phrase ("history", "the superseded status") fails the
critic's `q.acceptance_is_relationship`: a learner could echo the phrase without
understanding it, and a learner who understands could word it differently and be marked
wrong. Both fields are required and ≤ 600 characters (`questions.acceptance`).

**Layer 2 — fuller text**: `expected_answer` (a model answer in 1–3 sentences), `rationale`
(why this question tests this objective), `key_points` (2–4 phrases a grader can tick).
Quiz items add `explanation` and `feedback {confirmatory, remedial_hint}`; the remedial hint
points at the mechanism, never at the answer.

## 4. Neutral prediction phrasing

A stem must not reveal the outcome it asks about. Name the situation, then ask what changes
and why — without presupposing the direction.

| bad (leading) | good (neutral) |
|---|---|
| "Why is the override unaffected by the default?" | "What changes when an override is present, and why?" |
| "Why must the old ADR be kept?" | "A team reverses a two-year-old decision. What happens to the old ADR, and why?" |
| "Does a superseded record stay in the repository?" | "A record is superseded. Where does it end up, and what would a reader lose otherwise?" |
| "Why does the cache make reads faster?" | "Reads now go through a cache. What happens to latency and to staleness, and why?" |

Yes/no stems are rejected for `predict` and `check` (`questions.yes_no_stem`). Stems that
copy ≥ 6 consecutive words of the `source_quote` are rejected (`questions.quote_copy`);
a `check` whose `expected_answer` appears verbatim on its page is flagged
(`questions.answer_on_page`); the keyed answer never appears in the stem
(`questions.answer_in_stem`). The concept's label or an alias appears in the prompt.

## 5. Quiz items (`quiz[]`)

Ceilings, not quotas: `budget.quiz_items` is the upper bound (+1 for the recap item). Cover
every objective with ≥ 1 item (`questions.objective_uncovered`) and span ≥ 3 Bloom levels
(`questions.bloom_span`). `apply`+ items need a causal cue ("why", "what happens",
"compare", …) and a concrete scenario (`questions.no_causal_cue`).

| type | shape | policy |
|---|---|---|
| `multiple_choice` | exactly 4 `options`, one `correct_index`, `distractors[]` with a `misconception` per wrong option | the default. Every distractor comes from a `watch` misconception (the work file's `distractor_seeds`) or a plausible confusion of the knowledge point — never a joke option. Keep options parallel in length and grammar. |
| `true_false` | `options: ["True", "False"]` | sparingly (≤ 1 per lesson), only for a rule learners actively get backwards; the statement avoids `not` / `never`. |
| `short_answer` | no options; `expected_answer` + `key_points` | mechanisms and "why" questions whose answer cannot be guessed from a list. |
| `code_output` | no options; `expected_answer` is the printed result | only when the lesson has code; the prompt contains the snippet. |

Item-writing flaws the checker counts (`questions.mcq_flaws`: 0–1 pass, ≥ 2 reject): key is
the longest option by a margin, "all/none of the above", absolute terms (always, never, all),
a negative stem, duplicate or near-identical options, a stem↔key word shared by no distractor.

Bloom → mastery (`contracts.BLOOM_TO_MASTERY`, enforced by `questions.mastery_level`):

| bloom_level | remember | understand | apply | analyze | evaluate | create |
|---|---|---|---|---|---|---|
| mastery_level | 1 | 2 | 3 | 3 | 4 | 4 |

**Recap item.** When `budget.recap_item` is true, add exactly one quiz item with
`recap_of: "<prerequisite lesson id>"` (a committed prerequisite listed in the work file's
`prerequisites`), `lo_id: null`, citing that lesson's units and concept. It re-tests the
prerequisite's generalization in this lesson's vocabulary, using the prerequisite's recap
page as source, and does not count toward objective coverage (`questions.recap_of`).

**Variants.** A variant re-asks the same knowledge point in a different scenario — new
nouns, new frame, same key relationship. Changing a number or a product name is a surface
rewrite and is rejected (`questions.variant_too_similar`). Variant ids are `<item id>v<n>`
and MCQ variants carry their own `options` / `correct_index`.

## 6. Exercises (`exercises[]`)

Up to `budget.exercises_max`. Each exercise has `title`, `problem_statement`, exactly
**3 progressive hints** (orientation → strategy → the step that unblocks;
`questions.exercise_hints`), `expected_solution`, `common_mistakes`, `difficulty` 1–3 and
`fading`. Fading across the lesson's exercises runs `worked` (a complete example the learner
studies and labels) → `completion` (a partial solution the learner finishes) →
`conventional` (the learner solves from the statement); the order is enforced
(`questions.fading_order`).

## 7. Probes

`probes.correct_learner_answer` and `probes.wrong_learner_answer` are **realistic** answers
in learner language: the correct one expresses the acceptance relationship in words other
than the key's; the wrong one is what a learner holding the matching `watch` misconception
would write. The critic blind-grades both against `acceptance` only — if the correct probe
fails or the wrong probe passes, `q.probe_matrix_discriminating` fails and the item returns.

## 8. What is checked

Deterministic (`questions.py check`, prefix `questions.`): `id_format`, `duplicate_id`,
`enum`, `position_kind`, `unknown_lo`, `unknown_concept`, `unknown_unit`, `page_index`,
`quote_missing`, `quote_mismatch`, `quote_copy`, `quote_overlap`, `unreachable`,
`slot_overflow`, `none_reachable`, `embedded_budget`, `prompt_length`, `not_a_question`,
`multiple_interrogatives`, `answer_in_stem`, `answer_on_page`, `concept_missing`,
`no_causal_cue`, `literal_recall`, `yes_no_stem`, `acceptance`, `probes`, `mcq_options`,
`correct_index`, `distractors_incomplete`, `mcq_flaws`, `mastery_level`, `extraneous_field`,
`objective_uncovered`, `bloom_span`, `quiz_budget`, `recap_of`, `variant_id`,
`variant_too_similar`, `exercise_hints`, `fading_order`, `difficulty`, `exercise_budget`.

Critic (`contracts.PER_ITEM_CRITERIA["questions"]`; protocol in rubrics/critique_protocol.md):
`q.answerable` (the answer can be derived from the page/units before reading the key),
`q.central`, `q.acceptance_is_relationship`, `q.not_leading`, `q.probe_matrix_discriminating`
— all critical; `q.understandable_clear`, `q.bloom_relabel_match` — minor;
`q.answer_leaks_on_page` — undesirable. The critic also runs a **test student** over the
lesson: it answers every embedded question before reading on and lists undefined terms.

## 9. Before you finish

- Re-read each stem as a learner who has not read the page: can the outcome be inferred from
  the stem alone? If yes, neutralize it (§4).
- Re-read each `causal_reason`: is it a relationship ("because", "so", "which means") or a
  label? Rewrite labels.
- Run `python questions.py check` and clear every error; clear warnings unless you can say
  in `lessons_learned` why they are false positives.
