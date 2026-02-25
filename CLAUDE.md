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
- Go → Python bridge (`internal/python/bridge.go`) — persistent sidecar via stdin/stdout JSON-RPC

### Python Sidecar

The bridge spawns a single long-lived `python3 -u -m intermap --sidecar` process on first use. Requests are newline-delimited JSON on stdin, responses on stdout. Benefits:
- Python in-memory FileCache survives across MCP tool calls
- No per-call subprocess startup overhead (~200ms saved per call after first)
- Crash recovery: EOF detection + auto-respawn (max 3 in 10s, then falls back to single-shot mode)
- `python3 -m intermap --command/--project/--args` still works for debugging

## MCP Tools

| Tool | Source | Description |
|------|--------|-------------|
| `project_registry` | Go | Scan workspace projects |
| `resolve_project` | Go | Find project for a file path |
| `agent_map` | Go+intermute | Active agents overlay |
| `code_structure` | Python | Functions/classes/imports |
| `impact_analysis` | Python | Reverse call graph |
| `change_impact` | Python | Affected tests for changes |
| `cross_project_deps` | Python | Monorepo dependency graph |
| `detect_patterns` | Python | Architecture pattern detection |
| `live_changes` | Python | Git-diff with structural annotation |

## Extracted Modules

`python/intermap/workspace.py` and `python/intermap/ignore.py` were extracted from the tldr-swinton vendor directory. They are now owned by intermap.

## Vendored Files

`python/intermap/vendor/` contains remaining files from tldr-swinton (dirty_flag.py). Do not modify — update source and re-vendor.
