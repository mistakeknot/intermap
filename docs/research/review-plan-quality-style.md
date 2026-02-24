# Quality & Style Review: 2026-02-23-intermap-project-level-code-mapping.md

**Reviewer:** Flux-drive Quality & Style Reviewer
**Date:** 2026-02-23
**Plan file:** `/home/mk/projects/Demarch/docs/plans/2026-02-23-intermap-project-level-code-mapping.md`
**Codebase:** `/home/mk/projects/Demarch/interverse/intermap/`

---

## Summary Verdict

The plan is well-structured and the code is largely idiomatic, but has four issues that would cause real pain at implementation time: (1) the Go MCP tool registration pattern is wrong — the existing codebase uses `server.ServerTool` factory functions, not `server.AddTool()`; (2) the dispatch routes added to `analyze.py` use a calling convention incompatible with how the existing dispatcher works; (3) tests are entirely live-state-dependent with no fixture fallback, making CI fragile; (4) several regex patterns have correctness gaps that will produce false positives or silently miss real cases. The Python style is otherwise consistent with the existing codebase.

---

## 1. Go Code Quality: MCP Tool Registration Pattern Mismatch (Critical)

### Finding

The plan's `internal/tools/tools.go` additions use `server.AddTool()` with an inline handler closure:

```go
// Plan proposes:
server.AddTool(mcp.NewTool("cross_project_deps", ...), func(ctx context.Context, req mcp.CallToolRequest) (*mcp.CallToolResult, error) {
    ...
})
```

The actual codebase uses a completely different pattern: factory functions returning `server.ServerTool` structs, assembled into a slice passed to `s.AddTools()`:

```go
// Existing pattern in /home/mk/projects/Demarch/interverse/intermap/internal/tools/tools.go
func RegisterAll(s *server.MCPServer, c *client.Client) {
    bridge := pybridge.NewBridge(pybridge.DefaultPythonPath())
    s.AddTools(
        projectRegistry(),
        resolveProject(),
        agentMap(c),
        codeStructure(bridge),
        impactAnalysis(bridge),
        changeImpact(bridge),
    )
}

func codeStructure(bridge *pybridge.Bridge) server.ServerTool {
    return server.ServerTool{
        Tool: mcp.NewTool(...),
        Handler: func(ctx context.Context, req mcp.CallToolRequest) (*mcp.CallToolResult, error) {
            args := req.GetArguments()
            project, _ := args["project"].(string)
            ...
        },
    }
}
```

### Specific Problems

1. `server.AddTool()` vs `s.AddTools()` — these may not be the same call or may have different semantics in the mcp-go SDK version in use. The existing code always uses `s.AddTools()` with a variadic list.

2. Argument extraction: the plan uses `req.Params.Arguments` (map access via struct field), but the existing codebase uses `req.GetArguments()` and then `args["key"].(string)` type assertions. This is consistent throughout all 6 existing tools.

3. The `bridge` variable in the plan closures is referenced as a free variable, but under the factory function pattern `bridge` is a closure parameter. The plan's proposed code would need to share a package-level `bridge` or receive it as a closure capture — but neither of those is shown consistently.

### Correct Pattern to Follow

```go
// Add to RegisterAll():
s.AddTools(
    crossProjectDeps(bridge),
    detectPatterns(bridge),
    liveChanges(bridge),
)

// Each tool is a factory function:
func crossProjectDeps(bridge *pybridge.Bridge) server.ServerTool {
    return server.ServerTool{
        Tool: mcp.NewTool("cross_project_deps",
            mcp.WithDescription("Map cross-project dependencies in a monorepo — Go module deps, Python path deps, plugin references"),
            mcp.WithString("root",
                mcp.Description("Monorepo root directory"),
                mcp.Required(),
            ),
        ),
        Handler: func(ctx context.Context, req mcp.CallToolRequest) (*mcp.CallToolResult, error) {
            args := req.GetArguments()
            root, _ := args["root"].(string)
            if root == "" {
                return mcp.NewToolResultError("root is required"), nil
            }
            result, err := bridge.Run(ctx, "cross_project_deps", root, map[string]any{})
            if err != nil {
                return mcp.NewToolResultError(err.Error()), nil
            }
            return jsonResult(result)
        },
    }
}
```

