# Correctness Review of Intermap Sprint — Full Analysis

**Reviewer:** Julik (fd-correctness)
**Date:** 2026-02-23
**Diff source:** `/tmp/qg-diff-1771897180.txt`
**Output gate file:** `/home/mk/projects/Demarch/interverse/intermap/.clavain/quality-gates/fd-correctness.md`

---

## Scope

Changed files reviewed:

| File | Risk area |
|------|-----------|
| `python/intermap/live_changes.py` | New file: subprocess, regex hunk parsing, symbol annotation |
| `python/intermap/cross_project.py` | New file: file I/O, go.mod/pyproject.toml regex |
| `python/intermap/patterns.py` | New file: regex pattern detection across file walks |
| `python/intermap/change_impact.py` | Modified: removed `use_session`, signature change |
| `python/intermap/analyze.py` | Modified: new dispatch routes, import changes |
| `python/intermap/workspace.py` | New file: extracted from vendor, import path changed |
| `python/intermap/ignore.py` | New file: extracted from vendor |
| Import chain: 5 files | Changed from `.vendor.workspace` to `.workspace` |
| `internal/tools/tools.go` | New MCP tool registrations |
| `internal/registry/registry_test.go` | Test path portability fix |

---

## Invariants Verified

1. `get_live_changes` must only report symbols whose definitions were actually touched by the diff — PARTIALLY MET (see C-01).
2. `scan_cross_project_deps` must not emit false edges from comments or irrelevant content — PARTIALLY MET (see C-02).
3. `analyze_change_impact` must not silently change behavior for callers using removed kwargs — MET (via `**_kwargs`), with an informational note (C-05).
4. All subprocess calls must be shell-injection-safe and check return codes — MET for both git invocations in `live_changes.py` and the single invocation in `change_impact.py`.
5. Import refactoring must cover every call site — MET. Grep confirms no remaining `.vendor.workspace` references in non-vendor code.
6. Extracted modules must be logically equivalent to their vendor originals — MET. `workspace.py` and `ignore.py` are clean copies with updated internal import paths.

---

## Findings Summary

**Verdict: needs-changes**

Five issues found: one logic error producing incorrect output (C-01), two regex scope gaps (C-02, C-03), one encoding robustness gap (C-04), and one silent behavioral change (C-05). No data corruption or data loss risk. All new tools are read-only.

---

## Detailed Findings

### C-01 (LOW) — Deletion hunk clamped to count=1 causes false-positive symbol annotation

**File:** `python/intermap/live_changes.py`, line 131

**The invariant this breaks:** `get_live_changes` must only report symbols touched by the diff.

**Root cause:**

The hunk-count extraction code is:

```python
new_count = int(new_match.group(2) or 1)
files[current_file]["hunks"].append({
    "old_start": old_start,
    "new_start": new_start,
    "new_count": max(new_count, 1),
})
```

There are two distinct cases conflated here:

- `@@ -3 +5 @@` — no comma on the `+` side. `group(2)` is `None`. `None or 1` correctly gives `1` (single-line hunk).
- `@@ -5,3 +5,0 @@` — pure deletion. `group(2)` is `"0"`. `int("0")` is `0`. `0 or 1` gives `1` (wrong — should be `0`). Then `max(1, 1)` = `1`.

Both guards (`or 1` and `max(..., 1)`) treat count=0 as count=1. A pure-deletion hunk inserts zero lines, so `range(new_start, new_start + 0)` is the correct empty set. Instead, the code computes `range(5, 6)` and adds line 5 to `changed_lines`. If a function or class starts at line 5 of the post-diff file, it is spuriously listed in `symbols_affected`.

**Concrete failure sequence:**

1. Commit file `api.py` with `def serve()` at line 5.
2. Delete lines 5–7 in a follow-up edit (pure deletion, no replacement).
3. Call `get_live_changes(project, baseline="HEAD")`.
4. Git emits `@@ -5,3 +5,0 @@` for that hunk.
5. Code computes `new_count = max(int("0") or 1, 1) = 1`.
6. `changed_lines` = {5}.
7. The extractor finds `def serve()` at line 5 of the post-diff file (the function that is now at that position after deletion).
8. Output lists `serve` in `symbols_affected` even though `serve` was not modified.

**Fix:**

```python
raw_count = new_match.group(2)
new_count = int(raw_count) if raw_count is not None else 1
old_start = int(old_match.group(1)) if old_match else new_start
files[current_file]["hunks"].append({
    "old_start": old_start,
    "new_start": new_start,
    "new_count": new_count,   # preserve 0 for pure deletions
})
```

And in `get_live_changes`, guard the range expansion:

```python
for hunk in change["hunks"]:
    start = hunk["new_start"]
    count = hunk["new_count"]
    if count > 0:
        changed_lines.update(range(start, start + count))
```

---

### C-02 (LOW) — Go replace regex matches commented-out directives

**File:** `python/intermap/cross_project.py`, line 101

Pattern: `r'\S+\s+=>\s+(\.\./\S+)'`

This pattern does not exclude Go comment lines (`//`). A `go.mod` comment such as:

```
// replace example.com/foo => ../local-fork   (disabled for release)
```

produces a false edge in the dependency graph. The fix is to strip comment lines before applying the regex:

```python
non_comment = "\n".join(
    l for l in content.splitlines() if not l.lstrip().startswith("//")
)
for match in re.finditer(r'\S+\s+=>\s+(\.\./\S+)', non_comment):
    ...
```

