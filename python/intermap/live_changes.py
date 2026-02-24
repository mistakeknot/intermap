"""Live change awareness — git-diff based change detection with structural annotation."""

import logging
import os
import re
import subprocess

from .extractors import DefaultExtractor

logger = logging.getLogger(__name__)


def get_live_changes(
    project_path: str, baseline: str = "HEAD", language: str = "auto"
) -> dict:
    """Detect changes since baseline and annotate with affected symbols.

    Uses git diff to find changed files, then extracts which functions/classes
    were affected by the changes (not just line numbers).

    Args:
        project_path: Project root (must be in a git repo)
        baseline: Git ref to diff against (HEAD, branch name, commit SHA)
        language: Language hint for extraction (auto-detects if "auto")

    Returns:
        Dict with project, baseline, changes list, and counts.
    """
    changes = _get_git_diff(project_path, baseline)
    extractor = DefaultExtractor()
    total_symbols = 0

    for change in changes:
        fpath = os.path.join(project_path, change["file"])
        symbols = []

        if change["status"] != "deleted" and os.path.isfile(fpath):
            try:
                extraction = extractor.extract(fpath)
                changed_lines = set()
                for hunk in change["hunks"]:
                    start = hunk["new_start"]
                    count = hunk["new_count"]
                    changed_lines.update(range(start, start + count))

                # Find functions whose start line is in the changed lines
                # (amendment #13: removed _symbol_overlaps heuristic,
                #  use direct line membership only)
                for func in extraction.functions:
                    if func.line_number in changed_lines:
                        symbols.append({
                            "name": func.name,
                            "type": "function",
                            "line": func.line_number,
                        })

                for cls in extraction.classes:
                    if cls.line_number in changed_lines:
                        symbols.append({
                            "name": cls.name,
                            "type": "class",
                            "line": cls.line_number,
                        })
                    for method in cls.methods:
                        if method.line_number in changed_lines:
                            symbols.append({
                                "name": f"{cls.name}.{method.name}",
                                "type": "method",
                                "line": method.line_number,
                            })
            except Exception as e:
                logger.debug("extraction failed for %s: %s", fpath, e)

        change["symbols_affected"] = symbols
        total_symbols += len(symbols)

    return {
        "project": project_path,
        "baseline": baseline,
        "changes": changes,
        "total_files": len(changes),
        "total_symbols_affected": total_symbols,
    }


def _get_git_diff(project_path: str, baseline: str) -> list[dict]:
    """Run git diff and parse into structured changes."""
    try:
        # Get file list with status
        result = subprocess.run(
            ["git", "diff", "--name-status", baseline],
            capture_output=True, text=True, cwd=project_path, timeout=10,
        )
        if result.returncode != 0:
            return []

        files: dict[str, dict] = {}
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            parts = line.split("\t")
            status_code = parts[0][0]  # M, A, D, R
            fname = parts[-1]
            status = {
                "M": "modified", "A": "added", "D": "deleted", "R": "renamed",
            }.get(status_code, "modified")
            files[fname] = {"file": fname, "status": status, "hunks": []}

        # Get hunk details
        # (amendment #4: check returncode after second git diff)
        result = subprocess.run(
            ["git", "diff", "--unified=0", baseline],
            capture_output=True, text=True, cwd=project_path, timeout=10,
        )
        if result.returncode != 0:
            return list(files.values())

        current_file = None
        for line in result.stdout.split("\n"):
            if line.startswith("+++ b/"):
                current_file = line[6:]
            elif line.startswith("@@ ") and current_file and current_file in files:
                # (amendment #14: parse both - and + sides, handle count=0)
                old_match = re.search(r'-(\d+)(?:,(\d+))?', line)
                new_match = re.search(r'\+(\d+)(?:,(\d+))?', line)
                if new_match:
                    new_start = int(new_match.group(1))
                    raw_count = new_match.group(2)
                    new_count = int(raw_count) if raw_count is not None else 1
                    old_start = int(old_match.group(1)) if old_match else new_start
                    files[current_file]["hunks"].append({
                        "old_start": old_start,
                        "new_start": new_start,
                        "new_count": new_count,
                    })

        return list(files.values())
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []
