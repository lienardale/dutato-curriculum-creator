# Lesson Design Rubric

How to turn a knowledge brief (`syntheses/<id>.json`) into a lesson (`lessons/<id>.json`).
Shapes live in `docs/artifacts.md` (Stage 5); thresholds in `shared/contracts.py`. A lesson is
read in the app one page at a time, each page a markdown screen; every page except the last is
followed by an embedded question. Write for that experience.

## The contract in numbers

| What | Target | Gate |
|---|---|---|
| Lesson length | 900–2400 words (4–10 min at ~238 wpm) | warning `lessons.words_over_target` above 2400; error outside 900–2800 |
| Pages | `budget.pages` = knowledge points + 3 (+ 1 with an example unit), clamped to 4–8; one core page per knowledge point | error if different |
| Orientation / recap page | 150–250 words | warning ±15 %, error beyond 2× |
| Core / worked-example page | 250–450 words | same |
| Pitfalls page | 150–450 words, 2–4 misconceptions | same; count error |
| New bold terms per page | ≤ 4, each defined within 150 characters of first use | error |
| Term density on core pages | 0.08–0.15 (tokens of terms introduced on the page / page tokens) | warning |
| Sentences | mean ≤ 22 words; none over 45 | warning / info |
| Paragraphs | ≤ 4 sentences | warning |
| Example | every page over 200 words has one (code fence or "for example", "consider", "suppose", "imagine", "in practice", "e.g.") | error |
| Verbatim copying | no copied run ≥ 8 words outside code and registry terms; 5-gram Jaccard ≤ 0.06; density ≤ 2.0 | error |
| Page furniture, dangling references, mid-sentence page starts | 0 | error |

Numbers are ceilings and bands, not quotas: a thin group yields a shorter lesson or is merged
(`regroup.py`), never padded.

## Page roles, in order

1. **orientation** — a hook (a concrete situation the learner recognises), why it matters, one
   prerequisite recalled in a sentence, the objectives in learner words ("by the end you can …"),
   and the line that every page ends with a question. No new terms beyond naming the concept.
2. **core** (one per knowledge point) — *definition → mechanism → example*. Name the term in
   bold once, define it, then explain **how and why** it works (cause → effect, condition →
   consequence), then one concrete example (and a non-example for kinds-of concepts). Terms are
   named before mechanisms (pre-training). One knowledge point per page; if a page needs two, the
   outline is wrong.
3. **worked_example** (when the brief has an `examples` entry) — walk through code or a scenario
   step by step; after every 2–3 steps add a self-explanation prompt ("Why does this step come
   before …?"). Code is reproduced from the cited unit in a fenced block with a language tag and is
   exempt from the verbatim gates; the prose around it is not.
4. **pitfalls** — 2–4 misconceptions from the group's `watch` items, each specific enough to
   predict a learner's wrong answer, each with one remediation sentence; when *not* to use the
   idea; the contrast with the neighbouring concept.
5. **recap** — the group's `generalization` **verbatim**, 3–5 takeaways, the key terms, one link
   back to a prerequisite lesson and one forward to a later lesson.

## Three chained passes

1. **Outline** (`--pass outline`): pages with `role`, `title`, `lo_ids`, `knowledge_point_id`,
   `unit_ids`, `brief_item_ids`, `markdown: null`. Every objective ↔ ≥ 1 page, every page ↔ ≥ 1
   objective, each core page exactly one knowledge point, no page supported only by `inference`
   items. Run `python lessons.py check output/<name>/ --outline` and fix before writing prose.
2. **Core + worked example**: write those pages from their brief items only.
3. **Orientation, pitfalls, recap**: write with the core pages in front of you so the hook,
   misconceptions and takeaways match what was actually taught.

## Writing rules

- **Source-faithful, never verbatim.** Every claim traces to a `brief_item_id` (and through it to
  units). Rephrase; never lift sentences. If the brief lacks something the page needs, go back to
  the brief (and its units) — do not invent.
- **Standalone pages.** Start with a full sentence; restate what the page depends on in a clause;
  no "as we saw above", "this chapter", "Figure 3-2" without an image placeholder.
- **Second person, direct register.** "You", "your service", present tense; short paragraphs.
- **Bold exactly once**: a key term is bold at its definition and plain afterwards.
- **Markdown**: `##` page heading, `###` sub-headings, GFM tables (≤ 4 columns), nested lists,
  fenced code with a language tag, images only as `![alt](image:<image_id>)` placeholders for
  ids listed in the work file.
- **Density correction**: when a page is over the term-density band, add an example or an
  elaboration between the terms rather than deleting content; when under, bold and define the
  knowledge point's term on that page.

## What the critic checks (`rubrics/critique_protocol.md`)

Fidelity per paragraph against brief + units (precision ≥ 0.95, recall ≥ 0.9), mechanism
explained rather than named, one knowledge point per page, examples where the unit type demands
them, standalone pages, objective alignment, ≤ 1 flagged paragraph per page for omission /
discontinuity / duplication / inconsistency, register, and an independent Bloom re-label of the
objectives. Two repair rounds at most; then the lesson is FAILED for the human.

## Example outline (7 pages: 3 knowledge points + an example unit)

```
0 orientation     "Why decisions get lost"            lo1,lo2   —     b1,b2
1 core            "What an ADR records"               lo1       kp1   b3,b4,b5
2 core            "The decision lifecycle"            lo2       kp2   b6,b7
3 core            "Superseding a decision"            lo2       kp3   b8,b9
4 worked_example  "Writing an ADR for a cache change" lo1       —     b3,ex1
5 pitfalls        "ADRs that nobody reads"            lo1,lo2   —     w1,w2,w3
6 recap           "Decisions you can revisit"         lo1,lo2   —     b1
```

Without an example unit the worked_example page is absent (6 pages for 3 knowledge points).
Only at the 8-page cap does a knowledge point ride on the worked_example or pitfalls page; the
work file lists it under `knowledge_points_on_optional_pages`.
