package tools

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"strings"
	"time"

	"github.com/mark3labs/mcp-go/mcp"
	"github.com/mark3labs/mcp-go/server"
	"github.com/mistakeknot/intermap/internal/cache"
	"github.com/mistakeknot/intermap/internal/client"
	pybridge "github.com/mistakeknot/intermap/internal/python"
	"github.com/mistakeknot/intermap/internal/registry"
)

var projectCache = cache.New[[]registry.Project](5*time.Minute, 10)

// RegisterAll registers all MCP tools with the server and returns the Python
// bridge for lifecycle management. Caller should defer bridge.Close().
func RegisterAll(s *server.MCPServer, c *client.Client) *pybridge.Bridge {
	bridge := pybridge.NewBridge(pybridge.DefaultPythonPath())
	s.AddTools(
		projectRegistry(),
		resolveProject(),
		agentMap(c),
		codeStructure(bridge),
		impactAnalysis(bridge),
		changeImpact(bridge),
		crossProjectDeps(bridge),
		detectPatterns(bridge),
		liveChanges(bridge),
	)
	return bridge
}

func projectRegistry() server.ServerTool {
	return server.ServerTool{
		Tool: mcp.NewTool("project_registry",
			mcp.WithDescription("Scan workspace and list all projects with their language, group, and git branch."),
			mcp.WithString("root",
				mcp.Description("Workspace root directory to scan (defaults to CWD)"),
			),
			mcp.WithBoolean("refresh",
				mcp.Description("Force cache refresh"),
			),
		),
		Handler: func(ctx context.Context, req mcp.CallToolRequest) (*mcp.CallToolResult, error) {
			args := req.GetArguments()
			root := stringOr(args["root"], "")
			refresh, _ := args["refresh"].(bool)

			if root == "" {
				var err error
				root, err = os.Getwd()
				if err != nil {
					return mcp.NewToolResultError(fmt.Sprintf("getwd: %v", err)), nil
				}
			}

			cacheKey := root
			if !refresh {
				if cached, ok := projectCache.Get(cacheKey, ""); ok {
					return jsonResult(cached)
				}
			}

			projects, err := registry.Scan(root)
			if err != nil {
				return mcp.NewToolResultError(fmt.Sprintf("scan: %v", err)), nil
			}

			projectCache.Put(cacheKey, "", projects)
			return jsonResult(projects)
		},
	}
}

func resolveProject() server.ServerTool {
	return server.ServerTool{
		Tool: mcp.NewTool("resolve_project",
			mcp.WithDescription("Find which project a file path belongs to by walking up to the nearest .git directory."),
			mcp.WithString("path",
				mcp.Description("File or directory path to resolve"),
				mcp.Required(),
			),
		),
		Handler: func(ctx context.Context, req mcp.CallToolRequest) (*mcp.CallToolResult, error) {
			args := req.GetArguments()
			path, _ := args["path"].(string)
			if path == "" {
				return mcp.NewToolResultError("path is required"), nil
			}

			project, err := registry.Resolve(path)
			if err != nil {
				return mcp.NewToolResultError(err.Error()), nil
			}
			return jsonResult(project)
		},
	}
}

// AgentOverlay holds the combined agent + project + reservation data.
type AgentOverlay struct {
	AgentID      string   `json:"agent_id"`
	Name         string   `json:"name"`
	Status       string   `json:"status"`
	Project      string   `json:"project"`
	ProjectPath  string   `json:"project_path,omitempty"`
	SessionID    string   `json:"session_id,omitempty"`
	LastSeen     string   `json:"last_seen,omitempty"`
	Reservations []string `json:"reservations,omitempty"`
}

// AgentMapResult is the top-level response for the agent_map tool.
type AgentMapResult struct {
	Agents          []AgentOverlay `json:"agents"`
	AgentsAvailable bool           `json:"agents_available"`
	AgentsError     string         `json:"agents_error,omitempty"`
	ProjectCount    int            `json:"project_count"`
}

