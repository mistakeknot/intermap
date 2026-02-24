# Correctness Review: Intermap Project-Level Code Mapping Plan

**Plan:** `/home/mk/projects/Demarch/docs/plans/2026-02-23-intermap-project-level-code-mapping.md`
**Reviewer:** Julik (Flux-drive Correctness Reviewer)
**Date:** 2026-02-23
**Codebase:** `/home/mk/projects/Demarch/interverse/intermap/`

---

## Invariants Established

Before findings, I am stating the invariants that must remain true for this system to be correct:

1. **Bridge type contract:** `bridge.Run()` returns `(map[string]any, error)`. Any Go tool handler must serialize this to a string before passing to `mcp.NewToolResultText()`.
2. **Python import graph:** Every module must be importable after Task 3's vendor extraction. No broken relative import chains.
3. **Dispatch completeness:** Every command string dispatched from Go via `bridge.Run()` must have a matching `elif` branch in `analyze.py::dispatch()`.
4. **Go registration pattern:** New tools must follow the `server.ServerTool` struct pattern and call `s.AddTools()`, matching the existing codebase style.
5. **Git diff atomicity:** The two `git diff` subprocess calls in `_get_git_diff` must produce consistent data. If either fails, the caller must receive an error, not silently empty data.
6. **Project discovery consistency:** `_discover_projects()` in `cross_project.py` must agree with `registry.Scan()` in Go about what constitutes a project.
7. **Hunk data correctness:** The `old_start` field in a parsed hunk must refer to the old-file line number, not the new-file line number.

---

## Findings

### CRITICAL: Go Tool Handlers Use Wrong Return Type (Tasks 7, 8, 9)

**Severity: Compile Error. Ship-blocker.**

**Location:** Plan Tasks 7, 8, and 9, Go tool handler code in `internal/tools/tools.go` additions.

All three new Go tool handlers contain:

```go
result, err := bridge.Run(ctx, "cross_project_deps", root, map[string]any{})
// ...
return mcp.NewToolResultText(result), nil   // COMPILE ERROR
```

`bridge.Run()` is defined in `/home/mk/projects/Demarch/interverse/intermap/internal/python/bridge.go` line 35:

```go
func (b *Bridge) Run(ctx context.Context, command, project string, args map[string]any) (map[string]any, error) {
```

It returns `map[string]any`, not `string`. `mcp.NewToolResultText()` takes a `string`. This is a Go compile-time type mismatch — the binary will not build.

The correct pattern, used consistently by all six existing tools in `internal/tools/tools.go` (lines 246-251, 288-293, 328-333), is:

```go
result, err := bridge.Run(ctx, "cross_project_deps", root, map[string]any{})
if err != nil {
    return mcp.NewToolResultError(err.Error()), nil
}
return jsonResult(result)  // re-marshals map[string]any to JSON string
```

**Fix:** Replace `mcp.NewToolResultText(result)` with `jsonResult(result)` in all three new handlers.

---

### CRITICAL: Go Tool Registration API Mismatch (Tasks 7, 8, 9)

**Severity: Compile Error. Ship-blocker.**

**Location:** Plan Tasks 7, 8, 9 — "Register Go MCP tool" steps.

The plan writes:

```go
server.AddTool(mcp.NewTool("cross_project_deps", ...), func(ctx context.Context, req mcp.CallToolRequest) (...) {
    ...
})
```

This calls `server.AddTool()` as if `server` is the package name. But `server` is the import alias for `github.com/mark3labs/mcp-go/server`. Package-level `server.AddTool()` does not exist — `AddTool` is a method on a `*server.MCPServer` instance.

The existing `RegisterAll` function in `/home/mk/projects/Demarch/interverse/intermap/internal/tools/tools.go` lines 22-31 uses:

```go
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
```

Each tool is a named function returning a `server.ServerTool` struct. The new tools must follow this pattern: define named functions like `crossProjectDeps(bridge)`, `detectPatterns(bridge)`, `liveChanges(bridge)`, each returning `server.ServerTool`, and add them to the `s.AddTools()` call.

**Fix:** Rewrite the Go registration blocks as `server.ServerTool` structs following the existing pattern, and add them to the `s.AddTools()` call in `RegisterAll`.

---

### HIGH: Task 3 Leaves Broken Import Inside workspace.py After Vendor Extraction

**Severity: Runtime ImportError. Breaks four existing Python modules.**

**Location:** `/home/mk/projects/Demarch/interverse/intermap/python/intermap/vendor/workspace.py` line 241 — the lazy import inside `iter_workspace_files`.

The plan renames `vendor/tldrsignore.py` to `intermap/ignore.py`. Step 2 tells implementers to search for `from .vendor.tldrsignore import` and replace with `from .ignore import`. But `workspace.py` itself contains a lazy import that uses a *different* form:

```python
# vendor/workspace.py, line 241 — inside iter_workspace_files
from .tldrsignore import load_ignore_patterns, should_ignore
```

This is a package-relative import within the `vendor/` package, not `from .vendor.tldrsignore`. After moving `workspace.py` to `intermap/workspace.py`, this line becomes a relative import within the `intermap/` package and looks for `intermap/tldrsignore.py` — a file that does not exist because it was renamed to `ignore.py`.

**Failure narrative:** Plan Task 3 completes. Developer runs `PYTHONPATH=python python3 -m pytest python/tests/ -v`. The import chain is: `code_structure.py` imports `from .workspace import iter_workspace_files`. Python imports `intermap/workspace.py`. At call time, `iter_workspace_files()` reaches line 241: `from .tldrsignore import ...`. Python looks for `intermap/tldrsignore.py`. File not found. `ImportError` propagates. All four Python modules that call `iter_workspace_files` (`code_structure`, `change_impact`, `cross_file_calls`, `project_index`) fail at runtime.

The plan's grep pattern (`from .vendor`) will not catch this import because it does not contain `.vendor`.

**Fix:** In the new `python/intermap/workspace.py`, replace line 241:
```python
# Before (after extraction, broken)
from .tldrsignore import load_ignore_patterns, should_ignore
# After (correct)
from .ignore import load_ignore_patterns, should_ignore
```

This fix must be documented as a required edit in Task 3 Step 1 or Step 2.

---

### HIGH: go.mod Block-Form Replace Directives Are Missed (Task 7)

**Severity: Systematic false negatives in cross-project dependency detection.**

**Location:** `cross_project.py::_scan_go_deps()`, plan implementation.

The Go module system supports two forms of `replace` directives. The plan's regex only handles the single-line form:

```
# Caught by plan's regex: r'replace\s+\S+\s+=>\s+(\.\./\S+)'
replace github.com/mistakeknot/intermute => ../intermute

# NOT caught — block form
replace (
    github.com/mistakeknot/interlock => ../interlock
    github.com/mistakeknot/interflux => ../../interverse/interflux
)
```

In the block form, individual replacement lines do not start with the word `replace`, so `replace\s+\S+\s+=>\s+` never matches them. Any project using block-form replace directives will appear to have zero Go module cross-project dependencies.

Verification: running the regex against block-form content returns 0 matches (confirmed in analysis). The two forms are equally common in Go projects. Demarch projects that use block replace blocks will produce a silent empty result.

**Fix:** Use a two-pass parse: first extract all block-form `replace (...)` sections and strip the keyword, then apply a simpler per-line regex to both single-line and block-form entries:

```python
def _scan_go_deps(project_path: str, project_lookup: dict) -> list[dict]:
    gomod = os.path.join(project_path, "go.mod")
    if not os.path.isfile(gomod):
        return []
    content = Path(gomod).read_text()
    deps = []
    # Normalize: collect all "X => Y" pairs regardless of block vs single-line
    for match in re.finditer(r'\S+\s+=>\s+(\.\./\S+)', content):
        rel = match.group(1)
        abs_path = os.path.normpath(os.path.join(project_path, rel))
        target_name = os.path.basename(abs_path)
        if target_name in project_lookup:
            deps.append({"project": target_name, "type": "go_module", "via": f"replace => {rel}"})
    return deps
```

---

### HIGH: _get_git_diff Has No Error Check on Second Subprocess Call (Task 9)

**Severity: Silent wrong data. Produces empty symbol lists when git fails mid-function.**

**Location:** `live_changes.py::_get_git_diff()`, plan implementation.

The function makes two sequential `subprocess.run` calls. The first call (with `--name-status`) is checked for `returncode != 0` and returns `[]` on failure. The second call (with `--unified=0`) has **no returncode check**:

```python
# Second call — no error handling:
result = subprocess.run(
    ["git", "diff", "--unified=0", baseline],
    capture_output=True, text=True, cwd=project_path, timeout=10,
)
current_file = None
for line in result.stdout.split("\n"):
    ...
```

If the second subprocess fails (e.g., baseline ref becomes invalid between calls, git lock contention, timeout), `result.stdout` is empty, the for loop runs over `['']`, nothing is parsed, and `files` dict retains empty `hunks` lists from step one. The caller receives a plausible-looking response with `total_files > 0` but all hunks empty, so `changed_lines` is empty for every file, and `symbols_affected` is `[]` everywhere. The tool silently returns partial data.

**TOCTOU note:** Between the two subprocess calls, any `git reset`, `git stash`, or concurrent commit would cause the second diff to reflect a different repository state than the first. In a multi-agent environment where several agents operate on the same repo concurrently, this is a realistic scenario.

**Fix:**

```python
# After second subprocess.run:
if result.returncode != 0:
    # Return what we have from name-status without hunk details
    return list(files.values())
```

---

### MEDIUM: _discover_projects Does Not Check for .git, Diverges from Go Registry (Task 7)

**Severity: Data integrity. Cross-project graph may include non-projects.**

**Location:** `cross_project.py::_discover_projects()`, plan implementation.

The plan's `_discover_projects` uses:

```python
for name in sorted(os.listdir(group_path)):
    proj_path = os.path.join(group_path, name)
    if os.path.isdir(proj_path):
        projects.append({"name": name, "path": proj_path, "group": group_dir})
```

Any subdirectory of a group directory is treated as a project. The Go `registry.Scan()` in `/home/mk/projects/Demarch/interverse/intermap/internal/registry/registry.go` lines 46-51 explicitly requires a `.git` directory:

```go
gitDir := filepath.Join(projectPath, ".git")
if _, err := os.Stat(gitDir); err != nil {
    continue
}
```

Group directories in the monorepo may contain non-project subdirectories: `docs/`, `scripts/`, placeholder directories, or directories created by tools. Without the `.git` check, these become phantom entries in `project_lookup`. This inflates `total_projects`, pollutes the dependency graph, and causes `_scan_go_deps`/`_scan_python_deps` to open files in non-project directories.

**Fix:** Add `.git` presence check:

```python
if os.path.isdir(proj_path) and os.path.isdir(os.path.join(proj_path, ".git")):
    projects.append(...)
```

---

### MEDIUM: Duplicate Project Names in _discover_projects Silently Lose Paths (Task 7)

**Severity: Wrong dependency edges. Silent data loss.**

**Location:** `cross_project.py`, plan implementation — `project_lookup` construction.

```python
project_lookup = {p["name"]: p["path"] for p in projects}
```

If two projects in different group directories share the same name (e.g., `core/intermute` and `interverse/intermute`), the dict comprehension silently overwrites the first with the second. All dependency lookups using the lost path will resolve to the wrong directory. The `_scan_go_deps` call will open the wrong `go.mod`, producing incorrect or missing dependencies.

This is deterministic data corruption, not a race. The overwrite order depends on which group directory is iterated last in `_discover_projects`.

**Fix:** Use `(group, name)` as the lookup key, or detect collisions and log a warning. At minimum, use `setdefault` instead of dict comprehension to keep the first occurrence:

```python
project_lookup = {}
for p in projects:
    project_lookup.setdefault(p["name"], p["path"])
```

---

### MEDIUM: pyproject.toml Regex Misparses Hyphenated Package Names (Task 7)

**Severity: False negatives for packages with hyphens in names.**

**Location:** `cross_project.py::_scan_python_deps()`, plan implementation.

The regex `r'(\w+)\s*=\s*\{[^}]*path\s*=\s*"([^"]+)"'` uses `\w+` to capture the package name. `\w` matches `[a-zA-Z0-9_]` and does not match hyphens. For a dependency like:

```toml
my-package = {path = "../mypackage"}
```

The regex matches starting at `package` (after the hyphen), capturing `package` as the name rather than `my-package`. The lookup `project_lookup.get("package")` will fail to find `mypackage`, and the dependency is silently dropped.

Python package names frequently use hyphens (PEP 508 allows them). This is systematic, not an edge case.

