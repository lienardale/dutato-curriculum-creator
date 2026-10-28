# Lesson pipeline — artifact contract

This is the single source of truth for every checkpoint file the **lesson** pipeline
(`manifest.pipeline = "lesson"`) reads or writes. `shared/contracts.py` holds the same
enums, id formats and thresholds in code; change both together. The legacy **raw** pipeline
(`structure.json` with `source_sections` → `chunk_bridge.py`) is unchanged and documented in
`AGENT.md`.

Conventions:

- Scripts are pure data utilities (no AI calls). Stages marked **authored** are written by the
  operating agent or its sub-agents; stages marked **script** are produced by a command.
- Every authored file carries `"contract_version": 1`.
- Every claim-bearing artifact cites `unit_ids` and, where it states a fact, a verbatim `quote`
  that `validate.py` checks as a whitespace/case-insensitive substring of one cited unit.
  A dangling id or a quote that does not match is an **error**.
- Per-item stages store one file per item: `<stage_dir>/<item_file>.json` where
  `item_file = item_id.replace(":", "_").replace("#", "__")` (so `g:event-sourcing` →
  `g_event-sourcing.json`). `prepare` commands write work files to `<stage_dir>/work/`.
- All thresholds below are defaults from `shared/contracts.py`.

## Directory layout

```
output/<name>/
  manifest.json                     authored   (+ "pipeline": "lesson", optional "audience", "scope")
  extracted/*.json, extracted/images/          extractors
  scope.json                        authored or `qualify.py scope-from-structure` (optional)
  units/<source>.json, units/index.json         script   qualify.py prepare
  qualification/<source>.json       authored   (one per units file)
  concepts.json                     authored
  groups.draft.json                 script     regroup.py draft
  groups.json                       authored   (+ script-owned fields: budget, prerequisites[].status)
  syntheses/work/<id>.json          script     synthesize.py prepare
  syntheses/<id>.json               authored
  lessons/work/<id>.json            script     lessons.py prepare
  lessons/<id>.json                 authored   (outline pass, then full pages)
  lesson_metrics.json               script     lessons.py check
  structure.json, chunks.json       script     lessons.py build   (same shapes as the raw pipeline)
  questions/work/<id>.json          script     questions.py prepare
  questions/<id>.json               authored
  cards/work/<id>.json              script     cards.py prepare
  cards/<id>.json                   authored
  critique/<stage>/work/<id>.json   script     critique.py prepare <stage>
  critique/<stage>/<id>.json        authored by an INDEPENDENT critic sub-agent
  critique/summary.json             script     critique.py check <stage>
  validation_report.json            script     validate.py --json
  review.json                       authored
  upload_result.json                script     upload.py
```

## Identifiers

| Kind | Format | Example |
|---|---|---|
| source key | extracted filename without `.json` | `peaa`, `martinfowler.com_bliki_CQRS.html` |
| unit | `u:<source>:<section_index>:<n>` | `u:peaa:12:3` |
| concept | `c:<slug>` | `c:service-layer` |
| area / module | `a:<slug>` / `m:<slug>` | `a:architecture-leadership` |
| group = lesson | `g:<slug>` (one lesson per group, same id) | `g:architecture-decision-records` |
| sub-items | `<group_id>#<kind><n>` with kind ∈ `lo` objective, `kp` knowledge point, `w` watch, `b` brief item, `q` question, `k` card, `e` exercise, `ex` example; variants add `v<n>` | `g:adr#lo1`, `g:adr#q7v1` |

Slugs: lowercase ASCII, hyphen-separated, ≤48 chars (`contracts.slugify`). Lesson **titles must
be unique curriculum-wide** (the uploader keys topics by title).

## Stage 2 (script) — `qualify.py prepare output/<name>/ [--scope scope.json]`

Reads `extracted/*.json`, cleans text (`shared/clean_text.py`: page numbers, running headers
recurring on ≥3 pages, watermarks, `N | Chapter` footers, footnote markers, de-hyphenation,
ligatures, hard-wrap rejoin) and segments each section into **knowledge units**.

