package mcpfilter

import "testing"

func TestIntermapClusters(t *testing.T) {
	expectedTools := []string{
		"project_registry", "resolve_project", "code_structure",
		"impact_analysis", "change_impact", "detect_patterns",
		"cross_project_deps", "agent_map", "live_changes",
	}
	for _, name := range expectedTools {
		if _, ok := ToolClusters[name]; !ok {
			t.Errorf("tool %q not in ToolClusters", name)
		}
	}
	if len(ToolClusters) != 9 {
		t.Errorf("want 9 tools in ToolClusters, got %d", len(ToolClusters))
	}
}

func TestIntermapProfiles(t *testing.T) {
	getName := func(name string) string { return name }
	allNames := make([]string, 0, len(ToolClusters))
	for name := range ToolClusters {
		allNames = append(allNames, name)
	}

	core := Filter(allNames, getName, ProfileCore, ToolClusters, ProfileClusters)
	if len(core) != 6 {
		t.Errorf("core profile: want 6 tools, got %d", len(core))
	}

	minimal := Filter(allNames, getName, ProfileMinimal, ToolClusters, ProfileClusters)
	if len(minimal) != 3 {
		t.Errorf("minimal profile: want 3 tools, got %d", len(minimal))
	}
}
