# Curriculum Creator — Agent Instructions

You are an AI agent creating a structured curriculum from source materials for the DuTaTo learning platform. This document guides you through the full pipeline.

## Overview

You will:
1. **Extract** content from source files using Python scripts
2. **Explore** the extracted content to understand key concepts
3. **Structure** a topic hierarchy with levels and ordering
4. **Chunk** the content aligned to topics
5. **Review** the output quality with the user
6. **Upload** to Supabase

Your working directory is `tools/curriculum_creator/`. All output goes to `output/{curriculum_name}/`.

## Prerequisites

Before starting, ensure dependencies are installed:
```bash
cd tools/curriculum_creator && uv sync
```

## Step 1: Extract Sources

For each source file the user provides, run the appropriate extractor:

```bash
# Auto-detect source type
uv run python -c "from extractors import extract_source; extract_source('<source_path>', 'output/<name>/extracted/')"

# Or use specific extractors directly
uv run python -m extractors.pdf /path/to/book.pdf -o output/<name>/extracted/
uv run python -m extractors.web https://example.com/docs -o output/<name>/extracted/
uv run python -m extractors.code ./my-repo/ -o output/<name>/extracted/
uv run python -m extractors.office /path/to/slides.pptx -o output/<name>/extracted/
uv run python -m extractors.tabular /path/to/data.csv -o output/<name>/extracted/
uv run python -m extractors.notion /path/to/export.zip -o output/<name>/extracted/
```

**Parallelization**: If there are multiple sources, spawn sub-agents to extract them in parallel. Each sub-agent runs one extractor and saves to the shared `output/<name>/extracted/` directory.

After extraction, read each JSON file to verify it has sections with content.

## Step 2: Create Manifest

Write `output/<name>/manifest.json`:
```json
{
  "name": "Curriculum Display Name",
  "domain": "domain-slug",
  "description": "One-line description of what this curriculum covers",
  "sources": [
    {"type": "pdf", "path": "/path/to/source.pdf", "title": "Source Title"}
  ],
  "created_at": "2026-03-31T12:00:00Z",
  "created_by": "agent"
}
```

## Step 3: Explore Content

Read all extracted JSON files from `output/<name>/extracted/`. For each one, read the section titles and sample content.

Follow `rubrics/exploration.md` to:
1. Identify the key concepts across all sources
2. Map relationships between concepts (prerequisites, related, contrasting)
3. Assess coverage: what's well-covered, what's thin, what's missing
4. Note which sources cover which concepts

**Present your exploration findings to the user** before proceeding:
- List the main concept areas you identified
- Note any gaps in coverage
- Ask if they want to add more sources or adjust focus

## Step 4: Build Topic Hierarchy

Following `rubrics/structuring.md`, create the topic hierarchy.

Write `output/<name>/structure.json`:
```json
[
  {
    "title": "Topic Level 1",
    "depth": 0,
    "sort_order": 0,
    "description": "What this topic covers",
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
- Maximum 3 levels of depth (0, 1, 2)
- Each leaf topic should map to 1-5 extracted sections via `source_sections`
- Order topics from foundational → advanced
- Assign `suggested_level` (1 = beginner, 2 = intermediate, 3 = advanced)
- Every extracted section must appear in at least one topic's `source_sections`

**Present the structure to the user** for review. Be ready to iterate.

## Step 5: Chunk Content

Run the chunking bridge:
```bash
uv run python chunk_bridge.py \
  --structure output/<name>/structure.json \
  --extracted output/<name>/extracted/ \
  -o output/<name>/chunks.json
```

Verify the output: read `chunks.json` and check:
- All topics have at least one chunk
- No chunks are empty
- Token counts are in the 500-1500 range (most of them)
- Total chunk count is reasonable for the content volume

## Step 6: Review

Follow `rubrics/review_checklist.md` to verify quality:
- [ ] All source content is represented in chunks
- [ ] Topic hierarchy is balanced (no topic has 50 chunks while another has 1)
- [ ] No orphan chunks (chunks without a matching topic)
- [ ] Content makes sense when read topic-by-topic
- [ ] Levels are assigned correctly (foundational topics at level 1)

**Present a summary to the user**:
- Total topics, chunks, tokens
- Per-topic chunk distribution
- Any quality concerns
- Ask for final approval to upload

## Step 7: Upload

After user approval:
```bash
uv run python upload.py \
  --input output/<name>/ \
  --owner user --user-id <uuid>
```

Or for org-owned:
```bash
uv run python upload.py \
  --input output/<name>/ \
  --owner org --org-id <org-uuid>
```

Report the domain ID back to the user.

## Sub-Agent Guidance

### When to use sub-agents

- **Parallel extraction**: One sub-agent per source file (if >2 sources)
- **Large content exploration**: One sub-agent per source for initial analysis, then merge findings in the main agent
- **Review**: One sub-agent to validate chunk quality while the main agent prepares the summary

### Sub-agent prompt template

When spawning a sub-agent for extraction:
```
Extract content from [source_path] using the curriculum_creator extractors.
Working directory: tools/curriculum_creator/
Run: uv run python -c "from extractors import extract_source; extract_source('[source_path]', 'output/[name]/extracted/')"
Verify the output JSON has non-empty sections.
Report: source_type, section count, total tokens.
```

When spawning a sub-agent for exploration:
```
Read the extracted JSON at output/[name]/extracted/[file].json.
Follow the rubrics/exploration.md instructions to analyze the content.
Report: key concepts found, relationships, coverage quality (1-5).
```

## Resumability

Before each step, check if the output already exists:
- `output/<name>/extracted/*.json` → skip extraction
- `output/<name>/structure.json` → skip exploration + structuring (ask user if they want to redo)
- `output/<name>/chunks.json` → skip chunking
- Manifest always gets regenerated

If the user says "resume", check what exists and continue from there.

## Error Handling

- **Extraction fails**: Report the error, skip the source, ask user if they want to retry or continue without it
- **No sections extracted**: The source may be an image-heavy PDF or a binary file. Tell the user and suggest alternatives
- **Chunking produces 0 chunks**: The structure.json titles likely don't match extracted section titles. Check the mapping and fix structure.json
- **Upload fails**: Check Supabase credentials in .env. Report the specific error to the user
