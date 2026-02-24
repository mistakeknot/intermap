# Architecture Review — Intermap Sprint (~1700 lines, 22 files)

**Date:** 2026-02-23
**Reviewer:** fd-architecture (Flux-drive Architecture & Design Reviewer)
**Diff source:** `/tmp/qg-diff-1771897180.txt`

---

## Summary

The sprint adds three new MCP tools (`cross_project_deps`, `detect_patterns`, `live_changes`) following
the established Go factory + Python dispatch pattern. It also promotes two vendored modules
(`workspace.py`, `ignore.py`) into first-class intermap code and removes the `dirty_flag` session
dependency from `change_impact`. The layer structure is respected. Tests are fixture-first and
correctly skip live paths when the Demarch root is absent.

Four issues require attention before this is fully clean. One is a stale-copy risk (vendor shadow),
one is a correctness gap (symbol detection misses modified function bodies), one is a naming
inconsistency in the cross-project bridge call, and one is an undocumented `**_kwargs` suppressor
that could hide future callers passing unknown args silently.

---

## 1. Boundaries & Coupling

### Layer map — this sprint

```
Go MCP server (tools.go)
  └─ pybridge.Bridge.Run(ctx, command, project, args)
       └─ analyze.py dispatch()
            ├─ cross_project.scan_cross_project_deps(root)
            ├─ patterns.detect_patterns(project, language)
            └─ live_changes.get_live_changes(project, baseline, language)
```

All three new tools route through the existing bridge correctly. No new direct Go→Python import
coupling is introduced.

### Vendor extraction

`workspace.py` and `ignore.py` are now owned by intermap. The old vendor copies
(`python/intermap/vendor/workspace.py`, `vendor/tldrsignore.py`) still exist on disk. The `vendor/`
path is no longer imported by intermap code, but the files have not been deleted. The `vendor/`
directory retains `dirty_flag.py` plus the two now-superseded copies. This is a latent trap: a
future developer browsing `vendor/` may not realise `workspace.py` there is dead code, or may edit
the wrong file.

CLAUDE.md says "do not modify — update source and re-vendor," which now applies only to `dirty_flag`.
The documentation has been updated to say so, but the stale files remain.

### cross_project discovery vs. Go registry

`_discover_projects()` walks two levels (group → project) and requires a `.git` directory,
matching the Go `registry.Scan()` approach documented in the code comments. This alignment is good.
However, the Python scanner uses `project_lookup` keyed only on basename, while the Go registry uses
full paths. A name collision (two groups containing a project with the same name) is silently resolved
by `setdefault` — the first one wins and the second is silently ignored. The comment calls this
"amendment #10" but does not document the resolution policy.

### dirty_flag removal

`analyze_change_impact()` previously had a three-way fall-through: explicit files → session dirty
flag → git diff. The sprint removes the session path and simplifies to explicit → git. The
`**_kwargs` absorber is added to the function signature so that callers passing `use_session` do not
raise TypeError. This is defensive but creates a silent failure mode: any caller that still passes
`use_session=True` will get the git path without warning. Since the dispatch in `analyze.py` no
longer passes `use_session`, this is safe today, but the absorber hides the contract change.

---

## 2. Pattern Analysis

### Established pattern: Go factory function

All tools follow the `func toolName(bridge *pybridge.Bridge) server.ServerTool` factory shape. The
three new tools comply. No deviation.

### Dispatch pattern: analyze.py elif chain

The existing pattern is an explicit `elif command == "..."` chain with lazy imports. All three new
commands follow this pattern. No deviation.

### New pattern introduced: `language` parameter not forwarded

`cross_project_deps` accepts no `language` parameter at either the Go tool layer or the Python
function. This is correct — dependency scanning is manifest-based, not language-specific. The
parameter omission is intentional but creates a visible asymmetry with `detect_patterns` and
`live_changes`, which both accept `language`. The asymmetry is fine architecturally; it is not a
defect.

### Confidence scores in patterns.py

Pattern confidence values are hardcoded literals (0.85, 0.9, 0.95). They are not calibrated or
documented. For a read-only analysis tool used by agents to make decisions, uncalibrated confidence
output that looks authoritative is a design risk. Agents may treat a 0.95 confidence from a regex
scan as a strong signal. No validation data exists yet per PHILOSOPHY.md ("Source confidence:
inferred — no brainstorm/plan corpus").

### Symbol detection heuristic — line-membership only

`live_changes.py` detects "symbols affected" by checking whether a function's `line_number` (the
line of its `def` or `func` keyword) falls inside the set of changed lines. A change to the body
of an existing function — the most common case — will not match unless the `def` line itself is
in the diff. This means most function-body edits report zero symbols affected, defeating the
purpose of the annotation. The comment "amendment #13: removed `_symbol_overlaps` heuristic"
documents a deliberate choice, but the replacement is weaker for the stated use case.

### patterns.py does not prune vendor/

`_detect_go_patterns` prunes `{".git", "vendor", "node_modules"}`. `_detect_python_patterns` prunes
`{".git", "__pycache__", "venv", ".venv", "node_modules"}` but not `vendor/`. On a project like
intermap itself, this means vendored Python files (including the old `workspace.py` copy) will be
scanned and may generate spurious pattern hits. Adding `"vendor"` to the Python prune set matches
the Go prune set and removes the inconsistency.

