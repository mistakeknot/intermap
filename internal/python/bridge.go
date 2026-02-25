// Package python provides a subprocess bridge to the intermap Python analysis module.
//
// The Go MCP server delegates analysis work (call graphs, impact analysis,
// dead code detection, etc.) to Python via a persistent sidecar subprocess.
//
// The sidecar runs `python3 -u -m intermap --sidecar` and communicates via
// newline-delimited JSON on stdin/stdout. If the sidecar crashes, it is
// automatically respawned (up to 3 times in 10 seconds before falling back
// to single-shot subprocess mode).
package python

import (
	"bufio"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"os/exec"
	"path/filepath"
	"sync"
	"sync/atomic"
	"time"
)

// Bridge calls the Python analysis module via a persistent sidecar subprocess.
type Bridge struct {
	pythonPath string
	timeout    time.Duration

	mu      sync.Mutex
	proc    *exec.Cmd
	stdin   io.WriteCloser
	scanner *bufio.Scanner
	nextID  atomic.Int64

	// Crash tracking for fallback
	crashTimes []time.Time
	fallback   bool // true = use single-shot mode (sidecar too unstable)
}

// NewBridge creates a Bridge. pythonPath should be the directory containing
// the intermap Python package (e.g., <plugin-root>/python).
func NewBridge(pythonPath string) *Bridge {
	return &Bridge{
		pythonPath: pythonPath,
		timeout:    60 * time.Second,
	}
}

// sidecarRequest is the JSON request sent to the Python sidecar.
type sidecarRequest struct {
	ID      int64          `json:"id"`
	Command string         `json:"command"`
	Project string         `json:"project"`
	Args    map[string]any `json:"args"`
}

// sidecarResponse is the JSON response from the Python sidecar.
type sidecarResponse struct {
	ID     int64          `json:"id"`
	Result map[string]any `json:"result,omitempty"`
	Error  *sidecarError  `json:"error,omitempty"`
}

type sidecarError struct {
	Type    string `json:"type"`
	Message string `json:"message"`
}

// Run executes a Python analysis command and returns the parsed JSON result.
func (b *Bridge) Run(ctx context.Context, command, project string, args map[string]any) (map[string]any, error) {
	if b.fallback {
		return b.runSingleShot(ctx, command, project, args)
	}

	b.mu.Lock()
	defer b.mu.Unlock()

	result, err := b.runSidecar(ctx, command, project, args)
	if err != nil {
		// Sidecar failed — try to respawn once
		b.stopLocked()
		b.recordCrash()

		if b.fallback {
			// Too many crashes, use single-shot
			b.mu.Unlock()
			r, e := b.runSingleShot(ctx, command, project, args)
			b.mu.Lock()
			return r, e
		}

		// Retry with fresh sidecar
		result, err = b.runSidecar(ctx, command, project, args)
		if err != nil {
			b.stopLocked()
			return nil, fmt.Errorf("python sidecar %s (retry failed): %w", command, err)
		}
	}

	return result, nil
}

func (b *Bridge) runSidecar(ctx context.Context, command, project string, args map[string]any) (map[string]any, error) {
	if err := b.ensureStarted(); err != nil {
		return nil, err
	}

	reqID := b.nextID.Add(1)
	req := sidecarRequest{
		ID:      reqID,
		Command: command,
		Project: project,
		Args:    args,
	}

	reqBytes, err := json.Marshal(req)
	if err != nil {
		return nil, fmt.Errorf("marshal request: %w", err)
	}

	// Write request
	if _, err := b.stdin.Write(append(reqBytes, '\n')); err != nil {
		return nil, fmt.Errorf("write to sidecar: %w", err)
	}

	// Read response with timeout.
	// Snapshot scanner into a local to avoid racing with stopLocked().
	scanner := b.scanner
	type scanResult struct {
		line string
		ok   bool
	}
	ch := make(chan scanResult, 1)
	go func() {
		ok := scanner.Scan()
		ch <- scanResult{line: scanner.Text(), ok: ok}
	}()

	deadline := b.timeout
	if d, ok := ctx.Deadline(); ok {
		if remaining := time.Until(d); remaining < deadline {
			deadline = remaining
		}
	}

	select {
	case sr := <-ch:
		if !sr.ok {
			return nil, fmt.Errorf("sidecar EOF (process crashed)")
		}
		var resp sidecarResponse
		if err := json.Unmarshal([]byte(sr.line), &resp); err != nil {
			return nil, fmt.Errorf("parse sidecar response: %w", err)
		}
		if resp.Error != nil {
			return nil, fmt.Errorf("python %s: [%s] %s", command, resp.Error.Type, resp.Error.Message)
		}
		return resp.Result, nil

	case <-time.After(deadline):
		return nil, fmt.Errorf("python %s: timeout after %s", command, deadline)

	case <-ctx.Done():
		return nil, ctx.Err()
	}
}

