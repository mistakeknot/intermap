# Intermap Correctness Review

**Reviewer**: Julik (Flux-drive Correctness)
**Date**: 2026-02-16
**Scope**: Go MCP server + Python subprocess bridge + in-memory caching
**Lines Reviewed**: ~7700 (Go: ~1200, Python: ~6500)

## Executive Summary

Intermap is a project-level code mapping MCP server with a **two-tier caching architecture**: Go-side caching for filesystem metadata and Python-side subprocess isolation for analysis work. The design is clean and safe overall, but has **three high-consequence correctness issues** and several lower-priority risks.

**Critical Findings:**
1. **Cache write-after-read race** in `projectCache.Get()` → decision → `Scan()` → `projectCache.Put()` pattern (TOCTOU with stale data)
2. **Subprocess orphan risk** when Python analysis hangs beyond 60s timeout (no cleanup, resource leak)
3. **TOCTOU in MtimeHash** between filesystem scan and actual Python analysis (invalidation miss window)

**Medium-Risk Findings:**
4. Empty mtime hash bypasses cache validation in `projectCache`
5. HTTP client lacks retry/backoff for intermute calls (fail-fast is correct but could be flaky)
6. No Python test suite for subprocess contract (JSON I/O, error propagation)

**Low-Priority:**
7. `lru_cache` parsers in Python subprocess (safe but wasteful per-invocation, no cross-call reuse)

---

## Architecture Context

### Invariants to Preserve

1. **Cache Coherence**: Cached project metadata must reflect filesystem state within TTL (5min) + mtime-hash tolerance
2. **Subprocess Isolation**: Each Python analysis call is stateless; no shared process state across Go → Python invocations
3. **Error Transparency**: Python analysis failures (parse errors, missing files) must propagate as structured JSON errors to Go
4. **Timeout Discipline**: Python subprocess must not block MCP tool calls indefinitely (60s hard cap)

### Data Flow

```
MCP Tool Call (Go)
  ↓
Cache.Get(key, mtimeHash)  ← if hit: return cached
  ↓ miss
registry.Scan(root)         ← filesystem walk, stat() each .git dir
  ↓
Cache.Put(key, "", data)    ← store with empty mtime hash
  ↓
return to caller

OR (for Python analysis):

MCP Tool Call (Go)
  ↓
bridge.Run(ctx, cmd, project, args)
  ↓
exec.CommandContext(python3 -m intermap ...)  ← 60s timeout
  ↓
Python: build_project_call_graph() → mtime cache → tree-sitter parse
  ↓
JSON stdout → Go unmarshal
```

---

## Critical Issues

### 1. Cache Write-After-Read Race (TOCTOU, Stale Data)

**Location**: `internal/tools/tools.go:58-70` (projectRegistry handler)

**Failure Narrative:**

Two MCP tool calls arrive concurrently for the same `root`:

```
Time   | Thread A                     | Thread B
-------|------------------------------|--------------------------------
T0     | projectCache.Get(root, "")   |
       | → cache miss                 |
T1     |                              | projectCache.Get(root, "")
       |                              | → cache miss
T2     | registry.Scan(root)          |
       | → sees projects [X, Y, Z]    |
T3     |                              | registry.Scan(root)
       |                              | → sees projects [X, Y, Z, W]
       |                              | (W just added to filesystem)
T4     |                              | projectCache.Put(root, "", [X,Y,Z,W])
T5     | projectCache.Put(root, "", [X,Y,Z])  ← OVERWRITES with stale data
```

**Consequence**: Cache now contains stale project list `[X, Y, Z]` for next 5 minutes. New project `W` invisible until TTL expires or explicit `refresh=true`.

**Root Cause**: Classic check-then-act race. `cache.Get()` → decision → `Scan()` → `cache.Put()` has no atomicity. The Go `cache.Mutex` only protects individual map operations, **not** the entire read-decide-write sequence.

**Why Mutex Doesn't Help**: Each `Get()` and `Put()` call is individually atomic, but the **time window between them** is unprotected. Two goroutines can both miss, both scan (at slightly different times), and race to write.

**Fix Strategy (choose one):**

