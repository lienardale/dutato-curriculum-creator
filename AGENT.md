# Curriculum Creator — Agent Operating Manual (lesson pipeline)

You are the AI agent that turns source material into a DuTaTo curriculum: lessons read one
page at a time, embedded questions, quizzes, exercises and spaced-repetition cards. Scripts
do the mechanical work (segmenting, drafting, gating, assembling, uploading). You — and the
sub-agents you spawn — do the reading, tagging, grouping, writing and critiquing.

Three documents govern the work. When they disagree, the earlier one wins:

1. `docs/artifacts.md` — the artifact contract: every file shape, id format and threshold.
   This manual never restates a schema; it tells you which file to write and points there.
2. `shared/contracts.py` — the same enums and thresholds in code (change both together).
3. This manual and the rubrics under `rubrics/`.

The legacy **raw** pipeline (`structure.json` with `source_sections` → `chunk_bridge.py`)
still works for old outputs; it is summarized in the appendix at the end.

---

## 0. Session start — always

```bash
cd tools/curriculum_creator
uv sync                          # creates/updates .venv/
source .venv/bin/activate        # every command below uses plain `python`
python status.py output/<name>/  # stage table, "<-- RESUME HERE", FAILED items
python critique.py status output/<name>/   # once any critique has run
```

`status.py` reads `manifest.pipeline`: `"lesson"` prints the stage table of this manual
(`contracts.LESSON_STAGES` plus a critique pseudo-stage after cards) with per-stage counts
(sources with units, units qualified, concepts, groups and budgets, briefs, outline/full
lessons with their critique status, chunks, questions, cards) and a `RESUME HERE` marker at
the first incomplete non-optional stage. Without an argument it lists every curriculum under
`output/`. If the directory does not exist, start at Stage 1.

**Resumability is the first rule.** Every stage writes a checkpoint file (or one file per
item) before the next stage starts. A new session resumes at the marker; it never redoes a
complete stage unless the user asks. When you must stop mid-stage, write whatever per-item
files you have — partial progress on disk is the handoff.

---

## 1. Operating rules (every stage)

- **Scripts are pure data utilities.** They never call an AI API. Stages marked *authored*
  are written by you or a sub-agent; stages marked *script* are produced by a command.
- **Never invent, never pad.** Every claim in every authored file cites `unit_ids`, and every
  stated fact carries a `quote` copied verbatim from one cited unit. A fact no unit supports
  is not written. When the units are thin, the lesson is shorter or the group is merged.
- **Ceilings, not quotas.** `budget` fields (pages, question and card counts, exercises) and
  every "3–5", "2–4" range are upper bounds and bands: write fewer items when the units do
  not support more, never more.
- **Check before critique.** Run the stage's deterministic `check` and fix every `[ERROR]`
  before `critique.py prepare`. Warnings need a fix or a one-line reason in the artifact's
  `rationale` / `lessons_learned`.
- **Fan-out rule.** One sub-agent per source (qualification), per group (briefs), per lesson
  (lessons, questions, cards). Each sub-agent reads its `work/` file and writes its own
  item file to disk — item files are the checkpoints. **The critic is never the author**: a
  critique sub-agent is a fresh context that sees only `critique/<stage>/work/<item>.json`.
  Claude Code operators may use Workflows to run the fan-out; the per-item files and the
  `--item` / `--source` / `--group` / `--lesson` flags are designed for it.
- **Script-owned fields are never hand-edited**: `budget`, `prerequisites[].status`,
  `prerequisite_edges_reduced`, `lesson_metrics.json`, `critique/summary.json`,
  `units/*.json`, `structure.json`, `chunks.json`.
- **Revisions.** Authored per-item files carry `revision` (0 on first write, +1 per repair)
  and 1–3 `lessons_learned` lines that the next `prepare` hands to the next author.

---

## 2. Pipeline map

| # | Stage | Checkpoint | Who writes it |
|---|---|---|---|
| 1 | Manifest | `manifest.json` (`"pipeline": "lesson"`) | you |
| 2 | Extract | `extracted/*.json`, `extracted/images/` | extractors |
| 2b | Normalize titles | `extracted/*.json` (in place) | `normalize_titles.py` |
| 2c | Images (optional) | `image_analysis.json` | `analyze_images.py` + you |
| 3 | Qualify | `units/*.json` → `qualification/*.json`, `concepts.json` | script → sub-agents |
| 4 | Regroup | `groups.draft.json` → `groups.json` | script → you |
| 5 | Synthesize | `syntheses/work/` → `syntheses/*.json` | script → sub-agents |
| 6 | Lessons | `lessons/work/` → `lessons/*.json`, `lesson_metrics.json` | script → sub-agents |
| 7 | Build | `structure.json`, `chunks.json` | `lessons.py build` |
| 8 | Questions | `questions/work/` → `questions/*.json` | script → sub-agents |
| 9 | Cards | `cards/work/` → `cards/*.json` | script → sub-agents |
| — | Critique (after 3, 4, 5, 6, 8, 9) | `critique/<stage>/*.json`, `critique/summary.json` | critic sub-agents → script |
| 10 | Review | `validation_report.json`, `review.json` | script → you |
| 11 | Upload | `upload_result.json` | `upload.py` |
| 12 | Condense (optional) | `condensation_plan.json`, `variants/` | you → `condense.py` |

Directory layout, ids and per-stage shapes: `docs/artifacts.md`.

---

## 3. Stage 1 — MANIFEST (`manifest.json`)

Write it first; it records what the user asked for. This is one of the few files written
by hand without a `prepare` step:

```json
{
  "name": "Software Architecture",
  "domain": "software-architecture",
  "domain_family": "software-architecture",
  "variant": "extensive",
  "pipeline": "lesson",
  "audience": "Senior engineers moving into an architect role; comfortable with code, new to decision records and evolutionary design",
  "description": "One-line description",
  "sort_order": 3,
  "sources": [
    {"type": "pdf", "path": "/path/to/book.pdf", "title": "Book Title"},
    {"type": "url", "path": "https://example.com/docs", "title": "Online Docs", "crawl": true, "include_paths": ["/blog/"]}
  ],
  "created_at": "2026-10-28T12:00:00Z",
  "created_by": "agent"
}
```

- `pipeline` **must** be `"lesson"`; `validate.py` warns when it is missing (defaults to raw).
- `audience` is free text that every author and critic work file carries; write who the
  learner is and what they already know — it decides register, examples and Bloom ceilings.
- `domain_family` / `variant` group the extensive curriculum with condensed variants
  (`"extensive"` here; `condense.py` emits the others). `sort_order` orders app cards.
- Optional `scope`: when the curriculum covers only part of the sources, write
  `scope.json` (shape in `docs/artifacts.md`, Stage 2) or derive it from a legacy structure
  (Stage 3). `qualify.py prepare` applies `output/<name>/scope.json` automatically.

**Resume rule:** if `manifest.json` exists, read it and ask the user whether anything changed.

---

## 4. Stage 2 — EXTRACT (`extracted/*.json`)

One extractor call per source. The source key used in every unit id is the extracted
filename without `.json` (`peaa`, `martinfowler.com_bliki_CQRS.html`).

```bash
python -m extractors.pdf /path/to/book.pdf -o output/<name>/extracted/
python -m extractors.web https://example.com -o output/<name>/extracted/
python -m extractors.web https://docs.example.com/tutorial --crawl --max-pages 50 --max-depth 1 \
    --max-tokens 200000 --include-paths /blog/ -o output/<name>/extracted/
python -m extractors.code ./my-repo/ --exclude vendor --exclude generated -o output/<name>/extracted/
python -m extractors.office /path/to/slides.pptx -o output/<name>/extracted/
python -m extractors.tabular /path/to/glossary.csv -o output/<name>/extracted/
python -m extractors.notion /path/to/export.zip -o output/<name>/extracted/
python -m extractors.notion_api https://www.notion.so/My-Page-abc123 --token <token> -o output/<name>/extracted/
python -m extractors.video https://youtu.be/<id> --lang en --window-seconds 300 -o output/<name>/extracted/
python -m extractors.video "https://www.youtube.com/playlist?list=<id>" --max-videos 10 -o output/<name>/extracted/
python -m extractors.video https://youtu.be/<id> --whisper-model base -o output/<name>/extracted/   # no captions
python -m extractors.video https://youtu.be/<id> --no-whisper -o output/<name>/extracted/           # error instead
# auto-detect by extension / URL:
python -c "from extractors import extract_source; extract_source('<source_path>', 'output/<name>/extracted/')"
```

- `--crawl` follows links under the entry URL's path prefix; `--include-paths` (repeatable)
  allows extra same-origin prefixes; a manifest source can carry `"crawl": true` and
  `"include_paths": [...]`.
- Video sources: captions first, Whisper fallback (`uv sync --extra video-heavy`); sections
  are chapter markers or fixed windows. Transcripts with `metadata.transcript_source ==
  "whisper"` or `metadata.is_auto_generated` are lower quality — lower their `quality` at
  qualification and prefer written sources on conflicts.
- Images are extracted automatically when `-o` is given; scanned PDFs are OCR'd when
  `uv sync --extra ocr` is installed.

**Fan-out:** one sub-agent per source for 3+ sources (template: run the command, verify the
JSON has non-empty `sections`, report source_type / section count / tokens).
**Resume rule:** extract only sources without a JSON in `extracted/`.

---

## 5. Stage 2b — NORMALIZE TITLES (in place)

```bash
python normalize_titles.py output/<name>/                # strip "Chapter N:", "Part I", drop book-meta sections
python normalize_titles.py output/<name>/ --dry-run      # preview
python normalize_titles.py output/<name>/ --remap remap.json   # bare "Chapter N" titles -> TOC names
```

`remap.json` maps `{"<file>.json": {"Chapter 1": "Introduction to Docker", ...}}`. The
step is idempotent; `status.py` shows "Titles normalized" only when no title still carries
a prefix. Heading paths in units come from these titles, so run it before Stage 3.

---

## 6. Stage 2c — IMAGES (optional, `image_analysis.json`)

```bash
python analyze_images.py prepare output/<name>/   # work list of extracted images
python analyze_images.py ocr output/<name>/       # optional OCR (uv sync --extra ocr)
# you: fill description / educational_value / contains_* per image in image_analysis.json
python analyze_images.py apply output/<name>/     # write alt_text back into extracted/*.json
```

Units inherit the images whose offset falls inside them; lesson pages place them as
`![alt](image:<id>)` placeholders (Stage 6) and `upload.py` rewrites them to storage URLs.

---

## 7. Stage 3 — QUALIFY (`units/`, `qualification/`, `concepts.json`)

### 7.1 Script: segment into knowledge units

```bash
python qualify.py prepare output/<name>/                          # all sources (applies scope.json when present)
python qualify.py prepare output/<name>/ --source peaa --source c4model.com_introduction   # only these keys
python qualify.py prepare output/<name>/ --scope output/<name>/scope.json
# scope from a legacy raw structure (areas = depth-0 titles):
python qualify.py scope-from-structure output/<name>/legacy/structure.json \
    --area "Architecture Leadership" --area "Decision Records" \
    -o output/<name>/scope.json [--extracted output/<name>/extracted/]
```

Writes `units/<source>.json` and `units/index.json` (cleaned, typed, token-counted units with
`heading_path`, `page`, attached images). Idempotent. Read `units/index.json` to size the
fan-out.

