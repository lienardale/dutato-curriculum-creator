"""
Assemble condensed curriculum variants from a condensation plan.

This is a data-transformation utility — all AI decisions (topic selection,
content synthesis) are made by the operating agent and written into a
condensation_plan.json file. This script reads that plan + the original
curriculum and produces standard output files (structure.json, chunks.json,
manifest.json) for each variant tier.

Usage:
  python condense.py --input output/postgresql/ --plan output/postgresql/condensation_plan.json
  python condense.py --input output/postgresql/ --plan output/postgresql/condensation_plan.json --tiers detailed
"""

import argparse
import json
import sys
from pathlib import Path

import tiktoken
from rich.console import Console
from rich.table import Table

console = Console()
_encoder = tiktoken.get_encoding("cl100k_base")


def count_tokens(text: str) -> int:
    return len(_encoder.encode(text))


# ---------------------------------------------------------------------------
# Tier metadata (used for manifest generation)
# ---------------------------------------------------------------------------

TIER_DESCRIPTIONS = {
    "detailed": "Condensed curriculum covering all major areas with focused content",
    "classic": "Crash-course curriculum focused on core concepts and practical essentials",
    "core": "Cheat-sheet curriculum covering absolute fundamentals only",
}


# ---------------------------------------------------------------------------
# Load inputs
# ---------------------------------------------------------------------------

def load_curriculum(input_dir: Path) -> tuple[dict, list[dict], list[dict]]:
    """Load manifest, structure, and chunks from the output directory."""
    manifest = json.loads((input_dir / "manifest.json").read_text(encoding="utf-8"))
    structure = json.loads((input_dir / "structure.json").read_text(encoding="utf-8"))
    chunks = json.loads((input_dir / "chunks.json").read_text(encoding="utf-8"))

    topics = structure if isinstance(structure, list) else structure.get("topics", [])
    return manifest, topics, chunks


def compute_stats(topics: list[dict], chunks: list[dict]) -> dict:
    """Compute summary stats for a curriculum."""
    def count_all(ts):
        n = 0
        for t in ts:
            n += 1
            n += count_all(t.get("children", []))
        return n

    total_tokens = sum(c.get("token_count", count_tokens(c.get("content", ""))) for c in chunks)
    return {
        "depth0_topics": len(topics),
        "total_topics": count_all(topics),
        "total_chunks": len(chunks),
        "total_tokens": total_tokens,
    }


def build_chunk_index(chunks: list[dict]) -> dict[str, list[dict]]:
    """Map leaf topic title (last element of topic_path) to its chunks."""
    index: dict[str, list[dict]] = {}
    for chunk in chunks:
        path = chunk.get("topic_path", [])
        if path:
            key = path[-1]
            index.setdefault(key, []).append(chunk)
    return index


# ---------------------------------------------------------------------------
# Assemble a single tier from the plan
# ---------------------------------------------------------------------------

