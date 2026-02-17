"""Reimplementation of get_code_structure for intermap.

Replaces the api.get_code_structure() import that analysis.py needs,
without pulling in the full API facade from tldr-swinton.
"""

from pathlib import Path

from .extractors import DefaultExtractor
from .vendor.workspace import iter_workspace_files


# Extension map for language detection
_EXT_MAP = {
    "python": {".py"},
    "typescript": {".ts", ".tsx"},
    "javascript": {".js", ".jsx"},
    "go": {".go"},
    "rust": {".rs"},
    "java": {".java"},
    "c": {".c", ".h"},
    "cpp": {".cpp", ".cc", ".cxx", ".hpp"},
}

_extractor = DefaultExtractor()


def get_code_structure(
    root: str,
    language: str = "python",
    max_results: int = 1000,
) -> dict:
    """Get code structure (functions, classes, imports) for all files in a project.

    Args:
        root: Root directory to analyze
        language: Language to analyze
        max_results: Maximum number of files to analyze

    Returns:
        Dict with {root, language, files: [{path, functions, classes, imports}]}
    """
    root_path = Path(root)
    extensions = _EXT_MAP.get(language, {".py"})

    result = {"root": str(root_path), "language": language, "files": []}

    count = 0
    for file_path in iter_workspace_files(root_path, extensions=extensions):
        if count >= max_results:
            break

        try:
            info = _extractor.extract(str(file_path))
            info_dict = info.to_dict()

            file_entry = {
                "path": str(file_path.relative_to(root_path)),
                "functions": [f["name"] for f in info_dict.get("functions", [])],
                "classes": [c["name"] for c in info_dict.get("classes", [])],
                "imports": info_dict.get("imports", []),
            }
            result["files"].append(file_entry)
            count += 1
        except Exception:
            pass

    return result
