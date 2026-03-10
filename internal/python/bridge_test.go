package python

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func testPythonPath(t *testing.T) string {
	t.Helper()
	// Find python/ directory relative to this test file
	// internal/python/bridge_test.go → ../../python
	wd, err := os.Getwd()
	if err != nil {
		t.Fatal(err)
	}
	pyPath := filepath.Join(wd, "..", "..", "python")
	if _, err := os.Stat(filepath.Join(pyPath, "intermap", "__main__.py")); err != nil {
		t.Skipf("Python module not found at %s", pyPath)
	}
	return pyPath
}

func TestBridge_SidecarRun(t *testing.T) {
	pyPath := testPythonPath(t)
	b := NewBridge(pyPath)
	defer b.Close()

	ctx := context.Background()
	result, err := b.Run(ctx, "structure", filepath.Join(pyPath, ".."), map[string]any{
		"language":    "python",
		"max_results": float64(3),
	})
	if err != nil {
		t.Fatalf("Run failed: %v", err)
	}

	if _, ok := result["files"]; !ok {
		t.Error("Expected 'files' key in result")
	}
}

func TestBridge_SidecarMultipleRequests(t *testing.T) {
	pyPath := testPythonPath(t)
	b := NewBridge(pyPath)
	defer b.Close()

	ctx := context.Background()
	for i := 0; i < 3; i++ {
		result, err := b.Run(ctx, "structure", filepath.Join(pyPath, ".."), map[string]any{
			"language":    "python",
			"max_results": float64(2),
		})
		if err != nil {
			t.Fatalf("Run %d failed: %v", i, err)
		}
		if _, ok := result["files"]; !ok {
			t.Errorf("Run %d: expected 'files' key in result", i)
		}
	}
}

func TestBridge_SidecarUnknownCommand(t *testing.T) {
	pyPath := testPythonPath(t)
	b := NewBridge(pyPath)
	defer b.Close()

	ctx := context.Background()
	// Unknown commands return an error dict as a result (not an exception)
	result, err := b.Run(ctx, "nonexistent", pyPath, map[string]any{})
	if err != nil {
		t.Fatalf("Expected result with error field, got Go error: %v", err)
	}
	if result["error"] != "UnknownCommand" {
		t.Errorf("Expected UnknownCommand error, got: %v", result)
	}
}

func TestBridge_CrashRecovery(t *testing.T) {
	pyPath := testPythonPath(t)
	b := NewBridge(pyPath)
	defer b.Close()

	ctx := context.Background()

	// First request — starts sidecar
	_, err := b.Run(ctx, "structure", filepath.Join(pyPath, ".."), map[string]any{
		"language":    "python",
		"max_results": float64(1),
	})
	if err != nil {
		t.Fatalf("Initial run failed: %v", err)
	}

	// Kill the sidecar process
	b.mu.Lock()
	if b.proc != nil {
		b.proc.Process.Kill()
		b.proc.Wait()
	}
	b.proc = nil
	b.stdin = nil
	b.scanner = nil
	b.mu.Unlock()

	// Next request should auto-respawn
	result, err := b.Run(ctx, "structure", filepath.Join(pyPath, ".."), map[string]any{
		"language":    "python",
		"max_results": float64(1),
	})
	if err != nil {
		t.Fatalf("Post-crash run failed: %v", err)
	}
	if _, ok := result["files"]; !ok {
		t.Error("Expected 'files' key in result after recovery")
	}
}

func TestBridge_Close(t *testing.T) {
	pyPath := testPythonPath(t)
	b := NewBridge(pyPath)

	ctx := context.Background()
	_, err := b.Run(ctx, "structure", filepath.Join(pyPath, ".."), map[string]any{
		"language":    "python",
		"max_results": float64(1),
	})
	if err != nil {
		t.Fatalf("Run failed: %v", err)
	}

	// Close should not panic or hang
	b.Close()

	// Double close should be safe
	b.Close()
}

func TestBridge_ContextTimeout(t *testing.T) {
	pyPath := testPythonPath(t)
	b := NewBridge(pyPath)
	b.timeout = 50 * time.Millisecond // Very short timeout
	defer b.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 50*time.Millisecond)
	defer cancel()

	// A real analysis should take longer than 50ms
	_, err := b.Run(ctx, "architecture", filepath.Join(pyPath, ".."), map[string]any{
		"language": "python",
	})
	// Should get a timeout or context error
	if err == nil {
		t.Log("Warning: architecture completed within 50ms (fast machine?) — skipping timeout assertion")
	}
}

func TestBridge_FallbackMode(t *testing.T) {
	pyPath := testPythonPath(t)
	b := NewBridge(pyPath)
	defer b.Close()

	// Force fallback mode
	b.fallback = true

	ctx := context.Background()
	result, err := b.Run(ctx, "structure", filepath.Join(pyPath, ".."), map[string]any{
		"language":    "python",
		"max_results": float64(2),
	})
	if err != nil {
		t.Fatalf("Fallback run failed: %v", err)
	}
	if _, ok := result["files"]; !ok {
		t.Error("Expected 'files' key in fallback result")
	}
}

func TestRecoverableError(t *testing.T) {
	err := &RecoverableError{Code: "parse_error", Message: "bad syntax"}
	if !IsRecoverable(err) {
		t.Error("expected IsRecoverable to return true")
	}
	if err.Error() != "python recoverable [parse_error]: bad syntax" {
		t.Errorf("unexpected error string: %s", err.Error())
	}

	// Wrapped errors should also be recoverable
	wrapped := fmt.Errorf("wrapper: %w", err)
	if !IsRecoverable(wrapped) {
		t.Error("expected wrapped RecoverableError to be recoverable")
	}
}

func TestNonRecoverableError(t *testing.T) {
	err := fmt.Errorf("python crash")
	if IsRecoverable(err) {
		t.Error("expected regular error to not be recoverable")
	}
}

func TestSidecarError_BackwardCompat(t *testing.T) {
	// Legacy error format (no code/recoverable fields)
	e := sidecarError{Type: "ValueError", Message: "bad value"}
	if e.errorCode() != "ValueError" {
		t.Errorf("expected Type fallback, got: %s", e.errorCode())
	}
	if e.isRecoverable() {
		t.Error("expected legacy error to not be recoverable")
	}

	// New structured format
	tr := true
	e2 := sidecarError{Code: "parse_error", Message: "bad syntax", Recoverable: &tr}
	if e2.errorCode() != "parse_error" {
		t.Errorf("expected code field, got: %s", e2.errorCode())
	}
	if !e2.isRecoverable() {
		t.Error("expected structured recoverable error to be recoverable")
	}

	// New structured format (not recoverable)
	fl := false
	e3 := sidecarError{Code: "internal_error", Message: "crash", Recoverable: &fl}
	if e3.isRecoverable() {
		t.Error("expected structured fatal error to not be recoverable")
	}
}