def assemble_tier(
    plan_tier: list[dict],
    chunks: list[dict],
    manifest: dict,
    tier: str,
    original_stats: dict,
    output_dir: Path,
) -> dict:
    """Assemble output files for one condensed tier from a plan."""
    console.print(f"\n[bold cyan]--- Assembling {tier} variant ---[/]")

    chunk_index = build_chunk_index(chunks)
    condensed_chunks: list[dict] = []
    output_structure: list[dict] = []

    for i, topic in enumerate(plan_tier):
        children_out = []

        topic_children = topic.get("children", [])
        if not topic_children:
            # Leaf topic at depth 0 — collect its chunks
            _collect_chunks_for_leaf(
                topic, chunk_index, [topic["title"]], condensed_chunks,
            )
        else:
            for j, child in enumerate(topic_children):
                children_out.append({
                    "title": child["title"],
                    "depth": 1,
                    "sort_order": j,
                    "description": child.get("description", ""),
                    "source_sections": child.get("source_children", [child["title"]]),
                    "children": [],
                })
                _collect_chunks_for_leaf(
                    child, chunk_index,
                    [topic["title"], child["title"]],
                    condensed_chunks,
                )

        output_structure.append({
            "title": topic["title"],
            "depth": 0,
            "sort_order": i,
            "description": topic.get("description", ""),
            "suggested_level": topic.get("suggested_level", 1),
            "children": children_out,
        })

    condensed_stats = compute_stats(output_structure, condensed_chunks)

    # Build manifest
    domain_family = manifest.get("domain", "unknown").lower().replace(" ", "-")
    tier_label = tier.capitalize()
    condensed_manifest = {
        "name": f"{manifest.get('name', 'Curriculum')} ({tier_label})",
        "domain": f"{domain_family}-{tier}",
        "domain_family": domain_family,
        "variant": tier,
        "description": TIER_DESCRIPTIONS.get(tier, f"{tier_label} curriculum variant"),
        "sources": manifest.get("sources", []),
        "created_at": manifest.get("created_at"),
        "created_by": "condense.py",
        "condensed_from": str(output_dir),
        "condensation_stats": {
            "original_topics": original_stats["total_topics"],
            "condensed_topics": condensed_stats["total_topics"],
            "original_chunks": original_stats["total_chunks"],
            "condensed_chunks": condensed_stats["total_chunks"],
            "original_tokens": original_stats["total_tokens"],
            "condensed_tokens": condensed_stats["total_tokens"],
            "compression_ratio": round(
                original_stats["total_tokens"]
                / max(condensed_stats["total_tokens"], 1),
                1,
            ),
        },
    }

    # Write output
    variant_dir = output_dir / "variants" / tier
    variant_dir.mkdir(parents=True, exist_ok=True)

    (variant_dir / "manifest.json").write_text(
        json.dumps(condensed_manifest, indent=2, ensure_ascii=False), encoding="utf-8",
    )
    (variant_dir / "structure.json").write_text(
        json.dumps(output_structure, indent=2, ensure_ascii=False), encoding="utf-8",
    )
    (variant_dir / "chunks.json").write_text(
        json.dumps(condensed_chunks, indent=2, ensure_ascii=False), encoding="utf-8",
    )

    console.print(f"  Topics: {condensed_stats['total_topics']}")
    console.print(f"  Chunks: {condensed_stats['total_chunks']}")
    console.print(f"  Tokens: {condensed_stats['total_tokens']:,}")
    console.print(f"  [green]Written to {variant_dir}/[/]")

    return condensed_manifest["condensation_stats"]


