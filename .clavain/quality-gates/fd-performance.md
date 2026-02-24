# Performance Review — Intermap Sprint

**Reviewer:** fd-performance
**Date:** 2026-02-23
**Scope:** cross_project.py, patterns.py, live_changes.py, workspace.py (new/modified)

---

### Findings Index

| SEVERITY | ID | Section | Title |
|----------|----|---------|-------|
| HIGH | P1 | patterns.py | Full file read on every tool invocation — no caching |
| HIGH | P2 | live_changes.py | Two sequential git subprocesses per call — no caching |
| MEDIUM | P3 | live_changes.py | DefaultExtractor instantiated once but no file-level cache used |
| MEDIUM | P4 | workspace.py | Double ignore check per file in iter_workspace_files |
| LOW | P5 | cross_project.py | Plugin JSON read twice per project (two candidate paths) |
| LOW | P6 | patterns.py | re.DOTALL cobra regex scans full file content in one pass |

**Verdict:** needs-changes

---

### Summary

The three new Python tools (detect_patterns, cross_project_deps, live_changes) bypass the existing Go-level LRU cache entirely because the Go bridge spawns a fresh Python subprocess per invocation. The Go cache only covers project_registry. The Python FileCache exists but is per-subprocess (dies on exit), so every MCP tool call that invokes detect_patterns or cross_project_deps re-reads and re-scans all source files from scratch. On the Demarch monorepo with 33+ projects each containing hundreds of Go/Python files, detect_patterns will read and regex-scan every .go or .py file on each call. live_changes has a bounded scope (only git-changed files) but spawns two blocking git subprocesses sequentially with no caching of the diff output. These are agent-interactive tools — agents call them during active sessions — so the latency is felt directly.

---

### Issues Found

**P1. HIGH: detect_patterns reads every source file on every invocation — no caching**

`_detect_go_patterns` and `_detect_python_patterns` in `/home/mk/projects/Demarch/interverse/intermap/python/intermap/patterns.py` (lines 56-171) use `os.walk` to visit every `.go` or `.py` file in the project and call `Path(fpath).read_text(errors="replace")` on each. There is no mtime check, no in-process cache, and no Go-level cache wrapping `detect_patterns`. The Go bridge spawns a fresh `python3 -m intermap` subprocess for every MCP tool invocation (`/home/mk/projects/Demarch/interverse/intermap/internal/python/bridge.go` line 44), so the Python-level `FileCache` dies with each subprocess. For a project like `intermute` (Go, large) this means reading every `.go` file on every `detect_patterns` call. At 10x monorepo scale (hundreds of Go files per project) this would be the dominant wall-clock cost.

**P2. HIGH: live_changes runs two sequential git subprocesses with no result caching**

`_get_git_diff` in `/home/mk/projects/Demarch/interverse/intermap/python/intermap/live_changes.py` (lines 83-136) calls `subprocess.run` twice in sequence: first `git diff --name-status`, then `git diff --unified=0`. Both are blocking calls with a 10-second timeout. These cannot be parallelized with `subprocess.Popen` in the current design. More importantly, if an agent calls `live_changes` more than once in a session (e.g., to check progress), the full diff is re-executed both times. There is no Go-level cache for this tool. A monorepo with a large diff (hundreds of changed files) will produce a large `--unified=0` output that must be fully buffered into memory on each call.

**P3. MEDIUM: DefaultExtractor in live_changes has no file-level cache — re-parses every changed file on repeat calls**

`get_live_changes` in `/home/mk/projects/Demarch/interverse/intermap/python/intermap/live_changes.py` (lines 26-80) creates a single `DefaultExtractor()` instance and calls `extractor.extract(fpath)` for each changed file. The `DefaultExtractor` does not use `FileCache` — it reads the file and runs AST parse or regex every time. Since the Go bridge spawns a new subprocess per call, the in-process `FileCache` from `file_cache.py` is never populated across calls. For Python files this is a full `ast.parse()` per changed file per invocation. If 20 Python files changed and the agent calls `live_changes` 3 times, that is 60 AST parses. The fix is to add a Go-level cache entry for `live_changes` keyed on `(project, baseline, mtime_hash_of_changed_files)`.

**P4. MEDIUM: iter_workspace_files calls should_ignore twice per file (directory prune + file check)**

