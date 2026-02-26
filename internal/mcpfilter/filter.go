// Package mcpfilter provides startup-time tool filtering for MCP servers.
// Tools are assigned to clusters; profiles select which clusters are exposed.
package mcpfilter

import (
	"log/slog"
	"os"
)

// Profile controls which tool clusters are exposed.
type Profile string

const (
	ProfileFull    Profile = "full"
	ProfileCore    Profile = "core"
	ProfileMinimal Profile = "minimal"
)

// Cluster groups related tools by function.
type Cluster string

// ReadProfile reads the tool profile from env vars.
// Priority: server-specific > global > default (full).
func ReadProfile(serverEnvKey string) Profile {
	if v := os.Getenv(serverEnvKey); v != "" {
		return parseProfile(v, serverEnvKey)
	}
	if v := os.Getenv("MCP_TOOL_PROFILE"); v != "" {
		return parseProfile(v, "MCP_TOOL_PROFILE")
	}
	return ProfileFull
}

func parseProfile(s string, source string) Profile {
	switch Profile(s) {
	case ProfileFull, ProfileCore, ProfileMinimal:
		return Profile(s)
	default:
		slog.Warn("mcpfilter: unknown profile, defaulting to full", "value", s, "source", source)
		return ProfileFull
	}
}

// Filter returns only the tools whose cluster is allowed by the profile.
func Filter[T any](
	tools []T,
	getName func(T) string,
	profile Profile,
	toolClusters map[string]Cluster,
	profileClusters map[Profile][]Cluster,
) []T {
	if profile == ProfileFull {
		return tools
	}
	allowed := make(map[Cluster]bool)
	for _, c := range profileClusters[profile] {
		allowed[c] = true
	}
	var filtered []T
	for _, t := range tools {
		name := getName(t)
		c, ok := toolClusters[name]
		if !ok {
			slog.Warn("mcpfilter: tool not in any cluster, excluding", "tool", name, "profile", profile)
			continue
		}
		if allowed[c] {
			filtered = append(filtered, t)
		}
	}
	return filtered
}
