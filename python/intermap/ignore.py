# Extracted from tldr-swinton vendor (originally tldrsignore.py).
# Now owned by intermap — modifications welcome.
"""Ignore file handling (.tldrsignore).

Provides gitignore-style pattern matching for excluding files from indexing.
Uses pathspec library if available, otherwise falls back to fnmatch.
"""

from __future__ import annotations

import fnmatch
import os
from pathlib import Path
try:
    import pathspec
    PATHSPEC_AVAILABLE = True
except ImportError:
    pathspec = None  # type: ignore
    PATHSPEC_AVAILABLE = False

# Default .tldrsignore template
DEFAULT_TEMPLATE = """\
# TLDR ignore patterns (gitignore syntax)
node_modules/
.venv/
venv/
env/
__pycache__/
.tox/
.nox/
.pytest_cache/
.mypy_cache/
.ruff_cache/
vendor/
dist/
build/
out/
target/
*.egg-info/
*.whl
*.pyc
*.pyo
*.so
*.dylib
*.dll
*.exe
*.bin
*.o
*.a
*.lib
.idea/
.vscode/
*.swp
*.swo
*~
.env
.env.*
*.pem
*.key
*.p12
*.pfx
.git/
.hg/
.svn/
.tldrs/
.DS_Store
Thumbs.db
"""


class _FnmatchSpec:
    """Fallback PathSpec-like implementation using fnmatch when pathspec is not installed."""

    def __init__(self, patterns: list[str]):
        self._patterns = [
            p.strip() for p in patterns
            if p.strip() and not p.strip().startswith("#")
        ]

    def match_file(self, path: str) -> bool:
        """Check if a file matches any ignore pattern."""
        normalized = path.replace("\\", "/")
        for pattern in self._patterns:
            pat = pattern.rstrip("/")
            # Directory pattern (ends with /)
            if pattern.endswith("/"):
                if normalized.startswith(pat + "/") or f"/{pat}/" in normalized or normalized == pat:
                    return True
                # Also match files inside the directory
                if f"/{pat}/" in f"/{normalized}":
                    return True
            # Glob pattern
            if fnmatch.fnmatch(normalized, pattern):
                return True
            # Check basename match for patterns without /
            if "/" not in pattern and fnmatch.fnmatch(os.path.basename(normalized), pattern):
                return True
        return False


def load_ignore_patterns(
    project_dir: str | Path,
    include_gitignore: bool = False,
) -> object:
    """Load ignore patterns from .tldrsignore.

    Returns a PathSpec-compatible matcher.
    """
    project_path = Path(project_dir)
    tldrsignore_path = project_path / ".tldrsignore"
    legacy_path = project_path / ".tldrignore"
    patterns: list[str] = []

    if include_gitignore:
        patterns.extend(_load_gitignore_patterns(project_path))

    if tldrsignore_path.exists():
        content = tldrsignore_path.read_text()
        patterns.extend(content.splitlines())
    elif legacy_path.exists():
        content = legacy_path.read_text()
        patterns.extend(content.splitlines())
    else:
        patterns.extend(DEFAULT_TEMPLATE.splitlines())

    if PATHSPEC_AVAILABLE:
        return pathspec.PathSpec.from_lines("gitignore", patterns)
    return _FnmatchSpec(patterns)


def _load_gitignore_patterns(project_path: Path) -> list[str]:
    patterns: list[str] = []
    skip_dirs = {
        ".git", ".hg", ".svn", ".tldrs", "node_modules",
        ".venv", "venv", "__pycache__", "dist", "build", "out", "target", "vendor",
    }

    for dirpath, dirnames, filenames in os.walk(project_path):
        dirnames[:] = [
            name for name in dirnames if name not in skip_dirs and not name.startswith(".git")
        ]
        if ".gitignore" not in filenames:
            continue
        gitignore_path = Path(dirpath) / ".gitignore"
        rel_dir = os.path.relpath(dirpath, project_path)
        prefix = "" if rel_dir == "." else rel_dir.replace(os.sep, "/")
        for line in gitignore_path.read_text().splitlines():
            patterns.append(_translate_gitignore_pattern(line, prefix))

    return patterns


def _translate_gitignore_pattern(pattern: str, prefix: str) -> str:
    line = pattern.rstrip("\n")
    if not line or line.lstrip().startswith("#"):
        return line

    negated = line.startswith("!")
    body = line[1:] if negated else line

    if body.startswith("\\#"):
        body = body[1:]

    if not prefix:
        return f"!{body}" if negated else body

    prefix = prefix.strip("/")
    if body.startswith("/"):
        body = body[1:]
        combined = f"{prefix}/{body}"
    elif "/" not in body:
        combined = f"{prefix}/**/{body}"
    else:
        combined = f"{prefix}/{body}"

    return f"!{combined}" if negated else combined


def should_ignore(
    file_path: str | Path,
    project_dir: str | Path,
    spec: object | None = None,
    include_gitignore: bool = False,
) -> bool:
    """Check if a file should be ignored."""
    if spec is None:
        spec = load_ignore_patterns(project_dir, include_gitignore=include_gitignore)

    project_path = Path(project_dir)
    file_path = Path(file_path)

    try:
        rel_path = file_path.relative_to(project_path)
    except ValueError:
        rel_path = file_path

    return spec.match_file(str(rel_path))
