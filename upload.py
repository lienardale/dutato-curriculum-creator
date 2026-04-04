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
    sort_order: int = 99,
    domain_family: str | None = None,
    variant: str | None = None,
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
        "sort_order": sort_order,
    }

    if owner_id:
        row["owner_id"] = owner_id
    if org_id:
        row["org_id"] = org_id
    if domain_family:
        row["domain_family"] = _sanitize(domain_family)
    if variant:
        row["variant"] = variant

    client.table("domains").insert(row).execute()
    console.print(f"  [green]✓[/] Created domain: {name} ({domain_id})")
    return domain_id


LEVEL_NAMES = {1: "Fundamentals", 2: "Intermediate", 3: "Advanced"}
LEVEL_DESCRIPTIONS = {
    1: "Definitions, core concepts, basic examples",
    2: "Techniques, patterns, comparative analysis",
    3: "Complex applications, trade-offs, synthesis",
}


def create_curriculum_levels(
    client,
    domain_id: str,
    topics: list[dict],
) -> dict[int, str]:
    """Create curriculum_levels rows from suggested_level values. Returns level_number → id."""
    # Collect distinct levels from depth-0 topics
    levels_needed = sorted({
        t["suggested_level"]
        for t in topics
        if t.get("depth", 0) == 0 and "suggested_level" in t
    })

    if not levels_needed:
        return {}

    # Check which levels already exist for this domain
    existing = (
        client.table("curriculum_levels")
        .select("id, level_number")
        .eq("domain_id", domain_id)
        .execute()
    )
    existing_map = {row["level_number"]: row["id"] for row in existing.data}

    level_map: dict[int, str] = {}
    for level_num in levels_needed:
        if level_num in existing_map:
            level_map[level_num] = existing_map[level_num]
            continue

        level_id = str(uuid.uuid4())
        client.table("curriculum_levels").insert({
            "id": level_id,
            "domain_id": domain_id,
            "level_number": level_num,
            "name": LEVEL_NAMES.get(level_num, f"Level {level_num}"),
            "description": LEVEL_DESCRIPTIONS.get(level_num, ""),
        }).execute()
        level_map[level_num] = level_id

    created = len(level_map) - len(existing_map)
    if created > 0:
        console.print(f"  [green]✓[/] {created} curriculum levels created")
    if existing_map:
        console.print(f"  [yellow]→[/] {len(existing_map)} curriculum levels already existed")

    return level_map


def backfill_levels(
    client,
    domain_id: str,
    topics: list[dict],
    level_map: dict[int, str],
    existing_topics: dict[tuple[str, int, str | None], str],
) -> int:
    """Set curriculum_level_id on existing depth-0 topics that are missing it.

    Returns count of topics updated.
    """
    updated = 0
    for topic in topics:
        if topic.get("depth", 0) != 0 or "suggested_level" not in topic:
            continue
        level_id = level_map.get(topic["suggested_level"])
        if not level_id:
            continue

        key = (topic["title"].lower(), 0, None)
        topic_id = existing_topics.get(key)
        if not topic_id:
            continue

        client.table("topics").update({
            "curriculum_level_id": level_id,
        }).eq("id", topic_id).execute()
        updated += 1

    return updated


def find_existing_topics(
    client,
    domain_id: str,
) -> tuple[dict[tuple[str, int, str | None], str], dict[str, str]]:
    """Fetch existing topics for a domain.

    Returns:
        (key_to_id, id_to_title) where key is (lower_title, depth, lower_parent_title|None).
    """
    result = (
        client.table("topics")
        .select("id, title, depth, parent_topic_id")
        .eq("domain_id", domain_id)
        .execute()
    )

    id_to_title: dict[str, str] = {row["id"]: row["title"] for row in result.data}
    key_to_id: dict[tuple[str, int, str | None], str] = {}

    for row in result.data:
        parent_title = id_to_title.get(row["parent_topic_id"])
        key = (
            row["title"].lower(),
            row["depth"],
            parent_title.lower() if parent_title else None,
        )
        key_to_id[key] = row["id"]

    return key_to_id, id_to_title