Segmentation: typed blocks are atomic — fenced/indented code (`code`), markdown/pipe tables
(`table`), bullet/numbered lists (`list`), figure captions (`figure_caption`, lines matching
`^(Figure|Fig\.|Table)\s+\d`). Prose is grouped at blank-line boundaries up to
`UNIT_MAX_TOKENS` (400); a `#` heading always starts a new unit; a trailing prose unit under
`UNIT_MIN_TOKENS` (40) merges into the previous prose unit. Images whose offset falls inside the
unit's span are attached.

`units/<source>.json`:

```json
{
  "contract_version": 1,
  "source": "peaa", "source_file": "peaa.json", "source_type": "pdf", "title": "Patterns of Enterprise Application Architecture",
  "scope_applied": true,
  "units": [
    {"id": "u:peaa:12:3", "section_index": 12, "section_title": "Service Layer",
     "heading_path": ["Domain Logic Patterns", "Service Layer"],
     "block_type": "prose", "text": "cleaned text …", "token_count": 231,
     "raw_span": [10234, 11871], "page": 142,
     "images": [{"id": "p142_img0", "local_path": "images/p142_img0.png", "alt_text": "", "mime_type": "image/png"}],
     "cleaning": {"removed_lines": 3, "dehyphenated": 2}}
  ]
}
```

`units/index.json`: `{"contract_version": 1, "sources": {"peaa": {"units": 412, "tokens": 98000, "sections": 60, "skipped_sections": 4}}, "total_units": n, "total_tokens": n, "scope": {...} | null}`.

`scope.json` (optional): `{"include_sources": ["peaa", ...] | null, "include_sections": {"peaa": ["Service Layer", ...]}}`.
Section titles match after `normalize_ws` + lowercase. `qualify.py scope-from-structure
output/<name>/legacy/structure.json --area "<depth-0 title>" [--area ...] -o output/<name>/scope.json`
builds a scope from a legacy structure's `source_sections` (resolving titles to the extracted
files that contain them).

## Stage 2 (authored) — `qualification/<source>.json`

One sub-agent per source. Written incrementally (the map may be partial between runs).

```json
{
  "contract_version": 1, "source": "peaa",
  "units": {
    "u:peaa:12:3": {
      "concepts": [{"concept_id": "c:service-layer", "quote": "A Service Layer defines an application's boundary"}],
      "unit_type": "definition",
      "knowledge_level": "concept",
      "learning_step": "building_block",
      "bloom_ceiling": "analyze", "bloom_ceiling_reason": null,
      "quality": 4,
      "keep": true, "drop_reason": null,
      "depends_on": ["c:domain-model"],
      "notes": ""
    }
  },
  "gleaning": {"12": {"missed": false, "note": ""}}
}
```

Rules: every unit id of the units file appears; `quote` is verbatim from that unit; `drop_reason`
∈ `meta | duplicate_of:<unit_id> | off_topic | noise` when `keep` is false; `bloom_ceiling`
defaults by knowledge level (fact→understand, concept→analyze, procedure→apply,
principle→evaluate) and any override needs `bloom_ceiling_reason`; `noise`/`reference`
units are normally `keep: false`. `gleaning` has one entry per section index.

## Stage 2 (authored) — `concepts.json`

```json
{
  "contract_version": 1,
  "concepts": [
    {"id": "c:service-layer", "label": "Service Layer", "description": "Application boundary that coordinates domain operations and transactions.",
     "aliases": ["application service layer"], "learning_step": "building_block",
     "depends_on": ["c:domain-model"], "coverage": "strong", "sources": ["peaa", "clean-architecture"]}
  ],
  "gaps": [{"concept": "Fitness functions", "severity": "recommended", "suggestion": "Add Building Evolutionary Architectures", "resolution": "user_accepted"}],
  "ordering": ["c:domain-model", "c:service-layer"],
  "merges": [{"kept": "c:service-layer", "merged": ["c:application-service"], "reason": "same pattern, PEAA vs Clean Architecture naming"}]
}
```

`qualify.py check output/<name>/` validates both authored files against the units (ids, quotes,
enums, DAG on `depends_on`) and reports: units per concept, thin concepts (<2 units), merge
candidates (label `SequenceMatcher ≥ 0.85`), too-broad labels (stoplist or >40% of units),
unit duplicates across sources (`SequenceMatcher ≥ 0.85` → `alternate_of`, 0.75–0.85 flagged),
drop statistics. Output: findings + `units/dedup.json` (`[{"unit_id", "alternate_of", "ratio"}]`).

