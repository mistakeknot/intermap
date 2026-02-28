"""Live change awareness - git-diff based change detection with structural annotation."""

import ast
import logging
import os
import re
import subprocess
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path

from .extractors import DefaultExtractor

logger = logging.getLogger(__name__)
_MAX_PY_SYMBOL_CACHE_ENTRIES = 2048
_MAX_PY_SYMBOL_CACHE_BYTES = 8 * 1024 * 1024
_PY_SYMBOL_CACHE: OrderedDict[
    tuple[str, int, int, int], tuple[list[dict], int]
] = OrderedDict()
_PY_SYMBOL_CACHE_BYTES = 0
_MAX_BASELINE_SYMBOL_CACHE_ENTRIES = 1024
_MAX_BASELINE_SYMBOL_CACHE_BYTES = 8 * 1024 * 1024
_BASELINE_SYMBOL_CACHE: OrderedDict[
    tuple[str, str, str], tuple[list[dict], int]
] = OrderedDict()
_BASELINE_SYMBOL_CACHE_BYTES = 0
_VALID_MODES = {"optimized", "legacy"}


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
    del language  # Reserved for future language-specific extraction controls.

    mode_raw = os.getenv("INTERMAP_LIVE_CHANGES_MODE", "optimized").strip().lower()
    if mode_raw not in _VALID_MODES:
        logger.warning(
            "live_changes.invalid_mode",
            extra={"mode": mode_raw, "fallback_mode": "legacy"},
        )
        mode = "legacy"
    else:
        mode = mode_raw

    optimized_mode = mode == "optimized"
    baseline_identity: str | None = None
    changes = (
        _get_git_diff_optimized(project_path, baseline)
        if optimized_mode
        else _get_git_diff_legacy(project_path, baseline)
    )

    total_symbols = 0
    fallback_extractor: DefaultExtractor | None = None

    for change in changes:
        fpath = os.path.join(project_path, change["file"])
        symbols: list[dict] = []

        if change["status"] != "deleted" and os.path.isfile(fpath):
            if fallback_extractor is None:
                fallback_extractor = DefaultExtractor()

            changed_ranges = _hunks_to_new_line_ranges(change["hunks"])

            if optimized_mode:
                py_symbols = _extract_python_symbol_ranges(fpath, use_cache=True)
                if py_symbols:
                    matched = [
                        sym
                        for sym in py_symbols
                        if _range_overlaps_any(changed_ranges, sym["start"], sym["end"])
                    ]
                    seen_keys = {
                        (sym["name"], sym["type"], sym["line"]) for sym in matched
                    }

                    # For pure deletions, supplement with old-side symbol spans from baseline.
                    old_deletion_ranges = _hunks_to_old_deletion_ranges(change["hunks"])
                    if old_deletion_ranges:
                        if baseline_identity is None:
                            baseline_identity = _resolve_baseline_identity(
                                project_path, baseline,
                            )
                        baseline_file = change.get("old_file", change["file"])
                        old_symbols = _extract_python_symbol_ranges_from_baseline(
                            project_path, baseline_identity, baseline_file,
                        )
                        for sym in old_symbols:
                            if _range_overlaps_any(
                                old_deletion_ranges, sym["start"], sym["end"]
                            ):
                                _append_symbol_if_missing(matched, seen_keys, sym)

                    symbols = _flatten_matched_python_symbols(matched)
                else:
                    extraction = _extract_with_logging(
                        fallback_extractor, fpath, project_path, baseline
                    )
                    if extraction is not None:
                        symbols = _symbols_from_extraction(
                            extraction,
                            lambda line: _range_contains_line(changed_ranges, line),
                        )
            else:
                # Legacy rollback mode preserves pre-span, start-line attribution.
                legacy_changed_lines = _hunks_to_legacy_changed_lines(change["hunks"])
                extraction = _extract_with_logging(
                    fallback_extractor, fpath, project_path, baseline
                )
                if extraction is not None:
                    symbols = _symbols_from_extraction(
                        extraction,
                        lambda line: line in legacy_changed_lines,
                    )

        # Keep public payload backward compatible.
        change.pop("old_file", None)
        change["symbols_affected"] = symbols
        total_symbols += len(symbols)

    return {
        "project": project_path,
        "baseline": baseline,
        "changes": changes,
        "total_files": len(changes),
        "total_symbols_affected": total_symbols,
    }