def insert_topics(
    client,
    domain_id: str,
    topics: list[dict],
    parent_id: str | None = None,
    level_map: dict[int, str] | None = None,
    existing_topics: dict[tuple[str, int, str | None], str] | None = None,
    parent_title: str | None = None,
) -> tuple[dict[str, str], int]:
    """Recursively insert topics. Returns (title → id mapping, skipped count)."""
    path_to_id = {}
    skipped = 0

    for topic in topics:
        depth = topic.get("depth", 0)
        title = topic["title"]

        # Check if topic already exists in update mode
        existing_id = None
        if existing_topics is not None:
            key = (
                title.lower(),
                depth,
                parent_title.lower() if parent_title else None,
            )
            existing_id = existing_topics.get(key)

        if existing_id:
            path_to_id[title] = existing_id
            skipped += 1
        else:
            topic_id = str(uuid.uuid4())

            # Assign curriculum_level_id for depth-0 topics with a suggested_level
            curriculum_level_id = None
            if level_map and depth == 0 and "suggested_level" in topic:
                curriculum_level_id = level_map.get(topic["suggested_level"])

            client.table("topics").insert({
                "id": topic_id,
                "book_id": None,  # Curriculum topics have no book
                "domain_id": domain_id,
                "parent_topic_id": parent_id,
                "title": _sanitize(title),
                "depth": depth,
                "sort_order": topic.get("sort_order", 0),
                "curriculum_level_id": curriculum_level_id,
            }).execute()

            path_to_id[title] = topic_id
            existing_id = topic_id

        for child in topic.get("children", []):
            child_paths, child_skipped = insert_topics(
                client, domain_id, [child], existing_id, level_map,
                existing_topics, title,
            )
            path_to_id.update(child_paths)
            skipped += child_skipped

    return path_to_id, skipped


def insert_books(
    client,
    domain_id: str,
    sources: list[dict],
) -> dict[str, str]:
    """Create a book record for each manifest source. Returns source_path → book_id."""
    # Fetch existing books for this domain to deduplicate
    existing = (
        client.table("books")
        .select("id, title, file_name")
        .eq("domain_id", domain_id)
        .execute()
    )
    existing_by_title = {row["title"].lower(): row["id"] for row in existing.data if row["title"]}
    existing_by_file = {row["file_name"]: row["id"] for row in existing.data if row["file_name"]}

    source_to_book: dict[str, str] = {}
    created = 0
    reused = 0

    for source in sources:
        source_path = source.get("path", "")
        file_name = Path(source_path).name if source_path else None
        title = source.get("title", file_name or "Untitled")

        # Check for existing book by file_name or title
        existing_id = None
        if file_name and file_name in existing_by_file:
            existing_id = existing_by_file[file_name]
        elif title and title.lower() in existing_by_title:
            existing_id = existing_by_title[title.lower()]

        if existing_id:
            source_to_book[source_path] = existing_id
            reused += 1
        else:
            book_id = str(uuid.uuid4())
            client.table("books").insert({
                "id": book_id,
                "domain_id": domain_id,
                "title": _sanitize(title),
                "author": _sanitize(source.get("author")),
                "file_name": file_name,
            }).execute()
            source_to_book[source_path] = book_id
            created += 1

    if created:
        console.print(f"  [green]✓[/] {created} books created")
    if reused:
        console.print(f"  [yellow]→[/] {reused} books already existed")
    return source_to_book


def _walk_topics(topics: list[dict], parent_path: list[str] | None = None):
    """Yield (topic_dict, topic_path) for every node in the tree."""
    if parent_path is None:
        parent_path = []
    for item in topics:
        path = parent_path + [item["title"]]
        yield item, path
        yield from _walk_topics(item.get("children", []), path)


def insert_learning_objectives(
    client,
    path_to_id: dict[str, str],
    topics: list[dict],
) -> int:
    """Insert topic_learning_objectives from structure.json.  Returns count inserted."""
    lower_to_id = {k.lower(): v for k, v in path_to_id.items()}
    batch: list[dict] = []

    for topic, _ in _walk_topics(topics):
        objectives = topic.get("learning_objectives", [])
        if not objectives:
            continue

        title = topic["title"]
        topic_id = path_to_id.get(title) or lower_to_id.get(title.lower())
        if not topic_id:
            continue

        for i, obj in enumerate(objectives):
            batch.append({
                "id": str(uuid.uuid4()),
                "topic_id": topic_id,
                "objective_text": _sanitize(obj["text"]),
                "bloom_level": obj.get("bloom_level", "understand"),
                "sort_order": i,
            })

    # Insert in batches of 50
    inserted = 0
    for i in range(0, len(batch), 50):
        sub = batch[i:i + 50]
        client.table("topic_learning_objectives").insert(sub).execute()
        inserted += len(sub)

    return inserted


