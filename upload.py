"""
Upload curriculum output to Supabase.

Reads structure.json + chunks.json + manifest.json from the output directory
and inserts into Supabase: domain → topics → content_chunks.

Usage:
  # Upload as personal curriculum
  uv run python upload.py \
    --input output/my-curriculum/ \
    --owner user --user-id <uuid>

  # Upload as org-owned curriculum
  uv run python upload.py \
    --input output/my-curriculum/ \
    --owner org --org-id <uuid>

  # Upload to a custom database
  uv run python upload.py \
    --input output/my-curriculum/ \
    --target custom --db-url <url> --db-key <key>
"""

import argparse
import json
import os
import sys
import uuid
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console

console = Console()


def _sanitize(text: str | None) -> str | None:
    """Remove null bytes that PostgreSQL rejects."""
    if text is None:
        return None
    return text.replace("\x00", "").replace("\u0000", "")


def get_client(target: str = "default", db_url: str | None = None, db_key: str | None = None):
    """Create a Supabase client for the specified target."""
    load_dotenv()

    if target == "custom":
        url = db_url or os.getenv("CUSTOM_DB_URL")
        key = db_key or os.getenv("CUSTOM_DB_SERVICE_KEY")
    else:
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

    if not url or not key:
        console.print(
            "[red]Error:[/] Database credentials not set.\n"
            "For default: set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY in .env\n"
            "For custom: use --db-url and --db-key or set CUSTOM_DB_URL and CUSTOM_DB_SERVICE_KEY"
        )
        sys.exit(1)

    from supabase import create_client
    return create_client(url, key)


def create_domain(
    client,
    name: str,
    slug: str,
    description: str = "",
    owner_id: str | None = None,
    org_id: str | None = None,
    icon_name: str = "school",
) -> str:
    """Create a new domain or return existing ID."""
    # Check if domain already exists by slug
    result = client.table("domains").select("id").eq("slug", slug).execute()
    if result.data:
        domain_id = result.data[0]["id"]
        console.print(f"  [yellow]→[/] Domain '{slug}' already exists ({domain_id})")
        return domain_id

    domain_id = str(uuid.uuid4())
    row = {
        "id": domain_id,
        "name": _sanitize(name),
        "slug": _sanitize(slug),
        "description": _sanitize(description),
        "icon_name": icon_name,
        "sort_order": 99,  # New domains go at the end
    }

    if owner_id:
        row["owner_id"] = owner_id
    if org_id:
        row["org_id"] = org_id

    client.table("domains").insert(row).execute()
    console.print(f"  [green]✓[/] Created domain: {name} ({domain_id})")
    return domain_id


def insert_topics(
    client,
    domain_id: str,
    topics: list[dict],
    parent_id: str | None = None,
) -> dict[str, str]:
    """Recursively insert topics. Returns title → id mapping."""
    path_to_id = {}

    for topic in topics:
        topic_id = str(uuid.uuid4())

        client.table("topics").insert({
            "id": topic_id,
            "book_id": None,  # Curriculum topics have no book
            "domain_id": domain_id,
            "parent_topic_id": parent_id,
            "title": _sanitize(topic["title"]),
            "depth": topic.get("depth", 0),
            "sort_order": topic.get("sort_order", 0),
        }).execute()

        path_to_id[topic["title"]] = topic_id

        for child in topic.get("children", []):
            child_paths = insert_topics(client, domain_id, [child], topic_id)
            path_to_id.update(child_paths)

    return path_to_id


def insert_chunks(
    client,
    chunks: list[dict],
    path_to_id: dict[str, str],
) -> int:
    """Insert content chunks, matching each to its topic. Returns count."""
    inserted = 0
    skipped = 0

    # Build case-insensitive lookup
    lower_to_id = {k.lower(): v for k, v in path_to_id.items()}

    batch = []
    for chunk in chunks:
        content = chunk.get("content", "").strip()
        if not content:
            skipped += 1
            continue

        topic_path = chunk.get("topic_path", [])

        # Find the most specific matching topic
        topic_id = None
        for title in reversed(topic_path):
            if title in path_to_id:
                topic_id = path_to_id[title]
                break
            if title.lower() in lower_to_id:
                topic_id = lower_to_id[title.lower()]
                break

        if not topic_id:
            skipped += 1
            continue

        meta = {"has_code": chunk.get("has_code", False)}
        extra = chunk.get("metadata", {})
        if isinstance(extra, dict):
            meta.update(extra)

        batch.append({
            "id": str(uuid.uuid4()),
            "topic_id": topic_id,
            "book_id": None,
            "chunk_index": chunk.get("chunk_index", 0),
            "content": _sanitize(content),
            "page_number": chunk.get("page_number"),
            "token_count": chunk.get("token_count"),
            "metadata": json.dumps(meta),
        })

    # Insert in batches of 50
    for i in range(0, len(batch), 50):
        sub = batch[i:i + 50]
        client.table("content_chunks").insert(sub).execute()
        inserted += len(sub)

    if skipped:
        console.print(f"    [yellow]⚠[/] {skipped} chunks skipped (empty or unmatched)")

    return inserted