def _get_git_diff_legacy(project_path: str, baseline: str) -> list[dict]:
    """Run git diff and parse into structured changes."""
    try:
        result = subprocess.run(
            ["git", "diff", "--name-status", baseline],
            capture_output=True,
            text=False,
            cwd=project_path,
            timeout=10,
        )
        if result.returncode != 0:
            _log_git_diff_failure(
                "legacy",
                "name_status",
                project_path,
                baseline,
                returncode=result.returncode,
                stderr=(result.stderr or b"").decode("utf-8", errors="replace"),
            )
            return []

        files: dict[str, dict] = {}
        output = (result.stdout or b"").decode("utf-8", errors="replace")
        for line in output.strip().split("\n"):
            if not line:
                continue
            parts = line.split("\t")
            status_code = parts[0][0]
            old_file = None
            if status_code == "R" and len(parts) >= 3:
                old_file = parts[1]
                fname = parts[2]
            else:
                fname = parts[-1]
            status = {
                "M": "modified",
                "A": "added",
                "D": "deleted",
                "R": "renamed",
            }.get(status_code, "modified")
            files[fname] = {"file": fname, "status": status, "hunks": []}
            if old_file:
                files[fname]["old_file"] = old_file

        result = subprocess.run(
            ["git", "diff", "--unified=0", baseline],
            capture_output=True,
            text=False,
            cwd=project_path,
            timeout=10,
        )
        if result.returncode != 0:
            _log_git_diff_failure(
                "legacy",
                "unified_0",
                project_path,
                baseline,
                returncode=result.returncode,
                stderr=(result.stderr or b"").decode("utf-8", errors="replace"),
            )
            return list(files.values())

        current_file = None
        output = (result.stdout or b"").decode("utf-8", errors="replace")
        for line in output.split("\n"):
            if line.startswith("--- a/"):
                candidate = line[6:]
                if candidate in files and files[candidate]["status"] == "deleted":
                    current_file = candidate
                continue
            if line.startswith("+++ b/"):
                current_file = line[6:]
                continue
            if line.startswith("+++ /dev/null"):
                continue
            if line.startswith("@@ ") and current_file and current_file in files:
                parsed_hunk = _parse_hunk_header(line)
                if parsed_hunk is not None:
                    files[current_file]["hunks"].append(parsed_hunk)

        return list(files.values())
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        _log_git_diff_failure(
            "legacy",
            "exception",
            project_path,
            baseline,
            error_type=type(e).__name__,
            error_message=str(e),
        )
        return []


def _get_git_diff_optimized(project_path: str, baseline: str) -> list[dict]:
    """Optimized parser using a single git subprocess via --patch-with-raw."""
    try:
        result = subprocess.run(
            ["git", "diff", "--patch-with-raw", "--unified=0", baseline],
            capture_output=True,
            text=False,
            cwd=project_path,
            timeout=10,
        )
        if result.returncode != 0:
            _log_git_diff_failure(
                "optimized",
                "patch_with_raw",
                project_path,
                baseline,
                returncode=result.returncode,
                stderr=(result.stderr or b"").decode("utf-8", errors="replace"),
            )
            return []

        files: dict[str, dict] = {}
        current_file = None

        output = (result.stdout or b"").decode("utf-8", errors="replace")
        for line in output.split("\n"):
            if line.startswith(":"):
                current_file = None
                parts = line.split("\t")
                meta = parts[0].split()
                if not meta:
                    continue
                status_token = meta[-1]
                status_code = status_token[0] if status_token else "M"
                old_file = None
                if status_code == "R" and len(parts) >= 3:
                    old_file = parts[-2]
                    fname = parts[-1]
                else:
                    fname = parts[-1] if len(parts) > 1 else ""
                if not fname:
                    continue
                status = {
                    "M": "modified",
                    "A": "added",
                    "D": "deleted",
                    "R": "renamed",
                }.get(status_code, "modified")
                files[fname] = {"file": fname, "status": status, "hunks": []}
                if old_file:
                    files[fname]["old_file"] = old_file
                continue

            if line.startswith("--- a/"):
                candidate = line[6:]
                if candidate in files and files[candidate]["status"] == "deleted":
                    current_file = candidate
                continue
            if line.startswith("+++ b/"):
                current_file = line[6:]
                continue
            if line.startswith("+++ /dev/null"):
                continue
            if line.startswith("@@ ") and current_file and current_file in files:
                parsed_hunk = _parse_hunk_header(line)
                if parsed_hunk is not None:
                    files[current_file]["hunks"].append(parsed_hunk)

        return list(files.values())
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        _log_git_diff_failure(
            "optimized",
            "exception",
            project_path,
            baseline,
            error_type=type(e).__name__,
            error_message=str(e),
        )
        return []