---

## 3. Simplicity & YAGNI

### `**_kwargs` in `analyze_change_impact`

The absorber is the smallest possible compatibility shim. It is acceptable as a transition measure
but should be removed once callers are confirmed to not pass `use_session`. Leaving it permanently
hides future accidental args.

### `language` parameter in `live_changes.get_live_changes`

The parameter is accepted and forwarded through the Go tool → dispatch → Python function chain, but
`live_changes.py` never uses it. `DefaultExtractor` auto-detects language from file extension. The
parameter is dead at the Python layer today. It is not harmful, but it inflates the apparent surface
and implies behavior that does not exist.

### `import Path` unused in `cross_project.py`

Line 5 of `cross_project.py` imports `from pathlib import Path` but the module uses only `os.path`
functions throughout. The import is dead.

### `ignore.py` TYPE_CHECKING block

`python/intermap/ignore.py` lines 27-28 contain:
```python
if TYPE_CHECKING:
    pass
```
This is a no-op leftover from the extraction. It should be removed.

---

## Issues Found

### I-01 MEDIUM: Stale vendor copies not deleted after extraction

`python/intermap/vendor/workspace.py` and `python/intermap/vendor/tldrsignore.py` still exist on
disk after their promoted counterparts (`workspace.py`, `ignore.py`) became the canonical source.
No intermap code imports them, but they are confusing and the "do not modify" header still applies
to them, potentially misleading contributors. Smallest fix: delete both files from `vendor/` and
update `vendor/__init__.py` if it re-exports them.

Evidence: diff adds `python/intermap/workspace.py` (line 1172) and `python/intermap/ignore.py`
(line 599) while `vendor/workspace.py` and `vendor/tldrsignore.py` remain (confirmed by `ls`).

### I-02 MEDIUM: Symbol detection misses body-only changes (live_changes.py)

The current check (`func.line_number in changed_lines`) matches only when the definition keyword
line itself appears in the diff. A change that modifies only the body of an existing function —
the dominant real-world case — will produce an empty `symbols_affected` list. The existing test
`test_symbol_annotation` passes only because it adds a new function (`baz`), whose `def` line
is new. Modifying `bar`'s body would produce no symbols. The smallest fix is to extend `changed_lines`
to also cover a symbol if any line in `[func.line_number, func.line_number + func_body_lines]`
intersects the hunk — or, minimally, to flag any hunk that falls inside a function's line range
using a simple interval scan.

Evidence: `live_changes.py` lines 46-48 in the diff; `test_symbol_annotation` test validates
only the additive case.

### I-03 LOW: `cross_project_deps` passes `root` as the `project` positional arg

`tools.go` crossProjectDeps handler calls:
```go
bridge.Run(ctx, "cross_project_deps", root, map[string]any{})
```
Here `root` (the monorepo root) is passed as the `project` positional argument. All other Python
tools pass the project's own directory as `project`. The Python function `scan_cross_project_deps`
receives this as its `root` argument, so semantics are correct. But in the bridge protocol, the
`project` slot is repurposed as `root`, making the call harder to read and inconsistent with the
other six tools. An optional `args` key (`"root": root`) with an empty `project` value would be
more consistent with how `detect_patterns` and `live_changes` pass extra args.

Evidence: diff `internal/tools/tools.go` lines 222-224.

### I-04 LOW: `**_kwargs` absorber hides API contract erosion

`analyze_change_impact`'s `**_kwargs` silently swallows any unrecognised keyword argument. Callers
that pass `use_session=True` will get unexpected git-fallback behavior without any error. Remove the
absorber once the migration is confirmed complete (i.e., no external callers pass `use_session`).

Evidence: `change_impact.py` diff, `+    **_kwargs,` on the function signature line.

---

## Improvements

### P-01: Delete `vendor/workspace.py` and `vendor/tldrsignore.py`

Removes dead stale copies and eliminates the "do not modify" header confusion. One line of work.

### P-02: Add `vendor` to `_detect_python_patterns` prune set

Change `dirs[:] = [d for d in dirs if d not in {".git", "__pycache__", "venv", ".venv", "node_modules"}]`
to include `"vendor"`, matching Go's prune set and preventing vendored Python files from generating
spurious pattern hits.

### P-03: Remove unused `from pathlib import Path` in `cross_project.py`

No runtime impact; reduces reader confusion.

### P-04: Remove no-op `if TYPE_CHECKING: pass` block in `ignore.py`

Leftover from extraction. Removes a confusing stub.

### P-05: Document the `project_lookup.setdefault` name-collision policy

Add a comment stating that when two groups contain a project with the same basename, the first one
alphabetically wins. This makes the silent policy explicit for future maintainers.

### P-06: Add a body-range test case to `test_symbol_annotation`

Modify the test to also check that editing a body-only line of an existing function (not adding a
new one) is handled — even if the current behavior is "no symbols" — so regressions are caught
if the heuristic is improved.

---

## Verdict: needs-changes

Two medium issues (stale vendor copies, symbol detection gap) and two low issues should be resolved.
None block the three new tools from being safe to ship, but I-01 and I-02 should be addressed
before or shortly after merge.