Impact is low because go.mod comment-out of replace directives is rare, but the false edge would be persistent and silently incorrect.

---

### C-03 (LOW) — Cobra regex misses `Use:` field when it follows a function-literal inner brace

**File:** `python/intermap/patterns.py`, lines 115-125

Pattern: `r'&cobra\.Command\s*\{[^}]*Use:\s*"([^"]+)"'` with `re.DOTALL`

`re.DOTALL` has no effect on `[^}]*` — negated character classes match newlines regardless. The class stops at the first `}`. In real cobra commands where `RunE` is defined before `Use:`, the `}` closing the function literal prevents the pattern from seeing `Use:`. Example:

```go
var cmd = &cobra.Command{
    RunE: func(cmd *cobra.Command, args []string) error { return nil },
    Use:  "serve",  // not matched
}
```

In idiomatic Go, `Use:` is typically first, so the miss rate is low in practice. When it does occur, the command is silently absent from the output — no error, just an incomplete result.

Fix: use `(?:.*?)` with `re.DOTALL` instead of `[^}]*`:

```python
cobra_cmds = re.findall(
    r'&cobra\.Command\s*\{(?:.*?)Use:\s*"([^"]+)"',
    content, re.DOTALL,
)
```

---

### C-04 (LOW) — `open()` without encoding guard risks `UnicodeDecodeError`

**File:** `python/intermap/cross_project.py`, lines 97, 116, 143

`_scan_go_deps`, `_scan_python_deps`, and `_scan_plugin_deps` all open files with `open(path)` using the system default encoding in strict mode. A corrupted `go.mod`, a Windows-1252 `pyproject.toml`, or a misnamed binary file raises `UnicodeDecodeError`, which propagates uncaught through `scan_cross_project_deps` to the MCP bridge, producing an unstructured error response.

`patterns.py` handles this correctly with `errors="replace"`. `cross_project.py` should match:

```python
with open(gomod, encoding="utf-8", errors="replace") as f:
    content = f.read()
```

---

### C-05 (INFO) — `**_kwargs` silently absorbs `use_session=True`

**File:** `python/intermap/change_impact.py`, line 324

External callers passing `use_session=True` receive git-based results with no warning. The dispatch layer was correctly updated and no internal caller passes `use_session`, so this is informational only. A debug log line would help diagnosis:

```python
if _kwargs:
    logger.debug("analyze_change_impact: ignoring unknown kwargs: %s", list(_kwargs))
```

---

## Import Refactoring — Completeness Audit

All five files that previously imported from `.vendor.workspace` have been updated:

| File | Old import | New import | Status |
|------|-----------|-----------|--------|
| `change_impact.py` | `.vendor.workspace` | `.workspace` | Correct |
| `code_structure.py` | `.vendor.workspace` | `.workspace` | Correct |
| `cross_file_calls.py` (top-level) | `.vendor.workspace` | `.workspace` | Correct |
| `cross_file_calls.py` (inline in `scan_project`) | `.vendor.workspace` | `.workspace` | Correct |
| `project_index.py` | `.vendor.workspace` | `.workspace` | Correct |

Grep confirms no remaining `.vendor.workspace` references in any non-vendor file. `vendor/workspace.py` is retained for backward compatibility but is no longer imported by any production code.

The `dirty_flag` import removal from `change_impact.py` is correct: the new code no longer calls `get_dirty_files()`, and `vendor/dirty_flag.py` remains in place for the vendor directory.

---

## Subprocess Safety Audit

All subprocess calls use list form (not `shell=True`) — shell injection via `baseline`, `project_path`, or `base` arguments is not possible.

Return code checks:
- `live_changes.py` `_get_git_diff`: checked for both invocations (name-status and unified diff). Second invocation falls back to returning the files list without hunks on non-zero return.
- `change_impact.py` `get_git_changed_files`: checked with `if result.returncode == 0`.

Exception handling:
- `live_changes.py`: catches `subprocess.TimeoutExpired` and `FileNotFoundError`.
- `change_impact.py`: catches broad `Exception` and logs at debug level.

Timeout: both set `timeout=10` seconds — adequate for a local git diff.

---

## Go Tool Registrations

`internal/tools/tools.go` adds three new tool handlers (`crossProjectDeps`, `detectPatterns`, `liveChanges`). All three:
- Validate the required `project`/`root` argument and return a tool error (not a Go error) when missing.
- Pass arguments to the Python bridge via `bridge.Run()`.
- Return bridge errors as tool errors (no panics, no unhandled errors).

The `stringOr` helper is used consistently for optional parameters with defaults.

---

## Test Portability Fix

`internal/registry/registry_test.go` replaces the hardcoded `/root/projects/Interverse` path with a `findDemarchRoot` function that walks upward from `os.Getwd()` looking for an `interverse` subdirectory. This is correct for the monorepo structure. The `t.Skip` guard ensures the test is skipped cleanly when not running inside the Demarch tree.

Path update from `plugins/interlock` to `interverse/interlock` is consistent with the monorepo restructure.

---

## Recommendations Priority

1. **Fix C-01** — deletion hunk clamping. One-line fix; incorrect output.
2. **Fix C-04** — add `encoding="utf-8", errors="replace"` to three `open()` calls in `cross_project.py`. Two-line fix per site.
3. **Fix C-02** — strip comment lines before go.mod regex. Three-line fix.
4. **Fix C-03** — update cobra regex. One-line fix.
5. **Consider C-05** — add a debug log warning for absorbed kwargs.
