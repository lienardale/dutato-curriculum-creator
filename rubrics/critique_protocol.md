# Critique Protocol

Every authored stage of the lesson pipeline (qualification, groups, syntheses, lessons,
questions, cards) is checked by an **independent critic** before the next stage may start.
The critic's job is to find what is wrong, grounded in the sources — never to polish, never to
approve by default. The pass/fail decision is **computed by `critique.py check`**, not stated by
the critic.

## 1. Independence (structural, not promised)

- The critic is a **fresh sub-agent** that receives only the work file written by
  `critique.py prepare <stage>`: the artifact, exactly the source material it may consult
  (units or brief text), the stage's criteria, and a reference (answer key, brief, or anchor
  exemplar). It never sees the author's reasoning, transcript, `rationale`, `source_quote`
  or `probes` fields where the protocol says they are withheld.
- The critic never edits the artifact. It returns a verdict file; the **author** (or a
  separate fixer sub-agent) makes the repair.
- Critics answer **source-anchored questions** (is this claim supported by unit X? can the
  answer be derived from this page?) rather than holistic preferences. Where a holistic
  judgement is unavoidable, the criterion is binary and must cite an `evidence_span` copied
  verbatim from the artifact.
- The same model family may author and critique; independence comes from the different
  context and task, and from the script computing the result.

## 2. Reasoning before verdicts

For every criterion: first re-derive from the sources (what do the cited units actually say?
what answer does the page alone support?), then compare with the artifact, then write the
verdict. Write `failure_reason` as a falsifiable statement ("page 2 states X; unit u:… says Y")
and `correction_instruction` as an edit the author can make in one place, naming the unit to
ground it in — or "no unit covers this → delete".

## 3. Default to failure

- Uncertain → `passed: false`. A criterion passes only on positive evidence.
- For an undesirable criterion, `passed: true` means the defect is absent and `passed: false` means the defect is present; a `false` blocks the item like a failed critical criterion, so it needs a `failure_reason`, an actionable `correction_instruction` and, for a question or card, the verdict `rewrite` or `drop` (never `keep`), else the verdict itself is rejected as a verdict error.
- A claim with no cited unit that supports it is `not_supported`, even if it is true in the
  world. The pipeline's contract is *source-faithful*, not *true*.
- Never invent an easier criterion so the item can pass; never soften the rubric because the
  artifact is otherwise good.
- Ambiguous output, a missing criterion, or an unparseable verdict file is treated by
  `critique.py check` as **not passed**, never as passed.

## 4. Per-stage protocol

### qualification (blind re-tag)
Input: a 10% sample of units, at least 15 (text only). Task: assign `concepts` (from `concepts.json`),
`unit_type`, `keep` without seeing the author's tags. The script compares; disagreement >20%
on concepts or unit_type fails the source.

### groups
Input: `groups.json` entry + its units' text + `concepts.json`. Criteria in
`contracts.CRITERIA["groups"]`. Also judge **every proposed prerequisite edge** 3-way
(`confirmed | either | uncertain`) with a one-sentence reason and the unit ids that show the
dependency. `uncertain` is a legitimate answer; guessing is not.

### syntheses (factored fidelity)
Input: each brief item **with only the text of its cited units** (not the whole brief). Label
every item `supported | not_supported | irrelevant`. Then, with the full brief and the group's
knowledge points, list knowledge points no map item covers (recall) and watch items that merge
conflicting sources. The script computes precision and recall.

### lessons
Input: pages + brief + cited units. Decompose each page into claims (one per paragraph at
least), label each; judge mechanism depth, one-knowledge-point-per-page, examples where the
unit type demands them, standalone pages, objective alignment, coherence (flag paragraphs for
concept/step/causal omission, discontinuity, duplication, inconsistency, language), register.
Independently re-label each objective's Bloom level **before** reading the author's label.

### questions (answer-first)
Input per question: the page, cited units, the prompt and the marked answer/options. Withheld:
`rationale`, `source_quote`, `probes`, `key_points`, `bloom_level`, `distractors`, `explanation`,
`feedback`, `variants`. Protocol in order, fail-fast:
1. **Answer first**: derive the answer from the page/units before reading the key. For
   `check`/MCQ the derived answer must match the key.
2. understandable → topic-related → clear → **central** (tests the objective, not a literal)
   → independent Bloom re-label → `acceptance.causal_reason` names a *relationship* that could
   be worded several ways (fail if it is a phrase or a single term) → leading-wording check
   (would a learner infer the outcome from the stem?).
3. Verdict `keep | rewrite | drop`. A `rewrite` must change prompt **and** key together and
   keep `concept_id`/`lo_id`; it goes in `rewrite` and is only a suggestion to the author.
4. **Probe matrix**: the script then reveals `probes`; grade `correct_learner_answer` and
   `wrong_learner_answer` **only against `acceptance`, in any wording**. Correct must pass,
   wrong must fail; otherwise `q.probe_matrix_discriminating` fails.
5. **Test student** (once per lesson): read the prerequisite recaps, then the pages in order,
   answering each embedded question before reading on and listing every term you could not
   define from what you had read. Report counts in `test_student`.

### cards
Input per card: front, back, card_data, the page it cites and its units. Judge `card.correct`
(any false claim fails), `card.answerable_from_front_alone` (a learner who read the lesson can
produce the single intended answer), `card.single_filler` (list every plausible alternative for
cloze/discrimination), `card.tests_takeaway`.

## 5. Repair loop (enforced by `critique.py check`)

- At most **3 actionable items** per round, each naming the offending span and the unit (or
  brief item) to ground the fix in.
- The author edits only those spans; a repair whose diff exceeds half the item or touches
  unnamed paragraphs is rejected (`repair.oversized`, `repair.out_of_scope`).
- Each repair bumps `revision`; the next verdict carries `round` = the new revision.
- **At most 2 repair rounds**. A third failure marks the item `FAILED`; it is listed in
  `review.json` for the human and never silently passed.
- The author's checkpoint carries 1–3 `lessons_learned` lines across rounds; `prepare` prepends
  them to the next author work file.
- A round ≥ 1 critic receives `previous_round` (the failed criteria and actionable items of the
  last verdict). It judges first whether each named repair landed; a criterion that passed in
  the previous round fails again only if the repair introduced a new defect. Critics do not
  restart the review from scratch on every round, or nothing ever converges.

## 6. Calibration

- Planted-defect fixtures (`tests/fixtures/planted/`) must be caught by the deterministic gates;
  critic prompts are checked against the pilot lesson (the anchor) whenever a rubric changes.
- The pilot lesson is approved by a human before any batch run; its verdict file is the
  reference for `lessons`/`questions`/`cards` critics on the same curriculum.
