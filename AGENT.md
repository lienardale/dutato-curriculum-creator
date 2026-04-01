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
4. STRUCTURE   → structure.json        (topic hierarchy)
5. CHUNK       → chunks.json           (content chunks)
6. REVIEW      → review.json           (quality report)
7. UPLOAD      → upload_result.json    (domain ID + stats)
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
  "sources": [
    {"type": "pdf", "path": "/path/to/source.pdf", "title": "Source Title"},
    {"type": "url", "path": "https://example.com/docs", "title": "Online Docs"}
  ],
  "created_at": "2026-03-31T12:00:00Z",
  "created_by": "agent"
}
```

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
    "children": [
      {
        "title": "Subtopic",
        "depth": 1,
        "sort_order": 0,
        "description": "...",
        "source_sections": ["Section Title From Extracted JSON"],
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

Verify: read `chunks.json` and check token counts are in the 500-1500 range.

**Resume rule**: If `chunks.json` exists and `structure.json` hasn't changed, skip. If the structure was modified, re-run chunking.

---

## Stage 6: REVIEW (checkpoint: `review.json`)

Follow `rubrics/review_checklist.md` to verify quality. **Save the review results.**

Write `output/<name>/review.json`:
```json
{
  "total_topics": 25,
  "total_chunks": 142,
  "total_tokens": 95000,
  "avg_tokens_per_chunk": 669,
  "chunks_per_topic": {"min": 1, "max": 15, "avg": 5.7},
  "empty_topics": [],
  "orphan_chunks": 0,
  "quality_concerns": ["Topic 'Advanced Patterns' has only 1 chunk"],
  "approved": false,
  "reviewed_at": "2026-03-31T14:00:00Z"
}
```

Present the summary to the user. Set `"approved": true` once the user approves.

**Resume rule**: If `review.json` exists with `"approved": true`, skip to upload. If `"approved": false`, present the concerns and ask the user again.

---

## Stage 7: UPLOAD (checkpoint: `upload_result.json`)

After user approval:
```bash
python upload.py \
  --input output/<name>/ \
  --owner user --user-id <uuid>

# Or for org-owned:
python upload.py \
  --input output/<name>/ \
  --owner org --org-id <org-uuid>
```

**Ask the user** for owner details (user-id, org-id) — never guess.

After successful upload, write `output/<name>/upload_result.json`:
```json
{
  "domain_id": "uuid-here",
  "topics_inserted": 25,
  "chunks_inserted": 142,
  "target": "default",
  "owner_type": "user",
  "uploaded_at": "2026-03-31T15:00:00Z"
}
```

**Resume rule**: If `upload_result.json` exists, the pipeline is complete. Report the domain ID to the user.

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
