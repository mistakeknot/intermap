# Intermap Vision

**Version:** 1.0 | **Date:** 2026-02-23

## Core Idea

Intermap is the spatial awareness layer for multi-agent development. It provides project-level code mapping via MCP tools — enabling agents to understand project structure, dependencies, and impact of changes without reading every file.

## Why This Exists

Agents need structural understanding beyond file contents. When modifying code across a monorepo with 30+ projects, an agent needs to know: which projects depend on this one, what tests to run for a given change, and what architectural patterns are in play. Intermap provides this knowledge as MCP tools that any agent can call.

## Current State

- 6 MCP tools: project registry, path resolver, agent map, code structure, impact analysis, change impact
- Go MCP server (mcp-go SDK) + Python analysis engine (AST-based)
- JSON-over-stdio subprocess bridge between Go and Python
- Two-level caching: Go (5min TTL LRU), Python (in-process mtime-keyed)

## Direction

- Cross-project dependency graphs (go.mod, pyproject.toml, plugin manifest parsing)
- Architecture pattern detection (MVC, event-driven, plugin systems)
- Live change awareness (git diff with structural annotation)
- Deeper intermute integration (agent activity heatmaps)

## Design Principles

1. **Stateless** — cache-only, no persistent state written to project directories
2. **Go host + Python engine** — Go handles MCP protocol and caching, Python handles analysis
3. **Subprocess isolation** — Python analysis runs in a subprocess, crashes don't take down the MCP server
4. **Graceful degradation** — tools return partial results rather than failing entirely
5. **Read-only** — intermap never modifies the codebase it analyzes