func agentMap(c *client.Client) server.ServerTool {
	return server.ServerTool{
		Tool: mcp.NewTool("agent_map",
			mcp.WithDescription("Show which agents are working on which projects and files. Combines project registry, agent list, and file reservations into a unified overlay."),
			mcp.WithString("root",
				mcp.Description("Workspace root directory to scan (defaults to CWD)"),
			),
		),
		Handler: func(ctx context.Context, req mcp.CallToolRequest) (*mcp.CallToolResult, error) {
			args := req.GetArguments()
			root := stringOr(args["root"], "")

			if root == "" {
				var err error
				root, err = os.Getwd()
				if err != nil {
					return mcp.NewToolResultError(fmt.Sprintf("getwd: %v", err)), nil
				}
			}

			// Scan projects from filesystem
			projects, err := registry.Scan(root)
			if err != nil {
				return mcp.NewToolResultError(fmt.Sprintf("scan: %v", err)), nil
			}

			// Build project path lookup
			projectByName := make(map[string]registry.Project)
			for _, p := range projects {
				projectByName[p.Name] = p
			}

			result := AgentMapResult{
				Agents:          []AgentOverlay{},
				AgentsAvailable: c.Available(),
				ProjectCount:    len(projects),
			}

			if !c.Available() {
				result.AgentsError = "intermute not configured (INTERMUTE_URL not set)"
				return jsonResult(result)
			}

			// Fetch agents from intermute
			agents, err := c.ListAgents(ctx)
			if err != nil {
				result.AgentsError = fmt.Sprintf("intermute unreachable: %v", err)
				return jsonResult(result)
			}

			// Fetch all reservations
			reservations, err := c.ListReservations(ctx, "")
			if err != nil {
				result.AgentsError = fmt.Sprintf("reservations unavailable: %v", err)
				// Still return agents without reservation data
			}

			// Index reservations by agent ID
			reservationsByAgent := make(map[string][]string)
			for _, r := range reservations {
				if r.IsActive {
					reservationsByAgent[r.AgentID] = append(reservationsByAgent[r.AgentID], r.Pattern)
				}
			}

			// Build overlay entries
			for _, agent := range agents {
				overlay := AgentOverlay{
					AgentID:      agent.AgentID,
					Name:         agent.Name,
					Status:       agent.Status,
					Project:      agent.Project,
					SessionID:    agent.SessionID,
					LastSeen:     agent.LastSeen,
					Reservations: reservationsByAgent[agent.AgentID],
				}

				// Match agent to project by name or path containment
				if p, ok := projectByName[agent.Project]; ok {
					overlay.ProjectPath = p.Path
				} else {
					// Try matching by path substring
					for _, p := range projects {
						if strings.Contains(p.Path, agent.Project) || strings.Contains(agent.Project, p.Name) {
							overlay.ProjectPath = p.Path
							break
						}
					}
				}

				result.Agents = append(result.Agents, overlay)
			}

			return jsonResult(result)
		},
	}
}

func codeStructure(bridge *pybridge.Bridge) server.ServerTool {
	return server.ServerTool{
		Tool: mcp.NewTool("code_structure",
			mcp.WithDescription("Analyze code structure of a project — list all functions, classes, and imports."),
			mcp.WithString("project",
				mcp.Description("Project path to analyze"),
				mcp.Required(),
			),
			mcp.WithString("language",
				mcp.Description("Programming language (python, typescript, go, rust)"),
			),
			mcp.WithNumber("max_results",
				mcp.Description("Maximum number of files to analyze (default 100)"),
			),
		),
		Handler: func(ctx context.Context, req mcp.CallToolRequest) (*mcp.CallToolResult, error) {
			args := req.GetArguments()
			project, _ := args["project"].(string)
			if project == "" {
				return mcp.NewToolResultError("project is required"), nil
			}

			pyArgs := map[string]any{
				"language":    stringOr(args["language"], "python"),
				"max_results": intOr(args["max_results"], 100),
			}

			result, err := bridge.Run(ctx, "structure", project, pyArgs)
			if err != nil {
				return mcp.NewToolResultError(err.Error()), nil
			}
			return jsonResult(result)
		},
	}
}

func impactAnalysis(bridge *pybridge.Bridge) server.ServerTool {
	return server.ServerTool{
		Tool: mcp.NewTool("impact_analysis",
			mcp.WithDescription("Find all callers of a function (reverse call graph) — useful for understanding what code is affected by changes."),
			mcp.WithString("project",
				mcp.Description("Project path to analyze"),
				mcp.Required(),
			),
			mcp.WithString("target",
				mcp.Description("Function name to find callers of"),
				mcp.Required(),
			),
			mcp.WithString("language",
				mcp.Description("Programming language"),
			),
			mcp.WithNumber("max_depth",
				mcp.Description("Maximum call graph traversal depth (default 3)"),
			),
		),
		Handler: func(ctx context.Context, req mcp.CallToolRequest) (*mcp.CallToolResult, error) {
			args := req.GetArguments()
			project, _ := args["project"].(string)
			target, _ := args["target"].(string)
			if project == "" || target == "" {
				return mcp.NewToolResultError("project and target are required"), nil
			}

			pyArgs := map[string]any{
				"target":    target,
				"language":  stringOr(args["language"], "python"),
				"max_depth": intOr(args["max_depth"], 3),
			}

			result, err := bridge.Run(ctx, "impact", project, pyArgs)
			if err != nil {
				return mcp.NewToolResultError(err.Error()), nil
			}
			return jsonResult(result)
		},
	}
}

