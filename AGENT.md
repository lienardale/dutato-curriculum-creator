# Curriculum Creator — Agent Instructions

You are an AI agent creating a structured curriculum from source materials for the DuTaTo learning platform.

## CRITICAL: Resumability

This pipeline is designed to survive agent session interruptions. **Every stage writes a checkpoint file before moving to the next.** If your session ends mid-pipeline, the next agent picks up where you left off.

**First thing to do in any session — activate the environment and check status:**
```bash
cd tools/curriculum_creator
uv sync                        # install/update deps (creates .venv/)
source .venv/bin/activate      # activate — use plain `python` from now on
python status.py output/<name>/
```

This shows which stages are complete and where to resume. If the output directory doesn't exist yet, start from Stage 1.

> **Important**: Always activate the venv at the start of your session. All commands below use plain `python` (not `python`).

## Pipeline Overview

```
1. MANIFEST    → manifest.json         (curriculum metadata)
2. EXTRACT     → extracted/*.json      (one per source)
3. EXPLORE     → exploration.json      (concept analysis — MUST be saved)
4. STRUCTURE   → structure.json        (topic hierarchy + objectives + prerequisites)
5. CHUNK       → chunks.json           (content chunks)
6. EXERCISES   → exercises.json        (practice problems)
7. REVIEW      → review.json           (quality report)
8. UPLOAD      → upload_result.json    (domain ID + stats)
```

Every stage has a checkpoint file. **Do NOT proceed to the next stage until the checkpoint is written to disk.**

---

## Stage 1: MANIFEST (checkpoint: `manifest.json`)

**Write this first**, before any extraction. It records what the user asked for so the next agent session knows the curriculum name, domain, and intended sources.

Write `output/<name>/manifest.json`:
```json
{
  "name": "Curriculum Display Name",
  "domain": "domain-slug",
  "description": "One-line description",
  "sort_order": 3,
  "sources": [
    {"type": "pdf", "path": "/path/to/source.pdf", "title": "Source Title"},
    {"type": "url", "path": "https://example.com/docs", "title": "Online Docs"}
  ],
  "created_at": "2026-03-31T12:00:00Z",
  "created_by": "agent"
}
```

- `sort_order` (optional): Controls display position in the app. Lower numbers appear first. Defaults to 99 if omitted.

**Resume rule**: If `manifest.json` exists, read it to understand the curriculum context. Ask the user if anything changed.

---

## Stage 2: EXTRACT (checkpoint: `extracted/*.json`)

For each source in the manifest, run the appropriate extractor:

```bash
python -c "from extractors import extract_source; extract_source('<source_path>', 'output/<name>/extracted/')"
```

Or use specific extractors:
```bash
python -m extractors.pdf /path/to/book.pdf -o output/<name>/extracted/
python -m extractors.web https://example.com -o output/<name>/extracted/
python -m extractors.code ./my-repo/ -o output/<name>/extracted/
python -m extractors.office /path/to/slides.pptx -o output/<name>/extracted/
python -m extractors.tabular /path/to/data.csv -o output/<name>/extracted/
python -m extractors.notion /path/to/export.zip -o output/<name>/extracted/
```

**Multi-page web sources** (documentation sites, tutorials with subpages):
When a URL points to a table-of-contents or index page, use `--crawl` to follow
links to subpages. This is essential for sites where the entry page is just a TOC.

```bash
python -m extractors.web https://docs.example.com/tutorial --crawl -o output/<name>/extracted/
```

Safety limits (all configurable):
- `--max-pages 50` — absolute cap on subpages fetched
- `--max-depth 1` — link-following hops (1 = direct links only, no recursive discovery)
- `--max-tokens 200000` — stop when accumulated tokens exceed budget

Only links sharing the entry URL's path prefix are followed (e.g. for
`/docs/current/tutorial.html`, only `/docs/current/tutorial-*.html` pages).

**Parallelization**: For 3+ sources, spawn one sub-agent per source.

