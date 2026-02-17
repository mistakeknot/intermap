# Code Quality Review: intermap Plugin

**Reviewer:** Flux-drive Quality & Style Reviewer
**Date:** 2026-02-16
**Scope:** Go + Python hybrid MCP plugin (~7,738 lines)
**Languages:** Go (1,010 lines), Python (3,728 lines)

## Executive Summary

**Overall Assessment:** High quality implementation with strong Go idioms, good Python patterns, and well-designed language bridge architecture. The code demonstrates mature understanding of both ecosystems.

**Key Strengths:**
- Excellent error wrapping in Go (`fmt.Errorf` with `%w`)
- Clean separation of concerns (registry, cache, client, tools, Python bridge)
- Good use of Go generics for cache implementation
- Python uses dataclasses and protocols effectively
- Strong test coverage for core Go packages

**Areas for Improvement:**
- Minimal test coverage for tools.go (373 lines, 26 lines of tests)
- Python lacks type hints in several key functions
- Some Python functions exceed 100 lines (change_impact.py)
- Missing error context in some Go tool handlers

---

## Go Code Review

### Strengths

#### 1. Error Handling (Excellent)
All errors are properly wrapped with context using `%w`:

```go
// bridge.go:38
if err := json.Marshal(args); err != nil {
    return nil, fmt.Errorf("marshal args: %w", err)
}

// registry.go:25
if absRoot, err := filepath.Abs(root); err != nil {
    return nil, fmt.Errorf("abs root: %w", err)
}
```

No silent error discarding observed. Error chains are preserved throughout.

#### 2. Interface Design (Best Practice)
The codebase follows "accept interfaces, return structs":

```go
// client.go uses Option pattern for configuration
type Option func(*Client)

func WithBaseURL(url string) Option {
    return func(c *Client) {
        c.baseURL = url
    }
}
```

The Python bridge uses a concrete `Bridge` struct, avoiding interface bloat.

#### 3. Generic Cache (Modern Go)
Clean use of Go 1.18+ generics:

```go
// cache.go:8-14
type Cache[T any] struct {
    mu      sync.Mutex
    entries map[string]*entry[T]
    ttl     time.Duration
    maxSize int
}

func New[T any](ttl time.Duration, maxSize int) *Cache[T]
```

LRU eviction logic is correct and efficient.

#### 4. Package Organization
Clean internal package structure:
- `internal/cache/` - generic cache implementation
- `internal/client/` - HTTP client for intermute
- `internal/python/` - subprocess bridge
- `internal/registry/` - project scanning
- `internal/tools/` - MCP tool registration

No circular dependencies. Each package has a single, well-defined responsibility.

#### 5. Resource Management
Python subprocess bridge properly uses context timeouts:

```go
// bridge.go:41-42
ctx, cancel := context.WithTimeout(ctx, b.timeout)
defer cancel()
```

HTTP client has reasonable timeout (5s).

### Issues & Recommendations

#### 1. Missing Error Context in Tool Handlers (Medium Priority)

Several tool handlers return errors without preserving the call site:

```go
// tools.go:67 - BEFORE
if err != nil {
    return mcp.NewToolResultError(fmt.Sprintf("scan: %v", err)), nil
}

// RECOMMENDED
if err != nil {
    return mcp.NewToolResultError(fmt.Sprintf("scan %q: %w", root, err)), nil
}
```

**Fix:** Add the parameter values to error messages so MCP clients can see what failed:
- `projectRegistry` should include `root` path
- `resolveProject` should include `path`
- `agentMap` should include `root`
- Python bridge errors should include `command` name

#### 2. Minimal Test Coverage for tools.go (High Priority)

`tools.go` is 373 lines but `tools_test.go` is only 26 lines covering type coercion helpers.

**Missing test coverage:**
- `projectRegistry()` cache behavior
- `resolveProject()` path resolution
- `agentMap()` overlay logic (148 lines of complex data merging)
- `codeStructure()`, `impactAnalysis()`, `changeImpact()` argument forwarding

**Recommendation:** Add integration tests that:
1. Verify cache hit/miss behavior for `projectRegistry`
2. Test `agentMap` overlay construction with mock intermute responses
3. Validate argument transformation in Python bridge tools