**Option A: Single-Flight / Deduplication Pattern**
```go
var projectCacheFlight = singleflight.Group{}

// Inside projectRegistry handler:
key := root
result, err, _ := projectCacheFlight.Do(key, func() (interface{}, error) {
    if cached, ok := projectCache.Get(key, ""); ok && !refresh {
        return cached, nil
    }
    projects, err := registry.Scan(root)
    if err != nil {
        return nil, err
    }
    projectCache.Put(key, "", projects)
    return projects, nil
})
```
**Pros**: Guarantees only one `Scan()` per cache key, even under concurrent load.
**Cons**: Adds dependency (`golang.org/x/sync/singleflight`).

**Option B: Compare-and-Swap with Generation Counter**
Add a `generation int64` field to cache entries. On `Put()`, only overwrite if generation is newer. Requires scanning timestamp or atomic counter.

**Option C: Accept the Race, Document It**
If project registry churn is rare and stale cache for <5min is acceptable, document the race and recommend `refresh=true` when adding projects.

**Recommended**: **Option A** (single-flight). The stdlib pattern eliminates the race and reduces duplicate filesystem scans under concurrent load.

---

### 2. Subprocess Orphan Risk (Resource Leak, Timeout)

**Location**: `internal/python/bridge.go:35-72` (`Bridge.Run`)

**Failure Narrative:**

1. MCP tool `impact_analysis` calls `bridge.Run(ctx, "impact", project, args)`
2. Python subprocess starts, begins analyzing a 100k-line codebase with deep call graphs
3. At T+60s, `context.WithTimeout` fires
4. `exec.CommandContext` kills the Python process (`SIGKILL` on Unix)
5. **BUT**: If Python spawned any child processes (git submodules, external parsers), those are NOT killed
6. Orphaned processes accumulate, consuming memory/CPU

**Current Behavior**: `cmd.Output()` propagates `context.DeadlineExceeded` but does NOT guarantee cleanup of process tree.

**Additional Risk**: Partial stdout/stderr from killed process may be unparseable JSON, causing opaque `json.Unmarshal` errors instead of clear "timeout" diagnostics.

**Fix Strategy:**

**Step 1: Process Group Cleanup** (Unix-specific)
```go
cmd := exec.CommandContext(ctx, "python3", "-m", "intermap", ...)
cmd.SysProcAttr = &syscall.SysProcAttr{Setpgid: true}

// After cmd.Start(), if timeout:
if ctx.Err() == context.DeadlineExceeded {
    pgid, _ := syscall.Getpgid(cmd.Process.Pid)
    syscall.Kill(-pgid, syscall.SIGKILL)  // Kill process group
}
```

**Step 2: Timeout Detection in Error Path**
```go
stdout, err := cmd.Output()
if err != nil {
    if ctx.Err() == context.DeadlineExceeded {
        return nil, fmt.Errorf("python %s: timeout after %v", command, b.timeout)
    }
    // ... existing error handling
}
```

**Step 3: Python-Side Timeout Logging**
Add a signal handler in `__main__.py` to log when SIGTERM/SIGKILL received:
```python
import signal
signal.signal(signal.SIGTERM, lambda s, f: sys.stderr.write('{"error":"Timeout","message":"Python subprocess killed"}\n'))
```

**Recommended**: All three steps. Process group cleanup prevents orphans; timeout detection gives better error messages; Python-side logging helps debugging.

---

### 3. TOCTOU in MtimeHash (Invalidation Miss Window)

**Location**: `internal/registry/registry.go:154-196` (`MtimeHash`)

**Failure Narrative:**

```
Time   | Thread/Action
-------|--------------------------------------------------------------
T0     | User edits src/foo.py (mtime changes)
T1     | MCP call: projectRegistry(root)
       | → MtimeHash(root) → walks filesystem → computes hash H1
T2     | User edits src/bar.py (mtime changes)
T3     | projectCache.Put(root, H1, projects)  ← stored with stale hash
T4     | MCP call: code_structure(root)
       | → Python bridge → build_project_call_graph()
       | → Python's FileCache checks mtime of bar.py
       | → sees NEW mtime, cache miss, re-parses
T5     | Result contains bar.py changes BUT Go cache still valid (hash H1)
```

**Consequence**: Go cache entry stays valid for 5min, but Python subprocess already sees newer file state. Next `projectRegistry` call returns cached data from T3, missing T2 changes.

