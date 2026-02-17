"""Simple in-process file cache keyed by (path, mtime).

This is NOT persistent across subprocess invocations.
The Go-side cache handles persistence. This cache only deduplicates
within a single analysis call (if the same file is referenced multiple
times during one build() invocation).
"""

from .protocols import FileExtractionResult


class FileCache:
    """In-process dict cache keyed by (path, mtime)."""

    def __init__(self):
        self._cache: dict[tuple[str, float], FileExtractionResult] = {}

    def get(self, path: str, mtime: float) -> FileExtractionResult | None:
        """Return cached result if path+mtime match, else None."""
        return self._cache.get((path, mtime))

    def put(self, path: str, mtime: float, result: FileExtractionResult) -> None:
        """Cache a result keyed by path+mtime."""
        self._cache[(path, mtime)] = result

    def clear(self) -> None:
        """Clear all cached entries."""
        self._cache.clear()
