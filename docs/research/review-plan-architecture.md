# Architecture Review: Intermap Project-Level Code Mapping Sprint

**Plan:** `/home/mk/projects/Demarch/docs/plans/2026-02-23-intermap-project-level-code-mapping.md`
**Reviewed against:** `/home/mk/projects/Demarch/interverse/intermap/`
**Date:** 2026-02-23

---

## Summary

The plan is sound at a high level. The Go/Python split is well-established, the bridge contract is clear, and the three new tools fit the existing dispatch pattern cleanly. The structural risks are concentrated in two areas: Task 3 (vendor extraction) leaves a hidden cross-layer dependency that needs explicit attention, and Tasks 7-9 (new tools) introduce implementation-level issues that will cause test failures and architectural drift if executed as written. The remaining tasks are correct as specified.

---

## 1. Boundaries and Coupling

### 1.1 The vendor extraction boundary is inverted (Task 3) — MUST FIX

`dirty_flag.py` uses `.tldrs/cache/dirty.json` as its state file path. This is a tldr-swinton–specific path convention. The file header says "Do not modify — update the source and re-vendor." Extracting this file to `python/intermap/dirty_flag.py` without changing the path constant violates the extraction's purpose: intermap would now own a module that writes to a `.tldrs/` directory, which is a tldr-swinton concern baked into the implementation.

`change_impact.py` imports `get_dirty_files` from `vendor.dirty_flag`. After extraction, it will call intermap's own copy. The question is whether intermap should own dirty-flag state at all. Looking at the call site in `change_impact.py`, `get_dirty_files` is used as one source of changed files (alongside git diff and session tracking). This is not a core intermap responsibility — it is a cached-state mechanism specific to the tldr-swinton session model.

**Smallest viable fix:** In Task 3, do not promote `dirty_flag.py` to a first-class intermap module. Instead, in `change_impact.py`, remove the `from .vendor.dirty_flag import get_dirty_files` path entirely when `use_session=False` (which is the intermap default). The `get_dirty_files` call only fires when `use_session=True`, a mode that has no MCP tool caller in intermap's current `tools.go`. Deleting that dead import path at extraction time prevents the tldr-swinton state model from silently entering intermap's codebase.

If `use_session` mode is genuinely needed later, it belongs behind a protocol or injected callable, not a hardcoded import of a tldr-swinton file.

### 1.2 `live_changes.py` duplicates `change_impact.py` git logic — MUST FIX

Both modules run `git diff` to get changed files. `change_impact.py` has `get_git_changed_files(project_path, base)` at line 291 which runs `git diff --name-only`. `live_changes.py` in Task 9 introduces `_get_git_diff(project_path, baseline)` which runs the same diff twice (`--name-status` then `--unified=0`). The symbol-overlay step in `live_changes.py` that maps changed lines to affected symbols is genuinely new. But the git file enumeration is not.

**Smallest viable fix:** Extract the "get changed files + status" step into a shared utility in `change_impact.py` or a new `python/intermap/git_utils.py`, and have `live_changes.py` call it. Do not run two separate `git diff` subprocesses when one `--name-status` pass followed by reuse of that file list suffices.

### 1.3 `cross_project.py` hardcodes the monorepo directory layout — ACCEPTABLE WITH NOTE

`_discover_projects` in Task 7 walks `["interverse", "core", "os", "sdk", "apps"]`. This matches the CLAUDE.md documented structure exactly. It is a legitimate coupling to the documented monorepo layout. However, this list will silently miss any new top-level group added to the monorepo. The registry Go-side uses a `.git` marker scan with no such hardcoded list — it walks all subdirectories.

**Smallest viable fix:** Either pass the group list as a parameter with this list as the default, or align the Python implementation with the Go registry's approach (walk all first-level subdirectories, check for `.git` marker). The Go registry pattern is already established and tested. Preferring consistency over a separate hardcoded list reduces future maintenance burden.

