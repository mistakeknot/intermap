package registry

import (
	"os"
	"path/filepath"
	"testing"
)

func TestScan_Interverse(t *testing.T) {
	// Test against actual Interverse structure
	root := findInterverseRoot(t)
	projects, err := Scan(root)
	if err != nil {
		t.Fatalf("Scan: %v", err)
	}
	if len(projects) == 0 {
		t.Fatal("expected at least one project")
	}

	// Verify known projects exist
	found := make(map[string]bool)
	for _, p := range projects {
		found[p.Name] = true
		if p.Path == "" {
			t.Errorf("project %q has empty path", p.Name)
		}
		if p.Language == "" {
			t.Errorf("project %q has empty language", p.Name)
		}
	}

	// Interverse should have interlock, intermute, clavain, etc.
	for _, name := range []string{"interlock", "clavain"} {
		if !found[name] {
			t.Errorf("expected project %q in scan results", name)
		}
	}
}

func TestScan_LanguageDetection(t *testing.T) {
	root := findInterverseRoot(t)
	projects, err := Scan(root)
	if err != nil {
		t.Fatalf("Scan: %v", err)
	}

	for _, p := range projects {
		switch p.Name {
		case "interlock":
			if p.Language != "go" {
				t.Errorf("interlock: expected go, got %s", p.Language)
			}
		case "intermute":
			if p.Language != "go" {
				t.Errorf("intermute: expected go, got %s", p.Language)
			}
		}
	}
}

func TestResolve(t *testing.T) {
	root := findInterverseRoot(t)
	interlockPath := filepath.Join(root, "plugins", "interlock")
	if _, err := os.Stat(interlockPath); err != nil {
		t.Skip("interlock directory not found")
	}

	p, err := Resolve(filepath.Join(interlockPath, "internal", "tools", "tools.go"))
	if err != nil {
		t.Fatalf("Resolve: %v", err)
	}
	if p.Name != "interlock" {
		t.Errorf("expected interlock, got %s", p.Name)
	}
	if p.Language != "go" {
		t.Errorf("expected go, got %s", p.Language)
	}
}

func TestResolve_NotInProject(t *testing.T) {
	_, err := Resolve("/tmp")
	if err == nil {
		t.Error("expected error for path not in any project")
	}
}

func TestMtimeHash(t *testing.T) {
	root := findInterverseRoot(t)
	interlockPath := filepath.Join(root, "plugins", "interlock")
	if _, err := os.Stat(interlockPath); err != nil {
		t.Skip("interlock directory not found")
	}

	hash1, err := MtimeHash(interlockPath)
	if err != nil {
		t.Fatalf("MtimeHash: %v", err)
	}
	if hash1 == "" {
		t.Error("expected non-empty hash")
	}

	// Same path should give same hash
	hash2, err := MtimeHash(interlockPath)
	if err != nil {
		t.Fatalf("MtimeHash second call: %v", err)
	}
	if hash1 != hash2 {
		t.Error("expected identical hashes for unchanged directory")
	}
}

func findInterverseRoot(t *testing.T) string {
	t.Helper()
	root := "/root/projects/Interverse"
	if _, err := os.Stat(root); err != nil {
		t.Skipf("Interverse root not found at %s", root)
	}
	return root
}