def insert_prerequisites(
    client,
    path_to_id: dict[str, str],
    topics: list[dict],
) -> int:
    """Insert topic_prerequisites from structure.json.  Returns count inserted."""
    lower_to_id = {k.lower(): v for k, v in path_to_id.items()}

    # Collect all prerequisite edges
    edges: list[dict] = []
    for topic, _ in _walk_topics(topics):
        prereqs = topic.get("prerequisites", [])
        if not prereqs:
            continue

        title = topic["title"]
        topic_id = path_to_id.get(title) or lower_to_id.get(title.lower())
        if not topic_id:
            continue

        for prereq in prereqs:
            prereq_title = prereq["topic"]
            prereq_id = path_to_id.get(prereq_title) or lower_to_id.get(prereq_title.lower())
            if not prereq_id or prereq_id == topic_id:
                continue
            edges.append({
                "id": str(uuid.uuid4()),
                "topic_id": topic_id,
                "prerequisite_topic_id": prereq_id,
                "strength": prereq.get("strength", "recommended"),
            })

    # Simple cycle detection (topological sort)
    from collections import defaultdict, deque

    graph: dict[str, set[str]] = defaultdict(set)
    in_degree: dict[str, int] = defaultdict(int)
    all_nodes: set[str] = set()
    for edge in edges:
        src = edge["prerequisite_topic_id"]
        dst = edge["topic_id"]
        if src not in graph[dst]:  # avoid double-counting
            graph[dst].add(src)
        all_nodes.update([src, dst])

    for node in all_nodes:
        in_degree.setdefault(node, 0)
    for node, deps in graph.items():
        for dep in deps:
            in_degree[node] = in_degree.get(node, 0)  # ensure key exists

    # BFS topo sort to detect cycles
    adj: dict[str, set[str]] = defaultdict(set)
    deg: dict[str, int] = defaultdict(int)
    for edge in edges:
        src = edge["prerequisite_topic_id"]
        dst = edge["topic_id"]
        adj[src].add(dst)
        deg[dst] = deg.get(dst, 0) + 1
        deg.setdefault(src, 0)

    queue = deque(n for n, d in deg.items() if d == 0)
    visited = 0
    while queue:
        node = queue.popleft()
        visited += 1
        for neighbor in adj.get(node, set()):
            deg[neighbor] -= 1
            if deg[neighbor] == 0:
                queue.append(neighbor)

    if visited < len(deg):
        console.print(
            "[yellow]Warning:[/] Prerequisite graph has cycles — "
            "skipping prerequisite insertion to avoid data inconsistency"
        )
        return 0

    # Insert in batches
    inserted = 0
    for i in range(0, len(edges), 50):
        sub = edges[i:i + 50]
        client.table("topic_prerequisites").insert(sub).execute()
        inserted += len(sub)

    return inserted


def insert_exercises(
    client,
    exercises: list[dict],
    path_to_id: dict[str, str],
    source_to_book: dict[str, str] | None = None,
) -> int:
    """Insert exercises as content_chunks with exercise metadata.  Returns count inserted."""
    lower_to_id = {k.lower(): v for k, v in path_to_id.items()}
    fallback_book_id = next(iter(source_to_book.values()), None) if source_to_book else None

    batch: list[dict] = []
    for entry in exercises:
        topic_path = entry.get("topic_path", [])
        topic_id = None
        for title in reversed(topic_path):
            topic_id = path_to_id.get(title) or lower_to_id.get(title.lower())
            if topic_id:
                break
        if not topic_id:
            continue

        for j, ex in enumerate(entry.get("exercises", [])):
            content = _sanitize(ex.get("problem_statement", ""))
            if not content:
                continue

            meta = {
                "type": "exercise",
                "has_code": bool(ex.get("expected_solution") and "```" in ex["expected_solution"]),
                "exercise": {
                    "title": ex.get("title", ""),
                    "hints": ex.get("hints", []),
                    "expected_solution": ex.get("expected_solution", ""),
                    "common_mistakes": ex.get("common_mistakes", []),
                    "bloom_level": ex.get("bloom_level", "apply"),
                    "difficulty": ex.get("difficulty", 1),
                },
            }

            batch.append({
                "id": str(uuid.uuid4()),
                "topic_id": topic_id,
                "book_id": fallback_book_id,
                "chunk_index": 1000 + j,  # offset to separate from regular chunks
                "content": content,
                "page_number": None,
                "token_count": len(content.split()),  # rough estimate
                "metadata": json.dumps(meta),
            })

    inserted = 0
    for i in range(0, len(batch), 50):
        sub = batch[i:i + 50]
        client.table("content_chunks").insert(sub).execute()
        inserted += len(sub)

    return inserted