**Resume rule**: Check which source files already have a corresponding JSON in `extracted/`. Only extract missing ones.

---

## Stage 3: EXPLORE (checkpoint: `exploration.json`)

This is the most expensive stage — you read all extracted content and analyze it. **You MUST save your analysis to `exploration.json` before proceeding.** If your session ends during exploration, the next agent reads this file instead of re-analyzing everything.

### Process

1. Read each extracted JSON from `output/<name>/extracted/`
2. Follow `rubrics/exploration.md` to analyze content
3. **Write per-source analysis incrementally** — if processing 5 sources, write after each one so partial progress is saved

### Write `output/<name>/exploration.json`:
```json
{
  "concepts": [
    {
      "name": "Binary Search",
      "sources": ["algorithms-book.json", "coding-interview.json"],
      "coverage": "strong",
      "prerequisites": ["Arrays", "Sorting"],
      "notes": "Well covered with examples in both sources"
    }
  ],
  "gaps": [
    {
      "concept": "Amortized Analysis",
      "severity": "recommended",
      "suggestion": "Consider adding a source on algorithm analysis"
    }
  ],
  "concept_order": ["Arrays", "Sorting", "Binary Search", "Trees", "Graphs"],
  "coverage_summary": "Core data structures well covered. Algorithm analysis is thin.",
  "per_source_analysis": {
    "algorithms-book.json": {
      "section_count": 25,
      "key_concepts": ["Sorting", "Searching", "Graphs", "Dynamic Programming"],
      "quality": 4,
      "notes": "Comprehensive textbook, strong on theory"
    }
  }
}
```

The `per_source_analysis` field is critical — it lets a resuming agent skip re-reading sources that were already analyzed. **Write this field incrementally as you process each source.**

**Present findings to the user** and ask if they want to add more sources or adjust focus.

**Resume rule**: If `exploration.json` exists, read it. Check `per_source_analysis` — if some sources are missing, only analyze those. If all sources are analyzed, skip to presenting findings.

---

## Stage 4: STRUCTURE (checkpoint: `structure.json`)

Following `rubrics/structuring.md`, create the topic hierarchy.

Write `output/<name>/structure.json`:
```json
[
  {
    "title": "Topic Title",
    "depth": 0,
    "sort_order": 0,
    "description": "What this covers",
    "suggested_level": 1,
    "learning_objectives": [
      {"text": "Explain the core concepts of X", "bloom_level": "understand"},
      {"text": "Implement X in a real scenario", "bloom_level": "apply"}
    ],
    "prerequisites": [
      {"topic": "Foundational Topic", "strength": "required"}
    ],
    "children": [
      {
        "title": "Subtopic",
        "depth": 1,
        "sort_order": 0,
        "description": "...",
        "source_sections": ["Section Title From Extracted JSON"],
        "learning_objectives": [
          {"text": "Define the key terms of X", "bloom_level": "remember"}
        ],
        "children": []
      }
    ]
  }
]
```

Rules:
- Maximum 3 depth levels (0, 1, 2)
- Each leaf topic maps to 1-5 extracted sections via `source_sections`
- Order: foundational → advanced
- Assign `suggested_level` (1 = beginner, 2 = intermediate, 3 = advanced)

### Learning Objectives

For each topic, generate 2-5 learning objectives following `rubrics/learning_objectives.md`:
- Use the stem "By the end of this topic, you will be able to..."
- Each objective needs a specific, measurable action verb + content
- Assign a Bloom's level: `remember`, `understand`, `apply`, `analyze`, `evaluate`, `create`
- Only promise knowledge that the source material actually covers

### Prerequisite Links

Using the prerequisite chains identified in `exploration.json`, add explicit `prerequisites` to topics that depend on prior knowledge from another topic in this curriculum:
- `topic`: title of the prerequisite topic (must match exactly, case-insensitive)
- `strength`: `"required"` (cannot proceed without) or `"recommended"` (helpful but not blocking)
- Only link to topics within this curriculum
- Avoid transitive redundancy: if A requires B and B requires C, don't also add A→C
- The prerequisite graph must be a DAG (no cycles)