### 1.4 `agentMap` does a second `registry.Scan` without cache — EXISTING, NOTE ONLY

This is a pre-existing issue, not introduced by the plan. `agentMap` in `tools.go` calls `registry.Scan(root)` directly, bypassing the `projectCache` used by `projectRegistry`. The plan does not touch this function. Document it in the Task 2 audit but do not fix it in this sprint unless it surfaces in benchmarks.

---

## 2. Pattern Analysis

### 2.1 Dispatch pattern: plan uses a different registration form than the codebase

The existing `RegisterAll` in `internal/tools/tools.go` uses `s.AddTools(...)` with `server.ServerTool` structs returned by factory functions (lines 24-32). Each tool is its own function (`projectRegistry()`, `resolveProject()`, etc.).

Tasks 7-9 in the plan register tools using `server.AddTool(mcp.NewTool(...), func(...))` — the inline callback form. This is a different API shape from the existing pattern and will not compile against the `server.MCPServer` type which uses `AddTools` not `AddTool`.

**Smallest viable fix:** Follow the existing factory function pattern for all three new tools:

```go
func crossProjectDeps(bridge *pybridge.Bridge) server.ServerTool {
    return server.ServerTool{
        Tool: mcp.NewTool("cross_project_deps", ...),
        Handler: func(ctx context.Context, req mcp.CallToolRequest) (*mcp.CallToolResult, error) {
            args := req.GetArguments()
            root := stringOr(args["root"], "")
            ...
        },
    }
}
```

Then add to `RegisterAll`:
```go
s.AddTools(
    projectRegistry(),
    resolveProject(),
    agentMap(c),
    codeStructure(bridge),
    impactAnalysis(bridge),
    changeImpact(bridge),
    crossProjectDeps(bridge),
    detectPatterns(bridge),
    liveChanges(bridge),
)
```

This also means the plan's proposed `bridge.Run(ctx, "cross_project_deps", root, map[string]any{})` needs updating: existing callers use `bridge.Run(ctx, "structure", project, pyArgs)` where the first positional after command is the project path. For `cross_project_deps` the concept is a `root` not a `project` — the bridge signature `Run(ctx, command, project, args)` forces a naming mismatch. Pass `root` as the `project` argument positionally; document the semantic difference in a comment.

### 2.2 `detect_patterns.py` duplicates `analysis.py`'s `analyze_architecture`

`analyze_architecture` already exists in `python/intermap/analysis.py` (line 419). Its signature is `analyze_architecture(path, language)`. The plan's `detect_patterns.py` covers much of the same ground with a different output schema. Before implementing `detect_patterns.py`, Task 2's audit should explicitly compare the two. If `analyze_architecture` already detects structural patterns, the correct action may be to extend it (and expose it via a new `detect_patterns` dispatch alias) rather than create a parallel implementation.

The existing `architecture` command is already registered in `analyze.py` (line 47) but no corresponding MCP tool exposes it in `tools.go`. This is likely the correct surface to expose via the new tool rather than writing a new Python module. Decision point: verify in Task 2 what `analyze_architecture` actually returns. If its output schema is usable, skip `patterns.py` and wire the existing command.

### 2.3 `dirty_flag.py` writes to disk — stateful module in stateless design

The plan's architecture description states "SQLite-free (stateless cache only)." `dirty_flag.py` writes JSON to `.tldrs/cache/dirty.json`. If intermap adopts this module as a first-class citizen (Task 3 as written), it introduces filesystem-side-effect state that contradicts the stated stateless design. This is not a minor concern — it means intermap would silently write into project directories during analysis, which is unexpected behavior for a read-only code mapping tool.

This reinforces the fix in section 1.1: do not promote `dirty_flag.py`.

### 2.4 `_symbol_overlaps` proximity heuristic in `live_changes.py` is unreliable

