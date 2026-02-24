# Correctness Review — Intermap Sprint

**Reviewer:** Julik (fd-correctness)
**Date:** 2026-02-23
**Diff:** `/tmp/qg-diff-1771897180.txt`

---

## Invariants Established Before Review

1. `get_live_changes` must only report symbols whose definitions were actually touched by the diff.
2. `scan_cross_project_deps` must only emit edges that correspond to real dependency declarations — no false edges from comments or unrelated content.
3. `analyze_change_impact` must not silently change behavior when old callers pass now-removed keyword args.
4. All subprocess calls must be safe from shell injection and must handle non-zero exit codes.
5. The import refactoring from `.vendor.workspace` to `.workspace` must cover every call site — no orphaned references.
6. Extracted modules (`workspace.py`, `ignore.py`) must be logically equivalent to their vendor originals.

---

### Findings Index

| SEVERITY | ID | Section | Title |
|----------|-----|---------|-------|
| LOW | C-01 | live_changes.py:131 | Deletion hunk clamped to count=1 causes false-positive symbol annotation |
| LOW | C-02 | cross_project.py:97-107 | Go replace regex matches commented-out directives |
| LOW | C-03 | patterns.py:115-125 | Cobra regex misses `Use:` field when it follows a function-literal inner brace |
| LOW | C-04 | cross_project.py:97-107 | `open()` without encoding guard risks `UnicodeDecodeError` on non-UTF-8 manifests |
| INFO | C-05 | change_impact.py:324 | `**_kwargs` silently absorbs `use_session=True` — behavioral change not surfaced to caller |

**Verdict: needs-changes**

---

## Summary

The sprint successfully extracts `workspace.py` and `ignore.py` from the vendor directory and introduces three new tools (cross-project deps, pattern detection, live changes). The import refactoring is complete and consistent — no orphaned `.vendor.workspace` references remain in non-vendor code. Subprocess handling is safe: commands are built as lists (no shell injection risk), returncode is checked for both git invocations in `live_changes.py`, and `TimeoutExpired`/`FileNotFoundError` are caught. The main correctness issue is a logic error in the deletion-hunk count clamping that will produce false-positive symbol annotations for pure-deletion hunks. Two regex patterns have narrowly-scoped blind spots worth documenting. The `use_session` removal is safe by design (`**_kwargs`) but the silent absorption deserves a logged warning. No data-corruption or data-loss risk exists; all new tools are read-only.

---

## Issues Found

**C-01. LOW: Deletion hunk clamped to count=1 causes false-positive symbol annotation**

File: `/home/mk/projects/Demarch/interverse/intermap/python/intermap/live_changes.py`, line 131.

When `git diff --unified=0` emits a pure-deletion hunk such as `@@ -5,3 +5,0 @@`, the new-side count is `0`. The code does `new_count = int(new_match.group(2) or 1)` then stores `max(new_count, 1)`. The `or 1` branch fires when `group(2)` is `None` (the no-comma single-line case like `@@ -3 +5 @@`, which correctly means count=1). However, when `group(2)` is the string `"0"`, `int("0")` evaluates to `0`, which is falsy, so `0 or 1` yields `1`. The final `max(0, 1)` also yields `1`. Both guards apply the same clamping regardless of whether the explicit zero means "one line" or "pure deletion." Result: `range(5, 6)` is added to `changed_lines` for a hunk that deleted lines and inserted nothing, so any symbol whose definition starts at line 5 of the post-diff file is spuriously listed in `symbols_affected`.

The correct behaviour is: when `group(2)` is the literal string `"0"`, honour it. The fix separates the two cases:

```python
raw = new_match.group(2)
new_count = int(raw) if raw is not None else 1
# Only expand range for non-zero counts
if new_count > 0:
    changed_lines.update(range(new_start, new_start + new_count))
```

Remove the `max(new_count, 1)` clamping and the `new_count` key in the stored hunk dict. The `hunks` list is already only used in `get_live_changes` via `range(start, start + count)` — if `count` is 0, the range is empty, which is correct for a pure deletion.

---

**C-02. LOW: Go replace regex matches commented-out directives**

File: `/home/mk/projects/Demarch/interverse/intermap/python/intermap/cross_project.py`, line 101.

Pattern: `r'\S+\s+=>\s+(\.\./\S+)'`

A `go.mod` file may contain lines like:

```
// example.com/foo => ../local-fork  (temporarily disabled)
```