### Split Hints (Optional)

If a source section contains distinct conceptual segments, add `split_after_headings` to the leaf topic — a list of heading texts where the chunker should force a boundary:

```json
"split_after_headings": ["Pseudocode", "Complexity Analysis"]
```

Only use this when the default heading-based splitting would combine concepts that should be separate chunks.

**Present the structure to the user** for review. Iterate if needed — overwrite `structure.json` with updates.

**Resume rule**: If `structure.json` exists, show it to the user and ask if changes are needed.

---

## Stage 5: CHUNK (checkpoint: `chunks.json`)

Run the chunking bridge:
```bash
python chunk_bridge.py \
  --structure output/<name>/structure.json \
  --extracted output/<name>/extracted/ \
  -o output/<name>/chunks.json
```

Verify: read `chunks.json` and check token counts are in the 500-1500 range. The chunker respects markdown heading boundaries and keeps fenced code blocks intact. If you added `split_after_headings` to leaf topics in structure.json, verify those splits occurred.

**Resume rule**: If `chunks.json` exists and `structure.json` hasn't changed, skip. If the structure was modified, re-run chunking.

---

## Stage 6: EXERCISES (checkpoint: `exercises.json`)

Generate practice problems for each leaf topic (or a subset of the most important topics).

### Process

1. Read `structure.json` and `chunks.json` to understand each topic's content
2. Follow `rubrics/exercises.md` for exercise design guidelines
3. For each leaf topic, generate 1-3 exercises:
   - At least one exercise per depth-0 topic group
   - Focus on topics where hands-on practice is most valuable
   - Skip purely definitional or reference-style topics

### Write `output/<name>/exercises.json`:
```json
[
  {
    "topic_path": ["SQL Fundamentals", "Querying a Table"],
    "exercises": [
      {
        "title": "Filter with WHERE Clause",
        "problem_statement": "Write a SQL query that selects all rows from the 'weather' table where the temperature is above 30 degrees.",
        "hints": [
          "Start with SELECT * FROM weather",
          "Add a WHERE clause to filter rows",
          "Use the > operator to compare the temp column"
        ],
        "expected_solution": "SELECT * FROM weather WHERE temp > 30;",
        "common_mistakes": [
          "Forgetting the semicolon",
          "Using = instead of > for 'above'"
        ],
        "bloom_level": "apply",
        "difficulty": 1
      }
    ]
  }
]
```

Exercises are uploaded as `content_chunks` with `metadata.type = "exercise"` and exercise details in `metadata.exercise`. They appear alongside regular content with a `chunk_index` offset of 1000.

**Resume rule**: If `exercises.json` exists, read it. Check which topics already have exercises. Only generate exercises for topics that are missing them.

---

## Stage 7: REVIEW (checkpoint: `review.json`)

Follow `rubrics/review_checklist.md` to verify quality. **Save the review results.**

Write `output/<name>/review.json`:
```json
{
  "total_topics": 25,
  "total_chunks": 142,
  "total_exercises": 35,
  "total_tokens": 95000,
  "avg_tokens_per_chunk": 669,
  "chunks_per_topic": {"min": 1, "max": 15, "avg": 5.7},
  "empty_topics": [],
  "orphan_chunks": 0,
  "objectives_coverage": "24/25 topics have objectives",
  "prerequisites_count": 12,
  "exercises_coverage": "18/25 leaf topics have exercises",
  "quality_concerns": ["Topic 'Advanced Patterns' has only 1 chunk"],
  "approved": false,
  "reviewed_at": "2026-03-31T14:00:00Z"
}
```

Present the summary to the user. Set `"approved": true` once the user approves.

**Resume rule**: If `review.json` exists with `"approved": true`, skip to upload. If `"approved": false`, present the concerns and ask the user again.

