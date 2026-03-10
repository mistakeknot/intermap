package tools

import (
	"context"
	"os"
	"path/filepath"
	"testing"

	pybridge "github.com/mistakeknot/intermap/internal/python"
)

func TestStringOr(t *testing.T) {
	if got := stringOr("hello", "default"); got != "hello" {
		t.Errorf("stringOr: expected hello, got %s", got)
	}
	if got := stringOr("", "default"); got != "default" {
		t.Errorf("stringOr: expected default, got %s", got)
	}
	if got := stringOr(nil, "default"); got != "default" {
		t.Errorf("stringOr: expected default, got %s", got)
	}
}

func TestStringOr_NonStringTypes(t *testing.T) {
	if got := stringOr(42, "default"); got != "default" {
		t.Errorf("stringOr(int): expected default, got %s", got)
	}
	if got := stringOr(true, "default"); got != "default" {
		t.Errorf("stringOr(bool): expected default, got %s", got)
	}
}

func TestGitHeadSHA_ReturnsNonEmpty(t *testing.T) {
	sha := gitHeadSHA(".")
	if sha == "" {
		t.Skip("not in a git repo")
	}
	if len(sha) != 40 {
		t.Errorf("expected 40-char SHA, got %d chars: %s", len(sha), sha)
	}
}

func TestGitHeadSHA_InvalidDir(t *testing.T) {
	sha := gitHeadSHA("/nonexistent/path")
	if sha != "" {
		t.Errorf("expected empty for invalid dir, got: %s", sha)
	}
}

// testPythonPath returns the python/ directory for benchmarks, skipping if unavailable.
func testPythonPath(t testing.TB) string {
	t.Helper()
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

// Benchmarks measure wall-clock time for Python-bridge tools.
// Run with: go test ./internal/tools/ -bench=. -benchtime=3x -run=^$
//
// Expected bounds (on a 20-file project):
//
//	BenchmarkDetectPatterns_Cold: < 5s
//	BenchmarkDetectPatterns_Warm: < 50ms
//	BenchmarkCrossProjectDeps_Cold: < 5s
//	BenchmarkCrossProjectDeps_Warm: < 50ms
func BenchmarkDetectPatterns_Cold(b *testing.B) {
	pyPath := testPythonPath(b)
	bridge := pybridge.NewBridge(pyPath)
	defer bridge.Close()
	ctx := context.Background()

	for i := 0; i < b.N; i++ {
		detectPatternsCache.Invalidate(".")
		_, err := bridge.Run(ctx, "detect_patterns", ".", map[string]any{"language": "auto"})
		if err != nil {
			b.Fatal(err)
		}
	}
}

func BenchmarkDetectPatterns_Warm(b *testing.B) {
	pyPath := testPythonPath(b)
	bridge := pybridge.NewBridge(pyPath)
	defer bridge.Close()
	ctx := context.Background()

	// Prime the cache
	result, err := bridge.Run(ctx, "detect_patterns", ".", map[string]any{"language": "auto"})
	if err != nil {
		b.Fatal(err)
	}
	sha := gitHeadSHA(".")
	detectPatternsCache.Put(".", sha, result)

	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		if _, ok := detectPatternsCache.Get(".", sha); !ok {
			b.Fatal("cache miss on warm benchmark")
		}
	}
}

func BenchmarkCrossProjectDeps_Cold(b *testing.B) {
	pyPath := testPythonPath(b)
	bridge := pybridge.NewBridge(pyPath)
	defer bridge.Close()
	ctx := context.Background()

	root := "../../../.."
	for i := 0; i < b.N; i++ {
		crossProjectDepsCache.Invalidate(root)
		_, err := bridge.Run(ctx, "cross_project_deps", root, map[string]any{})
		if err != nil {
			b.Fatal(err)
		}
	}
}

func BenchmarkCrossProjectDeps_Warm(b *testing.B) {
	pyPath := testPythonPath(b)
	bridge := pybridge.NewBridge(pyPath)
	defer bridge.Close()
	ctx := context.Background()

	root := "../../../.."
	result, err := bridge.Run(ctx, "cross_project_deps", root, map[string]any{})
	if err != nil {
		b.Fatal(err)
	}
	sha := gitHeadSHA(root)
	crossProjectDepsCache.Put(root, sha, result)

	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		if _, ok := crossProjectDepsCache.Get(root, sha); !ok {
			b.Fatal("cache miss on warm benchmark")
		}
	}
}
