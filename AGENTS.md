# AGENTS.md — Curriculum Creator

## Architecture: Agent-Operated Pipeline

This tool is designed to be **operated by an AI agent** (Claude Code, Codex, or similar). The division of labor is strict:

- **Python scripts** = pure data utilities (I/O, segmentation, drafting, gating, assembly, upload). They must **never call AI APIs** (no `anthropic`, `openai`, or similar SDK imports). They take structured input and produce structured output.
- **The operating agent** = does all reasoning, tagging, grouping, writing and critiquing. It reads source material, writes JSON checkpoints, spawns author and critic sub-agents, and invokes scripts for mechanical execution.

Every stage script follows one pattern:

1. `prepare` writes a **work file** (`<stage>/work/<item>.json`) holding exactly what an author needs
2. the agent (or one sub-agent per item) writes the **authored file** (`<stage>/<item>.json`)
3. `check` gates it deterministically with `contracts.Finding` rows (`check_id`, severity, location, message)
4. an **independent critic** sub-agent judges it through `critique.py prepare` / `check`; the script computes pass / fail / FAILED

Module API shared by every stage module: `check(input_dir, **opts)`, `check_item(input_dir, item_id, **opts)`, `main(argv)`; `validate.py` and `critique.py` import them. Shared code lives in `shared/` and is imported after `import _compat` (`from contracts import ...`). The artifact contract is `docs/artifacts.md`; `shared/contracts.py` mirrors it in code.

## Key Files

- `AGENT.md` — Operating manual for the lesson pipeline (stage commands, sub-agent templates, critique loop, resume rules, legacy raw-pipeline appendix)
- `docs/artifacts.md` — **Artifact contract**: directory layout, id formats, every file shape and threshold. Wins over any module or rubric.
- `rubrics/` — Rubrics the agent and its sub-agents follow: `qualification.md`, `concepts.md`, `regrouping.md`, `structuring.md`, `learning_objectives.md`, `synthesis.md`, `lesson_design.md`, `questions.md`, `cards.md`, `critique_protocol.md`, `review_checklist.md`, `condensation.md`, `exercises.md` (raw pipeline)
- `status.py` — Pipeline progress (raw or lesson from `manifest.pipeline`), per-stage counts, FAILED items, `RESUME HERE` marker. Run first in every session.
- `validate.py` — Machine-checks every checkpoint family; in lesson mode delegates to the stage modules and reads `critique/summary.json`. `--json` writes `validation_report.json`; `--stage upload` is the pre-upload gate. `upload.py` runs it automatically and blocks on errors.
- `qualify.py` — Stage 3: `prepare` cleans and segments `extracted/*.json` into knowledge units (`units/`); `scope-from-structure` derives `scope.json` from a legacy structure; `check` gates units, the authored `qualification/<source>.json` maps and `concepts.json`, writes `units/dedup.json`.
- `regroup.py` — Stage 4: `draft` candidate groups per concept; `check` gates `groups.json` (unit accounting, objectives, DAG hygiene, transitive reduction); `budget` fills script-owned budgets; `commit-edges` applies the critic's prerequisite verdicts.
- `synthesize.py` — Stage 5: `prepare` work files per group; `check` gates the knowledge briefs (citations, quotes, coverage, verbatim gates).
- `lessons.py` — Stage 6/7: `prepare` work files (outline or full pass, anchor exemplar); `check` gates outlines (`--outline`) and full pages (metrics → `lesson_metrics.json`); `build` assembles `structure.json` + `chunks.json`.
- `questions.py` — Stage 8: `prepare` / `check` for embedded questions, quiz items and exercises.
- `cards.py` — Stage 9: `prepare` / `check` for spaced-repetition cards.
- `critique.py` — Independent-critic harness: `prepare <stage>` work files (critic; plus test-student and blind-grader files for questions), `check <stage>` computes verdict status and repair gates into `critique/summary.json`, `status` prints the table.
- `upload.py` — Uploads a raw or lesson-mode output to Supabase (`--target default|staging|custom`); `check` runs the upload-side gates without a database; `plan` / `--dry-run` prints the row plan; `enrich` refreshes objectives, prerequisites and practice content.
- `condense.py` — Assembles condensed variants from `condensation_plan.json`
- `normalize_titles.py` — Required post-extraction pass: strips `Chapter N:` / `N.` / `Part I` prefixes and drops book-meta sections in `extracted/*.json`
- `chunk_bridge.py` — Raw pipeline only: structure + extracted → `chunks.json` (lesson mode uses `lessons.py build`)
- `analyze_images.py` — Image analysis utilities (prepare work list, run OCR, apply descriptions)
- `setup_db.py` — Apply the DuTaTo schema to a custom PostgreSQL database
- `extractors/` — `pdf`, `web`, `code`, `office`, `tabular`, `notion`, `notion_api`, `video`; `extract_source()` auto-detects by extension / URL
- `shared/contracts.py` — Enums, id regexes, thresholds, budgets, critique criteria, `Finding`, path helpers (`item_id_to_filename`)
- `shared/clean_text.py` — Page furniture, running headers, watermarks, de-hyphenation, ligatures, hard-wrap rejoin (used by `qualify.py prepare`)
- `shared/verbatim.py` — Extractive-fragment measures and the verbatim gates (`verbatim.fragment` / `jaccard5` / `density` / `ungrounded`) used by `synthesize.py` and `lessons.py`
- `shared/chunk.py` — Token counting and low-level chunking (raw pipeline)
- `shared/ocr.py` — OCR utility using EasyOCR (optional dep) for scanned content
- `tests/` — pytest suite with synthetic `tmp_path` fixtures (`tests/conftest.py` builds a complete lesson-mode curriculum); planted-defect tests live beside each stage's tests