## Stage 3 (script) — `regroup.py draft output/<name>/` → `groups.draft.json`

Mechanical candidates: one group per concept (units whose **first** concept is it), ordered by
`learning_step` then `concepts.ordering`; `related_unit_ids` = units tagging it as a secondary
concept; `suggested_merges` (source mass <300 tokens or <3 kept units), `suggested_splits`
(>8000 tokens, >1 `definition` unit for distinct concepts, >1 `procedure`);
`prerequisite_candidates`: `[{"a": "c:x", "b": "c:y", "evidence": ["co_occurrence", "source_order", "label_in_description"], "unit_ids": [...]}]`.

## Stage 3 (authored) — `groups.json`

```json
{
  "contract_version": 1,
  "areas": [{"id": "a:architecture-leadership", "title": "Architecture Leadership and Decision-Making", "sort_order": 0, "suggested_level": 2,
             "modules": [{"id": "m:decision-records", "title": "Recording Decisions", "sort_order": 0}]}],
  "groups": [
    {"id": "g:architecture-decision-records", "title": "Architecture Decision Records",
     "area_id": "a:architecture-leadership", "module_id": "m:decision-records", "sort_order": 1,
     "concept_ids": ["c:adr", "c:decision-context"], "unit_ids": ["u:adr.github.io:0:1", "u:building-microservices:40:2"],
     "related_unit_ids": ["u:cognitect.com_blog_2011_11_15_documenting-architecture-decisions:0:3"],
     "learning_step": "applied", "suggested_level": 2,
     "generalization": "Recording the context and consequences of each decision lets a team revisit choices without relitigating them.",
     "knowledge_points": [{"id": "g:architecture-decision-records#kp1", "title": "Why decisions get lost", "unit_ids": ["…"]},
                          {"id": "g:architecture-decision-records#kp2", "title": "The ADR template", "unit_ids": ["…"]},
                          {"id": "g:architecture-decision-records#kp3", "title": "Lifecycle: proposed, accepted, superseded", "unit_ids": ["…"]}],
     "objectives": [{"id": "g:architecture-decision-records#lo1", "text": "Write an ADR for a given decision with context, decision and consequences",
                     "bloom_level": "apply", "concept_ids": ["c:adr"], "unit_ids": ["…"], "knowledge_point_ids": ["g:architecture-decision-records#kp2"]}],
     "watch": [{"id": "g:architecture-decision-records#w1", "kind": "misconception", "text": "ADRs are documentation of the system, not of decisions", "unit_ids": ["…"]}],
     "duplicates": [{"unit_ids": ["u:a", "u:b"], "prefer": "u:a", "reason": "same template, clearer wording"}],
     "conflicts": [{"unit_ids": ["u:a", "u:c"], "note": "Nygard keeps superseded ADRs; Building Microservices suggests pruning"}],
     "prerequisites": [{"group_id": "g:what-architects-do", "strength": "recommended", "evidence_unit_ids": ["…"],
                        "reason": "assumes the reader knows the architect's accountability", "status": "proposed"}],
     "consequential": false, "rationale": "…",
     "budget": null
    }
  ],
  "dropped": [{"unit_id": "u:peaa:0:0", "reason": "meta: preface"}]
}
```

Script-owned fields (written by scripts, never by the agent): `budget` (`regroup.py budget`),
`prerequisites[].status` (`regroup.py check` after the prerequisite critique:
`committed | uncertain | rejected`). `budget` shape:
`{"pages", "embedded_questions_min", "embedded_questions_max", "quiz_items", "quiz_min_bloom_levels", "recap_item", "cards_min", "cards_max", "exercises_max"}` from `contracts.budget_for`.

`regroup.py check` gates: every kept unit is primary in exactly one group or in `dropped`;
group tokens 300–8000 (warn); 3–5 knowledge points; 2–4 objectives with observable verbs and
`bloom_level ≤` ceiling of their units; ≤5 concepts; ≥2 watch; generalization has ≥2 concept
labels and no weak verb; every non-gap concept appears in a group; DAG hygiene on **committed**
edges (0 cycles → error, fan-in ≤4 warn / ≥6 error, ≥3 roots when ≥10 groups, bottleneck >85%
warn), transitive reduction written to `groups.json` top-level `prerequisite_edges_reduced`.

