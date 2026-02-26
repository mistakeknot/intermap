package mcpfilter

const (
	ClusterStructure  Cluster = "structure"
	ClusterAnalysis   Cluster = "analysis"
	ClusterNavigation Cluster = "navigation"
)

// ToolClusters maps each Intermap tool to its cluster.
var ToolClusters = map[string]Cluster{
	"project_registry":   ClusterStructure,
	"resolve_project":    ClusterStructure,
	"code_structure":     ClusterStructure,
	"impact_analysis":    ClusterAnalysis,
	"change_impact":      ClusterAnalysis,
	"detect_patterns":    ClusterAnalysis,
	"cross_project_deps": ClusterNavigation,
	"agent_map":          ClusterNavigation,
	"live_changes":       ClusterNavigation,
}

// ProfileClusters defines which clusters are included in each non-full profile.
var ProfileClusters = map[Profile][]Cluster{
	ProfileCore:    {ClusterStructure, ClusterAnalysis},
	ProfileMinimal: {ClusterStructure},
}
