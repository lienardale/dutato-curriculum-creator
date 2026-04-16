"""
Image analysis utilities — prepare and store image descriptions.

This script is a DATA UTILITY only. It does NOT call AI APIs.
The operating agent:
  1. Runs `prepare` to get a list of images needing analysis
  2. Reads each image file and writes a description (agent does the reasoning)
  3. Runs `apply` to merge descriptions back into extracted JSONs

Usage:
  # Step 1: Agent runs prepare to get the work list
  uv run python analyze_images.py prepare output/<name>/

  # Step 2: Agent writes descriptions into image_analysis.json
  # (Agent reads images from extracted/images/ and writes analysis)

  # Step 3: Agent runs apply to merge descriptions into extracted JSONs
  uv run python analyze_images.py apply output/<name>/

  # Optional: Run OCR on all extracted images
  uv run python analyze_images.py ocr output/<name>/
"""

import argparse
import json
import sys
from pathlib import Path


def prepare_analysis(output_dir: Path) -> dict:
    """Scan extracted images and produce a work list for the agent.

    Returns a dict ready to be saved as image_analysis.json with empty
    description fields for the agent to fill in.
    """
    extracted_dir = output_dir / "extracted"
    images_dir = extracted_dir / "images"

    images: list[dict] = []

    # Scan all extracted JSONs for image references
    if extracted_dir.is_dir():
        for json_file in sorted(extracted_dir.glob("*.json")):
            with open(json_file, encoding="utf-8") as f:
                data = json.load(f)

            source_title = data.get("title", json_file.stem)
            for img in data.get("images", []):
                local_path = img.get("local_path", "")
                file_path = extracted_dir / local_path
                if not file_path.exists():
                    file_path = output_dir / local_path

                images.append({
                    "id": img["id"],
                    "local_path": img.get("local_path", ""),
                    "file_exists": file_path.exists(),
                    "mime_type": img.get("mime_type", ""),
                    "size_bytes": img.get("size_bytes", 0),
                    "source": source_title,
                    "description": "",       # Agent fills this in
                    "ocr_text": "",          # Filled by OCR or agent
                    "contains_text": None,   # Agent sets: true/false
                    "contains_diagram": None,
                    "contains_code": None,
                    "educational_value": None,  # Agent sets: "high", "medium", "low", "decorative"
                })

    return {
        "total_images": len(images),
        "analyzed": 0,
        "images": images,
    }


def run_ocr(output_dir: Path) -> int:
    """Run OCR on all extracted images and fill ocr_text in image_analysis.json."""
    analysis_path = output_dir / "image_analysis.json"
    if not analysis_path.exists():
        print("Error: image_analysis.json not found. Run 'prepare' first.")
        return 0

    with open(analysis_path, encoding="utf-8") as f:
        analysis = json.load(f)

    sys.path.insert(0, str(Path(__file__).parent / "shared"))
    from ocr import is_ocr_available, ocr_image

    if not is_ocr_available():
        print("Error: EasyOCR not installed. Run: uv add easyocr")
        return 0

    extracted_dir = output_dir / "extracted"
    ocr_count = 0

    for img in analysis.get("images", []):
        if img.get("ocr_text"):
            continue  # Already has OCR text

        local_path = img.get("local_path", "")
        file_path = extracted_dir / local_path
        if not file_path.exists():
            file_path = output_dir / local_path
        if not file_path.exists():
            continue

        try:
            text = ocr_image(file_path)
            if text and len(text.strip()) > 10:
                img["ocr_text"] = text.strip()
                img["contains_text"] = True
                ocr_count += 1
            else:
                img["contains_text"] = False
        except Exception as e:
            print(f"  Warning: OCR failed for {img['id']}: {e}")

    # Save back
    with open(analysis_path, "w", encoding="utf-8") as f:
        json.dump(analysis, f, indent=2, ensure_ascii=False)

    return ocr_count


def apply_analysis(output_dir: Path) -> int:
    """Merge image descriptions from image_analysis.json back into extracted JSONs.

    Updates image entries in each extracted/*.json with:
    - alt_text: from description (for accessibility and markdown rendering)
    - ocr_text: recognized text from the image
    - educational_value: agent's assessment

    Also updates section images and the top-level images registry.
    Returns count of images updated.
    """
    analysis_path = output_dir / "image_analysis.json"
    if not analysis_path.exists():
        print("Error: image_analysis.json not found")
        return 0

    with open(analysis_path, encoding="utf-8") as f:
        analysis = json.load(f)

    # Build lookup: image_id → analysis entry
    analysis_by_id = {img["id"]: img for img in analysis.get("images", [])}

    extracted_dir = output_dir / "extracted"
    updated = 0

    for json_file in sorted(extracted_dir.glob("*.json")):
        with open(json_file, encoding="utf-8") as f:
            data = json.load(f)

        modified = False

        # Update top-level images registry
        for img in data.get("images", []):
            entry = analysis_by_id.get(img.get("id"))
            if entry and entry.get("description"):
                img["alt_text"] = entry["description"]
                if entry.get("ocr_text"):
                    img["ocr_text"] = entry["ocr_text"]
                if entry.get("educational_value"):
                    img["educational_value"] = entry["educational_value"]
                modified = True
                updated += 1

        # Update section-level image references
        for section in data.get("sections", []):
            for img in section.get("images", []):
                entry = analysis_by_id.get(img.get("id"))
                if entry and entry.get("description"):
                    img["alt_text"] = entry["description"]

        if modified:
            with open(json_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

    return updated


def main():
    parser = argparse.ArgumentParser(
        description="Image analysis utilities for the curriculum pipeline",
    )
    parser.add_argument(
        "action",
        choices=["prepare", "ocr", "apply"],
        help="prepare: create work list, ocr: run OCR, apply: merge descriptions",
    )
    parser.add_argument("output_dir", help="Path to curriculum output directory")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)

    if args.action == "prepare":
        result = prepare_analysis(output_dir)
        analysis_path = output_dir / "image_analysis.json"
        analysis_path.parent.mkdir(parents=True, exist_ok=True)
        with open(analysis_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"Prepared {result['total_images']} images for analysis → {analysis_path}")
        print("Next: agent reads images and fills in descriptions, then runs 'apply'")

    elif args.action == "ocr":
        count = run_ocr(output_dir)
        print(f"OCR'd {count} images")

    elif args.action == "apply":
        count = apply_analysis(output_dir)
        print(f"Applied descriptions to {count} images in extracted JSONs")


if __name__ == "__main__":
    main()