def _parse_hunk_header(line: str) -> dict | None:
    old_match = re.search(r"-(\d+)(?:,(\d+))?", line)
    new_match = re.search(r"\+(\d+)(?:,(\d+))?", line)
    if not new_match:
        return None

    new_start = int(new_match.group(1))
    new_raw_count = new_match.group(2)
    new_count = int(new_raw_count) if new_raw_count is not None else 1

    old_start = int(old_match.group(1)) if old_match else new_start
    old_raw_count = old_match.group(2) if old_match else None
    old_count = int(old_raw_count) if old_raw_count is not None else 1

    return {
        "old_start": old_start,
        "old_count": old_count,
        "new_start": new_start,
        "new_count": new_count,
    }


def _extract_with_logging(
    extractor: DefaultExtractor,
    fpath: str,
    project_path: str,
    baseline: str,
):
    try:
        return extractor.extract(fpath)
    except Exception as e:
        logger.debug(
            "live_changes.extractor_error",
            extra={
                "file": fpath,
                "project_path": project_path,
                "baseline": baseline,
                "error_type": type(e).__name__,
                "error_message": str(e),
            },
        )
        return None


def _symbols_from_extraction(extraction, line_match: Callable[[int], bool]) -> list[dict]:
    symbols: list[dict] = []

    for func in extraction.functions:
        if line_match(func.line_number):
            symbols.append({
                "name": func.name,
                "type": "function",
                "line": func.line_number,
            })

    for cls in extraction.classes:
        if line_match(cls.line_number):
            symbols.append({
                "name": cls.name,
                "type": "class",
                "line": cls.line_number,
            })
        for method in cls.methods:
            if line_match(method.line_number):
                symbols.append({
                    "name": f"{cls.name}.{method.name}",
                    "type": "method",
                    "line": method.line_number,
                })

    return symbols


def _flatten_matched_python_symbols(matched: list[dict]) -> list[dict]:
    symbols: list[dict] = []
    matched_method_names = {m["name"] for m in matched if m["type"] == "method"}
    classes_with_matched_methods = {
        name.split(".", 1)[0] for name in matched_method_names if "." in name
    }

    for sym in matched:
        if sym["type"] == "class":
            if sym["name"] in classes_with_matched_methods:
                continue
        symbols.append({
            "name": sym["name"],
            "type": sym["type"],
            "line": sym["line"],
        })

    return symbols


def _append_symbol_if_missing(
    symbols: list[dict], seen_keys: set[tuple[str, str, int]], symbol: dict,
) -> None:
    key = (symbol["name"], symbol["type"], symbol["line"])
    if key in seen_keys:
        return
    seen_keys.add(key)
    symbols.append(symbol)


def _hunks_to_new_line_ranges(hunks: list[dict]) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    for hunk in hunks:
        start = int(hunk["new_start"])
        count = int(hunk["new_count"])
        if count > 0:
            ranges.append((start, start + count - 1))
        # Pure deletions have no new-side lines and are handled via old-side
        # baseline symbol attribution in optimized mode.

    return _merge_ranges(ranges)


def _hunks_to_old_deletion_ranges(hunks: list[dict]) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    for hunk in hunks:
        new_count = int(hunk.get("new_count", 0))
        if new_count != 0:
            continue
        old_start = int(hunk.get("old_start", hunk.get("new_start", 1)))
        old_count = int(hunk.get("old_count", 1))
        old_count = max(1, old_count)
        ranges.append((old_start, old_start + old_count - 1))
    return _merge_ranges(ranges)