---

## Stage 8: UPLOAD (checkpoint: `upload_result.json`)

After user approval:
```bash
python upload.py \
  --input output/<name>/ \
  --owner user --user-id <uuid>

# Or for org-owned:
python upload.py \
  --input output/<name>/ \
  --owner org --org-id <org-uuid>

# Incremental update (add new topics only, skip existing):
python upload.py \
  --input output/<name>/ \
  --owner user --user-id <uuid> --update

# Incremental update + refresh chunk content for existing topics:
python upload.py \
  --input output/<name>/ \
  --owner user --user-id <uuid> --update --replace-chunks
```

**Ask the user** for owner details (user-id, org-id) — never guess.

### Curriculum Levels

The `suggested_level` values in structure.json automatically create `curriculum_levels` rows during upload:
- Level 1 → "Fundamentals" (definitions, core concepts, basic examples)
- Level 2 → "Intermediate" (techniques, patterns, comparative analysis)
- Level 3 → "Advanced" (complex applications, trade-offs, synthesis)

The Flutter app uses these levels to group topics by difficulty tier and guide learning progression from basic to advanced. Without levels, all topics appear in a flat list.

### Update Mode

Use `--update` when expanding an existing curriculum with additional sources:
- Topics are matched by (title, depth, parent_title) — existing topics are skipped
- Chunks are matched by (topic_id, chunk_index) — existing chunks are skipped
- Add `--replace-chunks` to refresh content for existing topics instead of skipping

**Workflow for expanding a curriculum:**
1. Add new sources to `manifest.json`
2. Re-run extraction for the new sources only
3. Re-run exploration, structure, and chunking (the full pipeline)
4. Upload with `--update` to add only the new content

### Enrich Mode

Use `--enrich` to retroactively add learning objectives, prerequisites, and exercises to an existing curriculum that was uploaded before these features existed.

```bash
python upload.py --input output/<name>/ --enrich
```

This mode:
- Resolves the domain ID from `upload_result.json`, `manifest.json`, or by slug lookup
- **Clears** existing objectives, prerequisites, and exercise chunks (idempotent)
- Inserts new data from the enriched `structure.json` and `exercises.json`
- Does NOT touch domains, topics, or regular content chunks

**Workflow for enriching an existing curriculum:**
1. Read `structure.json` and `chunks.json` to understand the curriculum
2. Add `learning_objectives` to each topic in `structure.json` (follow `rubrics/learning_objectives.md`)
3. Add `prerequisites` to topics that depend on others (follow `rubrics/structuring.md`)
4. Generate `exercises.json` (follow `rubrics/exercises.md`)
5. Run `python upload.py --input output/<name>/ --enrich`
6. If variants exist, re-run `python condense.py` to propagate, then enrich each variant:
   ```bash
   python upload.py --input output/<name>/variants/detailed/ --enrich
   python upload.py --input output/<name>/variants/classic/ --enrich
   ```

After successful upload, write `output/<name>/upload_result.json`:
```json
{
  "domain_id": "uuid-here",
  "topics_inserted": 25,
  "topics_skipped": 0,
  "chunks_inserted": 142,
  "chunks_skipped": 0,
  "chunks_replaced": 0,
  "update_mode": false,
  "target": "default",
  "owner_type": "user",
  "uploaded_at": "2026-03-31T15:00:00Z"
}
```

**Resume rule**: If `upload_result.json` exists, the pipeline is complete. Report the domain ID to the user.

---

## Stage 9 (Optional): CONDENSE (checkpoint: `condensation_plan.json`)

After the extensive curriculum is uploaded, the user may ask for condensed variants: **detailed** (~10x shorter), **classic** (~100x shorter), **core** (~1000x shorter).

### Process

1. Read `structure.json` and `chunks.json` to understand the full curriculum
2. Follow `rubrics/condensation.md` for tier budgets and selection criteria
3. For each tier, decide which topics to keep, merge, drop, or synthesize
4. For `merge`/`synthesize` strategies, **write the condensed content yourself** — read the original chunks, reason about what's essential, and write new concise text
5. Write the plan to `output/<name>/condensation_plan.json`