def _collect_chunks_for_leaf(
    leaf: dict,
    chunk_index: dict[str, list[dict]],
    topic_path: list[str],
    out: list[dict],
) -> None:
    """Collect or create chunks for one leaf topic in the condensation plan."""
    strategy = leaf.get("condensation_strategy", "keep")

    # If the agent wrote inline content, use it directly
    if "content" in leaf:
        content = leaf["content"]
        tokens = count_tokens(content)
        out.append({
            "content": content,
            "token_count": tokens,
            "topic_path": topic_path,
            "chunk_index": len([c for c in out if c.get("topic_path") == topic_path]),
            "page_number": 1,
            "metadata": {"has_code": "```" in content},
        })
        return

    # Otherwise, copy chunks from original sources
    source_keys = leaf.get("source_children", leaf.get("source_topics", [leaf["title"]]))
    source_chunks: list[dict] = []
    for key in source_keys:
        source_chunks.extend(chunk_index.get(key, []))

    if strategy == "keep":
        # Copy original chunks as-is
        for ci, chunk in enumerate(source_chunks):
            out.append({
                "content": chunk["content"],
                "token_count": chunk.get("token_count", count_tokens(chunk["content"])),
                "topic_path": topic_path,
                "chunk_index": ci,
                "page_number": chunk.get("page_number", 1),
                "metadata": chunk.get("metadata", {"has_code": False}),
            })
    elif strategy in ("merge", "synthesize"):
        # For merge/synthesize the agent must provide "content" above.
        # If missing, fall back to concatenating sources (best effort).
        if source_chunks:
            merged = "\n\n".join(c["content"] for c in source_chunks)
            tokens = count_tokens(merged)
            has_code = any(
                c.get("has_code", False) or (c.get("metadata") or {}).get("has_code", False)
                for c in source_chunks
            )
            out.append({
                "content": merged,
                "token_count": tokens,
                "topic_path": topic_path,
                "chunk_index": 0,
                "page_number": 1,
                "metadata": {"has_code": has_code},
            })
            console.print(
                f"  [yellow]Warning:[/] '{topic_path[-1]}' uses strategy "
                f"'{strategy}' but no inline content provided — "
                f"falling back to concatenation ({tokens:,} tokens)"
            )


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def print_summary(all_stats: dict[str, dict]):
    """Print a summary table of all variants."""
    table = Table(title="Condensation Summary")
    table.add_column("Variant", style="bold")
    table.add_column("Topics", justify="right")
    table.add_column("Chunks", justify="right")
    table.add_column("Tokens", justify="right")
    table.add_column("Ratio", justify="right")

    for tier, stats in all_stats.items():
        table.add_row(
            tier.capitalize(),
            str(stats.get("condensed_topics", stats.get("original_topics", "?"))),
            str(stats.get("condensed_chunks", stats.get("original_chunks", "?"))),
            f"{stats.get('condensed_tokens', stats.get('original_tokens', 0)):,}",
            f"{stats.get('compression_ratio', 1)}x",
        )

    console.print()
    console.print(table)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Assemble condensed curriculum variants from a condensation plan",
    )
    parser.add_argument(
        "--input", required=True,
        help="Path to full curriculum output directory",
    )
    parser.add_argument(
        "--plan", required=True,
        help="Path to condensation_plan.json (written by the agent)",
    )
    parser.add_argument(
        "--tiers", default=None,
        help="Comma-separated tiers to assemble (default: all tiers in the plan)",
    )
    args = parser.parse_args()

    input_dir = Path(args.input)
    plan_path = Path(args.plan)

    if not plan_path.exists():
        console.print(f"[red]Error:[/] Plan file not found: {plan_path}")
        sys.exit(1)

    plan = json.loads(plan_path.read_text(encoding="utf-8"))

    # Plan format: {"detailed": [...topics...], "classic": [...], "core": [...]}
    available_tiers = list(plan.keys())
    tiers = (
        [t.strip() for t in args.tiers.split(",")]
        if args.tiers
        else available_tiers
    )

    for tier in tiers:
        if tier not in plan:
            console.print(
                f"[red]Error:[/] Tier '{tier}' not found in plan. "
                f"Available: {', '.join(available_tiers)}"
            )
            sys.exit(1)

    # Load curriculum
    console.print(f"[bold blue]Loading curriculum from:[/] {input_dir}")
    manifest, structure, chunks = load_curriculum(input_dir)
    original_stats = compute_stats(structure, chunks)
    console.print(
        f"  Extensive: {original_stats['depth0_topics']} depth-0 topics, "
        f"{original_stats['total_topics']} total, "
        f"{original_stats['total_chunks']} chunks, "
        f"{original_stats['total_tokens']:,} tokens"
    )

    # Assemble each tier
    all_stats: dict[str, dict] = {
        "extensive": {
            "condensed_topics": original_stats["total_topics"],
            "condensed_chunks": original_stats["total_chunks"],
            "condensed_tokens": original_stats["total_tokens"],
            "compression_ratio": 1,
        },
    }

    for tier in tiers:
        tier_stats = assemble_tier(
            plan[tier], chunks, manifest, tier, original_stats, input_dir,
        )
        all_stats[tier] = tier_stats

    print_summary(all_stats)
    console.print("\n[bold green]Done![/] Variants ready for upload.")


if __name__ == "__main__":
    main()