## Commands

```bash
uv sync                        # Install dependencies
source .venv/bin/activate      # Activate venv — every command below uses plain `python`
uv run pytest -q               # Test suite

python status.py output/<name>/                       # Pipeline progress + RESUME HERE marker (no arg: list curricula)
python validate.py output/<name>/                     # Machine-check every checkpoint family that exists
python validate.py output/<name>/ --stage lessons     # One family (manifest|extracted|units|qualification|concepts|groups|syntheses|lessons|chunks|structure|questions|cards|critique|review|upload|enrich; raw: exploration|exercises|condensation)
python validate.py output/<name>/ --json [--strict]   # Also write validation_report.json; --strict fails on warnings

# Extraction (images extracted automatically when -o is given)
python -m extractors.pdf /path/to/file.pdf -o output/<name>/extracted/
python -m extractors.web https://example.com [--crawl --max-pages 50 --max-depth 1 --max-tokens 200000 --include-paths /blog/] -o output/<name>/extracted/
python -m extractors.code ./repo/ [--exclude NAME ...] -o output/<name>/extracted/
python -m extractors.office /path/to/file.pptx -o output/<name>/extracted/
python -m extractors.tabular /path/to/file.csv -o output/<name>/extracted/
python -m extractors.notion /path/to/export.zip -o output/<name>/extracted/
python -m extractors.notion_api https://www.notion.so/My-Page-abc123 [--token <token> | --oauth] [--max-pages 100 --max-depth 5] -o output/<name>/extracted/
python -m extractors.video https://youtu.be/<id> [--lang en --lang ar] [--window-seconds 300] [--whisper-model base | --no-whisper] -o output/<name>/extracted/
python -m extractors.video "https://www.youtube.com/playlist?list=<id>" --max-videos 10 -o output/<name>/extracted/
python normalize_titles.py output/<name>/ [--remap remap.json] [--dry-run]
python analyze_images.py {prepare,ocr,apply} output/<name>/

# Stage 3 — qualify
python qualify.py prepare output/<name>/ [--scope scope.json] [--source KEY ...]
python qualify.py scope-from-structure output/<name>/legacy/structure.json --area "<depth-0 title>" [--area ...] -o output/<name>/scope.json [--extracted DIR]
python qualify.py check output/<name>/ [--source KEY] [--strict]

# Stage 4 — regroup
python regroup.py draft output/<name>/
python regroup.py check output/<name>/ [--strict] [--item g:<slug>] [--no-write]
python regroup.py budget output/<name>/
python regroup.py commit-edges output/<name>/

# Stage 5 — synthesize
python synthesize.py prepare output/<name>/ [--group g:ID ...] [--force] [--max-tokens 12000]
python synthesize.py check output/<name>/ [--group g:ID] [--strict]

# Stage 6/7 — lessons + build
python lessons.py prepare output/<name>/ [--lesson g:ID ...] [--force] [--pass outline|full]
python lessons.py check output/<name>/ [--lesson g:ID] [--outline] [--strict]
python lessons.py build output/<name>/ [--allow-failing]

# Stage 8 — questions
python questions.py prepare output/<name>/ [--lesson g:ID ...] [--force] [--allow-edge-questions]
python questions.py check output/<name>/ [--lesson g:ID ...] [--allow-edge-questions] [--strict]

# Stage 9 — cards
python cards.py prepare output/<name>/ [--lesson g:ID ...] [--force]
python cards.py check output/<name>/ [--lesson g:ID ...] [--strict]

# Critique (stage = qualification|groups|syntheses|lessons|questions|cards)
python critique.py prepare <stage> output/<name>/ [--item ID ...] [--force] [--sample-seed 0]
python critique.py check <stage> output/<name>/ [--item ID] [--strict]
python critique.py status output/<name>/

# Upload (--target is required: default=SUPABASE_URL, staging=STAGING_SUPABASE_URL, custom=--db-url/--db-key)
python upload.py check output/<name>/ [--item g:<slug>]
python upload.py plan   output/<name>/ --target staging --owner user --user-email <email>
python upload.py upload output/<name>/ --target staging --owner user (--user-id <uuid> | --user-email <email>) [--update] [--replace-chunks] [--dry-run] [--skip-validation]
python upload.py upload output/<name>/ --target staging --owner org --org-id <uuid>
python upload.py enrich output/<name>/ --target staging
python upload.py --input output/<name>/ --target default --owner user --user-id <uuid> [--enrich] [--dry-run]   # legacy flag form

# Raw pipeline / variants
python chunk_bridge.py --structure output/<name>/structure.json --extracted output/<name>/extracted/ -o output/<name>/chunks.json [--max-tokens 1500] [--min-tokens 200]
python condense.py --input output/<name>/ --plan output/<name>/condensation_plan.json [--tiers detailed,classic] [--allow-concat]
python setup_db.py --db-url postgresql://... (--apply-migrations | --check)
```