`iter_workspace_files` in `/home/mk/projects/Demarch/interverse/intermap/python/intermap/workspace.py` (lines 251-282) calls `should_ignore` on each directory entry during pruning (line 262-264) and then again on the full file path after the filename filter (line 276-277). The `_FnmatchSpec.match_file` fallback (used when `pathspec` is not installed) iterates all patterns including an inner loop over pattern parts for `**` patterns (lines 307-324 of `ignore.py`). With the default 8-pattern `DEFAULT_EXCLUDE_PATTERNS` list this is O(patterns * path_components) per file, applied twice. This is low-overhead for small projects but accumulates across the full file walk for `detect_patterns` which itself walks every source file. The pathspec library (when available) uses a compiled automaton and is fast; the fnmatch fallback is the risk.

**P5. LOW: cross_project._scan_plugin_deps checks two plugin.json paths per project unconditionally**

`_scan_plugin_deps` in `/home/mk/projects/Demarch/interverse/intermap/python/intermap/cross_project.py` (lines 129-153) always opens both `plugin.json` and `.claude-plugin/plugin.json` if they exist. This is two `os.path.isfile` stat calls plus up to two `json.load` calls per project. On a 33-project monorepo this is 66 stat calls for plugin manifests alone, in addition to the go.mod and pyproject.toml reads. Each is individually cheap, but taken together with the cross-project scan running on the full monorepo root with no caching, the cumulative I/O across all three file types (go.mod + pyproject.toml + plugin.json x2) is up to 5 small-file reads per project. This is acceptable today but becomes a design smell if the pattern count grows.

**P6. LOW: cobra command regex uses re.DOTALL across full file content**

The cobra command detection regex in `/home/mk/projects/Demarch/interverse/intermap/python/intermap/patterns.py` (lines 115-119) uses `re.DOTALL` which makes `.` match newlines, causing the `[^}]*` to scan potentially large file sections between `{` and `}` characters. On large Go files with many struct literals, this can cause catastrophic backtracking if a `{` is opened without a matching `}` in the expected form. The pattern is `&cobra\.Command\s*\{[^}]*Use:\s*"([^"]+)"` — the `[^}]*` is the safe part since it excludes `}`, but with `re.DOTALL` active the `.` in surrounding constructs (if any were added to the pattern) could cause issues. Currently bounded by `[^}]*` so backtracking risk is low, but the `re.DOTALL` flag is unnecessary and misleading.

---

### Improvements

**P1-fix. Add Go-level mtime-keyed cache for detect_patterns and cross_project_deps**

Both tools produce stable results for a given project state. The existing Go cache infrastructure (`/home/mk/projects/Demarch/interverse/intermap/internal/cache/cache.go`) supports mtime-keyed entries with LRU eviction and a 5-minute TTL. Add a per-project cache entry in the `crossProjectDeps` and `detectPatterns` handlers in `tools.go` using the project directory mtime-hash as the cache key. This would eliminate re-scans for the common case where files have not changed since the last call.

**P2-fix. Cache live_changes git diff output at the Go level keyed on (project, baseline, git HEAD SHA)**

Add a short-TTL cache (30 seconds) for `live_changes` results in the Go handler. Key on `(project_path, baseline, git_HEAD_sha)` where the SHA is obtained by a lightweight `git rev-parse HEAD` call. This prevents re-running both git diff subprocesses on back-to-back agent calls within a session. The 30-second TTL is appropriate since working-tree changes should be visible promptly.

**P3-fix. Pass file cache into DefaultExtractor in live_changes, or skip extraction for unchanged files**

The `DefaultExtractor` in `live_changes.py` could be extended to accept an optional `FileCache` instance. Since the Go-level cache is the primary cache for cross-call deduplication, the simpler approach is to skip AST extraction entirely for files that have not changed their hunk-line intersection since last call — i.e., defer extraction until the cache miss path is hit.

**P4-fix. Hoist ignore_spec compilation outside the walk loop (already done) — verify pathspec is installed**

The `load_ignore_patterns` call is correctly hoisted before the walk. The remaining improvement is to document in CLAUDE.md or requirements that `pathspec` should be installed (it is in the tldr-swinton dependency set) to avoid the slower fnmatch fallback, and to add a startup warning log if `PATHSPEC_AVAILABLE` is False.

**P6-fix. Remove re.DOTALL from cobra pattern — it is not needed**

The `[^}]*` quantifier already prevents multi-line span through closing braces. Remove `re.DOTALL` from the cobra regex call at `patterns.py:116` to avoid confusion and potential future bugs if the pattern is extended.