```python
def _symbol_overlaps(symbol_line: int, changed_lines: set, window: int = 20) -> bool:
    return any(abs(symbol_line - line) < window for line in changed_lines)
```

This returns true if any changed line is within 20 lines of the symbol's start line. It does not account for symbol end line (function body extent). A 20-line window applied to the start line of every function in the file will produce false positives whenever two functions are close together, and false negatives for long functions where changes occur deep in the body. The existing `DefaultExtractor` returns `FunctionInfo.line_number` but not an end line.

The safe fallback is: report only symbols whose `line_number` is directly in `changed_lines`. The proximity heuristic adds noise without the end-line data needed to make it accurate. If broader range matching is needed, add `end_line_number` to `FunctionInfo` and use that. Do not ship the 20-line window heuristic as the default behavior.

**Smallest viable fix:**
```python
# In live_changes.py
for func in extraction.functions:
    if func.line_number in changed_lines:
        symbols.append({"name": func.name, "type": "function", "line": func.line_number})
```

Remove `_symbol_overlaps` entirely from this implementation. File it as follow-on work requiring `end_line_number` in `FunctionInfo`.

---

## 3. Simplicity and YAGNI

### 3.1 Task 9's `live_changes.py` partially duplicates `change_impact.py` — consolidation opportunity

`change_impact.py` already does git diff + symbol mapping. Its `get_changed_functions` (line 24) maps changed lines to function names. The difference is that `change_impact` focuses on test impact (what tests to run) while `live_changes` focuses on structural annotation (what symbols changed). The underlying extraction is the same.

This is not a reason to block Task 9, but the implementation should import `get_changed_functions` from `change_impact.py` or a shared utility rather than re-implementing git diffing and symbol location from scratch. Two parallel git-diffing paths in the same package is technical debt from day one.

### 3.2 The `_scan_plugin_deps` env-var inspection in `cross_project.py` is brittle

```python
if "INTERMUTE" in key.upper() and "intermute" in project_lookup:
    deps.append({"project": "intermute", "type": "plugin_ref", "via": f"env.{key}"})
if isinstance(val, str):
    for proj_name in project_lookup:
        if proj_name in val.lower() and proj_name != os.path.basename(project_path):
            deps.append({"project": proj_name, "type": "plugin_ref", "via": f"env.{key}"})
```

The second block iterates all project names against all env var values. Short project names like `go`, `sdk`, `core` will produce false dependency edges. The substring match on `val.lower()` against project names is not a reliable dependency signal. The INTERMUTE-specific check is fine because it is explicit. The generic substring scan is noise.

**Smallest viable fix:** Remove the generic substring scan. Only emit `plugin_ref` edges for known explicit patterns: `INTERMUTE_URL` → `intermute`, and potentially other well-known env var prefixes documented in the Interverse manifest conventions. Add a comment that generic env-var dependency detection is deferred.

### 3.3 Task 7 tests use a hardcoded `DEMARCH_ROOT` environment variable

```python
DEMARCH_ROOT = os.environ.get("DEMARCH_ROOT", "/home/mk/projects/Demarch")
```

This matches Task 1's fix approach (`__file__`-relative resolution), but then immediately defeats it with a machine-specific default. The pattern established by the Task 1 fix should be applied consistently: derive the root from `__file__`, not from a hardcoded absolute path. The test will silently skip or pass vacuously on any machine where the default path does not exist.

**Smallest viable fix:** Use `__file__`-relative resolution in all three new test files (Tasks 7, 8, 9), matching what Task 1 establishes for the existing tests:

```python
import os
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
DEMARCH_ROOT = os.environ.get(
    "DEMARCH_ROOT",
    os.path.normpath(os.path.join(_TESTS_DIR, "../../../.."))  # python/tests → intermap → interverse → Demarch
)
```

### 3.4 Task 6 roadmap.json is parallel to existing docs/roadmap.json