### Write `output/<name>/condensation_plan.json`:
```json
{
  "detailed": [
    {
      "title": "SQL Fundamentals",
      "description": "Core SQL language concepts",
      "suggested_level": 1,
      "condensation_strategy": "keep",
      "source_topics": ["SQL Fundamentals"],
      "children": [
        {
          "title": "Basic Queries",
          "description": "SELECT, INSERT, UPDATE, DELETE",
          "condensation_strategy": "merge",
          "source_children": ["2.5. Querying a Table", "2.4. Populating a Table With Rows"],
          "content": "Your merged text covering both children..."
        }
      ]
    }
  ],
  "classic": [ ... ],
  "core": [ ... ]
}
```

Key rules:
- `source_topics` / `source_children` trace back to original topic titles
- For strategy `keep`: `condense.py` copies original chunks automatically
- For strategy `merge` or `synthesize`: you **must** include a `content` field with the text you wrote
- Each chunk should be 200-2000 tokens
- Maintain pedagogical ordering within each tier

### Present the plan to the user for review, then run:
```bash
python condense.py --input output/<name>/ --plan output/<name>/condensation_plan.json
```

This produces `output/<name>/variants/{tier}/` directories with standard files (manifest.json, structure.json, chunks.json).

### Upload each variant:
```bash
python upload.py --input output/<name>/variants/detailed/ --owner user --user-id <uuid>
python upload.py --input output/<name>/variants/classic/  --owner user --user-id <uuid>
python upload.py --input output/<name>/variants/core/     --owner user --user-id <uuid>
```

The manifest includes `domain_family` and `variant` fields, so the Flutter app groups them with the extensive domain under one card.

**Resume rule**: If `condensation_plan.json` exists, read it. Check which `variants/*/` directories already have all 3 output files — only re-assemble missing tiers.

---

## Sub-Agent Guidance

### When to use sub-agents

- **Parallel extraction**: One sub-agent per source (if >2 sources)
- **Large content exploration**: One sub-agent per source to write `per_source_analysis`, then merge
- **Review**: One sub-agent to validate chunk quality

### Sub-agent prompt template for extraction
```
Extract content from [source_path] using the curriculum_creator extractors.
Working directory: tools/curriculum_creator/
Run: python -c "from extractors import extract_source; extract_source('[source_path]', 'output/[name]/extracted/')"
Verify the output JSON has non-empty sections.
Report: source_type, section count, total tokens.
```

### Sub-agent prompt template for per-source exploration
```
Read the extracted JSON at output/[name]/extracted/[file].json.
Follow rubrics/exploration.md to analyze the content.
Write your analysis as a JSON object with keys: section_count, key_concepts (list), quality (1-5), notes.
Return the JSON object (do NOT write to disk — the main agent will merge).
```

---

## Session Handoff Checklist

If you are a NEW agent session resuming this pipeline:

1. Run `python status.py output/<name>/` to see progress
2. Read `manifest.json` to understand the curriculum
3. Check which checkpoint files exist
4. Read the most recent checkpoint to get context
5. Resume from the next incomplete stage
6. **Do NOT re-do completed stages** unless the user explicitly asks

If you are ENDING your session before the pipeline is complete:

1. **Save your current work** — write whatever checkpoint you have, even partial
2. If mid-exploration, write `exploration.json` with whatever `per_source_analysis` you've completed so far
3. Tell the user which stage you stopped at and what remains

---

## Error Handling

- **Extraction fails**: Report the error, skip that source, continue with others
- **No sections extracted**: The source may be image-heavy or binary. Tell the user
- **Chunking produces 0 chunks**: Structure titles likely don't match extracted sections — check the mapping
- **Upload fails**: Check Supabase credentials in `.env`. Report the error
