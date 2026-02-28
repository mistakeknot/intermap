# Performance Review — Intermap Sprint (2026-02-23)

**Scope:** Three new Python MCP tools added in this sprint: `cross_project_deps`, `detect_patterns`, `live_changes`. Supporting modules: `workspace.py`, `ignore.py`, `cross_project.py`, `patterns.py`, `live_changes.py`.

**Performance profile:** This is an interactive agent tool. Agents call it during active sessions. Latency is directly felt by the agent waiting for structural context before proceeding. No SLO is defined in the project docs, but the existing Go-level cache uses a 5-minute TTL with LRU eviction, implying the team targets fast repeat calls and is willing to pay full cost on cold invocations.

**Key constraint:** The Go bridge (`/home/mk/projects/Demarch/interverse/intermap/internal/python/bridge.go`) spawns a fresh `python3 -m intermap` subprocess per MCP tool call. This means the Python-level `FileCache` (`file_cache.py`) is ephemeral — it never persists across calls. Only the Go-level `cache.Cache` survives between calls.

---

## 2026-02-28 Hardening Benchmark Evidence (iv-54iqe)

`live_changes` now supports `INTERMAP_LIVE_CHANGES_MODE=optimized|legacy` (default `optimized`) and includes repeated-call optimizations:
- single `git` subprocess parse in optimized mode (`--patch-with-raw`)
- Python symbol span cache keyed by file metadata (`mtime_ns`, `ctime_ns`, `size`) with entry and byte caps
- baseline symbol extraction cache for pure-deletion attribution to avoid repeated `git show` calls

The earlier short-TTL diff cache was removed to avoid stale results on rapid successive edits.

Benchmark command:

```bash
PYTHONPATH=python python3 -m pytest python/tests/test_live_changes_perf.py -v
```

Current benchmark gate:
- `test_live_changes_optimized_mode_improves_repeated_call_median_by_15_percent`
- asserts at least `15%` median repeated-call improvement for optimized mode on a synthetic 15-file fixture
- captures provenance in assertion context (`commit_sha`, platform, python version, mode labels)
- `test_live_changes_optimized_mode_p95_not_worse_than_legacy_by_20_percent`
- `test_live_changes_optimized_mode_cold_call_not_more_than_75_percent_slower`
- `test_live_changes_optimized_mode_reuses_baseline_symbol_cache`
- `test_live_changes_symbol_cache_respects_byte_cap`

Rollback instruction (runtime guardrail):

```bash
export INTERMAP_LIVE_CHANGES_MODE=legacy
```

Use `legacy` mode for emergency rollback if optimized behavior regresses in production-like validation. Legacy mode restores the two-subprocess diff path and start-line extractor attribution.

---

## Architecture Context

```
MCP client
  -> Go MCP server (tools.go)
      -> Go-level LRU cache (cache.go, 5min TTL)  -- only projectRegistry uses this
      -> Python bridge (bridge.go)
          -> fresh python3 subprocess each call
              -> Python FileCache  -- dies on subprocess exit
```

The three new tools (`crossProjectDeps`, `detectPatterns`, `liveChanges`) do **not** have Go-level cache entries. Every invocation pays full Python startup + full analysis cost.

---

## Issues Found

### P1 — HIGH: detect_patterns reads every source file on every call

**File:** `/home/mk/projects/Demarch/interverse/intermap/python/intermap/patterns.py`, lines 56-171

`_detect_go_patterns` and `_detect_python_patterns` walk the full project tree with `os.walk`, then call `Path(fpath).read_text()` and run 4-5 regex patterns on every `.go` or `.py` file. No caching exists at any layer for this tool. On a Go project like `intermute` with ~50 .go files, this is 50 file reads + 250 regex operations per `detect_patterns` call. At 10x scale, a large Go project (500 files) would produce 2500+ regex operations per call. Every agent call pays this in full.