### 7.2 Authored: qualification — one sub-agent per source, plus the registry

Each sub-agent writes `qualification/<source>.json` (shape: `docs/artifacts.md`, Stage 2
authored; rules: `rubrics/qualification.md`). The concept registry `concepts.json`
(`rubrics/concepts.md`) is shared: seed it yourself from `units/index.json` and the sources'
own vocabulary, let sub-agents add entries they need, then reconcile.

Sequence that keeps ids consistent across sources:

1. Skim every `units/<source>.json` (`heading_path`, first unit per section) and write a
   first `concepts.json`: ids, labels, aliases, `learning_step`, `depends_on`, `coverage`.
2. Fan out one AUTHOR per source. Each reads `concepts.json`, tags its units, and appends new
   concepts to `concepts.json` only when no label or alias fits (write the registry entry in
   the same pass).
3. Reconcile: run `qualify.py check`, decide every merge candidate / thin / too-broad finding
   (`merges`, aliases, drops), record `gaps` with the user's `resolution`, write `ordering`.

**AUTHOR template — qualification (one per source):**

```
You are qualifying knowledge units for the DuTaTo curriculum "<name>" (audience: <manifest.audience>).
Working directory: tools/curriculum_creator/ (venv active).
Read: units/<source>.json (every unit), concepts.json (reuse ids; add an entry only when no
label/alias fits), rubrics/qualification.md, docs/artifacts.md "Stage 2 (authored)".
Write: qualification/<source>.json — one entry per unit id (all of them), incrementally
(save after every section). Each concept tag carries a `quote` copied verbatim from that
unit's text. Fill gleaning[<section_index>] after each section.
Never invent a concept the unit does not teach; never pad tags to make a unit look richer;
drop meta/noise/reference units with a drop_reason; cite the unit's own words.
Finish with: python qualify.py check output/<name>/ --source <source>  → 0 errors.
Report: units kept/dropped, new concepts added, sections where gleaning found a miss.
```

### 7.3 Script gate

```bash
python qualify.py check output/<name>/                   # units + every qualification + concepts; writes units/dedup.json
python qualify.py check output/<name>/ --source peaa     # one source (no dedup, no registry gates)
python qualify.py check output/<name>/ --strict          # warnings fail too
```

Fix every `units.*`, `qualification.*`, `concepts.*`, `scope.*` error. Read the
units-per-concept and drop statistics it prints: a concept with < 2 units is thin; one on
> 40% of units is too broad; `units.alternate_of` pairs are cross-source duplicates the
regrouping will deduplicate.

### 7.4 Critique: blind re-tag

```bash
python critique.py prepare qualification output/<name>/ [--item peaa] [--sample-seed 0]
#   → critique/qualification/work/<source>.json : a sample (10 %, min 15) of unit texts, the registry, enums — no author tags
# one CRITIC sub-agent per source writes critique/qualification/<source>.json
python critique.py check qualification output/<name>/
```

**CRITIC template — qualification:**

```
You are an independent critic (rubrics/critique_protocol.md §4 "qualification"). You have NOT
seen the author's tags and must not open qualification/.
Read only: critique/qualification/work/<source>.json (sample units, concepts, enums, output_shape).
For every sampled unit assign concepts (registry ids), unit_type and keep|drop from the text alone.
Write: critique/qualification/<source>.json exactly in output_shape (critic: "<your name>").
Uncertain → fewer concepts, not more. Never invent an id that is not in `concepts`.
```

`critique.py check` compares tags (`tags.concepts_agree`, `tags.unit_type_agrees`,
`tags.keep_agrees`); more than 20 % disagreement fails the source. A failing source means the
registry or the author's granularity is inconsistent: fix `concepts.json` (aliases, merges),
re-tag the affected units, re-run `qualify.py check` and the critique.

**Resume rule:** `status.py` counts qualified units per source; fan out only sources whose
file is missing or partial. `qualify.py prepare` is safe to re-run (byte-identical output).

---

## 8. Stage 4 — REGROUP (`groups.draft.json` → `groups.json`)

One group = one lesson. The script drafts; you decide what a lesson is
(`rubrics/regrouping.md`; shape: `docs/artifacts.md`, Stage 3).

```bash
python regroup.py draft output/<name>/            # groups.draft.json: one candidate per concept, merges/splits, prerequisite candidates
# you: author groups.json (areas, modules, groups with knowledge points, objectives, watch, generalization, prerequisites status "proposed")
python regroup.py check output/<name>/            # gates; writes prerequisite_edges_reduced
python regroup.py check output/<name>/ --item g:architecture-decision-records --no-write   # one group, no write
python regroup.py budget output/<name>/           # fills every group's script-owned budget (needed by Stage 5)
python critique.py prepare groups output/<name>/  # critique/groups/work/<id>.json per group
# one CRITIC sub-agent per group writes critique/groups/<id>.json (criteria + one verdict per proposed edge)
python critique.py check groups output/<name>/
python regroup.py commit-edges output/<name>/     # prerequisites[].status := committed | uncertain | rejected
python regroup.py check output/<name>/            # DAG hygiene now on committed edges only
python regroup.py budget output/<name>/           # recap_item depends on prerequisites — refresh (check warns groups.budget_stale)
```

Authoring rules that the gates enforce: every kept unit primary in exactly one group or in
`dropped`; 3–5 knowledge points; 2–4 objectives with observable verbs at or under the units'
Bloom ceiling; ≥ 2 watch items; a one-sentence generalization naming ≥ 2 concept labels;
prerequisites only with `evidence_unit_ids`; unique titles, no source branding; ≤ 4
prerequisites per lesson, ≥ 3 root lessons once there are ≥ 10. Areas/modules and ordering
rules: `rubrics/structuring.md`.

Regrouping is usually done by you, not fanned out: the unit accounting is global. For a
large draft (> 40 candidates) fan out one sub-agent per area to propose that area's groups,
then merge and run the accounting yourself.

**CRITIC template — groups (one per group):**

```
You are an independent critic (rubrics/critique_protocol.md §4 "groups").
Read only: critique/groups/work/<id>.json — the group (without the author's rationale), its
units' text, the concepts it names, the proposed prerequisite edges, criteria, output_shape.
Re-derive from the units: is there ONE generalization? are the knowledge points distinct
facets? can the units support each objective's Bloom level? Then judge every proposed edge
confirmed | either | uncertain with one sentence and the unit ids that show the dependency.
`uncertain` is legitimate; guessing is not. Default to failure on missing evidence.
Write: critique/groups/<id>.json in output_shape; ≤ 3 actionable_items, each naming a verbatim
span and the unit to ground the fix in.
```

Repair (see §14) edits only the named spans; after a repair re-run `check`, `budget`, and
the critique for that group (`--item g:<slug>`).

**Resume rule:** `status.py` reports groups, budgets filled and prerequisites committed. If
`groups.json` exists, do not redraft; `regroup.py draft` is only re-run after qualification
changes (it overwrites `groups.draft.json`, never `groups.json`).

---

## 9. Stage 5 — SYNTHESIZE (`syntheses/<id>.json`)

The brief is the complete, source-faithful list of what a lesson may teach; pages are written
from it alone (`rubrics/synthesis.md`; shape: `docs/artifacts.md`, Stage 4).

```bash
python synthesize.py prepare output/<name>/                       # syntheses/work/<id>.json per group (skips existing)
python synthesize.py prepare output/<name>/ --group g:adr --force  # one group, rewrite
python synthesize.py prepare output/<name>/ --max-tokens 12000     # work-file budget (related units dropped first)
# one AUTHOR sub-agent per group writes syntheses/<id>.json
python synthesize.py check output/<name>/ [--group g:adr] [--strict]
python critique.py prepare syntheses output/<name>/               # critique/syntheses/work/<id>.json
# one CRITIC sub-agent per group writes critique/syntheses/<id>.json
python critique.py check syntheses output/<name>/
```

**AUTHOR template — brief (one per group):**

```
You are writing the knowledge brief for lesson group <id> of "<name>" (audience: <audience>).
Read: syntheses/work/<id>.json (group, units with full text — primary then related,
duplicates marked —, concepts, neighbours, budget, instructions = rubrics/synthesis.md,
lessons_learned), docs/artifacts.md "Stage 4".
Write: syntheses/<id>.json — overview; ≤ 30 items of kind map | watch | inference, 4–40 words
each, ≤ 3 unit_ids each, `quote` copied verbatim from ONE cited unit (3–15 words);
examples (code referenced by unit id, never copied); terminology; revision 0.
Never invent; never pad to reach a count; an item no unit literally supports is an
`inference` citing ≥ 2 units, or it is deleted. Keep conflicts visible as watch items.
Rephrase everything (no copied run ≥ 8 words outside concept labels and quoted terms).
Finish with: python synthesize.py check output/<name>/ --group <id> → 0 errors; read warnings.
Report: items by kind, unit coverage, uncited units and why.
```

**CRITIC template — syntheses (factored fidelity):**

```
You are an independent critic (rubrics/critique_protocol.md §4 "syntheses").
Read only: critique/syntheses/work/<id>.json. `claims` lists every brief item WITH ONLY the text
of its cited units; `coverage_task` lists the group's knowledge points and the items.
1. Label each claim supported | not_supported | irrelevant from its cited_units alone — a true
   claim with no supporting cited unit is not_supported.
2. List knowledge points no map item covers, and watch items that silently merge conflicting sources.
3. Fill the criteria with evidence spans copied from the brief; default to failure.
Write: critique/syntheses/<id>.json in output_shape; ≤ 3 actionable_items naming span + unit.
```

The script computes precision (≥ 0.95) and recall (≥ 0.90) from the claims. Repair: §14.

**Resume rule:** `status.py` counts briefs per group; `prepare` skips groups whose work file
exists (use `--force` after `groups.json` changed for that group).

---

## 10. Stage 6 — LESSONS (`lessons/<id>.json`, `lesson_metrics.json`)

A lesson is 3–6 pages with roles orientation → core (one per knowledge point) →
worked_example → pitfalls → recap, written in three passes (`rubrics/lesson_design.md`;
shape: `docs/artifacts.md`, Stage 5).

### 10.1 Outline pass

```bash
python lessons.py prepare output/<name>/ --lesson g:adr --pass outline   # lessons/work/<id>.json (pass: outline)
# AUTHOR writes lessons/<id>.json with every page's markdown: null
python lessons.py check output/<name>/ --lesson g:adr --outline
```

The outline gate (`lessons.outline.*`): roles and order, page count = `budget.pages`, every
page ↔ ≥ 1 objective, every knowledge point on exactly one page, each core page exactly one
knowledge point, no page whose only support is an `inference` item. Fix before writing prose.

### 10.2 Full pass

```bash
python lessons.py prepare output/<name>/ --lesson g:adr --pass full      # work file now carries the outline + anchor
# AUTHOR fills every page's markdown (core + worked_example first, then orientation, pitfalls, recap)
python lessons.py check output/<name>/ --lesson g:adr                    # page metrics → lesson_metrics.json
python critique.py prepare lessons output/<name>/ --item g:adr
# CRITIC writes critique/lessons/<id>.json
python critique.py check lessons output/<name>/ --item g:adr
```

`--pass` defaults to `full` when the lesson file exists and `outline` otherwise; `prepare`
skips an up-to-date work file unless `--force`.

