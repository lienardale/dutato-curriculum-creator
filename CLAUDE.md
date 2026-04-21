# CLAUDE.md — Curriculum Creator

## Architecture: Agent-Operated Pipeline

This tool is designed to be **operated by an AI agent** (Claude Code, Codex, or similar). The division of labor is strict:

- **Python scripts** = pure data utilities (I/O, transformation, upload). They must **never call AI APIs** (no `anthropic`, `openai`, or similar SDK imports). They take structured input and produce structured output.
- **The operating agent** = does all reasoning, analysis, content synthesis, and decision-making. It reads source material, writes JSON plans, and invokes scripts for mechanical execution.

When adding a new pipeline stage, follow this pattern:
1. The agent reads inputs and makes decisions
2. The agent writes a JSON checkpoint (the "plan" or "result")
3. A Python script (if needed) performs mechanical assembly from that checkpoint
4. The agent reviews output and presents to user

## Key Files

- `AGENT.md` — Full instructions for the operating agent (pipeline stages, resume rules, sub-agent templates)
- `rubrics/` — Quality rubrics the agent follows during each stage
- `status.py` — Pipeline progress checker (agent runs this first on session start)
- `upload.py` — Uploads curriculum output to Supabase (including images to Storage)
- `chunk_bridge.py` — Converts structure + extracted content into semantic chunks
- `condense.py` — Assembles condensed variants from a condensation plan
- `analyze_images.py` — Image analysis utilities (prepare work list, run OCR, apply descriptions)
- `extractors/video.py` — YouTube / Vimeo / Twitch / local video extractor (captions → Whisper fallback)
- `shared/chunk.py` — Low-level chunking utilities (paragraph splitting, token counting)
- `shared/ocr.py` — OCR utility using EasyOCR (optional dep) for scanned content

## Commands

```bash
uv sync                        # Install dependencies
source .venv/bin/activate      # Activate venv
python status.py output/<name>/  # Check pipeline progress
python chunk_bridge.py --structure output/<name>/structure.json --extracted output/<name>/extracted/ -o output/<name>/chunks.json
python condense.py --input output/<name>/ --plan output/<name>/condensation_plan.json
python upload.py --input output/<name>/ --owner user --user-id <uuid>

# Extraction examples (images extracted automatically when -o is given):
python -m extractors.pdf /path/to/file.pdf -o output/name/extracted/
python -m extractors.web https://example.com -o output/name/extracted/
python -m extractors.office /path/to/file.pptx -o output/name/extracted/
python -m extractors.notion /path/to/export.zip -o output/name/extracted/
python -m extractors.notion_api https://www.notion.so/My-Page-abc123 -o output/name/extracted/
python -m extractors.notion_api <page_id> --token <token> -o output/name/extracted/
python -m extractors.notion_api <page_id> --oauth -o output/name/extracted/
python -m extractors.video https://youtu.be/<id> -o output/name/extracted/
python -m extractors.video "https://www.youtube.com/playlist?list=<id>" --max-videos 10 -o output/name/extracted/
python -m extractors.video https://youtu.be/<id> --lang ar --lang en -o output/name/extracted/  # non-English captions
python -m extractors.video https://youtu.be/<id> --whisper-model base -o output/name/extracted/  # Whisper fallback
```

## Pipeline Stages

1. **Manifest** → `manifest.json` (agent writes metadata)
2. **Extract** → `extracted/*.json` + `extracted/images/` (scripts extract, one per source; images extracted automatically; scanned PDFs OCR'd)
3. **Analyze Images** (if images exist) → `image_analysis.json` (script prepares list + runs OCR; agent describes each image and sets `educational_value`)
4. **Explore** → `exploration.json` (agent analyzes content, including image descriptions)
5. **Structure** → `structure.json` (agent designs topic hierarchy + learning objectives + prerequisites)
6. **Chunk** → `chunks.json` (script splits content into semantic chunks, images attached)
7. **Exercises** → `exercises.json` (agent generates practice problems)
8. **Review** → `review.json` (agent validates quality)
9. **Upload** → `upload_result.json` (script uploads to Supabase, images to Storage)
10. **Condense** (optional) → `condensation_plan.json` + `variants/` (agent writes plan, script assembles)

Each stage writes a checkpoint file. Sessions can resume from any stage.

## Image Pipeline

All extractors support optional image extraction. When `-o` is provided, images are saved to `output/<name>/extracted/images/` and referenced in the intermediate JSON:

- **PDF**: PyMuPDF `extract_image()` — extracts embedded raster images. Scanned/picture PDFs are auto-detected and OCR'd.
- **PPTX**: `shape.image.blob` — extracts slide images
- **DOCX**: `doc.part.rels` — extracts inline images
- **Web**: HTML `<img>` tag parsing + download
- **Notion ZIP**: Image files extracted from the ZIP archive, markdown `![alt](path)` refs resolved
- **Notion API**: `image` blocks downloaded from Notion S3 URLs
- **Video**: No images extracted (videos are temporal). Transcript text is chunked into sections by chapter markers or fixed time windows.

Images flow through: extractors → `analyze_images.py` (OCR + agent descriptions) → `chunk_bridge.py` (attached to chunks) → `upload.py` (uploaded to Supabase Storage `curriculum-images` bucket, markdown `![alt](url)` injected into chunk content). The Flutter app renders images via `ChunkImageBuilder` (cached, tap-to-expand).

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

Transcript resolution order per video: manual captions in `--lang` → auto-generated in `--lang` → any available transcript → local Whisper transcription (if the `video-heavy` extra is installed). If all paths fail the extractor raises a clear error.

Section boundaries: yt-dlp-reported chapters when present, else fixed windows via `--window-seconds` (default 300). Section titles are time-range-prefixed (`[MM:SS-MM:SS] …`) and carry `start_seconds`/`end_seconds` in `metadata`, so `chunk_bridge.py` matches them by title and the Flutter app can deep-link to a timestamp.

Playlists are handled as a single intermediate JSON with `source_type: "video_playlist"`; section titles are episode-qualified (`Ep 03 — <video title>: [MM:SS-MM:SS] <chapter>`) to stay unique across the playlist.

## Notion API

The Notion API extractor supports two authentication modes:

- **Integration token**: Set `NOTION_API_TOKEN` env var or use `--token`. User creates an integration at notion.so/my-integrations and shares pages with it.
- **OAuth**: Set `NOTION_CLIENT_ID` + `NOTION_CLIENT_SECRET` env vars, use `--oauth`. Starts a local server for the OAuth redirect. Token cached in `.notion_oauth_token`.

## Condensed Variants

Curricula can have 4 depth tiers stored as separate domains linked by `domain_family`:
- **extensive** (full) → **detailed** (~10x shorter) → **classic** (~100x shorter) → **core** (~1000x shorter)
- The agent writes `condensation_plan.json` with topic selections and synthesized content
- `condense.py` assembles output files from the plan (no AI calls)
- `upload.py` reads `domain_family` and `variant` from the manifest