**Root Cause**: `MtimeHash` computes hash **before** the operation it's supposed to invalidate. The hash should reflect the state **after** the scan/analysis, not before.

**Why It Matters**: For incremental analysis, stale mtime hash means:
- Cache hits when it should miss (returns outdated project structure)
- Python subprocess does fresh analysis (correct) but Go cache stays stale (incorrect)

**Fix Strategy:**

**Option A: Compute MtimeHash AFTER Scan**
```go
projects, err := registry.Scan(root)
if err != nil {
    return ...
}
mtimeHash, _ := registry.MtimeHash(root)  // Compute AFTER scan
projectCache.Put(cacheKey, mtimeHash, projects)
```
**Problem**: Still a race — files can change between `Scan()` and `MtimeHash()`.

**Option B: Scan Returns MtimeHash** (Atomic Snapshot)
Change `Scan()` signature to:
```go
func Scan(root string) ([]Project, string, error)
```
Compute mtime hash **during** the scan loop, so both are from the same filesystem traversal.

**Option C: Accept Eventual Consistency**
Document that cache may lag up to TTL behind filesystem state. Use `refresh=true` flag for strict consistency.

**Recommended**: **Option B**. Atomic snapshot eliminates the race and guarantees cache coherence property.

---

## Medium-Risk Issues

### 4. Empty Mtime Hash Bypasses Validation

**Location**: `internal/tools/tools.go:60, 70` — `projectCache.Get(cacheKey, "")` and `Put(cacheKey, "", projects)`

**Issue**: The `projectCache` is created with mtime-hash validation (`Cache[T].Get(key, mtimeHash)`), but **all callers pass empty string `""`**, effectively disabling mtime checks.

**Current Behavior**:
```go
if e.mtimeHash != mtimeHash || time.Since(e.cachedAt) > c.ttl {
    delete(c.entries, key)
    return zero, false
}
```
When `mtimeHash == ""`, the check becomes `"" != ""` → false, so cache only validates TTL, never mtime.

**Consequence**: Cache can return stale data even when project files change, as long as TTL (5min) hasn't expired.

**Why It Exists**: `projectRegistry` scans for `.git` directories (metadata), not source file mtimes. There's no single mtime to key on.

**Fix Options:**

1. **Remove mtime parameter from projectCache**: Change to `Cache[[]Project]` without mtime validation, rely only on TTL + `refresh` flag.
2. **Compute aggregate mtime**: `MtimeHash(root)` on put, pass to `Get()` — but this is expensive (walks all source files).
3. **Stat .git directories**: Use max mtime of all `.git` dirs as cache key — cheaper than full source scan.

**Recommended**: **Option 1** (remove mtime). The cache is already TTL-based, and mtime validation adds complexity without value for this use case. If stale data is unacceptable, use `refresh=true`.

---

### 5. No Retry/Backoff for Intermute HTTP Calls

**Location**: `internal/client/client.go:65-89` (`ListAgents`, `ListReservations`)

**Issue**: HTTP calls to intermute service use 5s timeout but no retry on transient failures (network blip, service restart).

**Current Behavior**:
```go
resp, err := c.http.Do(req)
if err != nil {
    return nil, fmt.Errorf("list agents: %w", err)
}
```

**Consequence**: Single packet loss or intermute service hiccup causes `agent_map` tool to return `agents_error` instead of data. The tool is designed to degrade gracefully (returns empty agents list), but **no attempt to retry**.

**When This Matters**: Long-running agent workflows may call `agent_map` hundreds of times. Without retry, transient failures become permanent gaps in the agent overlay.

**Fix Strategy:**

**Option A: Exponential Backoff with Max Attempts**
```go
var backoff = []time.Duration{100*time.Millisecond, 500*time.Millisecond, 2*time.Second}
for i, delay := range backoff {
    resp, err := c.http.Do(req)
    if err == nil {
        break
    }
    if i < len(backoff)-1 {
        time.Sleep(delay)
    }
}
```

**Option B: Accept Fail-Fast, Add Metrics**
Keep current behavior but log retry-worthy errors separately, so intermute flakiness is visible.

**Recommended**: **Option B** for now. The graceful degradation (empty agents, not crash) is correct. Retry adds complexity and may hide intermute availability issues. If flakiness becomes a problem, add retry with exponential backoff + jitter.

---

### 6. No Python Test Suite for Subprocess Contract

