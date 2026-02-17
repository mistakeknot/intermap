# Vendored from tldr-swinton (plugins/tldr-swinton/src/tldr_swinton/modules/core/dirty_flag.py)
# Version: 7eee4fd (2026-02-16)
# Do not modify — update the source and re-vendor.
"""Dirty flag system for lazy cache invalidation (P3).

This module implements a dirty flag mechanism to track when the cache
needs to be rebuilt due to file edits. Instead of rebuilding immediately
on every edit, we mark files as dirty and rebuild lazily on query.

Usage:
    from .dirty_flag import mark_dirty, is_dirty, clear_dirty

    # After editing a file
    mark_dirty(project_path, "src/auth.py")

    # Before running queries that need fresh data
    if is_dirty(project_path):
        rebuild_cache(project_path)  # Your rebuild logic
        clear_dirty(project_path)

    # Check how many files changed (useful for threshold tuning)
    count = get_dirty_count(project_path)
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Union


# Path to dirty flag file relative to project root
DIRTY_FILE = ".tldrs/cache/dirty.json"


def _get_dirty_path(project_path: Union[str, Path]) -> Path:
    """Get the full path to the dirty flag file."""
    return Path(project_path) / DIRTY_FILE


def _normalize_file_path(file_path: str) -> str:
    """Normalize a file path for consistent storage."""
    return file_path.replace("\\", "/")


def _get_timestamp() -> str:
    """Get current UTC timestamp in ISO format."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def mark_dirty(project_path: Union[str, Path], edited_file: str) -> None:
    """Mark a file as dirty (needing cache rebuild)."""
    dirty_path = _get_dirty_path(project_path)
    normalized_file = _normalize_file_path(edited_file)
    now = _get_timestamp()

    if dirty_path.exists():
        try:
            data = json.loads(dirty_path.read_text())
        except (json.JSONDecodeError, IOError):
            data = None
    else:
        data = None

    if data is None:
        data = {
            "dirty_files": [],
            "first_dirty_at": now,
            "last_dirty_at": now,
        }

    if normalized_file not in data["dirty_files"]:
        data["dirty_files"].append(normalized_file)

    data["last_dirty_at"] = now
    dirty_path.parent.mkdir(parents=True, exist_ok=True)
    dirty_path.write_text(json.dumps(data, indent=2))


def is_dirty(project_path: Union[str, Path]) -> bool:
    """Check if the project has dirty files needing rebuild."""
    dirty_path = _get_dirty_path(project_path)
    return dirty_path.exists()


def get_dirty_files(project_path: Union[str, Path]) -> List[str]:
    """Get the list of dirty files."""
    dirty_path = _get_dirty_path(project_path)

    if not dirty_path.exists():
        return []

    try:
        data = json.loads(dirty_path.read_text())
        return data.get("dirty_files", [])
    except (json.JSONDecodeError, IOError):
        return []


def get_dirty_count(project_path: Union[str, Path]) -> int:
    """Get the count of dirty files."""
    return len(get_dirty_files(project_path))


def clear_dirty(project_path: Union[str, Path]) -> None:
    """Clear the dirty flag (after rebuild)."""
    dirty_path = _get_dirty_path(project_path)

    try:
        dirty_path.unlink(missing_ok=True)
    except (OSError, IOError):
        pass