def insert_chunks(
    client,
    chunks: list[dict],
    path_to_id: dict[str, str],
    source_to_book: dict[str, str] | None = None,
    update_mode: bool = False,
    replace_chunks: bool = False,
) -> tuple[int, int, int]:
    """Insert content chunks, matching each to its topic.

    Returns (inserted, skipped, replaced) counts.
    """
    inserted = 0
    skipped = 0
    replaced = 0

    # Build case-insensitive lookup
    lower_to_id = {k.lower(): v for k, v in path_to_id.items()}

    # Fallback book_id: use the first book if available
    fallback_book_id = (
        next(iter(source_to_book.values()))
        if source_to_book
        else None
    )

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

        # Determine book_id from chunk's _source field
        book_id = fallback_book_id
        if source_to_book:
            chunk_source = chunk.get("_source", "")
            if chunk_source and chunk_source in source_to_book:
                book_id = source_to_book[chunk_source]

        meta = {"has_code": chunk.get("has_code", False)}
        extra = chunk.get("metadata", {})
        if isinstance(extra, dict):
            meta.update(extra)

        batch.append({
            "id": str(uuid.uuid4()),
            "topic_id": topic_id,
            "book_id": book_id,
            "chunk_index": chunk.get("chunk_index", 0),
            "content": _sanitize(content),
            "page_number": chunk.get("page_number"),
            "token_count": chunk.get("token_count"),
            "metadata": json.dumps(meta),
        })

    # In update mode, handle existing chunks
    if update_mode and batch:
        topic_ids = list({row["topic_id"] for row in batch})

        # Query existing chunks in batches of 50 topic IDs
        existing_keys: set[tuple[str, int]] = set()
        topics_with_chunks: set[str] = set()
        for i in range(0, len(topic_ids), 50):
            sub_ids = topic_ids[i:i + 50]
            result = (
                client.table("content_chunks")
                .select("topic_id, chunk_index")
                .in_("topic_id", sub_ids)
                .execute()
            )
            for row in result.data:
                existing_keys.add((row["topic_id"], row["chunk_index"]))
                topics_with_chunks.add(row["topic_id"])

        if replace_chunks and topics_with_chunks:
            # Delete existing chunks for topics that have them
            for tid in topics_with_chunks:
                client.table("content_chunks").delete().eq("topic_id", tid).execute()
            replaced = len(existing_keys)
            existing_keys.clear()
            console.print(f"    [yellow]↻[/] Replacing chunks for {len(topics_with_chunks)} topics")

        if existing_keys:
            # Filter out duplicates
            original_len = len(batch)
            batch = [
                row for row in batch
                if (row["topic_id"], row["chunk_index"]) not in existing_keys
            ]
            skipped += original_len - len(batch)

    # Insert in batches of 50
    for i in range(0, len(batch), 50):
        sub = batch[i:i + 50]
        client.table("content_chunks").insert(sub).execute()
        inserted += len(sub)

    if skipped:
        console.print(f"    [yellow]⚠[/] {skipped} chunks skipped (empty, unmatched, or existing)")

    return inserted, skipped, replaced


