"""
Pipeline status checker — shows where a curriculum stands in the pipeline.

A new agent session should run this FIRST to understand what's already done
and where to resume.

Usage:
  uv run python status.py output/my-curriculum/
"""

import json
import sys
from datetime import datetime, timezone
from pathlib import Path


# Pipeline stages in order
STAGES = [
    ("manifest", "manifest.json", "Manifest created"),
    ("extract", "extracted/", "Sources extracted"),
    ("explore", "exploration.json", "Content explored"),
    ("structure", "structure.json", "Topic hierarchy built"),
    ("chunk", "chunks.json", "Content chunked"),
    ("exercises", "exercises.json", "Practice exercises generated"),
    ("review", "review.json", "Quality reviewed"),
    ("upload", "upload_result.json", "Uploaded to Supabase"),
    ("condense", "condensation_plan.json", "Condensed variants planned (optional)"),
]


def check_status(output_dir: Path) -> dict:
    """Check pipeline status for a curriculum output directory."""
    status = {
        "directory": str(output_dir),
        "exists": output_dir.exists(),
        "stages": {},
        "current_stage": None,
        "next_stage": None,
    }

    if not output_dir.exists():
        status["next_stage"] = "manifest"
        return status

    for stage_name, artifact, description in STAGES:
        artifact_path = output_dir / artifact

        if stage_name == "extract":
            # Check for extracted files (directory with JSONs)
            exists = artifact_path.is_dir() and any(artifact_path.glob("*.json"))
            detail = {}
            if exists:
                files = list(artifact_path.glob("*.json"))
                detail["file_count"] = len(files)
                detail["files"] = [f.name for f in sorted(files)]
        else:
            exists = artifact_path.exists()
            detail = {}
            if exists and artifact_path.suffix == ".json":
                try:
                    with open(artifact_path) as f:
                        data = json.load(f)
                    if isinstance(data, dict):
                        detail["keys"] = list(data.keys())
                    elif isinstance(data, list):
                        detail["count"] = len(data)
                except (json.JSONDecodeError, OSError):
                    detail["error"] = "Could not read JSON"

        status["stages"][stage_name] = {
            "complete": exists,
            "artifact": str(artifact),
            "description": description,
            **detail,
        }

        if exists:
            status["current_stage"] = stage_name
        elif status["next_stage"] is None:
            status["next_stage"] = stage_name

    return status


def print_status(output_dir: Path):
    """Print a human-readable pipeline status."""
    status = check_status(output_dir)

    if not status["exists"]:
        print(f"Directory not found: {output_dir}")
        print("Pipeline has not started. Begin with extraction.")
        return

    # Read manifest for context
    manifest_path = output_dir / "manifest.json"
    if manifest_path.exists():
        with open(manifest_path) as f:
            manifest = json.load(f)
        print(f"Curriculum: {manifest.get('name', '?')}")
        print(f"Domain:     {manifest.get('domain', '?')}")
        print(f"Sources:    {len(manifest.get('sources', []))}")
        print()

    print("Pipeline Status:")
    print("-" * 50)

    for stage_name, artifact, description in STAGES:
        stage = status["stages"][stage_name]
        icon = "[DONE]" if stage["complete"] else "[    ]"
        line = f"  {icon} {description}"

        # Add detail
        if stage["complete"]:
            if "file_count" in stage:
                line += f" ({stage['file_count']} files)"
            elif "count" in stage:
                line += f" ({stage['count']} items)"

        # Mark current/next
        if stage_name == status["next_stage"]:
            line += "  <-- RESUME HERE"

        print(line)

    print("-" * 50)

    if status["next_stage"]:
        print(f"\nNext step: {status['next_stage']}")
    else:
        print("\nAll stages complete!")


def main():
    if len(sys.argv) < 2:
        print("Usage: uv run python status.py output/<curriculum_name>/")
        print()
        # List all curricula in output/
        output_root = Path(__file__).parent / "output"
        if output_root.exists():
            dirs = [d for d in output_root.iterdir() if d.is_dir() and d.name != ".gitkeep"]
            if dirs:
                print("Available curricula:")
                for d in sorted(dirs):
                    manifest = d / "manifest.json"
                    name = d.name
                    if manifest.exists():
                        with open(manifest) as f:
                            name = json.load(f).get("name", d.name)
                    status = check_status(d)
                    next_stage = status["next_stage"] or "complete"
                    print(f"  {d.name}/  ({name}) — next: {next_stage}")
            else:
                print("No curricula found in output/")
        sys.exit(0)

    output_dir = Path(sys.argv[1])
    print_status(output_dir)


if __name__ == "__main__":
    main()