def _hunks_to_legacy_changed_lines(hunks: list[dict]) -> set[int]:
    lines: set[int] = set()
    for hunk in hunks:
        start = int(hunk["new_start"])
        count = int(hunk["new_count"])
        if count > 0:
            lines.update(range(start, start + count))
    return lines


def _merge_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    if not ranges:
        return []
    ordered = sorted(ranges)
    merged = [ordered[0]]
    for start, end in ordered[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end + 1:
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return merged


def _range_overlaps_any(ranges: list[tuple[int, int]], start: int, end: int) -> bool:
    for range_start, range_end in ranges:
        if range_start <= end and start <= range_end:
            return True
    return False


def _range_contains_line(ranges: list[tuple[int, int]], line: int) -> bool:
    for start, end in ranges:
        if start <= line <= end:
            return True
    return False


def _log_git_diff_failure(
    mode: str,
    stage: str,
    project_path: str,
    baseline: str,
    *,
    returncode: int | None = None,
    stderr: str | None = None,
    error_type: str | None = None,
    error_message: str | None = None,
) -> None:
    extra = {
        "mode": mode,
        "stage": stage,
        "project_path": project_path,
        "baseline": baseline,
    }
    if returncode is not None:
        extra["returncode"] = returncode
    if stderr:
        extra["stderr"] = stderr.strip()[:500]
    if error_type:
        extra["error_type"] = error_type
    if error_message:
        extra["error_message"] = error_message
    logger.warning("live_changes.git_diff_failure", extra=extra)


def _estimate_symbols_size(symbols: list[dict]) -> int:
    # Approximate memory cost for cache budgeting.
    total = 0
    for sym in symbols:
        total += (
            len(sym.get("name", "")) + len(sym.get("type", "")) + 24
        )
    return total


def _put_symbol_cache_entry(
    key: tuple[str, int, int, int], symbols: list[dict],
) -> None:
    global _PY_SYMBOL_CACHE_BYTES
    size = _estimate_symbols_size(symbols)
    existing = _PY_SYMBOL_CACHE.pop(key, None)
    if existing is not None:
        _PY_SYMBOL_CACHE_BYTES -= existing[1]

    _PY_SYMBOL_CACHE[key] = (symbols, size)
    _PY_SYMBOL_CACHE_BYTES += size
    _PY_SYMBOL_CACHE.move_to_end(key)

    while (
        len(_PY_SYMBOL_CACHE) > _MAX_PY_SYMBOL_CACHE_ENTRIES
        or _PY_SYMBOL_CACHE_BYTES > _MAX_PY_SYMBOL_CACHE_BYTES
    ):
        _, (_, evicted_size) = _PY_SYMBOL_CACHE.popitem(last=False)
        _PY_SYMBOL_CACHE_BYTES -= evicted_size


def _put_baseline_symbol_cache_entry(
    key: tuple[str, str, str], symbols: list[dict],
) -> None:
    global _BASELINE_SYMBOL_CACHE_BYTES
    size = _estimate_symbols_size(symbols)
    existing = _BASELINE_SYMBOL_CACHE.pop(key, None)
    if existing is not None:
        _BASELINE_SYMBOL_CACHE_BYTES -= existing[1]

    _BASELINE_SYMBOL_CACHE[key] = (symbols, size)
    _BASELINE_SYMBOL_CACHE_BYTES += size
    _BASELINE_SYMBOL_CACHE.move_to_end(key)

    while (
        len(_BASELINE_SYMBOL_CACHE) > _MAX_BASELINE_SYMBOL_CACHE_ENTRIES
        or _BASELINE_SYMBOL_CACHE_BYTES > _MAX_BASELINE_SYMBOL_CACHE_BYTES
    ):
        _, (_, evicted_size) = _BASELINE_SYMBOL_CACHE.popitem(last=False)
        _BASELINE_SYMBOL_CACHE_BYTES -= evicted_size


def _extract_python_symbol_ranges(path: str, use_cache: bool = True) -> list[dict]:
    """Return Python symbol ranges using AST spans for body-overlap matching."""
    if not path.endswith(".py"):
        return []

    cache_key: tuple[str, int, int, int] | None = None
    if use_cache:
        try:
            st = os.stat(path)
            cache_key = (
                path,
                int(st.st_mtime_ns),
                int(st.st_ctime_ns),
                int(st.st_size),
            )
            cached = _PY_SYMBOL_CACHE.get(cache_key)
            if cached is not None:
                _PY_SYMBOL_CACHE.move_to_end(cache_key)
                return cached[0]
        except OSError as e:
            logger.debug(
                "live_changes.python_source_stat_error",
                extra={
                    "path": path,
                    "error_type": type(e).__name__,
                    "error_message": str(e),
                },
            )
            cache_key = None

    try:
        source = Path(path).read_text(errors="replace")
    except OSError as e:
        logger.debug(
            "live_changes.python_source_read_error",
            extra={
                "path": path,
                "error_type": type(e).__name__,
                "error_message": str(e),
            },
        )
        return []

    symbols = _extract_python_symbol_ranges_from_source(source, path)

    if use_cache and cache_key is not None:
        _put_symbol_cache_entry(cache_key, symbols)

    return symbols


def _extract_python_symbol_ranges_from_baseline(
    project_path: str, baseline_identity: str, rel_path: str,
) -> list[dict]:
    if not rel_path.endswith(".py"):
        return []

    cache_key = (project_path, baseline_identity, rel_path)
    cached = _BASELINE_SYMBOL_CACHE.get(cache_key)
    if cached is not None:
        _BASELINE_SYMBOL_CACHE.move_to_end(cache_key)
        return cached[0]

    try:
        result = subprocess.run(
            ["git", "show", f"{baseline_identity}:{rel_path}"],
            capture_output=True,
            text=False,
            cwd=project_path,
            timeout=10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        logger.debug(
            "live_changes.baseline_symbol_extract_error",
            extra={
                "project_path": project_path,
                "baseline": baseline_identity,
                "file": rel_path,
                "error_type": type(e).__name__,
                "error_message": str(e),
            },
        )
        return []

    if result.returncode != 0:
        logger.debug(
            "live_changes.baseline_symbol_extract_error",
            extra={
                "project_path": project_path,
                "baseline": baseline_identity,
                "file": rel_path,
                "returncode": result.returncode,
                "stderr": (result.stderr or b"")
                .decode("utf-8", errors="replace")
                .strip()[:500],
            },
        )
        return []

    symbols = _extract_python_symbol_ranges_from_source(
        result.stdout.decode("utf-8", errors="replace"),
        f"{rel_path}@{baseline_identity}",
    )
    _put_baseline_symbol_cache_entry(cache_key, symbols)
    return symbols


def _extract_python_symbol_ranges_from_source(source: str, filename: str) -> list[dict]:
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as e:
        logger.debug(
            "live_changes.python_ast_parse_error",
            extra={
                "filename": filename,
                "error_type": type(e).__name__,
                "error_message": str(e),
            },
        )
        return []

    symbols: list[dict] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start_line = _definition_start_lineno(node)
            symbols.append(
                {
                    "name": node.name,
                    "type": "function",
                    "line": node.lineno,
                    "start": start_line,
                    "end": getattr(node, "end_lineno", node.lineno),
                }
            )
        elif isinstance(node, ast.ClassDef):
            class_start = _definition_start_lineno(node)
            symbols.append(
                {
                    "name": node.name,
                    "type": "class",
                    "line": node.lineno,
                    "start": class_start,
                    "end": getattr(node, "end_lineno", node.lineno),
                }
            )
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    method_start = _definition_start_lineno(child)
                    symbols.append(
                        {
                            "name": f"{node.name}.{child.name}",
                            "type": "method",
                            "line": child.lineno,
                            "start": method_start,
                            "end": getattr(child, "end_lineno", child.lineno),
                        }
                    )

    return symbols


def _resolve_baseline_identity(project_path: str, baseline: str) -> str:
    """Resolve baseline ref to immutable commit identity for cache keying."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", f"{baseline}^{{commit}}"],
            capture_output=True,
            text=True,
            cwd=project_path,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return baseline

    if result.returncode != 0:
        return baseline

    resolved = result.stdout.strip()
    if not resolved:
        return baseline
    return resolved


def _definition_start_lineno(node: ast.AST) -> int:
    start = getattr(node, "lineno", 1)
    decorators = getattr(node, "decorator_list", None) or []
    if decorators:
        deco_lines = [getattr(deco, "lineno", start) for deco in decorators]
        start = min([start, *deco_lines])
    return start