func changeImpact(bridge *pybridge.Bridge) server.ServerTool {
	return server.ServerTool{
		Tool: mcp.NewTool("change_impact",
			mcp.WithDescription("Find which tests to run based on changed files — uses call graph analysis and import tracking."),
			mcp.WithString("project",
				mcp.Description("Project path to analyze"),
				mcp.Required(),
			),
			mcp.WithString("language",
				mcp.Description("Programming language"),
			),
			mcp.WithBoolean("use_git",
				mcp.Description("Use git diff to detect changed files"),
			),
			mcp.WithString("git_base",
				mcp.Description("Git ref to diff against (default HEAD~1)"),
			),
		),
		Handler: func(ctx context.Context, req mcp.CallToolRequest) (*mcp.CallToolResult, error) {
			args := req.GetArguments()
			project, _ := args["project"].(string)
			if project == "" {
				return mcp.NewToolResultError("project is required"), nil
			}

			pyArgs := map[string]any{
				"language": stringOr(args["language"], "python"),
				"use_git":  boolOr(args["use_git"], true),
				"git_base": stringOr(args["git_base"], "HEAD~1"),
			}

			result, err := bridge.Run(ctx, "change_impact", project, pyArgs)
			if err != nil {
				return mcp.NewToolResultError(err.Error()), nil
			}
			return jsonResult(result)
		},
	}
}

func crossProjectDeps(bridge *pybridge.Bridge) server.ServerTool {
	return server.ServerTool{
		Tool: mcp.NewTool("cross_project_deps",
			mcp.WithDescription("Map cross-project dependencies in a monorepo — Go module deps, Python path deps, plugin references."),
			mcp.WithString("root",
				mcp.Description("Monorepo root directory to scan"),
				mcp.Required(),
			),
		),
		Handler: func(ctx context.Context, req mcp.CallToolRequest) (*mcp.CallToolResult, error) {
			args := req.GetArguments()
			root, _ := args["root"].(string)
			if root == "" {
				return mcp.NewToolResultError("root is required"), nil
			}
			// Pass root as the "project" positional arg to bridge.Run
			result, err := bridge.Run(ctx, "cross_project_deps", root, map[string]any{})
			if err != nil {
				return mcp.NewToolResultError(err.Error()), nil
			}
			return jsonResult(result)
		},
	}
}

func detectPatterns(bridge *pybridge.Bridge) server.ServerTool {
	return server.ServerTool{
		Tool: mcp.NewTool("detect_patterns",
			mcp.WithDescription("Detect architectural patterns: HTTP handlers, MCP tools, middleware, interfaces, CLI commands, plugin structures."),
			mcp.WithString("project",
				mcp.Description("Project root directory to analyze"),
				mcp.Required(),
			),
			mcp.WithString("language",
				mcp.Description("Language (go, python, auto)"),
			),
		),
		Handler: func(ctx context.Context, req mcp.CallToolRequest) (*mcp.CallToolResult, error) {
			args := req.GetArguments()
			project, _ := args["project"].(string)
			if project == "" {
				return mcp.NewToolResultError("project is required"), nil
			}
			pyArgs := map[string]any{
				"language": stringOr(args["language"], "auto"),
			}
			result, err := bridge.Run(ctx, "detect_patterns", project, pyArgs)
			if err != nil {
				return mcp.NewToolResultError(err.Error()), nil
			}
			return jsonResult(result)
		},
	}
}

func liveChanges(bridge *pybridge.Bridge) server.ServerTool {
	return server.ServerTool{
		Tool: mcp.NewTool("live_changes",
			mcp.WithDescription("Detect changes since a git baseline and annotate with affected symbols (functions, classes)."),
			mcp.WithString("project",
				mcp.Description("Project root directory (must be in a git repo)"),
				mcp.Required(),
			),
			mcp.WithString("baseline",
				mcp.Description("Git ref to diff against (default HEAD)"),
			),
			mcp.WithString("language",
				mcp.Description("Language hint for extraction (auto-detects if not set)"),
			),
		),
		Handler: func(ctx context.Context, req mcp.CallToolRequest) (*mcp.CallToolResult, error) {
			args := req.GetArguments()
			project, _ := args["project"].(string)
			if project == "" {
				return mcp.NewToolResultError("project is required"), nil
			}
			pyArgs := map[string]any{
				"baseline": stringOr(args["baseline"], "HEAD"),
				"language": stringOr(args["language"], "auto"),
			}
			result, err := bridge.Run(ctx, "live_changes", project, pyArgs)
			if err != nil {
				return mcp.NewToolResultError(err.Error()), nil
			}
			return jsonResult(result)
		},
	}
}

// --- Helpers ---

func jsonResult(v any) (*mcp.CallToolResult, error) {
	data, err := json.Marshal(v)
	if err != nil {
		return mcp.NewToolResultError(fmt.Sprintf("marshal: %v", err)), nil
	}
	return mcp.NewToolResultText(string(data)), nil
}

func stringOr(v any, def string) string {
	if s, ok := v.(string); ok && s != "" {
		return s
	}
	return def
}

func intOr(v any, def int) int {
	switch n := v.(type) {
	case float64:
		return int(n)
	case int:
		return n
	case json.Number:
		if i, err := n.Int64(); err == nil {
			return int(i)
		}
	}
	return def
}

func boolOr(v any, def bool) bool {
	if b, ok := v.(bool); ok {
		return b
	}
	return def
}
