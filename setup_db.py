"""
Custom database setup — applies Supabase migrations to a custom PostgreSQL DB.

This allows organizations to run their own database instance with the
DuTaTo schema, independent of the default Supabase project.

Usage:
  uv run python setup_db.py --db-url postgresql://user:pass@host:5432/db --apply-migrations
  uv run python setup_db.py --db-url postgresql://... --check  # Dry-run: list pending migrations
"""

import argparse
import re
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

console = Console()

# Path to migrations (relative to this script or the dutato repo)
_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent.parent / "supabase" / "migrations"


def _get_migrations() -> list[tuple[str, Path]]:
    """Get all migration files in order."""
    if not _MIGRATIONS_DIR.exists():
        console.print(f"[red]Error:[/] Migrations directory not found: {_MIGRATIONS_DIR}")
        console.print("This script must be run from within the dutato repository,")
        console.print("or migrations must be available at supabase/migrations/.")
        sys.exit(1)

    migrations = []
    for sql_file in sorted(_MIGRATIONS_DIR.glob("*.sql")):
        # Extract version number from filename (e.g., "001" from "001_initial_schema.sql")
        match = re.match(r"^(\d+)", sql_file.name)
        if match:
            version = match.group(1)
            migrations.append((version, sql_file))

    return migrations


def _get_applied_versions(conn) -> set[str]:
    """Get the set of already-applied migration versions."""
    cursor = conn.cursor()

    # Create tracking table if it doesn't exist
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS public.schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    conn.commit()

    cursor.execute("SELECT version FROM public.schema_migrations")
    return {row[0] for row in cursor.fetchall()}


def _apply_migration(conn, version: str, sql_file: Path):
    """Apply a single migration."""
    sql = sql_file.read_text(encoding="utf-8")

    cursor = conn.cursor()
    try:
        cursor.execute(sql)
        cursor.execute(
            "INSERT INTO public.schema_migrations (version) VALUES (%s)",
            (version,),
        )
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        console.print(f"    [red]Error:[/] {e}")
        return False


def check_migrations(db_url: str):
    """List all migrations and their status."""
    import psycopg2

    conn = psycopg2.connect(db_url)
    applied = _get_applied_versions(conn)
    migrations = _get_migrations()

    table = Table(title="Migration Status")
    table.add_column("Version", style="cyan")
    table.add_column("File")
    table.add_column("Status")

    pending = 0
    for version, sql_file in migrations:
        if version in applied:
            table.add_row(version, sql_file.name, "[green]Applied[/]")
        else:
            table.add_row(version, sql_file.name, "[yellow]Pending[/]")
            pending += 1

    console.print(table)
    console.print(f"\n{len(applied)} applied, {pending} pending")

    conn.close()
    return pending


def apply_migrations(db_url: str):
    """Apply all pending migrations."""
    import psycopg2

    conn = psycopg2.connect(db_url)
    applied = _get_applied_versions(conn)
    migrations = _get_migrations()

    pending = [(v, f) for v, f in migrations if v not in applied]
    if not pending:
        console.print("[green]All migrations already applied.[/]")
        conn.close()
        return

    console.print(f"[bold blue]Applying {len(pending)} migrations...[/]\n")

    success = 0
    for version, sql_file in pending:
        console.print(f"  [{version}] {sql_file.name}...", end=" ")
        if _apply_migration(conn, version, sql_file):
            console.print("[green]OK[/]")
            success += 1
        else:
            console.print("[red]FAILED[/]")
            console.print(f"\n[red]Stopped at migration {version}.[/]")
            break

    conn.close()
    console.print(f"\n[bold]Applied {success}/{len(pending)} migrations.[/]")


def main():
    parser = argparse.ArgumentParser(description="Set up custom database with DuTaTo schema")
    parser.add_argument("--db-url", required=True,
                        help="PostgreSQL connection URL (postgresql://user:pass@host:5432/db)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--apply-migrations", action="store_true",
                       help="Apply all pending migrations")
    group.add_argument("--check", action="store_true",
                       help="List migration status (dry-run)")
    args = parser.parse_args()

    if args.check:
        check_migrations(args.db_url)
    elif args.apply_migrations:
        apply_migrations(args.db_url)


if __name__ == "__main__":
    main()
