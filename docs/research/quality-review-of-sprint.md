# Quality Review — intermap sprint (2026-02-23)

**Scope:** 22 files, ~1700 lines. Go + Python. Three new Python modules, vendor extraction, Go tool factory additions, test path fixes.

**Full findings:** `/home/mk/projects/Demarch/interverse/intermap/.clavain/quality-gates/fd-quality.md`

---

## Overall Verdict: needs-changes

The sprint is well-structured and test-covered. The two blocking concerns are in `live_changes.py`: silent exception swallowing with no logging, and a symbol annotation heuristic that only matches `def`-line membership rather than body span — meaning most real edits produce empty `symbols_affected`. A third medium issue is a regex in `cross_project.py` that can match non-replace content including comments.

---

## Files Reviewed

- `internal/tools/tools.go` — 3 new tool factory functions (crossProjectDeps, detectPatterns, liveChanges)
- `internal/registry/registry_test.go` — path fixes (hardcoded root → dynamic monorepo walk)
- `python/intermap/cross_project.py` — NEW: monorepo dep graph via go.mod/pyproject.toml/plugin.json
- `python/intermap/patterns.py` — NEW: architectural pattern detection (HTTP, MCP tools, CLI, interfaces)
- `python/intermap/live_changes.py` — NEW: git diff + AST symbol annotation
- `python/intermap/analyze.py` — dispatch additions for 3 new commands
- `python/intermap/workspace.py` — vendor extraction (tldr-swinton), now owned by intermap
- `python/intermap/ignore.py` — vendor extraction (tldr-swinton), now owned by intermap
- `python/intermap/change_impact.py` — dirty_flag removal, use_session removal
- `python/tests/test_cross_project.py` — NEW: 6 fixture tests + 1 live monorepo test
- `python/tests/test_patterns.py` — NEW: 9 fixture tests + 1 live monorepo test
- `python/tests/test_live_changes.py` — NEW: 6 fixture tests using synthetic git repos
- `python/tests/test_analyze.py` — path fixes
- `python/tests/test_code_structure.py` — path fixes

---

## Findings Summary

### MEDIUM (3)

**Q1 — Silent exception swallowing in `live_changes.py:68`**

```python
except Exception:
    pass  # Extraction failure is non-fatal
```

No logger exists in this module. Extraction failures are silently discarded, producing `symbols_affected: []` with no diagnostic. The project convention (see `change_impact.py`) is to log at debug/warning level before continuing. Add `logger = logging.getLogger(__name__)` and replace `pass` with `logger.debug("extraction failed for %s: %s", fpath, e)`.

**Q2 — Symbol annotation misses body-level changes in `live_changes.py:47-67`**

The heuristic checks `func.line_number in changed_lines` — only matching when the `def` line itself is in the hunk. A change inside a function body (the typical case) will not trigger this. Result: `symbols_affected` is almost always empty for modifications to existing functions. Fix: check span overlap (`func.line_number` to `func.end_line` if available), or document this as a known limitation in the docstring.

**Q3 — `_scan_go_deps` regex matches non-replace content in `cross_project.py:101`**

```python
re.finditer(r'\S+\s+=>\s+(\.\./\S+)', content)
```

Matches any `=> ../path` token sequence, including inside comments. A `go.mod` with a commented-out replace directive produces false edges. Add `re.MULTILINE` and require `^\s*\S+\s+\S+\s+=>\s+(\.\./\S+)` to demand at least two tokens before the arrow, matching actual replace syntax.

### LOW (6)

**Q4 — `new_count=0` clamped to 1 inflates changed_lines for pure deletions — `live_changes.py:131`**

`max(new_count, 1)` causes a pure deletion hunk (`@@ -5,3 +5,0 @@`) to include line 5 in `changed_lines`, falsely attributing the symbol at that line as "affected." Remove the clamp; `range(start, start + 0)` is already empty and correct.

**Q5 — `patterns.py` vendor-dir guard is level-relative, not depth-recursive**

`dirs[:] = [d for d in dirs if d not in {".git", "vendor", "node_modules"}]` prunes only direct children named `"vendor"`. Nested vendor paths survive. Not a current breakage (detect_patterns is called per-project), but worth a comment documenting the assumption.

**Q6 — `Path` imported but unused in `cross_project.py:5`**

Remove `from pathlib import Path`. All path operations use `os.path.*`.

**Q7 — `typing.List` / `typing.Union` in `workspace.py` inconsistent with project style**

New Python files use PEP 585/604 (`list[str]`, `str | Path`). `workspace.py` uses `typing.List`, `typing.Union`. Since this module is now owned by intermap, plan modernization or note it as a follow-up.

**Q8 — Dead `if TYPE_CHECKING: pass` in `ignore.py:27-28`**

Remove the empty `TYPE_CHECKING` block and its import — it is a leftover from the vendor source.

**Q9 — `_init_git_repo` ignores subprocess return codes in `test_live_changes.py:1747-1757`**

Silent failures in git setup cause tests to assert against empty state rather than fail with useful messages. Use `check=True` or assert `returncode == 0` at minimum for `git init`.

### INFO (1)

**Q10 — `crossProjectDeps` variable named `root` vs `project` in peers — `tools.go:222`**

Not a bug. Minor naming inconsistency: Go-side variable is `root` while peer tools use `project`. Consider renaming to `project` for consistency in the bridge call site.

---

## Improvements

**I1.** Add `logger = logging.getLogger(__name__)` to `live_changes.py` and use it in the exception handler.

**I2.** Document the symbol annotation limitation (def-line only) in `get_live_changes` docstring until body-span overlap is implemented.

**I3.** Add `DEMARCH_ROOT` env-var override to `test_analyze.py` and `test_code_structure.py` for consistency with the pattern used in `test_cross_project.py` and `test_patterns.py`.

---

## What Went Well

- Test strategy is strong: fixture-based tests for all three new modules, live monorepo tests skipped gracefully when root is not present.
- Vendor extraction is clean: import sites updated consistently across 4 files (`change_impact.py`, `code_structure.py`, `cross_file_calls.py`, `project_index.py`).
- `dirty_flag` / `use_session` removal is complete and leaves no dangling references.
- Go tool factories follow the existing `ServerTool` factory pattern exactly.
- `findDemarchRoot` test helper is a correct improvement over the hardcoded `/root/projects/Interverse` path.
- Deduplication in `scan_cross_project_deps` uses `(project, type)` key — correct and intentional.