**Who feels it:** Any agent calling `detect_patterns` on a medium-to-large Go project. Re-calls within a session (common for incremental understanding) pay full cost each time.

**Fix:** Add a Go-level cache entry in `tools.go` for `detect_patterns` keyed on `(project_path, mtime_hash)`. The existing `cache.Cache` infrastructure handles this. The mtime hash can be computed by hashing the project root's directory mtime (already done for registry).

---

### P2 — HIGH: live_changes runs two sequential blocking git subprocesses with no caching

**File:** `/home/mk/projects/Demarch/interverse/intermap/python/intermap/live_changes.py`, lines 83-136

`_get_git_diff` calls `subprocess.run` twice in sequence — first `git diff --name-status`, then `git diff --unified=0`. Both are blocking, each with a 10-second timeout. Worst case: 20 seconds blocked per call. More typical: 200-500ms for a moderate diff. But on large diffs (`--unified=0` with hundreds of changed files), the full patch is buffered into a Python string, line-split, and scanned with regex. No Go-level cache wraps this tool.

If an agent calls `live_changes` twice in 30 seconds (e.g., to check progress after edits), the full git diff runs twice.

**Who feels it:** Any agent doing iterative development work that checks live_changes multiple times per session.

**Fix (two parts):**
1. Add a short-TTL Go-level cache (30-60 seconds) keyed on `(project_path, baseline, git_HEAD_sha)`. The SHA can be obtained with a cheap `git rev-parse HEAD` subprocess that costs ~5ms and guards against stale results.
2. Consider combining the two `subprocess.run` calls into one: `git diff --unified=0 baseline` already contains the `+++` headers from which status can be derived, eliminating the first call entirely.

---

### P3 — MEDIUM: DefaultExtractor in live_changes does no file-level caching

**File:** `/home/mk/projects/Demarch/interverse/intermap/python/intermap/live_changes.py`, lines 26-80

`get_live_changes` creates a single `DefaultExtractor()` and calls `extractor.extract(fpath)` for each changed file. For Python files, this runs `ast.parse()` — correct and fast for small files, but on a large Python module (thousands of lines) it is measurable. The `DefaultExtractor` does not use `FileCache`. Since the subprocess is fresh each call, there is no cross-call benefit even if FileCache were used.

The impact is bounded by the number of git-changed files (typically small), so this is medium rather than high severity. It becomes high if an agent works across a PR with 50+ changed Python files.

**Fix:** The P2-fix (Go-level cache) addresses this indirectly by caching the full result. If the Go cache is not added, pass a `FileCache` instance into `DefaultExtractor` and populate it during the single subprocess lifetime to deduplicate extractions within one call (currently not a problem since each file is extracted once per call).

---

### P4 — MEDIUM: iter_workspace_files calls should_ignore twice per file

**File:** `/home/mk/projects/Demarch/interverse/intermap/python/intermap/workspace.py`, lines 251-282

The walk prunes directories at line 261-264 using `should_ignore`, then checks the full file path again at line 275-277. This double-checks files in directories that are not pruned. The `_FnmatchSpec` fallback (used without `pathspec`) has O(patterns * path_components) cost per call. With 8 default exclude patterns, this is low — but `_detect_go_patterns` and `_detect_python_patterns` in `patterns.py` use their own `os.walk` with a simpler inline exclude set (`{".git", "vendor", "node_modules"}`), bypassing `iter_workspace_files` entirely. So this double-check cost falls on the pre-existing `code_structure` and `change_impact` tools, not the new ones.

**Note:** The new `patterns.py` walk does NOT use `iter_workspace_files` at all — it has its own inline directory exclusion. This is inconsistent with the rest of the codebase and means the new tools skip `.tldrsignore` patterns and workspace scoping. For the Demarch monorepo this could cause the scan to enter directories that `iter_workspace_files` would have pruned.

