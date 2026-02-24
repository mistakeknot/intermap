# Quality Gate — intermap sprint (22 files, ~1700 lines)

**Date:** 2026-02-23
**Reviewer:** fd-quality (Flux-drive Quality & Style Reviewer)
**Scope:** Go (tools.go, registry_test.go) + Python (cross_project.py, patterns.py, live_changes.py, analyze.py, workspace.py, ignore.py, change_impact.py) + tests

---

### Findings Index

| SEVERITY | ID | Section | Title |
|----------|-----|---------|-------|
| MEDIUM | Q1 | live_changes.py | Silent exception swallowing loses extraction errors |
| MEDIUM | Q2 | live_changes.py | Symbol annotation misses body-level changes (def-line-only heuristic) |
| MEDIUM | Q3 | cross_project.py | `_scan_go_deps` regex matches non-replace directives |
| LOW | Q4 | live_changes.py | `new_count=0` clamped to 1 inflates changed_lines for pure deletions |
| LOW | Q5 | patterns.py | `_detect_go_patterns` walks vendor dirs when project_path includes vendored files |
| LOW | Q6 | cross_project.py | `Path` imported but never used |
| LOW | Q7 | workspace.py | `List`, `Union` from `typing` — project uses PEP 604/585 builtins elsewhere |
| LOW | Q8 | ignore.py | `if TYPE_CHECKING: pass` block is dead code |
| LOW | Q9 | test_live_changes.py | `_init_git_repo` ignores subprocess return codes — silent failure on CI |
| INFO | Q10 | tools.go | `crossProjectDeps` passes `root` as positional, deviates from peer tool pattern |

**Verdict: needs-changes**

---

### Summary

The three new Python modules (cross_project, patterns, live_changes) are well-structured and test-covered. The extraction and dispatch integration is clean. The most important issues are: (1) silent `except Exception: pass` in `live_changes.py` that can hide real failures with no diagnostic path, (2) the symbol annotation heuristic only catches symbols whose `def` line is in the changed hunk, missing body-level edits which is the common case, and (3) a regex in `_scan_go_deps` that can match non-replace content. The workspace/ignore modules are faithful vendor extractions — low-risk.

---

### Issues Found

**Q1. MEDIUM: Silent exception swallowing loses extraction errors — `live_changes.py:68`**

```python
except Exception:
    pass  # Extraction failure is non-fatal
```

The project's `change_impact.py` logs extraction warnings before continuing. Here there is no logging at all. When a file fails to extract (e.g., encoding issues, malformed source, unsupported language), the caller gets `symbols_affected: []` with no signal that extraction was attempted and failed. Add at minimum `logger.debug("extraction failed for %s: %s", fpath, e)`. The module has no `logger` defined; one needs to be added.

**Q2. MEDIUM: Symbol annotation misses body-level changes — `live_changes.py:47-67`**

The current heuristic reports a symbol as "affected" only when the symbol's `def` line number falls inside `changed_lines`. A change inside a function body (the common case — modifying an existing function's logic) will not appear in `symbols_affected` because `func.line_number` is the `def` line, which is above the changed hunk. This produces empty `symbols_affected` for most meaningful edits. The fix is to track each symbol's span (start to end line) and check for overlap. Many extractors already expose `end_line` or similar. Alternatively, document the limitation clearly in the docstring so callers do not rely on completeness.

**Q3. MEDIUM: `_scan_go_deps` regex matches non-replace content — `cross_project.py:101`**

```python
for match in re.finditer(r'\S+\s+=>\s+(\.\./\S+)', content):
```

This pattern matches any `=> ../path` in the file, including inside comment lines, string literals, or require blocks that happen to contain `=>`. A `go.mod` file with a comment like `// replace example.com/x => ../x` would generate a false edge. The regex should anchor to actual replace semantics. A safer pattern: `r'^\s*\S+\s+\S+\s+=>\s+(\.\./\S+)'` with `re.MULTILINE`, which requires at least a module-path + version before the arrow (matching the `require` block form). Single-line `replace` already uses the same token sequence and is also covered.

**Q4. LOW: `new_count=0` clamped to 1 inflates changed_lines for pure deletions — `live_changes.py:131`**

```python
"new_count": max(new_count, 1),
```