// ensureStarted starts the sidecar if not already running.
func (b *Bridge) ensureStarted() error {
	if b.proc != nil {
		return nil
	}

	cmd := exec.Command("python3", "-u", "-m", "intermap", "--sidecar")
	cmd.Env = append(os.Environ(), "PYTHONPATH="+b.pythonPath)

	stdin, err := cmd.StdinPipe()
	if err != nil {
		return fmt.Errorf("create stdin pipe: %w", err)
	}

	stdout, err := cmd.StdoutPipe()
	if err != nil {
		stdin.Close()
		return fmt.Errorf("create stdout pipe: %w", err)
	}

	cmd.Stderr = os.Stderr // Forward Python errors to Go's stderr

	if err := cmd.Start(); err != nil {
		stdin.Close()
		return fmt.Errorf("start sidecar: %w", err)
	}

	scanner := bufio.NewScanner(stdout)
	scanner.Buffer(make([]byte, 0, 4*1024*1024), 4*1024*1024) // 4MB buffer for large results

	// Wait for ready signal
	if !scanner.Scan() {
		cmd.Process.Kill()
		cmd.Wait()
		return fmt.Errorf("sidecar failed to send ready signal")
	}

	var ready map[string]any
	if err := json.Unmarshal([]byte(scanner.Text()), &ready); err != nil || ready["status"] != "ready" {
		cmd.Process.Kill()
		cmd.Wait()
		return fmt.Errorf("sidecar ready signal invalid: %s", scanner.Text())
	}

	b.proc = cmd
	b.stdin = stdin
	b.scanner = scanner
	return nil
}

// stopLocked stops the sidecar subprocess. Caller must hold b.mu.
func (b *Bridge) stopLocked() {
	if b.proc == nil {
		return
	}
	b.stdin.Close()
	// Give it a moment to exit cleanly
	done := make(chan struct{})
	go func() {
		b.proc.Wait()
		close(done)
	}()
	select {
	case <-done:
	case <-time.After(2 * time.Second):
		b.proc.Process.Kill()
		<-done
	}
	b.proc = nil
	b.stdin = nil
	b.scanner = nil
}

// recordCrash tracks crash times and switches to fallback if too many.
func (b *Bridge) recordCrash() {
	now := time.Now()
	b.crashTimes = append(b.crashTimes, now)

	// Keep only crashes in the last 10 seconds
	cutoff := now.Add(-10 * time.Second)
	filtered := b.crashTimes[:0]
	for _, t := range b.crashTimes {
		if t.After(cutoff) {
			filtered = append(filtered, t)
		}
	}
	b.crashTimes = filtered

	if len(b.crashTimes) >= 3 {
		b.fallback = true
		fmt.Fprintf(os.Stderr, "intermap: sidecar crashed 3 times in 10s, falling back to single-shot mode\n")
	}
}

// runSingleShot is the original per-call subprocess mode (fallback).
func (b *Bridge) runSingleShot(ctx context.Context, command, project string, args map[string]any) (map[string]any, error) {
	argsJSON, err := json.Marshal(args)
	if err != nil {
		return nil, fmt.Errorf("marshal args: %w", err)
	}

	ctx, cancel := context.WithTimeout(ctx, b.timeout)
	defer cancel()

	cmd := exec.CommandContext(ctx, "python3", "-m", "intermap",
		"--command", command,
		"--project", project,
		"--args", string(argsJSON),
	)
	cmd.Env = append(os.Environ(), "PYTHONPATH="+b.pythonPath)

	stdout, err := cmd.Output()
	if err != nil {
		if exitErr, ok := err.(*exec.ExitError); ok {
			var pyErr map[string]any
			if json.Unmarshal(exitErr.Stderr, &pyErr) == nil {
				return nil, fmt.Errorf("python %s: %v", command, pyErr["message"])
			}
			return nil, fmt.Errorf("python %s: %s", command, string(exitErr.Stderr))
		}
		return nil, fmt.Errorf("python %s: %w", command, err)
	}

	var result map[string]any
	if err := json.Unmarshal(stdout, &result); err != nil {
		return nil, fmt.Errorf("parse python output: %w", err)
	}

	return result, nil
}

// Close stops the sidecar subprocess. Safe to call multiple times.
func (b *Bridge) Close() {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.stopLocked()
}

// DefaultPythonPath returns the python/ directory relative to the plugin root.
// It checks CLAUDE_PLUGIN_ROOT first, then falls back to the binary's directory.
func DefaultPythonPath() string {
	if root := os.Getenv("CLAUDE_PLUGIN_ROOT"); root != "" {
		return filepath.Join(root, "python")
	}

	// Fallback: look for python/ relative to the executable
	exe, err := os.Executable()
	if err != nil {
		return "python"
	}
	return filepath.Join(filepath.Dir(filepath.Dir(exe)), "python")
}
