package registry

import (
	"crypto/sha256"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"
)

// Project represents a discovered project in the workspace.
type Project struct {
	Name      string `json:"name"`
	Path      string `json:"path"`
	Language  string `json:"language"`
	Group     string `json:"group"`
	GitBranch string `json:"git_branch"`
}

// Scan walks root looking for directories containing .git, returning a Project for each.
func Scan(root string) ([]Project, error) {
	absRoot, err := filepath.Abs(root)
	if err != nil {
		return nil, fmt.Errorf("abs root: %w", err)
	}

	var projects []Project
	entries, err := os.ReadDir(absRoot)
	if err != nil {
		return nil, fmt.Errorf("read root: %w", err)
	}

	for _, group := range entries {
		if !group.IsDir() || strings.HasPrefix(group.Name(), ".") {
			continue
		}
		groupPath := filepath.Join(absRoot, group.Name())
		subEntries, err := os.ReadDir(groupPath)
		if err != nil {
			continue
		}
		for _, sub := range subEntries {
			if !sub.IsDir() || strings.HasPrefix(sub.Name(), ".") {
				continue
			}
			projectPath := filepath.Join(groupPath, sub.Name())
			gitDir := filepath.Join(projectPath, ".git")
			if _, err := os.Stat(gitDir); err != nil {
				continue
			}
			p := Project{
				Name:      sub.Name(),
				Path:      projectPath,
				Language:  detectLanguage(projectPath),
				Group:     group.Name(),
				GitBranch: readGitBranch(gitDir),
			}
			projects = append(projects, p)
		}
	}

	// Also check if root itself is a project
	if _, err := os.Stat(filepath.Join(absRoot, ".git")); err == nil {
		projects = append([]Project{{
			Name:      filepath.Base(absRoot),
			Path:      absRoot,
			Language:  detectLanguage(absRoot),
			Group:     "",
			GitBranch: readGitBranch(filepath.Join(absRoot, ".git")),
		}}, projects...)
	}

	sort.Slice(projects, func(i, j int) bool {
		if projects[i].Group != projects[j].Group {
			return projects[i].Group < projects[j].Group
		}
		return projects[i].Name < projects[j].Name
	})

	return projects, nil
}

// Resolve walks up from path to find the nearest directory containing .git.
func Resolve(path string) (*Project, error) {
	absPath, err := filepath.Abs(path)
	if err != nil {
		return nil, fmt.Errorf("abs path: %w", err)
	}

	current := absPath
	for {
		gitDir := filepath.Join(current, ".git")
		if _, err := os.Stat(gitDir); err == nil {
			p := &Project{
				Name:      filepath.Base(current),
				Path:      current,
				Language:  detectLanguage(current),
				GitBranch: readGitBranch(gitDir),
			}
			// Try to detect group from parent dir name
			parent := filepath.Dir(current)
			if parent != current {
				p.Group = filepath.Base(parent)
			}
			return p, nil
		}
		parent := filepath.Dir(current)
		if parent == current {
			break
		}
		current = parent
	}
	return nil, fmt.Errorf("path %q is not within any git project", path)
}

func detectLanguage(projectPath string) string {
	markers := []struct {
		file string
		lang string
	}{
		{"go.mod", "go"},
		{"pyproject.toml", "python"},
		{"setup.py", "python"},
		{"package.json", "typescript"},
		{"Cargo.toml", "rust"},
		{"build.gradle", "java"},
		{"pom.xml", "java"},
	}
	for _, m := range markers {
		if _, err := os.Stat(filepath.Join(projectPath, m.file)); err == nil {
			return m.lang
		}
	}
	return "unknown"
}

func readGitBranch(gitDir string) string {
	data, err := os.ReadFile(filepath.Join(gitDir, "HEAD"))
	if err != nil {
		return ""
	}
	head := strings.TrimSpace(string(data))
	if strings.HasPrefix(head, "ref: refs/heads/") {
		return strings.TrimPrefix(head, "ref: refs/heads/")
	}
	// Detached HEAD — return short hash
	if len(head) >= 8 {
		return head[:8]
	}
	return head
}

// MtimeHash computes a hash of all source file mtimes in a project for cache invalidation.
func MtimeHash(projectPath string) (string, error) {
	absPath, err := filepath.Abs(projectPath)
	if err != nil {
		return "", err
	}

	var entries []string
	err = filepath.WalkDir(absPath, func(path string, d os.DirEntry, err error) error {
		if err != nil {
			return nil // skip errors
		}
		name := d.Name()
		// Skip hidden dirs, vendor, node_modules, __pycache__, .git
		if d.IsDir() && (strings.HasPrefix(name, ".") || name == "vendor" || name == "node_modules" || name == "__pycache__" || name == "venv") {
			return filepath.SkipDir
		}
		if d.IsDir() {
			return nil
		}
		// Only hash source files
		ext := filepath.Ext(name)
		switch ext {
		case ".py", ".go", ".ts", ".js", ".rs", ".java", ".c", ".h", ".cpp", ".hpp":
			info, err := d.Info()
			if err != nil {
				return nil
			}
			entries = append(entries, fmt.Sprintf("%s:%d", path, info.ModTime().UnixNano()))
		}
		return nil
	})
	if err != nil {
		return "", err
	}

	sort.Strings(entries)
	h := sha256.New()
	for _, e := range entries {
		h.Write([]byte(e))
	}
	return fmt.Sprintf("%x", h.Sum(nil)), nil
}
