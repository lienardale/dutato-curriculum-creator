# DuTaTo Curriculum Creator

Create structured curricula from diverse source materials using an AI agent (Claude Code or Codex).

## How It Works

This tool is designed to be operated by an AI agent, not manually. The agent uses Python scripts for mechanical tasks (extraction, chunking, upload) while doing the intellectual work (content analysis, structuring, review) itself.

```
You → Launch AI agent session → Agent processes sources → Structured curriculum
```

## Quick Start

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (Python package manager)
- An AI coding agent (Claude Code with Opus, or Codex)
- Supabase credentials in `.env` (for upload)

### Setup

```bash
cd tools/curriculum_creator
uv sync                    # creates .venv/ with all dependencies
source .venv/bin/activate  # activate — use plain `python` from now on
```

### Create a Curriculum

1. **Start an AI agent session** in this directory
2. **Give it your sources** and ask it to create a curriculum:

```
Create a curriculum about Kubernetes from these sources:
- /path/to/k8s-book.pdf
- https://kubernetes.io/docs/concepts/
- /path/to/k8s-repo/
```

3. The agent will follow `AGENT.md` to:
   - Extract content from each source
   - Analyze the material and identify key concepts
   - Build a topic hierarchy
   - Chunk the content
   - Present the results for your review
   - Upload to Supabase after your approval

### Upload Only

If you already have a curriculum output directory:

```bash
python upload.py \
  --input output/my-curriculum/ \
  --owner user --user-id <your-uuid>
```

### Custom Database

To set up your own database with the DuTaTo schema:

```bash
python setup_db.py --db-url postgresql://... --apply-migrations
```

## Supported Source Types

| Type | Extension | Description |
|------|-----------|-------------|
| PDF | `.pdf` | Textbooks, papers, documentation |
| Word | `.docx` | Word documents |
| PowerPoint | `.pptx` | Slide decks |
| URL | `https://...` | Web articles and documentation |
| Codebase | directory | Source code repositories |
| CSV/TSV | `.csv`, `.tsv` | Tabular data, glossaries |
| Notion | `.zip` | Notion export archives |

## Output Structure

Each curriculum produces:

```
output/{name}/
├── extracted/       # One JSON per source (intermediate format)
├── structure.json   # Topic hierarchy (agent-designed)
├── chunks.json      # Chunked content aligned to topics
└── manifest.json    # Metadata (name, domain, sources)
```

## For Developers

See `AGENT.md` for the full agent instruction set and `rubrics/` for the quality rubrics the agent follows during each pipeline stage.