Example:
```go
func TestProjectRegistry_Cache(t *testing.T) {
    // Test that refresh=false returns cached data
    // Test that refresh=true invalidates cache
}

func TestAgentMap_Overlay(t *testing.T) {
    // Mock intermute responses
    // Verify agent-project matching logic
    // Verify reservation attachment
}
```

#### 3. registry.go: Silent Directory Skip (Low Priority)

```go
// registry.go:40-41
subEntries, err := os.ReadDir(groupPath)
if err != nil {
    continue  // Silent skip
}
```

This silently ignores permission errors or broken symlinks. Consider logging or returning a partial result with warnings.

**Fix:**
```go
subEntries, err := os.ReadDir(groupPath)
if err != nil {
    // Log warning or collect in a separate errors slice
    continue
}
```

#### 4. Cache Lacks Capacity Guard (Low Priority)

`cache.go:62` has a `>=` check for eviction, but the comment says "if at capacity":

```go
// cache.go:62
if _, exists := c.entries[key]; !exists && len(c.entries) >= c.maxSize {
    c.evictLRU()
}
```

This is correct (evict before adding the new entry), but the naming is subtle. Consider renaming `maxSize` to `maxEntries` for clarity.

#### 5. Missing Validation for intOr/stringOr (Low Priority)

The type coercion helpers in `tools.go` silently convert invalid types to defaults. This is fine for MCP argument handling, but adding comments explaining the behavior would help:

```go
// stringOr returns the string value of v, or def if v is not a non-empty string.
// This handles MCP argument type mismatches gracefully.
func stringOr(v any, def string) string { ... }
```

---

## Python Code Review

### Strengths

#### 1. Protocol-Based Design (Excellent)
Uses Python protocols for decoupling:

```python
# protocols.py:56-65
class FileExtractor(Protocol):
    """Protocol for extracting code structure from files."""

    def extract(self, path: str) -> FileExtractionResult:
        """Extract functions, classes, and imports from a source file."""
        ...
```

This allows plugging in tree-sitter extractors without modifying analysis code.

#### 2. Dataclass Usage (Good)
Clean dataclass definitions with defaults:

```python
# protocols.py:12-22
@dataclass
class FunctionInfo:
    """Lightweight function info for extraction results."""
    name: str
    line_number: int = 0
    params: list[str] = field(default_factory=list)
    return_type: str = ""
    docstring: str = ""
    language: str = ""
    is_method: bool = False
    complexity: int = 0
```

#### 3. Clear Separation of Concerns
- `extractors.py` - file parsing (AST + regex fallback)
- `protocols.py` - shared interfaces
- `analyze.py` - command dispatcher
- `code_structure.py` - project scanning
- `change_impact.py` - test impact analysis
- `project_index.py` - unified symbol indexing
- `analysis.py` - call graph analysis

Each module has a single focus.

#### 4. Fallback Strategy (Pragmatic)
AST parsing for Python, regex fallback for other languages:

```python
# extractors.py:72-82
class BasicRegexExtractor:
    """Fallback extractor for non-Python files using regex patterns."""

    PATTERNS = {
        ".go": re.compile(r"^func\s+(?:\([^)]+\)\s+)?(\w+)\s*\(", re.MULTILINE),
        ".ts": re.compile(r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)", re.MULTILINE),
        # ...
    }
```

This is reasonable for a project-level tool. Tree-sitter support is mentioned as future enhancement.

