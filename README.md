# intermap

Project-level code mapping MCP server for Claude Code.

## What This Does

intermap maintains a structural understanding of your codebase — project registry, call graphs, impact analysis — and exposes it through 6 MCP tools. The `agent_map` tool is particularly interesting: it overlays live agent activity from intermute onto the code structure, so you can see not just what the code looks like but who's currently working on which parts.

The server is Go with a Python analysis bridge. Go handles the MCP transport and project registry; Python (via a vendored subset of tldr-swinton) does the actual code analysis. They communicate over JSON-over-stdio subprocess calls.

## Installation

```bash
/plugin install intermap
```

## MCP Tools

- **project_registry** — List and manage tracked projects
- **resolve_project** — Resolve a path to its project context
- **agent_map** — Code structure with live agent overlay from intermute
- **code_structure** — Directory and symbol-level structural analysis
- **impact_analysis** — Dependency-aware change impact assessment
- **change_impact** — What does changing this function affect?

## Architecture

```
cmd/intermap-mcp/    Go MCP server (mark3labs/mcp-go)
python/intermap/     Python analysis layer
  vendor/            Vendored tldr-swinton code (do not modify)
bin/launch-mcp.sh    Server launcher
```

The Go → Python bridge uses subprocess with JSON-over-stdio — each analysis request spawns a Python subprocess, passes JSON in, gets JSON back. Simple and reliable, if not the fastest possible approach.