Every `check` prints `[ERROR] <check_id> @ <location>: <message>` rows and `<n> errors, <m> warnings`; exit code 1 on errors (or warnings with `--strict`).

## Pipeline Stages

Lesson pipeline (`manifest.pipeline = "lesson"`, the default for new curricula):

1. **Manifest** → `manifest.json` (agent; `pipeline`, `audience`, sources)
2. **Extract** → `extracted/*.json` + `extracted/images/` (extractors, one per source; scanned PDFs OCR'd)
2b. **Normalize titles** → in-place rewrite of `extracted/*.json` (`normalize_titles.py`; idempotent)
2c. **Analyze images** (optional) → `image_analysis.json` (script prepares + OCR; agent describes)
3. **Qualify** → `units/` (script) → `qualification/<source>.json` + `concepts.json` (one author sub-agent per source; blind re-tag critique)
4. **Regroup** → `groups.draft.json` (script) → `groups.json` (agent) → `check`, `budget`, groups critique with prerequisite verdicts, `commit-edges`
5. **Synthesize** → `syntheses/<id>.json` knowledge briefs (one author per group; factored fidelity critique)
6. **Lessons** → `lessons/<id>.json` outline then full pages (one author per lesson; critique; human-approved pilot lesson becomes `lessons/anchor.json`), `lesson_metrics.json`
7. **Build** → `structure.json` + `chunks.json` (`lessons.py build`, one chunk per page)
8. **Questions** → `questions/<id>.json` (one author per lesson; critic + test student + blind grader)
9. **Cards** → `cards/<id>.json` (one author per lesson; critic)
10. **Review** → `validation_report.json` (`validate.py --json`), `critique/summary.json`, `review.json` (agent; user approval)
11. **Upload** → `upload_result.json` (`upload.py`, staging first)
12. **Condense** (optional) → `condensation_plan.json` + `variants/` (agent writes plan, script assembles)

Each stage writes a checkpoint file (one file per item for the fan-out stages) and every authored stage is gated by its `check` and an independent critique (`critique/<stage>/`, `critique/summary.json`). Sessions resume from any stage via `status.py`. The legacy raw pipeline (`exploration.json` → `structure.json` with `source_sections` → `chunk_bridge.py` → `exercises.json`) is still supported for `manifest.pipeline` absent / `"raw"` and is described in the `AGENT.md` appendix.

## Image Pipeline

All extractors support optional image extraction. When `-o` is provided, images are saved to `output/<name>/extracted/images/` and referenced in the intermediate JSON:

- **PDF**: PyMuPDF `extract_image()` — extracts embedded raster images. Scanned/picture PDFs are auto-detected and OCR'd.
- **PPTX**: `shape.image.blob` — extracts slide images
- **DOCX**: `doc.part.rels` — extracts inline images
- **Web**: HTML `<img>` tag parsing + download
- **Notion ZIP**: Image files extracted from the ZIP archive, markdown `![alt](path)` refs resolved
- **Notion API**: `image` blocks downloaded from Notion S3 URLs
- **Video**: No images extracted (videos are temporal). Transcript text is chunked into sections by chapter markers or fixed time windows.

Images flow through: extractors → `analyze_images.py` (OCR + agent descriptions) → `qualify.py prepare` (attached to the unit whose span contains them) → `lessons.py` work files list the ids a lesson may place; pages reference them as `![alt](image:<id>)` placeholders → `lessons.py build` copies the used images onto the page's chunk → `upload.py` uploads them to the Supabase Storage `curriculum-images` bucket and rewrites each placeholder to its public URL. In the raw pipeline `chunk_bridge.py` attaches images to chunks directly. The Flutter app renders images via `ChunkImageBuilder` (cached, tap-to-expand).

### Image Analysis (agent-operated)

After extraction, the agent analyzes images in three steps:

```bash
# 1. Prepare work list (lists all extracted images)
python analyze_images.py prepare output/<name>/

# 2. (Optional) Run OCR on images to extract text
python analyze_images.py ocr output/<name>/

# 3. Agent reads each image, writes description + metadata into image_analysis.json
#    (This is the agent's job — the script just stores the results)

# 4. Apply descriptions back into extracted JSONs
python analyze_images.py apply output/<name>/
```

The agent fills in `image_analysis.json` fields:
- `description`: What the image shows (used as alt_text in markdown)
- `educational_value`: "high" | "medium" | "low" | "decorative"
- `contains_diagram`, `contains_code`, `contains_text`: booleans

### OCR for Degraded Inputs

OCR is optional — install with `uv sync --extra ocr` (adds EasyOCR + PyTorch).

- **Scanned PDFs**: Auto-detected during extraction. Pages with < 50 chars of text are rendered to images and OCR'd at 200 DPI.
- **Image OCR**: `analyze_images.py ocr` runs OCR on all extracted images, filling `ocr_text` field.
- **Graceful degradation**: If EasyOCR is not installed, OCR is silently skipped — text extraction still works for non-scanned content.

## Video Transcripts

The video extractor (`extractors/video.py`) fetches transcripts from YouTube, Vimeo, Twitch, and any other yt-dlp-supported URL — plus local video files. Install `uv sync --extra video-heavy` for Whisper fallback when captions are absent.

Transcript resolution order per video: manual captions in `--lang` → auto-generated in `--lang` → any available transcript → local Whisper transcription (if the `video-heavy` extra is installed). If all paths fail the extractor raises a clear error (`--no-whisper` errors out instead of falling back).

Section boundaries: yt-dlp-reported chapters when present, else fixed windows via `--window-seconds` (default 300). Section titles are time-range-prefixed (`[MM:SS-MM:SS] …`) and carry `start_seconds`/`end_seconds` in `metadata`, so units keep their heading path and the Flutter app can deep-link to a timestamp.

Playlists are handled as a single intermediate JSON with `source_type: "video_playlist"`; section titles are episode-qualified (`Ep 03 — <video title>: [MM:SS-MM:SS] <chapter>`) to stay unique across the playlist. Transcripts with `metadata.transcript_source == "whisper"` or `metadata.is_auto_generated == true` are lower quality: score them lower at qualification and prefer written sources on conflicts.

## Notion API

The Notion API extractor supports two authentication modes:

- **Integration token**: Set `NOTION_API_TOKEN` env var or use `--token`. User creates an integration at notion.so/my-integrations and shares pages with it.
- **OAuth**: Set `NOTION_CLIENT_ID` + `NOTION_CLIENT_SECRET` env vars, use `--oauth`. Starts a local server for the OAuth redirect. Token cached in `.notion_oauth_token`.

## Condensed Variants

Curricula can have 4 depth tiers stored as separate domains linked by `domain_family`:
- **extensive** (full) → **detailed** (~10x shorter) → **classic** (~100x shorter) → **core** (~1000x shorter)
- The agent writes `condensation_plan.json` with topic selections and synthesized content (`rubrics/condensation.md`)
- `condense.py` assembles output files from the plan (no AI calls); variants are raw-shaped outputs without practice content
- `upload.py` reads `domain_family` and `variant` from the manifest