**Location**: `python/tests/` — directory doesn't exist

**Issue**: The Go → Python bridge contract (JSON stdin/stdout, error formatting, timeout behavior) is **not tested**.

**Current Test Coverage**:
- Go side: `cache_test.go`, `client_test.go`, `registry_test.go` (good coverage)
- Python side: **NONE** (no pytest files found)

**Gaps**:
1. What happens if `--args` JSON is malformed? (Go sends, Python must reject gracefully)
2. What happens if Python analysis raises exception mid-stream? (Partial JSON on stdout)
3. What happens if `build_project_call_graph()` hangs? (Timeout should kill cleanly)
4. What happens if SIGTERM arrives during file write? (Atomic cleanup)

**Why It Matters**: Subprocess boundaries are high-risk for data corruption and silent failures. Without tests:
- Error-path JSON formatting can break (wrong keys, missing `"message"`)
- Stdout/stderr interleaving can corrupt JSON
- Timeout cleanup can fail silently

**Fix Strategy:**

**Step 1: Add Python Unit Tests**
```python
# python/tests/test_cli.py
import json, subprocess

def test_dispatch_structure():
    result = subprocess.run(
        ["python3", "-m", "intermap", "--command", "structure", "--project", ".", "--args", "{}"],
        capture_output=True, text=True
    )
    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert "functions" in data or "classes" in data
```

**Step 2: Add Integration Tests (Go → Python)**
```go
// internal/python/bridge_test.go
func TestBridge_StructuredError(t *testing.T) {
    bridge := NewBridge("../../python")
    _, err := bridge.Run(context.Background(), "invalid_command", "/tmp", nil)
    if err == nil {
        t.Fatal("expected error for invalid command")
    }
    // Verify error message format
}
```

**Step 3: Timeout Test**
```go
func TestBridge_Timeout(t *testing.T) {
    ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
    defer cancel()
    bridge := NewBridge("../../python")
    _, err := bridge.Run(ctx, "structure", "/huge/codebase", nil)
    if !errors.Is(err, context.DeadlineExceeded) {
        t.Errorf("expected timeout, got %v", err)
    }
}
```

**Recommended**: All three. Subprocess contract is too fragile to leave untested.

---

## Low-Priority Observations

### 7. `lru_cache` Parsers in Python (Wasteful, Not Wrong)

**Location**: `python/intermap/cross_file_calls.py:159-280` — `@lru_cache(maxsize=1)` on `_get_ts_parser()`, etc.

**Behavior**: Each subprocess invocation creates fresh parsers (tree-sitter `Parser` objects). The `lru_cache` deduplicates within a single subprocess lifetime, but **subprocess exits after each MCP call**.

**Why It's Wasteful**:
- Parser initialization is ~10-50ms per language (tree-sitter grammar loading)
- With `maxsize=1`, only one parser per language per subprocess
- **But subprocess is short-lived** — cache is thrown away after every `bridge.Run()`

**Why It's Not a Bug**:
- Correctness is unaffected (cache is per-process, no shared state)
- Performance impact is minor (initialization is one-time per call, not per file)

**Potential Fix** (if performance becomes issue):
- **Long-lived Python process**: Keep Python subprocess running, send commands via stdin/stdout loop
- **Risks**: State leaks between commands, memory accumulation, shutdown complexity

**Recommendation**: Leave as-is unless profiling shows parser init is >10% of runtime.

---

## Test Coverage Assessment

**Go Tests**: ✅ Strong
- `cache_test.go`: TTL, LRU eviction, mtime invalidation (95 lines, 6 test cases)
- `client_test.go`: HTTP mocking, error paths, unavailable fallback (126 lines, 6 cases)
- `registry_test.go`: Real Interverse filesystem tests, language detection (120 lines, 5 cases)
- `tools_test.go`: Helper function unit tests (27 lines)

**Python Tests**: ❌ Missing Entirely
- No pytest files found
- No CLI contract tests
- No subprocess timeout tests

**Race Detector**: ✅ Passes
```
go test -race ./...
ok  	internal/cache	1.073s
ok  	internal/client	1.023s
ok  	internal/registry	1.029s
ok  	internal/tools	1.015s
```
No races detected in current test suite, but **cache write-after-read race is not covered** (requires concurrent tool calls).

