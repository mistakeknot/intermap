// Package python provides a subprocess bridge to the intermap Python analysis module.
//
// The Go MCP server delegates analysis work (call graphs, impact analysis,
// dead code detection, etc.) to Python via JSON-over-stdio:
//
//	Go → python3 -m intermap --command X --project P --args '{...}' → JSON stdout
package python

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"time"
)

// Bridge calls the Python analysis module via subprocess.
type Bridge struct {
	pythonPath string // path to python/ directory containing intermap package
	timeout    time.Duration
}

// NewBridge creates a Bridge. pythonPath should be the directory containing
// the intermap Python package (e.g., <plugin-root>/python).
func NewBridge(pythonPath string) *Bridge {
	return &Bridge{
		pythonPath: pythonPath,
		timeout:    60 * time.Second,
	}
}

// Run executes a Python analysis command and returns the parsed JSON result.
func (b *Bridge) Run(ctx context.Context, command, project string, args map[string]any) (map[string]any, error) {
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

	// Set PYTHONPATH so Python can find the intermap package
	cmd.Env = append(os.Environ(), "PYTHONPATH="+b.pythonPath)

	stdout, err := cmd.Output()
	if err != nil {
		if exitErr, ok := err.(*exec.ExitError); ok {
			// Try to parse structured error from stderr
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