**Fix:** Use `[\w-]+` to match hyphenated package names:

```python
for match in re.finditer(r'([\w-]+)\s*=\s*\{[^}]*path\s*=\s*"([^"]+)"', content):
```

---

### MEDIUM: Plugin Env-Value Scan Creates False Positive Dependencies (Task 7)

**Severity: Phantom dependency edges in the graph.**

**Location:** `cross_project.py::_scan_plugin_deps()`, plan implementation.

The generic env-value scan in `_scan_plugin_deps`:

```python
for proj_name in project_lookup:
    if proj_name in val.lower() and proj_name != os.path.basename(project_path):
        deps.append({"project": proj_name, "type": "plugin_ref", "via": f"env.{key}"})
```

This checks whether a project name appears as a substring in an env var value. Env values frequently contain URLs like `http://intermute:7338`, filesystem paths like `/home/mk/projects/Demarch/interverse/interlock/`, or log strings. Any env value containing the substring of a project name triggers a phantom dependency.

The check `proj_name != os.path.basename(project_path)` only prevents self-referential deps. It does not filter out string coincidences. For example, if a project named `inter` existed (unlikely but possible), it would match in virtually every env value in the monorepo.

**Fix:** Require word-boundary matching rather than substring:

```python
import re
if re.search(r'\b' + re.escape(proj_name) + r'\b', val, re.IGNORECASE):
```

Or restrict the check to env keys whose names contain the project name (e.g., `INTERMUTE_URL` contains `INTERMUTE`).

---

### LOW: Hunk Parser Sets old_start Incorrectly (Task 9)

**Severity: Incorrect field in output. Misleads data consumers.**

**Location:** `live_changes.py::_get_git_diff()`, plan implementation, hunk parsing block.

```python
files[current_file]["hunks"].append({
    "old_start": start,   # BUG: start is derived from '+' side (new file)
    "new_start": start,
    "new_count": count,
})
```

The regex `re.search(r'\+(\d+)(?:,(\d+))?', line)` extracts the `+` side of the `@@ -old,count +new,count @@` header. `start` is thus the new-file line number. Setting `old_start = start` (the new-file number) is wrong. Callers that read `old_start` for the old-file position will get the wrong line.

The function currently only uses `new_start` and `new_count` internally, so the execution path is not broken today. But the field is part of the public schema (`"hunks": [{"old_start": int, "new_start": int, "new_count": int}]`) and any future consumer expecting the old-file line will get stale-read corruption.

**Fix:** Parse the `-` side separately:

```python
match = re.search(r'-(\d+)(?:,\d+)?\s+\+(\d+)(?:,(\d+))?', line)
if match:
    old_start = int(match.group(1))
    new_start = int(match.group(2))
    new_count = int(match.group(3) or 1)
    files[current_file]["hunks"].append({
        "old_start": old_start,
        "new_start": new_start,
        "new_count": new_count,
    })
```

---

### LOW: test_change_has_symbols Is Vacuously True When No Changes Exist (Task 9)

**Severity: False test confidence. Correctness verification gap.**

**Location:** `python/tests/test_live_changes.py` plan, `test_change_has_symbols`.

```python
def test_change_has_symbols():
    result = get_live_changes(..., baseline="HEAD~3")
    for change in result["changes"]:
        assert "file" in change
        assert "symbols_affected" in change
```

If `HEAD~3` does not exist in the repository (fresh clone, fewer than 3 commits), `git diff` returns error code 128 and `_get_git_diff` returns `[]`. `result["changes"]` is then `[]`. The `for` loop body never executes. The test passes without verifying anything.

Even when `HEAD~3` exists, if none of the last 3 commits touched Python or Go source files, `changes` is again `[]`.

**Fix:** Use a synthetic git setup in the test, or add an explicit assertion before the loop:

```python
# At minimum:
assert len(result["changes"]) > 0, "Expected at least one changed file; test data may be missing"
for change in result["changes"]:
    ...
```

Better: create a temporary git repo with known commits and verify specific symbols are detected.

---

### LOW: Cobra Command Regex Fails When Use: Field Follows RunE in Struct (Task 8)

**Severity: False negative in architecture detection. Confidence score not affected.**

**Location:** `patterns.py::_detect_go_patterns()`, plan implementation.