A unified diff hunk of `@@ -5,3 +5,0 @@` means three lines were deleted and zero new lines were inserted. Clamping `new_count` to 1 causes `changed_lines` to include line 5 even though no new content exists there. The result is a symbol whose `def` is at line 5 appearing in `symbols_affected` for a pure deletion — misleading. Pure deletions (new_count == 0) should result in an empty range: `range(start, start + 0)` is already empty, so the clamp should be removed and handled explicitly if hunk metadata is needed downstream.

**Q5. LOW: `_detect_go_patterns` does not exclude `vendor/` subdirs — `patterns.py:_detect_go_patterns`**

The `dirs[:] = [d for d in dirs if d not in {".git", "vendor", "node_modules"}]` guard only applies at each level during the walk. If `project_path` itself is passed as a monorepo root (which `cross_project_deps` allows via `detect_patterns` on each sub-project), vendored `.go` files under `vendor/` at deeper nesting levels can still be matched because the guard only prunes the name `"vendor"` at the direct child level of the walk's current `dirpath`. This is a latent issue rather than a current breakage, since `detect_patterns` is called per-project not per-monorepo-root. Worth a comment or test to document the assumption.

**Q6. LOW: `Path` imported but never used — `cross_project.py:5`**

```python
from pathlib import Path
```

`Path` is imported but all path operations in `cross_project.py` use `os.path.*`. Remove the unused import to keep the module clean and avoid lint warnings.

**Q7. LOW: `typing.List` / `typing.Union` in `workspace.py` — inconsistent with project style**

`workspace.py` uses `List[str]`, `Union[str, Path]`, and `Iterator` from `typing` (lines 1195–1196). All other new Python files in this sprint use PEP 585/604 builtins (`list[str]`, `str | Path`). Since `workspace.py` is a vendor extraction and now owned by intermap, modernize the annotations or add a note that it will be updated in a follow-up. The mismatch creates inconsistency that will generate mypy/ruff style warnings if the project ever adds strict typing.

**Q8. LOW: Dead `if TYPE_CHECKING: pass` block — `ignore.py:27-28`**

```python
if TYPE_CHECKING:
    pass
```

This block is a leftover from the original vendor source that imported something under `TYPE_CHECKING`. The `pass`-only block serves no purpose and should be removed along with the `TYPE_CHECKING` import from `typing`.

**Q9. LOW: `_init_git_repo` ignores subprocess return codes — `test_live_changes.py:1747-1757`**

All `subprocess.run` calls inside `_init_git_repo` discard their return values. On CI environments where `user.email` / `user.name` are already set globally this is fine, but on a clean container where git identity is unset, the commit calls in individual tests can fail silently, causing the test to assert against an empty git state rather than failing with a meaningful error. Use `check=True` inside `_init_git_repo` or at least assert `returncode == 0` for `git init` and `git config`.

**Q10. INFO: `crossProjectDeps` passes `root` as positional arg — `tools.go:222`**

```go
result, err := bridge.Run(ctx, "cross_project_deps", root, map[string]any{})
```

All other tools pass the project path as the first positional arg and use `pyArgs` for extra args — consistent with `codeStructure`, `impactAnalysis`, `changeImpact`. `crossProjectDeps` does the same but the `root` parameter name (vs `project` in peers) could cause confusion when reading the bridge call site. Not a bug — the Python dispatch receives `root` as the `project` positional — but consider renaming the Go-side variable to `project` for consistency, or documenting the mapping.

---

### Improvements

**I1. Add `logger` to `live_changes.py`** — The module uses structured error handling elsewhere in the codebase (e.g., `change_impact.py` has `logger = logging.getLogger(__name__)`); adding the same here enables diagnostic output without changing the non-fatal behavior.

**I2. Document symbol annotation limitation in `get_live_changes` docstring** — Until body-span overlap is implemented, the docstring should state that `symbols_affected` reports symbols whose definition line falls in a changed hunk, not all symbols whose bodies were modified. This prevents callers from treating an empty list as "no symbols changed."

**I3. Consider `DEMARCH_ROOT` env-var pattern for `test_analyze.py` and `test_code_structure.py`** — These two test files use a hardcoded relative path (`../..`) without the `DEMARCH_ROOT` env-var override that `test_cross_project.py` and `test_patterns.py` use. Consistent env-var support makes it easier to run the suite from arbitrary CWDs in CI.
