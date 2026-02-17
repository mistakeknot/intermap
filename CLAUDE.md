# intermap

Project-level code mapping MCP server. Go binary + Python analysis bridge.

## Build & Test

```bash
# Go
go build ./...
go test ./...

# Python
PYTHONPATH=python python3 -m pytest python/tests/ -v

# Integration (Go → Python)
go build -o bin/intermap-mcp ./cmd/intermap-mcp/
echo '{"jsonrpc":"2.0","method":"tools/list","id":1}' | PYTHONPATH=python CLAUDE_PLUGIN_ROOT=. ./bin/intermap-mcp
```

## Architecture

- Go MCP server (`cmd/intermap-mcp/`) — stdio transport, mcp-go SDK
- Python analysis (`python/intermap/`) — call graphs, impact analysis, code structure
- Go → Python bridge (`internal/python/bridge.go`) — subprocess JSON-over-stdio

## MCP Tools

| Tool | Source | Description |
|------|--------|-------------|
| `project_registry` | Go | Scan workspace projects |
| `resolve_project` | Go | Find project for a file path |
| `agent_map` | Go+intermute | Active agents overlay |
| `code_structure` | Python | Functions/classes/imports |
| `impact_analysis` | Python | Reverse call graph |
| `change_impact` | Python | Affected tests for changes |

## Vendored Files

`python/intermap/vendor/` contains files from tldr-swinton. Do not modify — update source and re-vendor.