def upload_curriculum(
    input_dir: Path,
    target: str = "default",
    owner_type: str = "user",
    user_id: str | None = None,
    org_id: str | None = None,
    db_url: str | None = None,
    db_key: str | None = None,
    update_mode: bool = False,
    replace_chunks: bool = False,
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
    sort_order = manifest.get("sort_order", 99)
    domain_family = manifest.get("domain_family")
    variant = manifest.get("variant")

    console.print(f"\n[bold blue]Uploading curriculum:[/] {name}")
    console.print(f"  Domain: {slug}")
    if variant:
        console.print(f"  Variant: {variant} (family: {domain_family})")
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
        sort_order=sort_order,
        domain_family=domain_family,
        variant=variant,
    )

    # Create curriculum levels from suggested_level values
    console.print("  Creating curriculum levels...")
    level_map = create_curriculum_levels(client, domain_id, topics)

    # In update mode, fetch existing topics for deduplication
    existing_topics = None
    if update_mode:
        existing_topics, _ = find_existing_topics(client, domain_id)
        console.print(
            f"  [yellow]Update mode:[/] {len(existing_topics)} existing topics found"
        )

        # Backfill curriculum_level_id on existing depth-0 topics
        if level_map and existing_topics:
            backfilled = backfill_levels(
                client, domain_id, topics, level_map, existing_topics,
            )
            if backfilled:
                console.print(
                    f"  [green]✓[/] {backfilled} existing topics assigned to curriculum levels"
                )

    # Create books from manifest sources
    sources = manifest.get("sources", [])
    source_to_book = {}
    if sources:
        console.print("  Creating books...")
        source_to_book = insert_books(client, domain_id, sources)

    # Insert topics
    console.print("  Inserting topics...")
    path_to_id, topics_skipped = insert_topics(
        client, domain_id, topics, level_map=level_map,
        existing_topics=existing_topics,
    )
    topics_inserted = len(path_to_id) - topics_skipped
    console.print(f"  [green]✓[/] {topics_inserted} topics inserted")
    if topics_skipped:
        console.print(f"  [yellow]→[/] {topics_skipped} topics already existed")

    # Insert chunks
    console.print("  Inserting chunks...")
    chunks_inserted, chunks_skipped, chunks_replaced = insert_chunks(
        client, chunks, path_to_id, source_to_book,
        update_mode=update_mode, replace_chunks=replace_chunks,
    )
    console.print(f"  [green]✓[/] {chunks_inserted} chunks inserted")
    if chunks_replaced:
        console.print(f"  [yellow]↻[/] {chunks_replaced} chunks replaced")

    # Insert learning objectives (if present in structure)
    objectives_inserted = 0
    has_objectives = any(
        t.get("learning_objectives") for t, _ in _walk_topics(topics)
    )
    if has_objectives:
        console.print("  Inserting learning objectives...")
        objectives_inserted = insert_learning_objectives(client, path_to_id, topics)
        console.print(f"  [green]✓[/] {objectives_inserted} objectives inserted")

    # Insert prerequisites (if present in structure)
    prerequisites_inserted = 0
    has_prereqs = any(
        t.get("prerequisites") for t, _ in _walk_topics(topics)
    )
    if has_prereqs:
        console.print("  Inserting prerequisite links...")
        prerequisites_inserted = insert_prerequisites(client, path_to_id, topics)
        console.print(f"  [green]✓[/] {prerequisites_inserted} prerequisite links inserted")

    # Insert exercises (if exercises.json exists)
    exercises_inserted = 0
    exercises_path = input_dir / "exercises.json"
    if exercises_path.exists():
        with open(exercises_path, encoding="utf-8") as f:
            exercises = json.load(f)
        console.print("  Inserting exercises...")
        exercises_inserted = insert_exercises(
            client, exercises, path_to_id, source_to_book,
        )
        console.print(f"  [green]✓[/] {exercises_inserted} exercises inserted")

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
    parser.add_argument("--update", action="store_true",
                        help="Incremental update: skip existing topics, add only new ones")
    parser.add_argument("--replace-chunks", action="store_true",
                        help="With --update: replace chunk content for existing topics")
    args = parser.parse_args()

    if args.owner == "org" and not args.org_id:
        console.print("[red]Error:[/] --org-id required when --owner org")
        sys.exit(1)

    if args.replace_chunks and not args.update:
        console.print("[red]Error:[/] --replace-chunks requires --update")
        sys.exit(1)

    upload_curriculum(
        input_dir=Path(args.input),
        target=args.target,
        owner_type=args.owner,
        user_id=args.user_id,
        org_id=args.org_id,
        db_url=args.db_url,
        db_key=args.db_key,
        update_mode=args.update,
        replace_chunks=args.replace_chunks,
    )


if __name__ == "__main__":
    main()