Note also: the existing `jsonResult()` helper already marshals a value — `bridge.Run()` returns a `map[string]any`, not a string, so `mcp.NewToolResultText(result)` would be wrong. The plan mixes `jsonResult(result)` and `mcp.NewToolResultText(result)` inconsistently. Looking at existing tools, `bridge.Run()` returns `(map[string]any, error)` so `jsonResult(result)` is the correct call.

---

## 2. Python Dispatch Convention Mismatch (Medium)

### Finding

The plan proposes adding dispatch routes like this:

```python
elif command == "cross_project_deps":
    from .cross_project import scan_cross_project_deps
    return scan_cross_project_deps(project, **args)
```

But the existing dispatch style in `/home/mk/projects/Demarch/interverse/intermap/python/intermap/analyze.py` explicitly unpacks named args with `.get()`:

```python
elif command == "structure":
    from .code_structure import get_code_structure
    return get_code_structure(
        project,
        language=args.get("language", "python"),
        max_results=args.get("max_results", 1000),
    )
```

### Why `**args` is Problematic Here

1. It leaks unknown keys into function signatures. If the Go bridge sends any extra field in the args dict, the Python function gets an unexpected `**kwargs` and will raise `TypeError` (since the plan's functions do not accept `**kwargs`).
2. It makes defaults invisible — you cannot see what the fallback is without reading the implementation.
3. It is inconsistent with every other dispatch branch in the file.

### Correct Pattern

```python
elif command == "cross_project_deps":
    from .cross_project import scan_cross_project_deps
    return scan_cross_project_deps(project)

elif command == "detect_patterns":
    from .patterns import detect_patterns
    return detect_patterns(
        project,
        language=args.get("language", "auto"),
    )

elif command == "live_changes":
    from .live_changes import get_live_changes
    return get_live_changes(
        project,
        baseline=args.get("baseline", "HEAD"),
    )
```

---

## 3. Test Design: Over-Reliance on Live Monorepo State (Medium)

### Finding

All three new test files anchor on `DEMARCH_ROOT` defaulting to `/home/mk/projects/Demarch` and make assertions about specific projects being present:

```python
DEMARCH_ROOT = os.environ.get("DEMARCH_ROOT", "/home/mk/projects/Demarch")

def test_plugin_deps():
    result = scan_cross_project_deps(DEMARCH_ROOT)
    projects = {p["project"]: p for p in result["projects"]}
    if "interlock" in projects:
        dep_names = [d["project"] for d in projects["interlock"]["depends_on"]]
        assert "intermute" in dep_names
```

The `if "interlock" in projects:` guard makes `test_plugin_deps` a vacuous pass when the monorepo structure is unavailable or when the assertion condition is never entered. This is not a meaningful test — it will always pass, even if the feature is completely broken.

Similarly, `test_go_handler_chain` and `test_python_mcp_registration` assert specific pattern types exist in specific live projects, meaning they will fail if those projects' source changes, or if run in a fresh clone without the monorepo.

### What the Existing Tests Do

The existing tests in `/home/mk/projects/Demarch/interverse/intermap/python/tests/test_extractors.py` use `__file__`-relative paths to test against the intermap source tree itself (which is always present). `test_code_structure.py` uses a hardcoded path (acknowledged as a bug the plan fixes). The approach for the new tests should similarly self-reference or use a small fixture directory.

### Recommended Approach

1. Create a `python/tests/fixtures/` directory containing minimal synthetic projects (a `go.mod`, a `pyproject.toml`, a `plugin.json`) that deterministically exercise each detection path.
2. Write one test against live monorepo state per module (gated on `pytest.mark.integration` or a `DEMARCH_ROOT` env check with `pytest.skip`), separate from the deterministic fixture tests.
3. The `test_output_structure` tests (which only check schema, not content) are fine and should be the baseline — add one fixture-based functional test per detection type.

Example fixture-based replacement:

```python
import os
import tempfile
import json
import pytest
from intermap.cross_project import scan_cross_project_deps

@pytest.fixture
def fake_monorepo(tmp_path):
    """Minimal synthetic monorepo with two interdependent Go modules."""
    # Project A
    a = tmp_path / "interverse" / "projecta"
    a.mkdir(parents=True)
    (a / "go.mod").write_text("module github.com/test/projecta\n\nreplace github.com/test/projectb => ../projectb\n")
    # Project B
    b = tmp_path / "interverse" / "projectb"
    b.mkdir(parents=True)
    (b / "go.mod").write_text("module github.com/test/projectb\n")
    return tmp_path

def test_go_replace_detected(fake_monorepo):
    result = scan_cross_project_deps(str(fake_monorepo))
    projects = {p["project"]: p for p in result["projects"]}
    assert "projecta" in projects
    deps = [d["project"] for d in projects["projecta"]["depends_on"]]
    assert "projectb" in deps

def test_output_structure(fake_monorepo):
    result = scan_cross_project_deps(str(fake_monorepo))
    assert "projects" in result
    assert "total_projects" in result
    for p in result["projects"]:
        assert "project" in p
        assert "depends_on" in p
```

### Missing Edge Cases

**cross_project.py tests:**
- Project with no `go.mod`, `pyproject.toml`, or `plugin.json` (should produce empty `depends_on`, not error)
- Self-referential replace directives (e.g., `replace github.com/test/foo => .`)
- `plugin.json` with malformed JSON (the code handles this via `except json.JSONDecodeError`, but there is no test for it)

**patterns.py tests:**
- File that cannot be read (OSError path — the code has `except OSError: continue` but it is never exercised in tests)
- Project with no Go or Python files (should return empty patterns, not error)
- The DOTALL regex for cobra commands (`re.DOTALL`) — no test verifies multi-line struct literals are matched correctly

**live_changes.py tests:**
- `baseline` pointing to a non-existent ref (git returns non-zero; `_get_git_diff` returns `[]` — needs a test)
- Project path that is not a git repository (subprocess fails; should return empty changes, not raise)
- Deleted files: `status == "deleted"` skips extraction — no test verifies `symbols_affected` is `[]` for deletions

---

## 4. Regex Correctness Issues

### 4a. `go.mod` Replace Directive Regex (cross_project.py)

```python
re.finditer(r'replace\s+\S+\s+=>\s+(\.\./\S+)', content)
```

Problems:
- Only matches `../` prefixes. Local replaces can use `./` for siblings at the same level (unlikely in a monorepo but valid).
- `\S+` on the path will consume a trailing comment (e.g., `replace foo => ../bar // comment` captures `../bar` correctly due to `\S+` stopping at space, but `replace foo => ../bar\n` works fine — this is actually OK).
- Does not handle the multi-module block form:
  ```
  replace (
      github.com/foo/bar => ../bar
  )
  ```
  The pattern does not anchor on line start, so it will miss the grouped form because `replace` only appears once at the block start, not before each path. Real `go.mod` files commonly use the block form.

Suggested fix:
```python
# Match both inline and block forms
# Inline: replace github.com/x => ../y
# Block:  replace (\n    github.com/x => ../y\n)
for match in re.finditer(r'=>\s+(\.\./[\w./-]+)', content):
    ...
```
Or parse line-by-line and look for `=>` in any line within a `replace` block.

### 4b. pyproject.toml Path Dependency Regex (cross_project.py)

```python
re.finditer(r'(\w+)\s*=\s*\{[^}]*path\s*=\s*"([^"]+)"', content)
```

Problems:
- `[^}]*` is greedy and not DOTALL — if the dependency table entry spans multiple lines (which is typical in TOML), the `[^}]` will fail to match because TOML inline tables `{path = "../foo"}` must be single-line, but the PEP 517 `[tool.poetry.dependencies]` style uses multi-line section syntax, not inline tables. Real `pyproject.toml` path deps look like:
  ```toml
  [tool.poetry.dependencies]
  intersearch = {path = "../intersearch", develop = true}
  ```
  The inline form works. But uv/pip-style `pyproject.toml` uses:
  ```toml
  [project.optional-dependencies]
  dev = ["foo @ ../foo"]
  ```
  This form is not matched at all.
- `(\w+)` as the name group will not match hyphenated package names like `my-package`.

### 4c. FastMCP Tool Decorator Regex (patterns.py)

```python
tools = re.findall(r'@\w+\.tool\s*\(\s*\)\s*\n\s*(?:async\s+)?def\s+(\w+)', content)
```

Problems:
- Requires `()` — FastMCP `@mcp.tool` (no parentheses) is also valid and common. The pattern will silently miss all bare decorator uses.
- `\w+\.tool` requires exactly one attribute level. `@app.mcp.tool()` (nested attribute) is not matched.
- `\s*\n\s*` between decorator and `def` requires exactly one newline. Multiple decorators stacked above the `def` (e.g., `@mcp.tool()\n@other_decorator\ndef foo`) will cause a miss.

A more robust alternative:

```python
# Two-pass approach: find files with @*.tool decorator, then extract def names
# First check if file imports FastMCP and uses tool decorator
if re.search(r'@\w+(?:\.\w+)*\.tool', content):
    tools = re.findall(r'@\w+(?:\.\w+)*\.tool(?:\([^)]*\))?\s*(?:\n\s*@\w[^\n]*)?\s*\n\s*(?:async\s+)?def\s+(\w+)', content)
```

### 4d. Cobra Command Regex (patterns.py)

```python
cobra_cmds = re.findall(r'&cobra\.Command\s*\{[^}]*Use:\s*"([^"]+)"', content, re.DOTALL)
```

Problems:
- `[^}]*` with `re.DOTALL` is greedy. If two `&cobra.Command{...}` blocks appear in the same file, `[^}]*` can over-match (spans from first `{` to the last `}` before `Use:` in the second block). This needs to be a non-greedy `[^}]*?` or better, limit to `.*?` with `re.DOTALL`:
  ```python
  re.findall(r'&cobra\.Command\s*\{.*?Use:\s*"([^"]+)"', content, re.DOTALL)
  ```
- Cobra commands often span many lines with nested struct fields. If `}` appears in a string value inside the struct before `Use:` is reached, the match terminates early.

### 4e. HTTP Handler Regex (patterns.py)

```python
handlers = re.findall(r'(?:HandleFunc|Handle|Get|Post|Put|Delete)\s*\(\s*"([^"]+)"', content)
```

Problems:
- `Get`, `Post`, `Put`, `Delete` are extremely common Go identifiers (e.g., `Get` in `cache.Get(key)`, `Delete` in `os.Remove()`). The pattern will produce significant false positives on any Go file that uses these method names. The minimum confidence of 0.5 + 0.1*count means even two false `Get()` matches yield 0.7 confidence — misleadingly high.
- Should at minimum require these to be called on a receiver that looks like a router: `(?:router|mux|r|srv)\.(?:HandleFunc|Handle|Get|Post|Put|Delete)` or require that the first argument starts with `/`.

### 4f. Git Diff Hunk Parsing (live_changes.py)

```python
match = re.search(r'\+(\d+)(?:,(\d+))?', line)
if match:
    start = int(match.group(1))
    count = int(match.group(2) or 1)
```

Problems:
- The `@@` hunk header format is `@@ -old_start,old_count +new_start,new_count @@`. Using `re.search(r'\+(\d+)(?:,(\d+))?')` will match the first `+` it finds in the line, which could be from the old-side range if the old range has a `+` character in surrounding context. The correct approach anchors to the known format:
  ```python
  match = re.search(r'@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@', line)
  ```
- When `count` is `0` (empty hunk — can occur for deletions), `int(match.group(2) or 1)` returns 1 instead of 0. This causes `range(start, start + 1)` to mark one line as changed when zero lines were added. Should be `int(match.group(2)) if match.group(2) is not None else 1`.

---

## 5. live_changes.py: `_symbol_overlaps` Window Heuristic is Fragile

```python
def _symbol_overlaps(symbol_line: int, changed_lines: set, window: int = 20) -> bool:
    """Check if a symbol's approximate range overlaps with changed lines."""
    return any(abs(symbol_line - line) < window for line in changed_lines)
```

This runs `O(changed_lines)` for each symbol per file — for a large diff this is quadratic. More importantly, the heuristic is semantically wrong: a function at line 1 with a 20-line body could be said to "overlap" with a change at line 19, but a function at line 50 in the same file would not, even if both are in different unchanged functions near the hunk.

This is only used when `func.line_number in changed_lines` is False — i.e., as a fallback when the start line of the symbol is not directly in the changed set. A function can easily be 200 lines long. The window of 20 is too small for real functions.

The correct approach is to track symbol end-lines (which `FunctionInfo` may not carry) or to use a simpler heuristic: if any changed line is between `symbol_start` and `symbol_start + window`, flag it. But without end-line data, any fixed window is a guess. The plan should either:
- Not include `_symbol_overlaps` and only match on `func.line_number in changed_lines` (precise, but misses functions where only the body changed)
- Or document this as a known approximation with a comment explaining the limitation

For correctness, at minimum fix the iteration:

```python
def _symbol_overlaps(symbol_line: int, changed_lines: set, window: int = 50) -> bool:
    """Check if any changed line falls within `window` lines after a symbol definition."""
    # This is an approximation: we check if any changed line is within `window` lines
    # after the symbol start (since we lack end-line data). window=50 covers most functions.
    return any(0 <= line - symbol_line < window for line in changed_lines)
```

---

## 6. live_changes.py: Silent Exception Swallowing

```python
try:
    extraction = extractor.extract(fpath)
    ...
except Exception:
    pass  # Extraction failure is non-fatal
```

This is consistent with the existing codebase pattern in `code_structure.py` (which also has `except Exception: pass`). However, the existing `change_impact.py` uses `logger.debug(...)` in its exception handlers, which is more maintainable. The new code should follow `change_impact.py`'s more informative pattern:

```python
import logging
logger = logging.getLogger(__name__)

except Exception as e:
    logger.debug("Symbol extraction failed for %s: %s", fpath, e)
```

---

## 7. cross_project.py: `_scan_plugin_deps` Has Generic Project Name Matching Risk

```python
for proj_name in project_lookup:
    if proj_name in val.lower() and proj_name != os.path.basename(project_path):
        deps.append({"project": proj_name, "type": "plugin_ref", "via": f"env.{key}"})
```

This will produce false positives for short project names. For example, if a project is named `"or"` (unlikely but possible) and an env value contains `"error"`, it matches. More practically, `"inter"` as a substring of any project name would match against `INTERMUTE_URL` values. The lookup should require word-boundary matching or at minimum check that `proj_name` appears as a standalone word:

```python
import re as _re
if _re.search(r'\b' + _re.escape(proj_name) + r'\b', val.lower()):
    ...
```

---

## 8. _discover_projects Does Not Use .git Markers (Minor)

The docstring says "walking known monorepo dirs for .git markers" but the implementation only checks `os.path.isdir(proj_path)` — any subdirectory under the known group dirs is treated as a project, including `__pycache__`, `.venv`, `dist`, `node_modules`, etc.

```python
def _discover_projects(root: str) -> list[dict]:
    """Find projects by walking known monorepo dirs for .git markers."""
    projects = []
    for group_dir in ["interverse", "core", "os", "sdk", "apps"]:
        group_path = os.path.join(root, group_dir)
        if not os.path.isdir(group_path):
            continue
        for name in sorted(os.listdir(group_path)):
            proj_path = os.path.join(group_path, name)
            if os.path.isdir(proj_path):  # <-- no .git check
                projects.append(...)
```

The existing `registry.Scan()` in Go uses `.git` directories as project markers. This Python version should either filter by `.git` presence or filter out known non-project directories:

```python
if os.path.isdir(proj_path) and (
    os.path.exists(os.path.join(proj_path, ".git")) or
    os.path.exists(os.path.join(proj_path, "go.mod")) or
    os.path.exists(os.path.join(proj_path, "pyproject.toml")) or
    os.path.exists(os.path.join(proj_path, "package.json"))
):
```

---

## 9. Python Style Consistency (Positive Notes + One Gap)

These are consistent with the existing codebase:

- `list[dict]`, `dict` return type hints in function signatures (matches `change_impact.py`)
- `from pathlib import Path` usage alongside `os.path` (mixed in existing code too)
- Module-level docstrings present
- `errors="replace"` on file reads (matches `extractors.py`)
- Skipping `.git`, `vendor`, `__pycache__` in `os.walk` (matches existing patterns)
- Lazy imports inside `dispatch` branches (matches existing `analyze.py`)

One gap: `cross_project.py` imports `from pathlib import Path` but the module body uses `os.path` exclusively. `Path` is imported but unused (this will produce a `F401` flake8 warning and would be caught by `ruff`).

---

## 10. Task 1 Fix: INTERMAP_ROOT Computation is Correct But Fragile

```python
INTERMAP_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
```

From `python/tests/test_code_structure.py`, this is `__file__` = `.../python/tests/test_code_structure.py`, so three `dirname` calls give `.../python/tests/` → `.../python/` → `.../<project>` → the intermap root. That's correct.

However, the existing `test_analyze.py` also has hardcoded `/root/projects/Interverse/plugins/intermap` paths that are not fixed by Task 1 (the plan only mentions `test_code_structure.py`). Both `test_analyze.py::test_dispatch_structure` and `test_analyze.py::test_dispatch_extract` use the old path. Task 1 should fix both files.

---

## Priority Summary

| # | Issue | Severity | File(s) |
|---|-------|----------|---------|
| 1 | Go tool registration uses wrong API pattern (`AddTool` + `req.Params.Arguments` vs `AddTools` factory + `req.GetArguments()`) | Critical | `internal/tools/tools.go` |
| 2 | `go.mod` replace block form not matched by regex | High | `cross_project.py` |
| 3 | Tests are vacuously passing (guarded `if` hides real assertion) | High | `test_cross_project.py` |
| 4 | Hunk regex `\+(\d+)` can match wrong side; zero-count bug | High | `live_changes.py` |
| 5 | Dispatch uses `**args` instead of explicit `.get()` unpacking | Medium | `analyze.py` additions |
| 6 | `Get`/`Post`/etc. handler regex produces false positives on non-router code | Medium | `patterns.py` |
| 7 | FastMCP decorator regex misses bare `@mcp.tool` (no parens) | Medium | `patterns.py` |
| 8 | Generic substring match in `_scan_plugin_deps` creates false dep edges | Medium | `cross_project.py` |
| 9 | `_discover_projects` does not filter non-project dirs (.git/go.mod check missing) | Medium | `cross_project.py` |
| 10 | `_symbol_overlaps` window heuristic semantically wrong for body-only changes | Low | `live_changes.py` |
| 11 | `except Exception: pass` — no logging; prefer `logger.debug(...)` | Low | `live_changes.py` |
| 12 | `from pathlib import Path` imported but unused | Low | `cross_project.py` |
| 13 | Task 1 fix incomplete — `test_analyze.py` hardcoded paths not addressed | Low | `test_analyze.py` |
| 14 | Cobra regex `[^}]*` should be `[^}]*?` (non-greedy) | Low | `patterns.py` |