**Recommended Next Steps**:
1. Add concurrent cache test (`TestProjectCache_Concurrent`)
2. Add Python CLI test suite (`python/tests/test_cli.py`)
3. Add bridge integration test with timeout (`internal/python/bridge_test.go`)

---

## Concurrency Review

### Go Side

**Synchronization Primitives**:
- `cache.go`: `sync.Mutex` — correctly protects map access, but not decision logic
- `tools.go`: No goroutines, all handlers are request-scoped
- `client.go`: No shared state, HTTP client is thread-safe

**Race Conditions**:
1. ❌ **Cache write-after-read** (Issue #1) — unprotected decision window
2. ✅ **LRU eviction** — correctly protected under `cache.mu`
3. ✅ **HTTP client reuse** — `http.Client` is documented thread-safe

**Context Propagation**:
- ✅ `bridge.Run(ctx, ...)` correctly inherits caller context
- ✅ Timeout context created with `defer cancel()` (no leak)
- ✅ HTTP requests use `http.NewRequestWithContext(ctx, ...)`

**Resource Lifecycle**:
- ✅ HTTP response bodies closed (`defer resp.Body.Close()`)
- ⚠️ Subprocess cleanup on timeout not guaranteed (Issue #2)

### Python Side

**Subprocess Isolation**: ✅ Correct
- Each `bridge.Run()` spawns fresh subprocess
- No shared process state across calls
- `FileCache` in Python is per-subprocess (discarded after exit)

**State Management**:
- `FileCache` (Python dict) — single-threaded, no concurrency risk
- `lru_cache` — thread-safe in Python, but irrelevant (single-threaded subprocess)

**Error Propagation**:
- ✅ Structured JSON errors on stderr (`_error_exit()`)
- ✅ Traceback included in error payload
- ⚠️ Partial stdout on timeout may corrupt JSON (Issue #2)

---

## Data Integrity Review

### Filesystem TOCTOU

**Issue #3 (MtimeHash)**: Already covered in Critical Issues.

**Additional TOCTOU in `registry.Scan()`**:
```go
for _, sub := range subEntries {
    projectPath := filepath.Join(groupPath, sub.Name())
    gitDir := filepath.Join(projectPath, ".git")
    if _, err := os.Stat(gitDir); err != nil {  // ← TOCTOU: .git may vanish
        continue
    }
    p := Project{
        Name:      sub.Name(),
        Language:  detectLanguage(projectPath),  // ← TOCTOU: files may change
        GitBranch: readGitBranch(gitDir),        // ← TOCTOU: HEAD may change
    }
    projects = append(projects, p)
}
```

**Consequence**: If `.git` directory is deleted between `Stat()` and `readGitBranch()`, the call fails. Current code **silently skips** (correct), but could return partial project with empty branch.

**Fix**: Wrap `detectLanguage()` and `readGitBranch()` in error checks, return empty string on failure (already done).

**Severity**: Low — `.git` deletion mid-scan is rare, graceful skip is acceptable.

---

### JSON Parsing Robustness

**Go Side (`bridge.go:66-69`)**:
```go
var result map[string]any
if err := json.Unmarshal(stdout, &result); err != nil {
    return nil, fmt.Errorf("parse python output: %w", err)
}
```

**Risk**: If Python writes partial JSON (killed mid-stream), `Unmarshal` fails with opaque error.

**Mitigation**: Issue #2 fix (timeout detection) makes this clearer.

**Python Side (`__main__.py:24-25`)**:
```python
result = dispatch(args.command, args.project, extra_args)
json.dump(result, sys.stdout)
sys.stdout.write("\n")
```

**Risk**: If `result` contains non-serializable types (e.g., datetime, Path), `json.dump()` raises `TypeError`, exits with traceback on stderr.

**Mitigation**: Current `_error_exit()` handler catches this, but **only if the exception is raised in `dispatch()`**. If `json.dump()` itself fails, no structured error.

**Fix**:
```python
try:
    json.dump(result, sys.stdout)
    sys.stdout.write("\n")
except (TypeError, ValueError) as e:
    _error_exit("JSONSerializationError", str(e))
```

---

## Error Handling Review

### Go Error Propagation

**Pattern**: All tool handlers return `(*mcp.CallToolResult, error)`, always return `(result, nil)` with errors embedded in result.

**Why**: MCP protocol requires result always returned, errors are in `CallToolResult.Content`.

**Correctness**: ✅ All error paths return `mcp.NewToolResultError(msg)`, no panics.

**Coverage**:
- ✅ `os.Getwd()` failure
- ✅ `registry.Scan()` failure
- ✅ `bridge.Run()` failure (Python error or timeout)
- ✅ HTTP client errors (intermute unavailable)

### Python Error Propagation

**Structured Errors** (`__main__.py:34-42`):
```python
def _error_exit(error_type: str, message: str):
    error = {
        "error": error_type,
        "message": message,
        "traceback": traceback.format_exc(),
    }
    json.dump(error, sys.stderr)
    sys.stderr.write("\n")
    sys.exit(1)
```

**Go Parsing** (`bridge.go:55-61`):
```go
if exitErr, ok := err.(*exec.ExitError); ok {
    var pyErr map[string]any
    if json.Unmarshal(exitErr.Stderr, &pyErr) == nil {
        return nil, fmt.Errorf("python %s: %v", command, pyErr["message"])
    }
    return nil, fmt.Errorf("python %s: %s", command, string(exitErr.Stderr))
}
```

**Correctness**: ✅ Structured errors propagate cleanly. If JSON parse fails, raw stderr returned (fallback).

**Gap**: If Python writes to both stdout and stderr before dying, `exitErr.Stderr` may be truncated or interleaved.

**Mitigation**: Python only writes to stderr on error, stdout on success (clean separation).

---

## Recommendations Summary

### Must Fix (Before Production Use)

1. **Cache write-after-read race** → Use `singleflight.Group` for deduplication
2. **Subprocess orphan risk** → Add process group cleanup + timeout detection
3. **TOCTOU in MtimeHash** → Compute mtime hash atomically with scan

### Should Fix (Quality/Robustness)

4. **Empty mtime hash** → Remove mtime parameter from `projectCache`, use TTL-only
5. **Python test suite** → Add CLI contract tests, timeout tests, integration tests
6. **JSON serialization safety** → Wrap `json.dump()` in try/except

### Consider Later (Low Impact)

7. **HTTP retry/backoff** → Add if intermute flakiness becomes measurable issue
8. **Long-lived Python process** → Only if parser init shows up in profiling

---

## Appendix: Race Detector Output

```bash
$ go test -race ./...
?   	github.com/mistakeknot/intermap/cmd/intermap-mcp	[no test files]
?   	github.com/mistakeknot/intermap/internal/python	[no test files]
ok  	github.com/mistakeknot/intermap/internal/cache	1.073s
ok  	github.com/mistakeknot/intermap/internal/client	1.023s
ok  	github.com/mistakeknot/intermap/internal/registry	1.029s
ok  	github.com/mistakeknot/intermap/internal/tools	1.015s
```

**Note**: No races detected, but cache write-after-read race is **logic-level**, not memory-level. The race detector only catches data races (concurrent unprotected memory access), not semantic races (TOCTOU decision bugs).

To detect Issue #1, need a test that:
1. Launches two goroutines
2. Both call `projectRegistry` handler concurrently
3. Verifies cache ends up with latest data, not stale

---

## Files Reviewed

**Go (1200 lines)**:
- `cmd/intermap-mcp/main.go` (30 lines)
- `internal/cache/cache.go` (97 lines)
- `internal/cache/cache_test.go` (95 lines)
- `internal/python/bridge.go` (88 lines)
- `internal/registry/registry.go` (197 lines)
- `internal/registry/registry_test.go` (120 lines)
- `internal/tools/tools.go` (374 lines)
- `internal/tools/tools_test.go` (27 lines)
- `internal/client/client.go` (124 lines)
- `internal/client/client_test.go` (126 lines)

**Python (6500 lines, sampled)**:
- `python/intermap/__main__.py` (48 lines)
- `python/intermap/analyze.py` (92 lines)
- `python/intermap/project_index.py` (322 lines)
- `python/intermap/file_cache.py` (29 lines)
- `python/intermap/change_impact.py` (100 lines, partial read)
- `python/intermap/cross_file_calls.py` (3424 lines, header + lru_cache pattern review)
- `python/intermap/extractors.py` (100 lines, partial read)

**Total**: ~7700 lines across 21 files.