```python
cobra_cmds = re.findall(r'&cobra\.Command\s*\{[^}]*Use:\s*"([^"]+)"', content, re.DOTALL)
```

`[^}]*` matches any character except `}`. In a cobra command struct where `RunE` or `PreRunE` is defined before `Use:`, the first `}` encountered is the closing brace of the `RunE` anonymous function, not the struct itself. At that point the regex terminates before reaching `Use:`. The command is silently missed.

This is confirmed: when `Use:` appears before any nested `}` (the common style), the regex works correctly. When `Use:` appears after `RunE` (less common but valid Go), it fails.

**Fix:** Parse cobra commands in a two-pass approach, or avoid `[^}]*` for deeply nested structs. A simple heuristic: use `mcp.NewTool` matching (already correct in the plan) as a model — match the field directly without depending on struct boundary:

```python
# Match Use: field anywhere in file proximity to cobra.Command
cobra_uses = re.findall(r'Use:\s*"([^"]+)"', content)
cobra_blocks = re.findall(r'&cobra\.Command', content)
if cobra_blocks and cobra_uses:
    # confidence proportional to command count
    patterns.append({...})
```

---

### LOW: FastMCP Decorator Regex Misses Named-Argument form (Task 8)

**Severity: False negative for tools using @mcp.tool(name="...") syntax.**

**Location:** `patterns.py::_detect_python_patterns()`, plan implementation.

```python
tools = re.findall(r'@\w+\.tool\s*\(\s*\)\s*\n\s*(?:async\s+)?def\s+(\w+)', content)
```

The `\(\s*\)` requires empty parentheses. The FastMCP and MCP Python SDK both support:

```python
@mcp.tool(name="my_tool", description="Does something")
async def my_tool_impl():
    ...
```

This form is common in production code. The regex misses it entirely. The confidence score is set to 0.95 — but a project with all tools using named arguments would receive a 0 match, meaning the `mcp_tools` pattern is not detected at all. This defeats the purpose of the detection.

**Fix:** Relax the parentheses match:

```python
tools = re.findall(r'@\w+\.tool\s*\([^)]*\)\s*\n\s*(?:async\s+)?def\s+(\w+)', content)
```

---

## Summary of Findings by Task

| Task | Finding | Severity |
|------|---------|----------|
| 3 | workspace.py internal import `from .tldrsignore` breaks after rename | **CRITICAL** |
| 7/8/9 | `mcp.NewToolResultText(result)` with `map[string]any` — compile error | **CRITICAL** |
| 7/8/9 | `server.AddTool()` package-call vs `s.AddTools()` method — compile error | **CRITICAL** |
| 7 | `_scan_go_deps` misses block-form replace directives | HIGH |
| 9 | No `returncode` check on second git subprocess — silent empty hunks | HIGH |
| 7 | `_discover_projects` has no `.git` check — non-projects enter graph | MEDIUM |
| 7 | Duplicate project names silently overwrite in `project_lookup` | MEDIUM |
| 7 | `\w+` misparses hyphenated package names in pyproject.toml | MEDIUM |
| 7 | Env-value substring scan creates phantom dependency edges | MEDIUM |
| 9 | `old_start` field set to new-file line number (naming bug) | LOW |
| 9 | `test_change_has_symbols` vacuously passes when no commits exist | LOW |
| 8 | Cobra regex fails when `Use:` follows nested `}` in struct | LOW |
| 8 | FastMCP decorator regex misses `@mcp.tool(name="...")` form | LOW |

---

## Required Pre-Implementation Fixes (Block Go Build)

The two compile errors in the Go tool handlers must be resolved before any of the new code can be tested:

1. Change `mcp.NewToolResultText(result)` to `jsonResult(result)` in all three new tool handlers.
2. Restructure Go tool registration to use `server.ServerTool` structs and `s.AddTools()`.

## Required Fix Before Task 3 Proceeds

The `workspace.py` internal import on line 241 must be explicitly listed as a required edit in Task 3, distinct from the generic `from .vendor` grep:

```
In python/intermap/workspace.py (moved from vendor/):
  Line 241: from .tldrsignore import ...
  Must become: from .ignore import ...
```

Without this fix, Task 3's Step 4 test run will fail with `ImportError` from `code_structure` (which uses `iter_workspace_files`), blocking all subsequent tasks.
