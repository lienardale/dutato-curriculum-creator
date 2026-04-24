"""
Codebase extractor — walks a directory tree and extracts source files
as sections, grouped by directory.

README is extracted first as an overview section. Binary files and
common non-source directories are skipped.
"""

import sys
from pathlib import Path

# Directories to always skip
_SKIP_DIRS = {
    ".git", ".svn", ".hg",
    "node_modules", "__pycache__", ".venv", "venv", "env",
    ".tox", ".mypy_cache", ".pytest_cache",
    "build", "dist", "target", ".next", ".nuxt",
    ".dart_tool", ".flutter-plugins",
    "vendor", "Pods",
}

# File extensions to treat as source code
_SOURCE_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".kt", ".swift",
    ".go", ".rs", ".c", ".cpp", ".h", ".hpp", ".cs",
    ".rb", ".php", ".lua", ".sh", ".bash", ".zsh",
    ".sql", ".r", ".scala", ".clj",
    ".dart", ".ex", ".exs", ".erl", ".hs",
    ".yaml", ".yml", ".toml", ".json", ".xml",
    ".md", ".txt", ".rst", ".adoc",
    ".html", ".css", ".scss", ".less",
    ".proto", ".graphql", ".tf",
    ".dockerfile", ".conf", ".ini", ".cfg",
}

# Maximum file size to read (skip large generated files)
_MAX_FILE_SIZE = 100_000  # 100KB


def _should_skip_dir(name: str, extra_skip: set[str] | None = None) -> bool:
    """Check if a directory should be skipped."""
    if name in _SKIP_DIRS or name.startswith("."):
        return True
    if extra_skip and name in extra_skip:
        return True
    return False


def _is_source_file(path: Path) -> bool:
    """Check if a file is a source file worth extracting."""
    if path.suffix.lower() in _SOURCE_EXTENSIONS:
        return True
    # Also include files with no extension if they look like configs
    if not path.suffix and path.name in {
        "Makefile", "Dockerfile", "Procfile", "Gemfile",
        "Rakefile", "Vagrantfile", ".gitignore", ".dockerignore",
    }:
        return True
    return False


def _detect_language(path: Path) -> str:
    """Detect programming language from file extension."""
    lang_map = {
        ".py": "python", ".js": "javascript", ".ts": "typescript",
        ".jsx": "jsx", ".tsx": "tsx", ".java": "java", ".kt": "kotlin",
        ".swift": "swift", ".go": "go", ".rs": "rust",
        ".c": "c", ".cpp": "cpp", ".h": "c", ".hpp": "cpp",
        ".cs": "csharp", ".rb": "ruby", ".php": "php",
        ".dart": "dart", ".sql": "sql", ".sh": "bash",
        ".yaml": "yaml", ".yml": "yaml", ".toml": "toml",
        ".json": "json", ".xml": "xml", ".html": "html",
        ".css": "css", ".scss": "scss", ".md": "markdown",
    }
    return lang_map.get(path.suffix.lower(), "text")


def extract_code(
    source: str,
    extra_skip: set[str] | None = None,
    images_dir: str | None = None,
) -> dict:
    """Extract a codebase directory to the unified intermediate format.

    `images_dir` is accepted for dispatcher compatibility but ignored — the
    code extractor does not extract images.
    """
    del images_dir
    root = Path(source).resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"Not a directory: {root}")

    sections = []

    # 1. Extract README as the first section
    readme_names = ["README.md", "README.rst", "README.txt", "README"]
    for name in readme_names:
        readme_path = root / name
        if readme_path.exists():
            content = readme_path.read_text(encoding="utf-8", errors="replace")
            sections.append({
                "title": "README",
                "content": content,
                "depth": 0,
                "metadata": {"file_path": str(readme_path.relative_to(root))},
            })
            break

    # 2. Walk the directory tree, group files by parent directory
    dir_files: dict[str, list[Path]] = {}
    for path in sorted(root.rglob("*")):
        # Skip directories
        if path.is_dir():
            continue

        # Skip if any parent dir is in skip list
        rel_parts = path.relative_to(root).parts
        if any(_should_skip_dir(part, extra_skip) for part in rel_parts[:-1]):
            continue

        # Skip non-source files
        if not _is_source_file(path):
            continue

        # Skip large files
        if path.stat().st_size > _MAX_FILE_SIZE:
            continue

        # Skip README (already handled)
        if path.name.lower().startswith("readme"):
            continue

        # Group by parent directory
        rel_dir = str(path.relative_to(root).parent)
        if rel_dir == ".":
            rel_dir = "(root)"
        dir_files.setdefault(rel_dir, []).append(path)

    # 3. Create sections per directory
    for dir_name, files in sorted(dir_files.items()):
        file_contents = []
        for f in sorted(files):
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except (OSError, UnicodeDecodeError):
                continue

            lang = _detect_language(f)
            rel_path = str(f.relative_to(root))
            file_contents.append(f"### {rel_path}\n\n```{lang}\n{text}\n```")

        if file_contents:
            sections.append({
                "title": dir_name,
                "content": "\n\n".join(file_contents),
                "depth": 1,
                "metadata": {
                    "directory": dir_name,
                    "file_count": len(files),
                },
            })

    # 4. Generate file tree as a metadata section
    tree_lines = _build_tree(root, extra_skip=extra_skip)
    if tree_lines:
        sections.insert(1 if sections else 0, {
            "title": "File Structure",
            "content": "```\n" + "\n".join(tree_lines) + "\n```",
            "depth": 0,
            "metadata": {"type": "file_tree"},
        })

    total_tokens = sum(len(s["content"].split()) for s in sections if s["content"])

    return {
        "source_type": "code",
        "source_path": str(root),
        "title": root.name,
        "author": "",
        "sections": sections,
        "metadata": {
            "total_sections": len(sections),
            "total_tokens": total_tokens,
            "root_directory": str(root),
        },
    }


def _build_tree(root: Path, max_depth: int = 3, extra_skip: set[str] | None = None) -> list[str]:
    """Build a simple file tree representation."""
    lines = [root.name + "/"]

    def _walk(path: Path, prefix: str, depth: int):
        if depth > max_depth:
            return
        entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name))
        entries = [e for e in entries if not _should_skip_dir(e.name, extra_skip)]
        for i, entry in enumerate(entries):
            is_last = i == len(entries) - 1
            connector = "└── " if is_last else "├── "
            if entry.is_dir():
                lines.append(f"{prefix}{connector}{entry.name}/")
                extension = "    " if is_last else "│   "
                _walk(entry, prefix + extension, depth + 1)
            else:
                if _is_source_file(entry):
                    lines.append(f"{prefix}{connector}{entry.name}")

    _walk(root, "", 0)
    return lines


if __name__ == "__main__":
    import json

    if len(sys.argv) < 2:
        print("Usage: python -m extractors.code <directory> [-o output_dir] "
              "[--exclude NAME ...]")
        sys.exit(1)

    from extractors import extract_source
    dir_path = sys.argv[1]
    output_dir = sys.argv[sys.argv.index("-o") + 1] if "-o" in sys.argv else None
    exclude: set[str] = set()
    i = 2
    while i < len(sys.argv):
        if sys.argv[i] == "--exclude" and i + 1 < len(sys.argv):
            exclude.add(sys.argv[i + 1])
            i += 2
        else:
            i += 1
    extra_skip = exclude or None
    result = extract_source(dir_path, output_dir, extra_skip=extra_skip)
    if not output_dir:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"Extracted {result['metadata']['total_sections']} sections "
              f"from {dir_path}")