**Fix:** The inconsistency is the higher-priority concern: `_detect_go_patterns` and `_detect_python_patterns` should use `iter_workspace_files` with `extensions={".go"}` or `{".py"}` instead of their own walk. This unifies ignore-pattern handling and will also reduce file reads by respecting project-scoped exclusions.

---

### P5 — LOW: cross_project._scan_plugin_deps stats two paths per project unconditionally

**File:** `/home/mk/projects/Demarch/interverse/intermap/python/intermap/cross_project.py`, lines 129-153

`_scan_plugin_deps` always calls `os.path.isfile` on both `plugin.json` and `.claude-plugin/plugin.json` for every project. On a 33-project monorepo this is 66 stat syscalls for plugin manifests alone, on top of 33 go.mod stats and 33 pyproject.toml stats — 132 stat calls total for the cross-project scan. Each syscall is sub-millisecond on a local filesystem with warm VFS cache, so this is not currently impactful. It is noted because `scan_cross_project_deps` is called without any Go-level caching, so this accumulates on every call.

**No immediate fix required.** Addressed by the P1-fix Go-level cache covering the full result.

---

### P6 — LOW: re.DOTALL flag unnecessary on cobra command regex

**File:** `/home/mk/projects/Demarch/interverse/intermap/python/intermap/patterns.py`, lines 115-119

```python
cobra_cmds = re.findall(
    r'&cobra\.Command\s*\{[^}]*Use:\s*"([^"]+)"',
    content, re.DOTALL,
)
```

The `[^}]*` quantifier already prevents the match from crossing closing braces, so `re.DOTALL` has no practical effect on this specific pattern. However, `re.DOTALL` is misleading to future maintainers — it implies the pattern is intended to span lines, which could lead to incorrect pattern extensions. The flag should be removed.

**Fix:** Remove `re.DOTALL` from this `re.findall` call. No behavioral change on correct input.

---

## Subprocess Overhead Summary

Every Python-delegated tool call pays:
- ~30-80ms Python interpreter startup
- Module import time (`ast`, `re`, `pathlib`, `subprocess`)
- Full analysis cost with no cross-call caching

For `cross_project_deps` on a 33-project monorepo: approximately 100 small-file reads + startup = ~200-400ms cold.
For `detect_patterns` on a medium Go project (50 .go files): approximately 50 file reads + 250 regex ops + startup = ~300-600ms cold.
For `live_changes` on a small diff (5 changed files): approximately 2 git subprocesses + 5 AST parses + startup = ~200-500ms cold.

All three are acceptable for a single call. The problem is repeat calls within a session, which pay full cost every time because no Go-level cache entry exists for any of these tools.

---

## Recommended Fix Priority

1. **Add Go-level cache entries for crossProjectDeps and detectPatterns** (high impact, low complexity — mirrors existing projectRegistry cache pattern)
2. **Combine the two git diff subprocesses in live_changes into one** (medium impact, low complexity — reduces worst-case latency by ~50%)
3. **Add short-TTL Go-level cache for liveChanges keyed on HEAD SHA** (medium impact, medium complexity)
4. **Migrate patterns.py walk to iter_workspace_files** (medium impact, improves correctness + consistency)
5. **Remove re.DOTALL from cobra regex** (no impact, correctness hygiene)

---

## What Is Not a Problem

- `_discover_projects` in `cross_project.py` is appropriately shallow (two levels of `os.listdir` only, plus a single `.git` stat per candidate). This is fast and correct.
- The go.mod and pyproject.toml regex patterns are simple, non-backtracking, and applied to small files. No concern.
- The `DefaultExtractor` design is correct — AST for Python, regex for Go/TS/Rust. No concern with the extractor logic itself.
- The 10-second subprocess timeout on git calls is appropriate. No risk of hanging the MCP server.
- The `_FnmatchSpec` fallback in `ignore.py` is only relevant when `pathspec` is not installed. The project likely has `pathspec` available (it is a tldr-swinton transitive dependency).