def upload_curriculum(
    input_dir: Path,
    target: str = "default",
    owner_type: str = "user",
    user_id: str | None = None,
    org_id: str | None = None,
    db_url: str | None = None,
    db_key: str | None = None,
):
    """Upload a curriculum from an output directory."""
    # Load manifest
    manifest_path = input_dir / "manifest.json"
    if not manifest_path.exists():
        console.print("[red]Error:[/] manifest.json not found in input directory")
        sys.exit(1)

    with open(manifest_path, encoding="utf-8") as f:
        manifest = json.load(f)

    # Load structure
    structure_path = input_dir / "structure.json"
    if not structure_path.exists():
        console.print("[red]Error:[/] structure.json not found")
        sys.exit(1)

    with open(structure_path, encoding="utf-8") as f:
        structure = json.load(f)

    # Load chunks
    chunks_path = input_dir / "chunks.json"
    if not chunks_path.exists():
        console.print("[red]Error:[/] chunks.json not found")
        sys.exit(1)

    with open(chunks_path, encoding="utf-8") as f:
        chunks = json.load(f)

    # Get topic list (handle both flat and nested formats)
    topics = structure if isinstance(structure, list) else structure.get("topics", [])

    # Connect
    client = get_client(target, db_url, db_key)

    name = manifest.get("name", input_dir.name)
    slug = manifest.get("domain", input_dir.name).lower().replace(" ", "-")
    description = manifest.get("description", "")

    console.print(f"\n[bold blue]Uploading curriculum:[/] {name}")
    console.print(f"  Domain: {slug}")
    console.print(f"  Topics: {_count_topics(topics)}")
    console.print(f"  Chunks: {len(chunks)}")

    # Create domain
    domain_id = create_domain(
        client,
        name=name,
        slug=slug,
        description=description,
        owner_id=user_id,
        org_id=org_id if owner_type == "org" else None,
    )

    # Insert topics
    console.print("  Inserting topics...")
    path_to_id = insert_topics(client, domain_id, topics)
    console.print(f"  [green]✓[/] {len(path_to_id)} topics inserted")

    # Insert chunks
    console.print("  Inserting chunks...")
    inserted = insert_chunks(client, chunks, path_to_id)
    console.print(f"  [green]✓[/] {inserted} chunks inserted")

    console.print(f"\n  [bold green]Done![/] Domain ID: {domain_id}")
    return domain_id


def _count_topics(topics: list[dict]) -> int:
    """Count total topics including children."""
    count = 0
    for t in topics:
        count += 1
        count += _count_topics(t.get("children", []))
    return count


def main():
    parser = argparse.ArgumentParser(description="Upload curriculum to Supabase")
    parser.add_argument("--input", required=True, help="Path to curriculum output directory")
    parser.add_argument("--target", default="default", choices=["default", "custom"],
                        help="Database target")
    parser.add_argument("--owner", default="user", choices=["user", "org"],
                        help="Ownership type")
    parser.add_argument("--user-id", help="Owner user ID (UUID)")
    parser.add_argument("--org-id", help="Organization ID (UUID, required if --owner org)")
    parser.add_argument("--db-url", help="Custom database URL")
    parser.add_argument("--db-key", help="Custom database service key")
    args = parser.parse_args()

    if args.owner == "org" and not args.org_id:
        console.print("[red]Error:[/] --org-id required when --owner org")
        sys.exit(1)

    upload_curriculum(
        input_dir=Path(args.input),
        target=args.target,
        owner_type=args.owner,
        user_id=args.user_id,
        org_id=args.org_id,
        db_url=args.db_url,
        db_key=args.db_key,
    )


if __name__ == "__main__":
    main()