**AUTHOR template — lesson (one per group):**

```
You are writing lesson <id> ("<title>") of "<name>" for <audience>. Pass: <outline | full>.
Read: lessons/work/<id>.json (group with budget and prerequisites, the brief, concepts,
neighbours with their recap pages, images you may place, page_template, anchor = the
human-approved exemplar lesson when present, your outline (full pass), lessons_learned,
instructions = rubrics/lesson_design.md, thresholds), docs/artifacts.md "Stage 5".
Write: lessons/<id>.json. Outline pass: pages with role/title/lo_ids/knowledge_point_id/
unit_ids/brief_item_ids and markdown null. Full pass: markdown per page, 150–450 words by
role, second person, each key term bold exactly once and defined within 150 characters,
an example on every page over 200 words, recap repeats the group generalization verbatim,
pitfalls = 2–4 misconceptions from watch items. Every paragraph traces to a brief_item_id.
Never invent a fact the brief lacks; never pad to a word band — a thin brief is a shorter
lesson; rephrase, never copy (no run ≥ 8 words outside code and registry terms); images only
as ![alt](image:<id>) for ids in the work file. Match the anchor's register and depth.
Finish with: python lessons.py check output/<name>/ --lesson <id> [--outline] → 0 errors.
Report: words per page, terms introduced, warnings you could not clear and why.
```

**CRITIC template — lessons:**

```
You are an independent critic (rubrics/critique_protocol.md §4 "lessons").
Read only: critique/lessons/work/<id>.json — pages, `paragraphs` (page:<i>:para:<j>), the brief
items each page cites, the cited units, objectives, generalization, criteria, protocol.
FIRST write bloom_relabels for every objective from the page text, before reading the
author's labels. Then one `claims` entry per paragraph ref: supported | not_supported |
irrelevant against brief_items + units. Then the criteria: mechanism explained (how/why, not
only what), one knowledge point per core page, examples where the unit type demands them,
standalone pages, objective alignment, ≤ 1 flagged paragraph per page, register.
Every verdict cites an evidence_span copied verbatim; uncertain → passed: false.
Write: critique/lessons/<id>.json in output_shape; ≤ 3 actionable_items (span, instruction,
ground_in unit or brief item).
```

### 10.3 PILOT-LESSON gate (human), anchor, batch

Do **not** fan out lessons before a pilot is approved:

1. Pick the pilot: a mid-complexity group with a committed prerequisite and a worked example.
2. Take it through outline → full → check → critique → repair until `critique.py status`
   shows `pass`.
3. Present the pilot to the user (pages, metrics line from `lesson_metrics.json`, the critic's
   verdict). Iterate on their feedback; each change is a repair round or, with their OK, a
   rewrite.
4. On approval write the anchor pointer by hand:

```json
{"lesson_id": "g:architecture-decision-records"}
```

   at `lessons/anchor.json`. From now on `lessons.py prepare` hands the anchor lesson to every
   author work file (`anchor`) and the critics judge against the same bar. Freeze the pilot
   and its verdict files under `tests/fixtures/planted/` only when the user wants a
   regression anchor for rubric changes (see that folder's README).
5. Batch: `python lessons.py prepare output/<name>/` for all remaining groups, fan out one
   AUTHOR per lesson (outline, check, full, check), then `critique.py prepare lessons`, one
   CRITIC per lesson, `critique.py check lessons`, repairs (§14).

**Resume rule:** `status.py` shows `n/N full, m outline; critique …`. Outline-only files are
resumed at the full pass; `lessons.py check` without `--lesson` rewrites `lesson_metrics.json`
for every complete lesson.

---

## 11. Stage 7 — BUILD (`structure.json`, `chunks.json`)

```bash
python lessons.py build output/<name>/                 # refuses while lessons.py check reports errors
python lessons.py build output/<name>/ --allow-failing # builds anyway (warning); only for a dry preview
python validate.py output/<name>/ --stage structure
python validate.py output/<name>/ --stage chunks
```

`build` assembles areas (depth 0, `suggested_level` from the area or its groups' majority) →
modules (depth 1, when the area has them) → lessons (leaves carrying `lesson_id`,
`description`, objectives, prerequisites from **committed** edges, `source_sections: []`),
and one chunk per page (`chunk_index` = page index, lesson metadata, image placeholders
resolved). Re-run it after any lesson or group change — it is cheap and idempotent. The
ordering rules it applies are in `rubrics/structuring.md`.

---

## 12. Stage 8 — QUESTIONS (`questions/<id>.json`)

Embedded questions (before/after a page), quiz items, exercises
(`rubrics/questions.md`; shape: `docs/artifacts.md`, Stage 6).

```bash
python questions.py prepare output/<name>/ [--lesson g:adr ...] [--force] [--allow-edge-questions]
# one AUTHOR sub-agent per lesson writes questions/<id>.json
python questions.py check output/<name>/ [--lesson g:adr ...] [--allow-edge-questions] [--strict]
python critique.py prepare questions output/<name>/ [--item g:adr]
#   → three work files per lesson under critique/questions/work/: <id>.json (critic),
#     <id>.student.json (test student), <id>.grader.json (+ hidden <id>.grader.key.json)
# CRITIC writes critique/questions/<id>.json; TEST STUDENT writes critique/questions/<id>.student.json  (in parallel)
python critique.py prepare questions output/<name>/ [--item g:adr]   # folds the student's answers into the grader candidates
# GRADER writes critique/questions/<id>.grader.json
python critique.py check questions output/<name>/
```

`--allow-edge-questions` widens embedded slots to the first/last page; pass it to both
`prepare` and `check` only when the target viewer supports it (default viewer: no).

**AUTHOR template — questions (one per lesson):**

```
You are designing the questions for lesson <id> of "<name>" for <audience>.
Read: questions/work/<id>.json (pages, objectives, knowledge points, watch items =
distractor seeds, generalization, budget, concepts, brief items, units, prerequisite recaps,
allowed_slots, existing, lessons_learned, instructions = rubrics/questions.md),
docs/artifacts.md "Stage 6".
Write: questions/<id>.json — embedded (≥ 1, only in allowed_slots), quiz (every objective
covered, ≥ 3 Bloom levels, MCQ distractors from misconceptions, recap item when
budget.recap_item), exercises (≤ budget.exercises_max, 3 hints, fading order). Every item:
unit_ids + verbatim source_quote, two-layer key (acceptance.prediction + causal_reason as a
relationship; expected_answer / rationale / key_points), probes (realistic correct and wrong
learner answers). Open book: never ask what one sentence on the page answers; neutral stems.
Budgets are ceilings — never pad. Never invent a fact the pages and units do not teach.
Finish with: python questions.py check output/<name>/ --lesson <id> → 0 errors.
```

**CRITIC template — questions (answer-first):**

```
You are an independent critic (rubrics/critique_protocol.md §4 "questions").
Read only: critique/questions/work/<id>.json. Withheld from you on purpose: rationale,
source_quote, probes, key_points, bloom_level, distractors, explanation, feedback, variants.
Per question, in order, fail-fast: derive the answer from page + units BEFORE reading the key;
for check/MCQ it must match. Then understandable → topic-related → clear → central →
independent Bloom re-label → acceptance.causal_reason is a relationship, not a phrase →
leading-wording check. Verdict keep | rewrite | drop; a rewrite changes prompt AND key and
keeps concept_id/lo_id (it is a suggestion to the author).
Write: critique/questions/<id>.json in output_shape (items[].criteria with evidence spans,
bloom_relabel, feedback_to_author).
```

**TEST STUDENT template:**

```
You are a learner, not a critic. Read only: critique/questions/work/<id>.student.json.
Read prerequisite_recaps, then the pages in order; answer every question in questions_before
before reading that page and every question in questions_after before reading further, in
your own words. List every term you could not define from what you had read so far.
Write: critique/questions/<id>.student.json in output_shape (answers, unanswerable, undefined_terms).
```

**GRADER template (blind probe matrix):**

```
You grade answers. Read only: critique/questions/work/<id>.grader.json. For each question,
candidates A/B/C are anonymous answers (you do not know which is the author's probe or the
student's). Grade each ONLY against `acceptance` (prediction + causal_reason) in any wording:
pass when it states the prediction for that causal reason, fail otherwise.
Write: critique/questions/<id>.grader.json in output_shape (grades per question per label).
```

`critique.py check questions` reveals the key: the correct probe must pass and the wrong probe
must fail (`q.probe_matrix_discriminating`), the student's answers must reach 80 % correct and
answer every embedded question (`critique.test_student_*`), and the critic's verdicts must
clear the critical criteria. A missing grader fails every probe criterion.

**Resume rule:** `status.py` counts question files per lesson; `prepare` skips up-to-date
work files; re-run `prepare` for a lesson after its pages changed (`--force`).

---

## 13. Stage 9 — CARDS (`cards/<id>.json`)

Spaced-repetition cards, 4–7 per lesson, ≥ 3 card types, ≥ 2 lenses on the generalization
(`rubrics/cards.md`; shape: `docs/artifacts.md`, Stage 7).

```bash
python cards.py prepare output/<name>/ [--lesson g:adr ...] [--force]
# one AUTHOR sub-agent per lesson writes cards/<id>.json
python cards.py check output/<name>/ [--lesson g:adr ...] [--strict]
python critique.py prepare cards output/<name>/ [--item g:adr]
# CRITIC writes critique/cards/<id>.json
python critique.py check cards output/<name>/
```

**AUTHOR template — cards (one per lesson):**

```
You are designing the review cards for lesson <id> of "<name>" for <audience>.
Read: cards/work/<id>.json (pages, objectives, knowledge points, generalization, terminology
and map items, budget with the one-card-per-100-words ceiling, knowledge-level counts with
suggested card types, lens_checklist, question_prompts (do not restate them),
do_not_duplicate (neighbouring lessons' fronts), existing, lessons_learned,
instructions = rubrics/cards.md), docs/artifacts.md "Stage 7".
Write: cards/<id>.json — one proposition per card, one intended answer, front ≤ 25 words,
back caps by type, every objective covered, cloze with one blank, ordering items shuffled,
discrimination options = real confusables; unit_ids and page_index on every card.
Four sharp cards beat seven; never pad to the ceiling; never claim what the units do not say.
Finish with: python cards.py check output/<name>/ --lesson <id> → 0 errors.
```

**CRITIC template — cards:**

```
You are an independent critic (rubrics/critique_protocol.md §4 "cards").
Read only: critique/cards/work/<id>.json — each card with its page and cited units; withheld:
lens, source_reference, the author's reasoning.
Per card: card.correct (any false claim fails), card.answerable_from_front_alone,
card.single_filler (list EVERY plausible alternative for cloze/discrimination in
`alternatives`), card.tests_takeaway. Verdict keep | drop; you never rewrite a card.
Write: critique/cards/<id>.json in output_shape.
```

**Resume rule:** as for questions.

---

## 14. The critique loop (all critiqued stages)

Stages: `qualification`, `groups`, `syntheses`, `lessons`, `questions`, `cards`.

```bash
python critique.py prepare <stage> output/<name>/ [--item ID ...] [--force] [--sample-seed N]
python critique.py check   <stage> output/<name>/ [--item ID] [--strict]
python critique.py status  output/<name>/
```

- `prepare` writes `critique/<stage>/work/<item>.json` containing exactly what the critic may
  see (artifact view, cited units or brief items, the stage's criteria from
  `contracts.CRITERIA` / `PER_ITEM_CRITERIA`, the protocol section, `output_shape`,
  `verdict_file`) and stores a snapshot of the artifact at its current `revision`. It skips
  up-to-date work files unless `--force` (which also resets the snapshot).
- The critic is a **fresh sub-agent** that reads only its work file, re-derives from the
  sources before judging, defaults to failure, and writes the verdict file named in
  `verdict_file`. It never edits the artifact.
- `check` computes the result by rule: `pass` iff every critical criterion passed, the
  stage's deterministic `check_item` has no errors, the weighted score of positive criteria
  is ≥ 0.80 and no undesirable criterion triggered; otherwise `fail`; `FAILED` when the item
  is still failing at round ≥ 2. It writes `critique/summary.json` and prints the table.
  `pending` = no verdict yet, or the verdict's `round` is behind the artifact's `revision`.

**Repair bounds (enforced by `check`):**

| Rule | Gate |
|---|---|
| ≤ 3 `actionable_items` per round, each naming a verbatim span and what to ground the fix in | `critique.actionable_items_excess` |
| The author edits only those spans; ≤ 50 % of the prose may change | `repair.oversized` (error), `repair.out_of_scope` (warning) |
| A repair bumps `revision` by exactly 1; the new verdict carries `round` = new revision | `repair.revision_not_bumped`, `repair.revision_skipped`, `critique.verdict_stale` |
| At most 2 repair rounds; a third failure is `FAILED` | `critique.item_status` |
| 1–3 `lessons_learned` lines per repair, carried into the next author work file | — |

Repair procedure for one item:

```bash
python critique.py check <stage> output/<name>/ --item <id>     # read actionable_items; snapshot of this revision is stored
# AUTHOR (fresh sub-agent or the original) edits only the named spans, revision += 1, adds lessons_learned
python <stage-script>.py check output/<name>/ --<item-flag> <id>  # 0 errors
python critique.py prepare <stage> output/<name>/ --item <id>     # work file for the new revision
# fresh CRITIC writes the verdict with round = new revision
python critique.py check <stage> output/<name>/ --item <id>
```

The critic prompt for a repair round is the same template; it does not see the previous
verdict. `FAILED` items are never silently passed: they are listed in `review.json` for the
human (§16).

---

## 15. Stage 10 — REVIEW (`validation_report.json`, `review.json`)

```bash
python validate.py output/<name>/ --json            # every family whose checkpoint exists → validation_report.json
python validate.py output/<name>/ --stage lessons   # one family (units|qualification|concepts|groups|syntheses|lessons|chunks|structure|questions|cards|critique|review)
python validate.py output/<name>/ --strict          # warnings fail too
python critique.py status output/<name>/
python status.py output/<name>/                     # FAILED items by id
```

In lesson mode `validate.py` delegates each family to its stage module (findings 1:1) and
reads `critique/summary.json` (`critique.item_failed`, `critique.pending`). Fix every error;
acknowledge every remaining warning in `review.json` → `quality_concerns`. Then do the
judgment review in `rubrics/review_checklist.md` (read lessons, questions and cards as a
learner would) and write `review.json`: the validator `summary` under `validation`, the
critique counts under `critique`, the lesson metrics summary under `lesson_metrics`, the
`failed_items`, `quality_concerns`, `approved: false`, `reviewed_at` (contract in the
checklist). Present it to the user; set `approved: true` only when they approve.

**Resume rule:** `review.json` with `approved: true` → upload; otherwise present the concerns
again. `validate.py --stage upload` re-checks approval.

---

## 16. Stage 11 — UPLOAD (`upload_result.json`)

`--target` is required: `default` (`SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY`), `staging`
(`STAGING_SUPABASE_URL` / `STAGING_SUPABASE_SERVICE_ROLE_KEY`), `custom` (`--db-url` /
`--db-key` or `CUSTOM_DB_URL` / `CUSTOM_DB_SERVICE_KEY`). `.env` is discovered in
`curriculum_creator/` then `dutato/`. A non-default target whose URL equals `SUPABASE_URL` is
refused, so a mis-set staging can never write to prod. **Always stage first.**

```bash
python upload.py check output/<name>/ [--item g:adr]         # upload-side gates, no database
python validate.py output/<name>/ --stage upload             # requires review.json approved: true, no failing critique item
python upload.py plan   output/<name>/ --target staging --owner user --user-email me@example.com   # dry run: row counts per table
python upload.py upload output/<name>/ --target staging --owner user --user-email me@example.com
python upload.py upload output/<name>/ --target default --owner user --user-id <uuid>              # prod, after staging review
python upload.py upload output/<name>/ --target staging --owner org --org-id <uuid>
python upload.py upload output/<name>/ --target staging --owner user --user-id <uuid> --update [--replace-chunks]
python upload.py enrich output/<name>/ --target staging       # objectives, prerequisites, practice content only
python upload.py upload output/<name>/ --target custom --db-url <url> --db-key <key> --owner user --user-id <uuid>
# legacy flag form (same flags): python upload.py --input output/<name>/ --target staging --owner user --user-email me@example.com [--dry-run] [--enrich]
```

- `--owner user` needs exactly one of `--user-id` / `--user-email` (the email is resolved on
  the target); `--owner org` needs `--org-id`. **Ask the user** for owner details — never guess.
- `--update` skips existing topics (matched by title, depth, parent); `--replace-chunks`
  refreshes chunk content and practice rows of existing topics.
- `enrich` clears and re-inserts objectives, prerequisites and practice content of an
  already-uploaded curriculum without touching domains, topics or regular chunks.
- `upload.py` runs the validator first and blocks on errors; `--skip-validation` only when the
  user explicitly asks. Lesson-mode mapping (reading_questions, topic_questions, topic_cards,
  topic_exercises) is in `docs/artifacts.md`, "Upload mapping"; missing tables on the target
  are reported and skipped (`skipped_tables`).
- `upload_result.json` is written by the script; report `domain_id` and `target` to the user.

**Resume rule:** `upload_result.json` present → done for that target. Uploading the same
output to a second target (staging → prod) overwrites `upload_result.json`.

---

## 17. Stage 12 — CONDENSE (optional, unchanged)

After the extensive curriculum is uploaded, the user may ask for `detailed` / `classic` /
`core` variants (`rubrics/condensation.md`). You write `condensation_plan.json` (keep /
merge / synthesize per topic, inline `content` for merge and synthesize), then:

```bash
python validate.py output/<name>/ --stage condensation
python condense.py --input output/<name>/ --plan output/<name>/condensation_plan.json [--tiers detailed,classic] [--allow-concat]
python upload.py upload output/<name>/variants/detailed/ --target staging --owner user --user-email me@example.com
```

`condense.py` errors on a merge/synthesize entry without `content` (`--allow-concat` only when
the user accepts raw concatenation). Variants are raw-shaped outputs (manifest, structure,
chunks) sharing `domain_family`; they carry no practice content.

**Resume rule:** re-assemble only tiers whose `variants/<tier>/` is incomplete.

---

## 18. Session handoff checklist

Resuming:

1. `python status.py output/<name>/` and `python critique.py status output/<name>/`.
2. Read `manifest.json` (audience, scope) and the latest checkpoints of the resume stage.
3. Re-run the stage's `check` before trusting files a previous session left.
4. Continue from the marker; never redo complete stages unless asked.

Ending early:

1. Write every per-item file you have, even partial (qualification maps may be partial).
2. Leave `lessons_learned` in files a critic has returned.
3. Tell the user the stage, the items done / remaining, and open decisions (gaps, FAILED).

---

## 19. Error handling

- **Extraction fails / 0 sections:** report, skip the source, continue; tell the user the
  source may be image-only (try `uv sync --extra ocr`).
- **`qualify.py prepare` yields 0 units for a source:** the extracted JSON has empty sections
  or the scope excludes it (`scope.*` findings). Fix the scope or re-extract.
- **Blind re-tag fails a source:** the registry is ambiguous. Merge or alias concepts, re-tag,
  re-check; do not loosen the sample.
- **`groups.unit_unassigned`:** every kept unit must land in a group or in `dropped` with a
  reason. Never drop to make counts fit.
- **Verbatim gates fail (`*.verbatim.fragment` / `jaccard5` / `density`):** rewrite from
  understanding; do not shuffle clauses. `verbatim.ungrounded` means the prose shares nothing
  with its citations — re-check the units say it.
- **`lessons.lesson_words` / page bands:** merge or split the group (`regroup.py`) rather than
  pad or trim; re-run `budget`.
- **`critique.grader_missing` / `test_student_missing`:** the questions stage cannot pass
  without both; re-run `critique.py prepare questions` and spawn the missing sub-agent.
- **A repair is `repair.oversized` / `repair.out_of_scope`:** revert to the snapshot's text
  outside the named spans, or ask the user to authorize a rewrite.
- **`FAILED` items:** stop repairing. List them in `review.json` → `failed_items` and ask the
  user: regroup (merge/split and re-run from Stage 4 for that group), rewrite from scratch
  (only with their OK: set `revision` to 0, remove the item's files under `critique/<stage>/`,
  `critique.py prepare <stage> --item <id> --force`, full critique again), or ship without
  the item (lesson mode refuses to upload a lesson without questions — drop the group
  entirely).
- **Validation errors at upload:** read `validation_report.json`, fix the artifact, re-run.
  Never `--skip-validation` unless the user asks.
- **Upload fails:** check the target's `.env` variables and the "refusing: target resolves to
  SUPABASE_URL" guard; report the error verbatim.

**When to ask the user:** before extraction of paid/credentialed sources; after
`qualify.py check` for every critical gap (`resolution`); after the groups critique when a
prerequisite edge stays `uncertain` and matters for ordering; at the pilot-lesson gate;
for every `FAILED` item; before any upload (owner, target) and before `--skip-validation`,
`--allow-failing`, `--allow-concat` or a from-scratch rewrite.

---

## Appendix — Legacy raw pipeline (`manifest.pipeline` absent or `"raw"`)

Old outputs keep working unchanged: manifest → extract → `normalize_titles.py` →
`exploration.json` (concept map + gaps, authored) → `structure.json` (topics with
`source_sections`, objectives, prerequisites; `rubrics/learning_objectives.md`) →
`chunk_bridge.py` → `exercises.json` (`rubrics/exercises.md`) → `review.json` → upload →
condense.

```bash
python validate.py output/<name>/ --stage structure     # structure.stale_ref, empty_leaf, prereq_cycle …
python chunk_bridge.py --structure output/<name>/structure.json --extracted output/<name>/extracted/ \
    -o output/<name>/chunks.json [--max-tokens 1500] [--min-tokens 200]   # writes chunk_report.json
python validate.py output/<name>/ --stage chunks
python validate.py output/<name>/ --stage exercises
python upload.py upload output/<name>/ --target staging --owner user --user-email me@example.com
```

`status.py`, `validate.py` and `upload.py` select the raw stage list automatically. To move
a raw curriculum to the lesson pipeline, keep its files under `output/<name>/legacy/`, set
`"pipeline": "lesson"` in the manifest, derive `scope.json` with
`qualify.py scope-from-structure`, and start at Stage 3. New curricula always use the lesson
pipeline.