## Stage 4 — `synthesize.py prepare|check` → `syntheses/<id>.json`

Work file: the group, its units' full cleaned text (primary then related), concept entries,
neighbour titles, budget. Authored brief:

```json
{
  "contract_version": 1, "group_id": "g:architecture-decision-records", "revision": 0,
  "overview": "Two or three sentences.",
  "items": [
    {"id": "g:architecture-decision-records#b1", "kind": "map", "text": "An ADR records one decision with its context, the decision and its consequences.",
     "unit_ids": ["u:adr.github.io:0:1"], "quote": "context, decision, and consequences", "confidence": "stated",
     "concept_ids": ["c:adr"], "lo_ids": ["g:architecture-decision-records#lo1"], "knowledge_point_id": "g:architecture-decision-records#kp2"}
  ],
  "examples": [{"id": "g:architecture-decision-records#ex1", "unit_id": "u:…", "language": "markdown", "note": "Nygard's template"}],
  "terminology": [{"term": "superseded", "definition": "…", "unit_id": "u:…"}],
  "lessons_learned": []
}
```

Rules: `map` = definitions, relations, mechanism steps; `watch` = misconceptions, boundaries,
contrasts, failure modes, example seeds; `inference` = conclusion joining ≥2 sourced facts
(`unit_ids` ≥2, `quote` may be empty, ≤10% of items, never citable by a page as fact). ≤30
items, ≤3 citations each, text 4–40 words. Code is referenced through `examples`, never copied
into `items`.

`synthesize.py check`: ids in-group, quotes match, coverage of kept units ≥70% (warn), every
objective has ≥1 map item, ≥2 watch items, **verbatim gates** on the brief text vs the union of
cited units (`shared/verbatim.py`: no non-whitelisted copied fragment ≥8 words, 5-gram Jaccard
≤0.03, density ≤1.5, coverage <0.02 → "ungrounded" warning). Whitelist = concept labels and
aliases, code identifiers, `terminology[].term`, quoted definitions.

## Stage 5 — `lessons.py prepare|check [--outline]|build` → `lessons/<id>.json`

```json
{
  "contract_version": 1, "lesson_id": "g:architecture-decision-records", "group_id": "g:architecture-decision-records", "revision": 1,
  "title": "Architecture Decision Records", "description": "One line.",
  "area_id": "a:architecture-leadership", "module_id": "m:decision-records",
  "suggested_level": 2, "estimated_minutes": 8,
  "key_terms": ["ADR", "superseded"],
  "objectives": [{"id": "g:architecture-decision-records#lo1", "text": "…", "bloom_level": "apply"}],
  "prerequisites": ["g:what-architects-do"],
  "pages": [
    {"index": 0, "role": "orientation", "title": "Why decisions get lost", "lo_ids": ["…#lo1"], "knowledge_point_id": null,
     "unit_ids": ["…"], "brief_item_ids": ["…#b1"], "markdown": "## Why decisions get lost\n\n…"},
    {"index": 1, "role": "core", "title": "…", "lo_ids": ["…"], "knowledge_point_id": "…#kp1", "unit_ids": ["…"], "brief_item_ids": ["…"], "markdown": "…"},
    {"index": 2, "role": "worked_example", "…": "…"},
    {"index": 3, "role": "pitfalls", "…": "…"},
    {"index": 4, "role": "recap", "…": "…"}
  ],
  "outline_approved": true,
  "lessons_learned": []
}
```

Outline pass: same file with `markdown: null` on every page; `lessons.py check --outline`
validates roles/order (orientation first, recap last, core before worked_example, pitfalls after
core), page count = budget, every page ↔ ≥1 objective, every objective ↔ ≥1 page, each core page
exactly one `knowledge_point_id`, no `brief_item_id` of kind `inference` cited as the only
support of a page.

