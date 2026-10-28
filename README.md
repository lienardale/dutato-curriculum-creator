# DuTaTo Curriculum Creator

Create structured, source-faithful curricula — lessons, embedded questions, quizzes, exercises and review cards — from books, docs, code, slides and videos, using an AI agent (Claude Code or Codex).

## How It Works

This tool is operated by an AI agent, not by hand. Python scripts do the mechanical work (extraction, segmentation, drafting, deterministic gates, assembly, upload) and never call an AI API; the agent and the sub-agents it spawns do the intellectual work (tagging, grouping, writing, critiquing).

```
You → launch an agent session → sources → units → concepts → lesson groups → briefs
    → lessons (pages) → questions + cards → review → Supabase
```

Every authored artifact passes two gates before the next stage starts:

1. a **deterministic check** (`<stage>.py check`) — ids, citations, verbatim quotes, budgets, verbatim-copy limits, placement rules;
2. an **independent critique** (`critique.py`) — a fresh sub-agent that sees only the artifact and its cited sources, judges it against rubric criteria, and whose verdict the script turns into pass / fail / FAILED with bounded repair rounds.

Every claim cites its source units; nothing is invented, nothing is padded to a quota. A human approves a pilot lesson before the batch runs, and approves the review before anything is uploaded.

## Quick Start

### Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (Python package manager)
- An AI coding agent (Claude Code or Codex)
- Supabase credentials in `.env` (for upload): `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` for the default target, `STAGING_SUPABASE_URL` / `STAGING_SUPABASE_SERVICE_ROLE_KEY` for staging

### Setup

```bash
cd tools/curriculum_creator
uv sync                    # creates .venv/ with all dependencies
source .venv/bin/activate  # activate — use plain `python` from now on
```

### Create a Curriculum

1. **Start an AI agent session** in this directory
2. **Give it your sources, the audience, and ask for a curriculum:**

```
Create a curriculum about Kubernetes for backend engineers new to operations from these sources:
- /path/to/k8s-book.pdf
- https://kubernetes.io/docs/concepts/
- /path/to/k8s-repo/
```

3. The agent follows `AGENT.md`:
   - extracts each source and normalizes section titles
   - segments the text into knowledge units and tags them with a concept registry (one sub-agent per source, blind re-tag critique)
   - groups units into lessons with objectives, prerequisites and budgets (critiqued, prerequisite edges confirmed)
   - writes a knowledge brief per lesson (fidelity critique)
   - writes a pilot lesson for your approval, then the remaining lessons (page metrics, critique, repair)
   - builds `structure.json` + `chunks.json`, then questions and cards per lesson (critic, test student, blind grader)
   - presents the review (validation report, critique table, metrics) and uploads after your approval

### Resume a Session

```bash
python status.py output/<name>/      # stage table with a RESUME HERE marker and FAILED items
python critique.py status output/<name>/
```

### Upload Only

If you already have a curriculum output directory, stage it first, then promote it:

```bash
python upload.py check output/<name>/                                     # upload-side gates, no database
python upload.py plan   output/<name>/ --target staging --owner user --user-email you@example.com   # dry run
python upload.py upload output/<name>/ --target staging --owner user --user-email you@example.com
python upload.py upload output/<name>/ --target default --owner user --user-id <your-uuid>         # production
```

`--target` is required (`default`, `staging`, or `custom` with `--db-url` / `--db-key`). A `staging` or `custom` target that resolves to `SUPABASE_URL` is refused, so a mis-set environment can never write to production. The validator runs first and blocks on errors.

### Custom Database

To set up your own database with the DuTaTo schema:

```bash
python setup_db.py --db-url postgresql://... --apply-migrations
```

## Supported Source Types

| Type | Extension | Description |
|------|-----------|-------------|
| PDF | `.pdf` | Textbooks, papers, documentation (scanned PDFs OCR'd with `uv sync --extra ocr`) |
| Word | `.docx` | Word documents |
| PowerPoint | `.pptx` | Slide decks |
| URL | `https://...` | Web articles and documentation (`--crawl` for multi-page sites) |
| Codebase | directory | Source code repositories |
| CSV/TSV | `.csv`, `.tsv` | Tabular data, glossaries |
| Notion | `.zip`, Notion URL | Notion export archives, or pages via the Notion API |
| Video | YouTube / Vimeo / Twitch URL, `.mp4` … | Captions, with Whisper fallback (`uv sync --extra video-heavy`) |

## Output Structure

Each curriculum lives under `output/<name>/` (shapes in `docs/artifacts.md`):

```
output/<name>/
├── manifest.json            # metadata: name, domain, pipeline: "lesson", audience, sources
├── extracted/               # one JSON per source (+ images/)
├── scope.json               # optional: which sources / sections are in scope
├── units/                   # knowledge units per source (script)
├── qualification/           # per-source unit tags (authored)
├── concepts.json            # concept registry: ids, labels, aliases, gaps, ordering (authored)
├── groups.draft.json        # mechanical lesson candidates (script)
├── groups.json              # lesson groups: areas, objectives, prerequisites, budgets (authored)
├── syntheses/<id>.json      # knowledge briefs, one per lesson (authored; work/ holds inputs)
├── lessons/<id>.json        # lessons: pages with roles and markdown (authored); anchor.json = approved pilot
├── lesson_metrics.json      # page metrics (script)
├── structure.json           # areas > modules > lessons (script: lessons.py build)
├── chunks.json              # one chunk per lesson page (script)
├── questions/<id>.json      # embedded questions, quiz, exercises per lesson (authored)
├── cards/<id>.json          # review cards per lesson (authored)
├── critique/<stage>/        # critic work files and verdicts; summary.json = pass/fail/FAILED table
├── validation_report.json   # validate.py --json
├── review.json              # review summary + approval (authored)
├── upload_result.json       # domain id, target, row counts (script)
└── variants/<tier>/         # optional condensed variants
```

Legacy outputs produced by the raw pipeline (`exploration.json`, `structure.json` with `source_sections`, `exercises.json`) are still recognised by `status.py`, `validate.py` and `upload.py`.

## For Developers

- `AGENT.md` — the operating manual (stage commands, sub-agent prompt templates, critique loop, error handling)
- `AGENTS.md` — key files, every command and subcommand, image / video / Notion / variant notes
- `docs/artifacts.md` — the artifact contract; `shared/contracts.py` mirrors it in code
- `rubrics/` — the rubrics authors and critics follow at each stage
- `uv run pytest -q` — the test suite (synthetic fixtures, planted-defect tests per stage)
