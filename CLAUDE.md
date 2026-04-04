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
- `upload.py` — Uploads curriculum output to Supabase
- `chunk_bridge.py` — Converts structure + extracted content into semantic chunks
- `condense.py` — Assembles condensed variants from a condensation plan
- `shared/chunk.py` — Low-level chunking utilities (paragraph splitting, token counting)

## Commands

```bash
uv sync                        # Install dependencies
source .venv/bin/activate      # Activate venv
python status.py output/<name>/  # Check pipeline progress
python chunk_bridge.py --structure output/<name>/structure.json --extracted output/<name>/extracted/ -o output/<name>/chunks.json
python condense.py --input output/<name>/ --plan output/<name>/condensation_plan.json
python upload.py --input output/<name>/ --owner user --user-id <uuid>
```

## Pipeline Stages

1. **Manifest** → `manifest.json` (agent writes metadata)
2. **Extract** → `extracted/*.json` (scripts extract, one per source)
3. **Explore** → `exploration.json` (agent analyzes content)
4. **Structure** → `structure.json` (agent designs topic hierarchy + learning objectives + prerequisites)
5. **Chunk** → `chunks.json` (script splits content into semantic chunks)
6. **Exercises** → `exercises.json` (agent generates practice problems)
7. **Review** → `review.json` (agent validates quality)
8. **Upload** → `upload_result.json` (script uploads to Supabase)
9. **Condense** (optional) → `condensation_plan.json` + `variants/` (agent writes plan, script assembles)

Each stage writes a checkpoint file. Sessions can resume from any stage.

## Condensed Variants

Curricula can have 4 depth tiers stored as separate domains linked by `domain_family`:
- **extensive** (full) → **detailed** (~10x shorter) → **classic** (~100x shorter) → **core** (~1000x shorter)
- The agent writes `condensation_plan.json` with topic selections and synthesized content
- `condense.py` assembles output files from the plan (no AI calls)
- `upload.py` reads `domain_family` and `variant` from the manifest