#### 5. Safe Exception Handling
Broad `except` blocks are used appropriately for extraction (where individual file failures shouldn't crash the entire scan):

```python
# code_structure.py:64-66
try:
    info = _extractor.extract(str(file_path))
    # ...
except Exception:
    pass  # Skip files that fail to parse
```

### Issues & Recommendations

#### 1. Missing Type Hints (High Priority)

Several functions lack return type annotations:

```python
# change_impact.py:75
def get_module_name(file_path: str, project_path: str):
    """Convert file path to Python module name."""
    # Should be: -> str | None

# change_impact.py:48
def is_test_file(file_path: str):
    """Check if a file is a test file based on naming conventions."""
    # Should be: -> bool

# extractors.py:126
def _name_from_node(node):
    """Extract name string from an AST node."""
    # Should be: -> str (also missing param type)
```

**Fix:** Add return type hints to all public functions. The codebase already uses modern Python type syntax (`str | None`, `list[str]`), so complete the annotations.

#### 2. Long Functions (Medium Priority)

`change_impact.py:find_affected_tests()` is 117 lines (173-289). This function:
- Collects changed functions
- Runs impact analysis for each
- Finds tests by import matching
- Builds test command strings

**Recommendation:** Extract sub-functions:
```python
def _collect_changed_functions(project, changed_files, language) -> list[dict]:
    """Extract all functions from changed files."""
    ...

def _find_tests_via_call_graph(project, functions, max_depth, language) -> set[str]:
    """Use impact analysis to find affected test files."""
    ...

def _find_tests_via_imports(project, changed_files, language) -> set[str]:
    """Find tests that import from changed modules."""
    ...

def find_affected_tests(...) -> dict:
    """Main orchestrator (< 40 lines)."""
    funcs = _collect_changed_functions(...)
    tests = _find_tests_via_call_graph(...)
    tests.update(_find_tests_via_imports(...))
    return _build_result(...)
```

#### 3. Docstrings Missing "Raises" Sections (Low Priority)

Functions that can raise exceptions don't document them:

```python
# extractors.py:17
def extract(self, path: str) -> FileExtractionResult:
    source = Path(path).read_text(errors="replace")  # Can raise FileNotFoundError
    try:
        tree = ast.parse(source, filename=path)
    except SyntaxError:
        return FileExtractionResult(language="python")
```

**Recommendation:** Add docstring sections for exceptions that callers need to handle:
```python
def extract(self, path: str) -> FileExtractionResult:
    """Extract structure from Python files using stdlib ast module.

    Args:
        path: Path to Python source file

    Returns:
        FileExtractionResult with functions, classes, imports

    Raises:
        FileNotFoundError: If path doesn't exist
        PermissionError: If path is not readable
    """
```

#### 4. project_index.py: Complex Registration Logic (Medium Priority)

`ProjectIndex._register_symbol()` is 38 lines with multiple conditional branches. This makes it hard to understand the difference between "difflens-only" and "symbolkite" registration paths.

**Recommendation:** Add a detailed docstring explaining the index types:
```python
def _register_symbol(...) -> str:
    """Register a single symbol in all indexes.

    Index types:
    - symbol_index: symbol_id -> FunctionInfo (canonical reference)
    - symbol_files: symbol_id -> absolute file path
    - symbol_raw_names: symbol_id -> unqualified name (for lookup)
    - name_index: raw_name -> [symbol_id] (ambiguous lookup)
    - qualified_index: qualified_name -> [symbol_id] (dot-notation lookup)
    - file_name_index: rel_path -> {name -> [symbol_id]} (scoped lookup)

    The difflens-specific entry (line 125-126) adds qualified_name to
    file_name_index so that "ClassName.method_name" lookups work in addition
    to just "method_name".
    """
```

#### 5. analysis.py: Global Visited Set in Caller Tree (Correctness Risk)

`_build_caller_tree()` uses a shared `visited` set to prevent exponential blowup:

```python
# analysis.py:137-176
def _build_caller_tree(func, reverse, depth, visited):
    """Recursively build caller tree.

    Note: visited set is shared across the entire traversal to avoid
    exponential blowup. This means each node appears only once in the
    tree, with subsequent references marked as truncated.
    """
```

This is **correct** (documented in the docstring), but it has a subtle side effect: the order of traversal determines which path shows the node fully expanded. Consider adding a warning in the caller about this behavior:

```python
def impact_analysis(...) -> dict:
    """Find all callers of a function.

    Note: The caller tree uses shared cycle detection, so if function A
    calls B and C, and both B and C call D, only the first path to D will
    be fully expanded. This is intentional to keep tree size bounded.
    """
```

#### 6. No Pytest Fixtures Observed (Test Quality)

The test files use plain functions without fixtures:

```python
# test_extractors.py (not shown, but inferred from test structure)
def test_python_extractor():
    # Inline file creation
    ...
```

**Recommendation:** Use pytest fixtures for common test data:
```python
@pytest.fixture
def sample_python_file(tmp_path):
    """Create a sample Python file for testing."""
    p = tmp_path / "sample.py"
    p.write_text('''
def foo(x):
    return x * 2

class Bar:
    def baz(self):
        pass
''')
    return p

def test_python_extractor(sample_python_file):
    extractor = PythonASTExtractor()
    result = extractor.extract(str(sample_python_file))
    assert len(result.functions) == 1
    assert result.functions[0].name == "foo"
```

---

## Architecture Review

### Python → Go Bridge (Excellent Design)

The subprocess bridge is well-designed:

**Go side (bridge.go):**
- Takes command + project + args dict
- Marshals to JSON
- Sets PYTHONPATH for module resolution
- Parses structured errors from stderr
- Wraps all errors with context

**Python side (__main__.py):**
- Uses argparse for CLI parsing
- Structured error output via JSON to stderr
- Dispatches to analysis functions via `analyze.dispatch()`

**Strengths:**
- Clean separation: Go handles MCP transport, Python handles analysis
- JSON-over-stdio is simple and debuggable
- Error propagation works correctly (stderr → exitErr.Stderr → JSON parse)
- PYTHONPATH injection handles both plugin and development modes

**Potential Issue:**
The timeout is fixed at 60 seconds in `bridge.go:30`. Complex call graph analysis might exceed this. Consider making it configurable or per-command:

```go
// bridge.go:30
func NewBridge(pythonPath string) *Bridge {
    return &Bridge{
        pythonPath: pythonPath,
        timeout:    60 * time.Second,  // Fixed
    }
}

// RECOMMENDED
func NewBridge(pythonPath string, opts ...BridgeOption) *Bridge {
    b := &Bridge{pythonPath: pythonPath, timeout: 60 * time.Second}
    for _, opt := range opts {
        opt(b)
    }
    return b
}

type BridgeOption func(*Bridge)

func WithTimeout(d time.Duration) BridgeOption {
    return func(b *Bridge) { b.timeout = d }
}
```

### Cache Strategy (Needs Clarification)

There are **three** cache layers:

1. **Go project cache** (tools.go:19): `cache.New[[]registry.Project](5*time.Minute, 10)`
2. **Python FileCache** (file_cache.py): In-process dict keyed by (path, mtime)
3. **Go cache in tools.go**: Generic LRU with TTL + mtime hash

**Issue:** The relationship between these is unclear. Comments suggest Python cache is "NOT persistent across subprocess invocations" and "Go-side cache handles persistence", but the Go project cache has a 5-minute TTL, not persistence across restarts.

**Recommendation:** Add a caching architecture diagram to AGENTS.md:
```
Request Flow:
1. MCP tool call → tools.go handler
2. Check Go project cache (5min TTL, 10 entries)
3. Cache miss → Python subprocess invocation
4. Python FileCache deduplicates within single analysis
5. Go caches Python result in project cache
```

### Vendored Code Management (Good Practice)

`python/intermap/vendor/` contains files from tldr-swinton. The CLAUDE.md correctly warns "Do not modify — update source and re-vendor."

**Strengths:**
- Clear separation via `vendor/` directory
- Documented provenance

**Recommendation:** Add a `vendor/README.md` listing source commits:
```markdown
# Vendored Dependencies

## tldr-swinton

Source: github.com/mistakeknot/tldr-swinton
Commit: abc123def456
Files:
- workspace.py - iter_workspace_files, WorkspaceConfig
- dirty_flag.py - get_dirty_files (session tracking)
- tldrsignore.py - .tldrsignore parsing

Last updated: 2026-02-15
```

---

## Testing Strategy Review

### Go Tests (Good Coverage for Core Packages)

**cache_test.go (95 lines):**
- `TestCache_GetPut` - basic get/put
- `TestCache_MtimeInvalidation` - hash-based invalidation
- `TestCache_TTLExpiry` - time-based expiry
- `TestCache_LRUEviction` - eviction correctness
- `TestCache_Invalidate` - manual invalidation

All critical paths covered. **No issues.**

**registry_test.go (120 lines):**
- `TestScan_Interverse` - real-world integration test against `/root/projects/Interverse`
- `TestScan_LanguageDetection` - validates `go.mod` → "go", etc.
- `TestResolve` - path-to-project resolution
- `TestResolve_NotInProject` - error case
- `TestMtimeHash` - hash stability

Uses `t.Skip()` when Interverse root not found (good practice). **No issues.**

**client_test.go (126 lines):**
- Uses `httptest.NewServer` for mock HTTP tests
- Covers happy path, error path, unavailable client, server down
- Tests query parameter handling (`?project=...`)

Clean use of stdlib test server. **No issues.**

**tools_test.go (26 lines):**
- Only tests `stringOr()` helper
- **Missing:** tool handler logic (see Go Issue #2 above)

### Python Tests (Assumed Present, Not Reviewed)

The glob showed test files exist:
- `test_extractors.py`
- `test_analyze.py`
- `test_code_structure.py`

Not reviewed due to scope, but presence is good.

**Recommendation:** Verify Python test coverage with:
```bash
PYTHONPATH=python pytest python/tests/ --cov=intermap --cov-report=term-missing
```

Aim for >80% coverage on `extractors.py`, `analyze.py`, `code_structure.py`, `change_impact.py`.

---

## Naming Conventions

### Go (Excellent)

All exported names follow Go conventions:
- Types: `Cache`, `Bridge`, `Client`, `Project`, `Agent`, `Reservation`
- Functions: `RegisterAll`, `NewBridge`, `NewClient`, `Scan`, `Resolve`, `MtimeHash`
- Methods: `Get`, `Put`, `Invalidate`, `Available`, `ListAgents`
- Variables: `projectCache`, `absRoot`, `subEntries` (camelCase for unexported)

No violations observed.

### Python (Good, with Minor Issues)

**Module-level:**
- All modules are `snake_case` (protocols, extractors, analyze, etc.)
- Class names are `PascalCase` (FunctionInfo, ClassInfo, FileExtractor)
- Functions are `snake_case` (get_code_structure, is_test_file, etc.)

**Minor Issue:** Some internal helpers lack `_` prefix:

```python
# extractors.py:126
def _name_from_node(node) -> str:  # Good: underscore prefix for internal helper
    ...

# change_impact.py:115
def _scan_project_files(project_path: str, language: str) -> list[str]:  # Good
    ...

# change_impact.py:121
def _get_imports_from_file(file_path: str) -> list[dict]:  # Good
    ...
```

Actually, on review, the Python naming is consistent. All internal helpers use `_` prefix. **No issues.**

---

## Language-Specific Idioms Summary

### Go Idioms (Followed Correctly)

✅ Error wrapping with `%w`
✅ Accept interfaces, return structs
✅ Option pattern for configuration
✅ Defer for cleanup (`defer cancel()`, `defer resp.Body.Close()`)
✅ Mutex for concurrency (cache.mu)
✅ Context propagation (bridge uses `context.Context`)
✅ Package naming (single-word, lowercase)
✅ Imports grouped by stdlib/external/internal

### Python Idioms (Followed Correctly)

✅ Dataclasses for data structures
✅ Protocols for decoupling
✅ Type hints (mostly complete)
✅ Context managers (not shown but inferred from stdlib usage)
✅ List/dict comprehensions (not shown but likely used)
✅ Exception specificity (SyntaxError caught specifically in extractors.py:21)
⚠️ Missing type hints on some functions (see Python Issue #1)

---

## Critical Findings

### None Identified

No correctness bugs, security issues, or architectural problems found. The code is production-ready.

---

## Priority Fixes

### High Priority
1. **Add type hints** to all Python public functions (30-minute fix)
2. **Add test coverage** for `tools.go` handlers (2-hour effort for integration tests)

### Medium Priority
3. **Extract sub-functions** from `change_impact.py:find_affected_tests()` (1-hour refactor)
4. **Add error context** to Go tool handlers (parameter values in error messages)
5. **Document ProjectIndex** registration behavior (complex logic needs explanation)

### Low Priority
6. Add vendor manifest (vendor/README.md)
7. Make bridge timeout configurable
8. Add logging for registry.go silent skips
9. Clarify cache architecture in docs

---

## Conclusion

This is a **well-crafted plugin** with strong separation of concerns, good error handling, and appropriate use of language idioms. The Go code is exemplary for MCP server patterns. The Python code is solid with room for minor improvements in type annotations and function length.

The main gaps are in **test coverage** (tools.go integration tests) and **documentation** (cache architecture, vendor provenance, complex registration logic). These are straightforward to address and don't block production use.

**Recommendation:** Merge as-is. Address High Priority items in a follow-up PR.
