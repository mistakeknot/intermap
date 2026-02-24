### Findings Index

| SEVERITY | ID | Section | Title |
|----------|-----|---------|-------|
| MEDIUM | I-01 | Boundaries & Coupling | Stale vendor copies not deleted after extraction |
| MEDIUM | I-02 | Pattern Analysis | Symbol detection misses body-only changes (live_changes.py) |
| LOW | I-03 | Boundaries & Coupling | cross_project_deps passes root as project positional arg |
| LOW | I-04 | Simplicity & YAGNI | **_kwargs absorber hides API contract erosion |

Verdict: needs-changes

---

### Summary

The sprint adds three new MCP tools following the established Go factory + Python dispatch pattern
without introducing layer violations or new cross-module coupling. The vendor extraction of
`workspace.py` and `ignore.py` is correct but incomplete — stale copies remain in `vendor/` and
should be deleted. The symbol detection in `live_changes.py` matches only definition-keyword lines,
so body-only changes produce empty `symbols_affected` output, defeating the annotation's purpose.
Two low-severity issues (bridge call convention inconsistency, silent `**_kwargs` absorber) are
safe but should be cleaned up.

---

### Issues Found

I-01. MEDIUM: Stale vendor copies not deleted after extraction — `python/intermap/vendor/workspace.py`
and `python/intermap/vendor/tldrsignore.py` still exist on disk after their promoted counterparts
(`workspace.py`, `ignore.py`) became canonical. No intermap code imports them but the "do not modify"
header remains, confusing contributors. Fix: delete both files from `vendor/`.
Reference: diff adds `python/intermap/workspace.py` (hunk +1172) and `python/intermap/ignore.py`
(hunk +599); `ls vendor/` confirms both originals remain.

I-02. MEDIUM: Symbol detection misses body-only changes — `live_changes.py` checks
`func.line_number in changed_lines` (the `def`/`func` keyword line). A modification to only the
function body will produce an empty `symbols_affected` list. The test `test_symbol_annotation`
validates only the new-function case (where `def baz` is added), not the body-edit case.
Fix: extend the check to flag a symbol when any hunk line falls within the symbol's line range.
Reference: `python/intermap/live_changes.py` lines 46-48 (diff); `python/tests/test_live_changes.py`
`test_symbol_annotation` (diff line 1814).

I-03. LOW: cross_project_deps passes root as project positional arg — `tools.go`
`crossProjectDeps` handler calls `bridge.Run(ctx, "cross_project_deps", root, map[string]any{})`,
reusing the `project` slot for the monorepo root, inconsistent with all other tools which use
`project` for the project's own directory. Semantics are correct (Python receives it as `root`) but
the convention break makes the call harder to audit. Fix: pass root as an `args` key and use an
empty string for `project`.
Reference: `internal/tools/tools.go` diff lines 222-224.

I-04. LOW: **_kwargs absorber hides API contract erosion — `analyze_change_impact` adds `**_kwargs`
to silently absorb `use_session` from any caller that has not been updated. A caller passing
`use_session=True` will silently fall through to git diff with no error. Remove the absorber once
no external callers pass `use_session`.
Reference: `python/intermap/change_impact.py` diff, `+    **_kwargs,` signature line.

---

### Improvements

P-01. Delete `vendor/workspace.py` and `vendor/tldrsignore.py` — removes dead stale copies and
eliminates the confusing "do not modify" header that no longer applies to intermap-owned files.

P-02. Add `"vendor"` to `_detect_python_patterns` prune set — matches the Go prune set
(`{".git", "vendor", "node_modules"}`) and prevents vendored Python files from generating spurious
pattern hits during scanning.
Reference: `python/intermap/patterns.py` `_detect_python_patterns` walk filter.

P-03. Remove unused `from pathlib import Path` in `cross_project.py` — import is dead; module uses
only `os.path` functions.
Reference: `python/intermap/cross_project.py` line 5.

P-04. Remove no-op `if TYPE_CHECKING: pass` block in `ignore.py` — leftover from extraction,
creates reader confusion with no purpose.
Reference: `python/intermap/ignore.py` lines 27-28.

P-05. Document the `project_lookup.setdefault` name-collision policy — state explicitly that when
two groups contain a project with the same basename, the first alphabetically wins and the second
is silently dropped.
Reference: `python/intermap/cross_project.py` line 27.

P-06. Add a body-range test case to `test_symbol_annotation` — verify behavior when only function
body lines change (not the `def` line), so any future improvement to the heuristic is regression-tested.
Reference: `python/tests/test_live_changes.py` `test_symbol_annotation`.