The regex does not require any line-start context, so it matches inside comments and produces a false edge in the dependency graph. While Go developers rarely comment out replace directives this way, it is a real pattern during migration. The fix is to add a line-start anchor and require the `replace` keyword (the original amendment #3 comment says "don't anchor on replace keyword, handle block form", but that reasoning only applies inside a block — the keyword is always present). A safer alternative: strip comment lines before matching.

Minimal fix — strip comment lines:

```python
non_comment = "\n".join(
    l for l in content.splitlines() if not l.lstrip().startswith("//")
)
for match in re.finditer(r'\S+\s+=>\s+(\.\./\S+)', non_comment):
    ...
```

---

**C-03. LOW: Cobra regex misses `Use:` field when it follows a function-literal inner brace**

File: `/home/mk/projects/Demarch/interverse/intermap/python/intermap/patterns.py`, lines 115-125.

Pattern: `r'&cobra\.Command\s*\{[^}]*Use:\s*"([^"]+)"'` with `re.DOTALL`.

`[^}]*` is a negated character class. `re.DOTALL` does not affect it — `[^}]` already matches newlines. The negated class stops at the first `}` it encounters. In cobra commands where `RunE`, `PersistentPreRunE`, or similar fields hold a function literal, the `}` that closes the function body appears before the `}` that closes the `cobra.Command` struct. If `Use:` appears after one of those inner `}` characters, the pattern will not match. Example that fails:

```go
var cmd = &cobra.Command{
    RunE: func(cmd *cobra.Command, args []string) error {
        return serve(cmd.Context())
    },
    Use: "serve",  // <- never seen by [^}]*
}
```

In practice, most cobra commands put `Use:` first (it is idiomatic Go), so the miss rate is low, but any codebase that places `Use:` after `RunE` will report zero cobra commands from that file. This is a correctness gap in pattern detection, not a data-corruption issue. Documented here as LOW.

Fix: replace `[^}]*` with a non-greedy all-char match: `(?:.*?)` in DOTALL mode:

```python
r'&cobra\.Command\s*\{(?:.*?)Use:\s*"([^"]+)"'
```

with `re.DOTALL`. This correctly scans through inner braces.

---

**C-04. LOW: `open()` without encoding guard risks `UnicodeDecodeError` on non-UTF-8 manifests**

Files:
- `/home/mk/projects/Demarch/interverse/intermap/python/intermap/cross_project.py`, lines 97, 116, 143
- (same pattern in `_scan_go_deps`, `_scan_python_deps`, `_scan_plugin_deps`)

All three file-reading functions call `open(path)` without `encoding` or `errors` parameters. On Linux with `LANG=en_US.UTF-8` this defaults to UTF-8 strict mode. A go.mod or pyproject.toml with a non-UTF-8 byte (stray Windows-1252 encoding, a corrupted file, or a binary artifact mistakenly named `go.mod`) will raise `UnicodeDecodeError`, which is not caught. The exception propagates up to `scan_cross_project_deps` and then to the MCP dispatcher, returning an unhandled error response.

`patterns.py` uses `errors="replace"` correctly. `cross_project.py` should be brought to parity:

```python
with open(gomod, encoding="utf-8", errors="replace") as f:
    content = f.read()
```

Alternatively, wrap each open in a try/except and skip the file:

```python
try:
    with open(gomod) as f:
        content = f.read()
except (OSError, UnicodeDecodeError):
    return []
```

---

**C-05. INFO: `**_kwargs` silently absorbs `use_session=True` — behavioral change not surfaced**

File: `/home/mk/projects/Demarch/interverse/intermap/python/intermap/change_impact.py`, line 324.

The `use_session` parameter was removed and the function now accepts `**_kwargs` to swallow unknown arguments. Any external caller (outside the dispatch layer) passing `use_session=True` will silently receive git-based results instead of session-based results, with no warning. This is acceptable given the comment on intent, but a defensive log line would help diagnosis:

```python
if _kwargs:
    logger.debug("analyze_change_impact: ignoring unknown kwargs: %s", list(_kwargs))
```

This is informational — no correctness failure in the current call sites (the dispatch layer was also updated and no longer passes `use_session`).

---

## Improvements

**I-01. Test coverage for deletion-hunk false positive** — Add a test in `test_live_changes.py` that creates a commit, then deletes a line from a file (no additions), and asserts that the deleted-line position is NOT listed in `symbols_affected`. This would have caught C-01 automatically.

**I-02. Encode regex comments for go.mod parsing** — Add a one-line comment stripper before the replace-directive regex to explicitly document the comment-exclusion intent; keeps the fix local to the parsing function.

**I-03. Cobra regex: document `Use:`-ordering convention** — Add an inline comment to the pattern explaining that `Use:` must precede function-literal fields in the struct for detection to work, so future maintainers know the limitation without a full regex audit.

**I-04. Centralise subprocess error handling** — Both `live_changes.py` and `change_impact.py` have identical try/except patterns for git subprocess calls. A small shared helper `_run_git(*args, cwd, timeout=10)` would reduce duplication and guarantee consistent error handling across all new git-calling paths.