Full pass gates (`lessons.py check` → `lesson_metrics.json` keyed by lesson id): lesson
900–2400 words; page words by role (orientation/recap 150–250, core/worked_example 250–450,
pitfalls 150–450; ±15% warn, beyond error); ≤4 new bold terms per page; core-page term density
0.08–0.15; undefined bold terms ≤2% of tokens (defined within 150 chars, on an earlier page, or
in a prerequisite lesson's recap); mean sentence ≤22 words (flag >45); ≤4 sentences per
paragraph; an example marker (fenced code, "for example", "consider", "suppose", "imagine",
"in practice", "e.g.") on every page >200 words; verbatim gates per page vs cited units (fragment
≥8 words fails, 5-gram Jaccard ≤0.06, density ≤2.0); 0 page-furniture matches; 0 dangling
references (a `Figure N-M` mention counts as dangling unless the page has an image placeholder);
0 mid-sentence page starts (first char uppercase or `#`, previous page ends with `.!?:` or a
code fence); every `![alt](image:<id>)` resolves to a unit image of this lesson; pitfalls page
has 2–4 misconception items; recap contains the generalization verbatim.

`lessons.py build` → `structure.json` (areas → modules → lessons; leaves carry `lesson_id`,
`sort_order`, `description`, `suggested_level` on depth-0, `learning_objectives
[{text, bloom_level}]`, `prerequisites [{topic: <lesson title>, strength}]` from committed
edges, `source_sections: []`) and `chunks.json` (one chunk per page: `topic_path`,
`chunk_index` = page index, `content` = page markdown, `token_count`, `has_code`,
`page_number: null`, `images: [{id, local_path, alt_text, mime_type}]` for placeholders used on
the page, `metadata: {lesson_id, page_role, lo_ids, source_units, key_terms, contract_version}`).
Lesson-mode chunk token gate: warn outside 150–900, error outside 50–2500.

## Stage 6 — `questions.py prepare|check` → `questions/<id>.json`

```json
{
  "contract_version": 1, "lesson_id": "g:architecture-decision-records", "revision": 0,
  "embedded": [
    {"id": "…#q1", "kind": "check", "page_index": 1, "position": "after",
     "lo_id": "…#lo1", "concept_id": "c:adr", "unit_ids": ["…"], "source_quote": "…",
     "prompt": "A team wants to change a decision recorded two years ago. What should happen to the old ADR, and why?",
     "acceptance": {"prediction": "The old ADR stays and is marked superseded by the new one", "causal_reason": "the record exists to preserve the reasoning history, so deleting it destroys the context later readers need"},
     "expected_answer": "…", "rationale": "…", "key_points": ["…"], "bloom_level": "understand",
     "probes": {"correct_learner_answer": "…", "wrong_learner_answer": "…"}}
  ],
  "quiz": [
    {"id": "…#q7", "type": "multiple_choice", "lo_id": "…", "concept_id": "…", "unit_ids": ["…"], "page_index": 2, "source_quote": "…",
     "prompt": "…", "options": ["…", "…", "…", "…"], "correct_index": 2,
     "distractors": [{"index": 0, "misconception": "…"}, {"index": 1, "misconception": "…"}, {"index": 3, "misconception": "…"}],
     "acceptance": {"prediction": "…", "causal_reason": "…"}, "expected_answer": "…", "rationale": "…", "key_points": ["…"],
     "explanation": "…", "bloom_level": "apply", "mastery_level": 3,
     "feedback": {"confirmatory": "…", "remedial_hint": "…"},
     "probes": {"correct_learner_answer": "…", "wrong_learner_answer": "…"},
     "variants": [{"id": "…#q7v1", "prompt": "…", "options": ["…"], "correct_index": 1}],
     "recap_of": null}
  ],
  "exercises": [
    {"id": "…#e1", "title": "…", "problem_statement": "…", "hints": ["…", "…", "…"], "expected_solution": "…",
     "common_mistakes": ["…", "…"], "bloom_level": "apply", "difficulty": 2, "fading": "completion", "lo_id": "…", "unit_ids": ["…"]}
  ],
  "lessons_learned": []
}
```

Embedded placement (default, today's viewer): `before` only on `page_index ∈ [1, N−1]`,
`after` only on `[0, N−2]`, ≤1 per position per page; `--allow-edge-questions` widens both to
`[0, N−1]`. **≥1 embedded question per lesson** (error otherwise). `true_false` quiz items use
`options: ["True", "False"]`; `short_answer`/`code_output` omit `options`/`correct_index`.
`mastery_level` = `BLOOM_TO_MASTERY[bloom_level]`.

`questions.py check` (deterministic): prompt is a question (ends with `?` or an imperative
"Explain/Describe/Predict…"); 5–150 words; keyed answer string absent from the prompt and, for
`check`, from its own page's text; stem shares <6 consecutive words with `source_quote`; no two
quotes overlap ≥6 words; concept label or alias appears in the prompt; `predict`/apply+ items
contain a causal cue and a scenario frame (a concrete noun phrase + condition); literal-recall
and naming-trivia regexes fail; no yes/no stem for `predict`/`check`; one `?` per prompt;
`acceptance` fields ≤600 chars and non-empty; MCQ: exactly 4 options, exactly one correct,
item-writing flaws counted (longest-option-correct when second-longest <80% of key length,
all/none-of-the-above, absolute terms in options, negative stem, duplicate/near-identical
options, stem↔key word repeats not shared by distractors) with 0–1 pass / ≥2 reject; every
objective has ≥1 quiz item; quiz spans ≥3 Bloom levels; budgets respected; `recap_of` names a
committed prerequisite; variants normalized ratio to the original <0.70; exercises have exactly
3 hints and fading order worked → completion → conventional.

## Stage 7 — `cards.py prepare|check` → `cards/<id>.json`

```json
{
  "contract_version": 1, "lesson_id": "g:architecture-decision-records", "revision": 0,
  "cards": [
    {"id": "…#k1", "card_type": "concept", "front": "What three sections does every ADR record?", "back": "Context, decision, consequences.",
     "card_data": {}, "lo_id": "…#lo1", "unit_ids": ["…"], "page_index": 1, "source_reference": "adr.github.io", "lens": "parts_wholes"},
    {"id": "…#k2", "card_type": "cloze", "front": "An ADR that is replaced by a newer decision is marked ___ rather than deleted.", "back": "superseded",
     "card_data": {"blank_answer": "superseded", "hint": "status word"}, "lo_id": "…", "unit_ids": ["…"], "page_index": 3, "source_reference": "…", "lens": "recall"},
    {"id": "…#k3", "card_type": "ordering", "front": "Order the ADR lifecycle states:", "back": "proposed → accepted → superseded",
     "card_data": {"items": ["accepted", "superseded", "proposed"], "correct_order": [2, 0, 1]}, "…": "…"},
    {"id": "…#k4", "card_type": "discrimination", "front": "Which of these belongs in an ADR's consequences section?", "back": "…",
     "card_data": {"options": ["…", "…", "…", "…"], "correct_index": 1}, "…": "…"}
  ],
  "lessons_learned": []
}
```

`cards.py check`: count within budget (4..cards_max, ≤1 per 100 lesson words); ≥3 card types;
≥2 cards on the generalization (`lens` differs); every objective has ≥1 card; front ≤25 words
(code exempt); back ≤15 words (concept, cloze) / ≤25 (application, comparison, discrimination) /
≤40 (code, ordering); no compound fronts ("and"/"as well as" joining two verb phrases); no
pronoun-only fronts; front/back content-word overlap ≤50%; no yes/no or "give an example" fronts;
cloze has exactly one `___`, <25 words, `blank_answer` not a stopword/name, hint ≤6 words sharing
no stem with the answer; ordering ≤5 items with a permutation `correct_order`; discrimination
and comparison fronts name both confusables; no two cards in a lesson share a back/answer; a
front never contains its back; dedup across the lesson and committed-prerequisite neighbours
(`SequenceMatcher ≥ 0.85` duplicate, 0.75–0.85 flagged).

## Critique — `critique.py prepare <stage> | check <stage>` → `critique/<stage>/<id>.json`

Work file (`critique/<stage>/work/<id>.json`): the artifact, exactly the inputs the critic may
see (units/brief/page text), the rubric criteria for the stage from `contracts.CRITERIA` /
`PER_ITEM_CRITERIA`, a reference where one exists, and the `round` number (= artifact `revision`).
The critic never receives the author's reasoning, rationale fields it must re-derive, or probes.

Verdict file, stage-level stages (`groups`, `syntheses`, `lessons`):

```json
{
  "contract_version": 1, "stage": "lessons", "item_id": "g:architecture-decision-records", "round": 1, "critic": "lesson-critic",
  "criteria": [
    {"criterion_id": "depth.mechanism_explained", "passed": false,
     "evidence_span": "An ADR is a short document that records a decision.",
     "failure_reason": "Page 1 names what an ADR is but never says why recording context prevents relitigation.",
     "correction_instruction": "On page 1 add one paragraph explaining the mechanism, grounded in u:adr.github.io:0:1."}
  ],
  "claims": [{"ref": "page:1:para:2", "verdict": "supported", "unit_ids": ["u:…"], "reason": ""}],
  "actionable_items": [{"span": "…", "instruction": "…", "ground_in": ["u:…"]}],
  "edges": [{"group_id": "g:what-architects-do", "verdict": "confirmed", "reason": "…", "unit_ids": ["…"]}],
  "notes": ""
}
```

`claims` is required for `syntheses` and `lessons` (one entry per brief item / per page
paragraph, verdict ∈ `supported | not_supported | irrelevant`); `edges` is required for
`groups` (one per proposed prerequisite, verdict ∈ `confirmed | either | uncertain`).

Per-item stages (`qualification`, `questions`, `cards`) carry `items` instead of `criteria`:

```json
{"contract_version": 1, "stage": "questions", "item_id": "g:architecture-decision-records", "round": 0, "critic": "question-critic",
 "items": [{"item_id": "…#q1", "verdict": "keep", "criteria": [{"criterion_id": "q.answerable", "passed": true, "evidence_span": "…", "failure_reason": null, "correction_instruction": null}],
            "rewrite": null, "bloom_relabel": "understand", "feedback_to_author": ""}],
 "test_student": {"answered": 4, "correct": 4, "unanswerable": [], "undefined_terms": []},
 "notes": ""}
```

`critique.py check <stage>` computes per item: `pass` iff all critical criteria passed ∧ the
stage's deterministic `check` has no errors for the item ∧ (positive weights passed / positive
weights total) ≥ 0.80 ∧ no undesirable criterion triggered; otherwise `fail`; `FAILED` when
`round ≥ 2` and still failing. Writes `critique/summary.json`:

```json
{"contract_version": 1, "stages": {"lessons": {"items": 24, "pass": 20, "fail": 3, "FAILED": 1, "pending": 0,
  "by_item": {"g:…": {"status": "pass", "round": 1, "score": 0.93, "critical_failed": [], "actionable": 0}}}}}
```

A repair is accepted only if the new artifact `revision` = previous + 1, its diff touches only
the spans named in `actionable_items`, and the changed share ≤ 0.5 of the item length
(`critique.py check` reports `repair.oversized` / `repair.out_of_scope` as errors).

## Upload mapping (`upload.py`, lesson mode)

| Artifact | Table | Mapping |
|---|---|---|
| `structure.json` leaves | `topics` (+ `curriculum_levels`, `topic_learning_objectives`, `topic_prerequisites`) | unchanged from the raw path |
| `chunks.json` | `content_chunks` | unchanged; `metadata` carries lesson metadata; `![alt](image:<id>)` → storage URL inline |
| `questions/*.embedded` | `reading_questions` | `question_type = kind`, `position`, `sort_order = page_index`, `chunk_id` of that page, `expected_answer` |
| `questions/*.quiz` | `topic_questions` | `question_type = type`, `bloom_level`, `mastery_level`, `question_text = prompt`, `explanation`, `payload = {options, correct_index, acceptance, key_points, expected_answer, rationale, feedback, distractors, variants, lo_id, concept_id, source_quote, recap_of}` |
| `questions/*.exercises` | `topic_exercises` (fallback: exercise chunks at `chunk_index ≥ 1000` when the table is absent) | fields 1:1 |
| `cards/*` | `topic_cards` | `card_type`, `front`, `back`, `card_data`, `source_reference`, `sort_order`, `chunk_id` of `page_index` |

`upload_result.json` (written by `upload.py`): `{"domain_id", "target", "owner_type", "owner_id", "topics_inserted", "topics_skipped", "chunks_inserted", "chunks_skipped", "chunks_replaced", "reading_questions", "topic_questions", "topic_cards", "topic_exercises", "exercise_chunks", "skipped_tables": [], "update_mode", "uploaded_at", "contract_version"}`.