There is already a `docs/roadmap.json` in the git status as a modified file at the Demarch root. Task 6 creates `docs/roadmap.json` inside `interverse/intermap/docs/`. Verify that the intermap-local `docs/roadmap.json` is intentional and not accidentally shadowing or conflicting with the monorepo-level roadmap system. The interwatch freshness tooling (referenced in `.interfluence/learnings-raw.log`) reads roadmap.json files. Confirm the path is correct for that tooling.

---

## 4. Test Coverage Gaps

### 4.1 No test for Go bridge + new Python commands (integration gap)

Tasks 7-9 add Python dispatch routes but the Go-side tool handlers are only tested via the manual MCP `echo`-pipe commands in Task 10. There is no `tools_test.go` coverage for the three new tools. The existing `tools_test.go` should be checked for the pattern; if it has integration tests using `httptest` or the bridge, add equivalent tests for the three new tools in the same file.

### 4.2 `analyze.py` dispatch has no test

The `analyze.py` dispatch function routes commands to Python modules. There is no test file for `analyze.py` itself. All new dispatch routes added in Tasks 7-9 are untested at the dispatch layer. A single `test_dispatch.py` covering the dispatch routing would catch import failures and argument forwarding bugs before they surface in Go integration tests.

---

## 5. Task Sequencing Concern

Tasks 7-9 depend on Task 3 being complete (the vendor extraction). The plan sequences them correctly in document order. However, Task 3 also changes the import paths that `change_impact.py` uses, which `live_changes.py` (Task 9) imports from. If Task 9 is implemented before Task 3 is committed, the import `from .extractors import DefaultExtractor` will work but any future attempt to share `change_impact`'s git utilities will hit the pre-extraction import paths. The commit ordering in the plan is correct; executing out of order will produce confusing import errors.

---

## 6. Pre-existing Test Path Bug (Task 1) — Confirmed

The `findInterverseRoot` in `internal/registry/registry_test.go` at line 113-119 hardcodes `/root/projects/Interverse`. The monorepo is at `/home/mk/projects/Demarch/interverse`. The fix in Task 1 is correct. The same test file also references `plugins/interlock` (line 63) and `plugins/intermute` (line 34 comment) — the path segment `plugins/` does not exist in the current monorepo structure (projects are directly under `interverse/`). The Task 1 fix updates `findInterverseRoot` to walk up to the Demarch root but the subsequent `filepath.Join(root, "plugins", "interlock")` call on line 63 will still fail. Task 1 must also update all `plugins/` path references in the test file.

---

## Priority Classification

**Must fix before execution:**

1. Task 1: Fix `plugins/` path references in addition to `findInterverseRoot` (registry_test.go lines 63, 89).
2. Tasks 7-9: Use `server.ServerTool` factory pattern, not `server.AddTool` inline form — the latter will not compile.
3. Task 3: Do not promote `dirty_flag.py` as an intermap module; remove the `use_session` path from `change_impact.py` at extraction time.
4. Task 9: Remove `_symbol_overlaps` proximity heuristic; use direct line membership only.
5. Tasks 7-9: Fix test DEMARCH_ROOT to use `__file__`-relative resolution.

**Fix in the same sprint (low effort, prevents debt):**

6. Task 9: Extract shared git-diff utility rather than duplicating the subprocess call from `change_impact.py`.
7. Task 7: Remove generic substring scan from `_scan_plugin_deps`; keep only explicit env-var patterns.
8. Task 7: Align `_discover_projects` with Go registry's `.git`-marker walk rather than hardcoding group names.
9. Task 2: Explicitly compare `analyze_architecture` output with the proposed `detect_patterns` schema; consider wiring existing command before writing new module (Task 8).

**Defer to follow-on:**

10. Add `end_line_number` to `FunctionInfo` to enable accurate symbol overlap detection in `live_changes`.
11. Add `test_dispatch.py` covering `analyze.py` routing.
12. Fix `agentMap` to use `projectCache` (pre-existing, not introduced by this plan).
