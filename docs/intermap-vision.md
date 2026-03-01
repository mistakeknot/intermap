# Intermap Vision

**Version:** 1.0 | **Date:** 2026-02-23

## Core Idea

Intermap is the spatial awareness layer for multi-agent development. It provides project-level code mapping via MCP tools — enabling agents to understand project structure, dependencies, and impact of changes without reading every file.

## Why This Exists

Agents need structural understanding beyond file contents. When modifying code across a monorepo with 30+ projects, an agent needs to know: which projects depend on this one, what tests to run for a given change, and what architectural patterns are in play. Intermap provides this knowledge as MCP tools that any agent can call.

## Current State (v0.1.4)

- 9 MCP tools: project registry, path resolver, agent map, code structure, impact analysis, change impact, cross-project deps, detect patterns, live changes
- Go MCP server (mcp-go v0.43.2) + Python analysis engine (AST-based)
- Persistent sidecar subprocess (JSON-over-stdin, crash recovery, single-shot fallback)
- Two-level caching: Go (5min TTL LRU), Python (in-process mtime-keyed FileCache)
- Full audit passed: 6 Go packages + 67 Python tests, all tools verified against live Demarch monorepo (86 projects)

## Direction

- Go-level result caching for detect_patterns and cross_project_deps (currently no cache, full scan per call)
- Symbol body-range detection (missed body-only edits in live_changes)
- Language expansion beyond Go/Python (Rust, TypeScript)
- Deeper intermute integration (agent activity heatmaps)

Intermap feeds the Demarch flywheel: better structural maps enable better routing (Interspect), better review (interflux), and better coordination (intermute). Its data quality directly compounds into system-wide quality (PHILOSOPHY.md: the flywheel compounds).

## Design Principles

1. **Stateless** — cache-only, no persistent state written to project directories
2. **Go host + Python engine** — Go handles MCP protocol and caching, Python handles analysis
3. **Subprocess isolation** — Python analysis runs in a subprocess, crashes don't take down the MCP server
4. **Graceful degradation** — tools return partial results rather than failing entirely
5. **Read-only** — intermap never modifies the codebase it analyzes
6. **Observable** — intermap's own accuracy is measurable: did impact analysis predict the right affected files? Did change_impact identify the right tests? Instrument first, optimize later (PHILOSOPHY.md). Structural analysis that can't be validated against outcomes is just opinion.
