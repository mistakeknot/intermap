# intermap Roadmap

**Version:** 0.2.0 | **Updated:** 2026-03-09

## Now (v0.2 — hardening) COMPLETE

All v0.2 goals achieved:
- **Go-level result caching** — `detect_patterns` and `cross_project_deps` cached with 5-min TTL, git HEAD SHA invalidation, `refresh` parameter
- **Symbol body-range detection** — unions baseline + working-tree symbol ranges to catch body-only edits regardless of line drift
- **Performance baselines** — Go benchmarks (cold/warm) and Python benchmarks (cold vs warm, mode parity, p95 regression)
- **Structured error types** — Python sidecar returns `{code, message, recoverable}`, Go bridge distinguishes recoverable (no restart) from fatal errors
- Full test suite: 71 Python tests + Go unit/integration tests

## Previous (v0.1.x)

All v0.1 goals achieved:
- 9 MCP tools working (project_registry, resolve_project, agent_map, code_structure, impact_analysis, change_impact, cross_project_deps, detect_patterns, live_changes)
- Python extraction from tldr-swinton complete (only vendor/dirty_flag.py remains)
- Persistent sidecar mode with crash recovery
- Full audit passed: 6 Go packages + 67 Python tests
- Tool overlap with tldr-swinton documented (coexistence, not replacement)

## Next (v0.3 — expansion)

## Later (v0.3+)

- **Language expansion** — Rust and TypeScript AST parsing beyond Go/Python
- **Persistent index** — optional SQLite cache for large monorepos
- **Real-time filesystem watching** — inotify/fsnotify instead of git-diff polling
- **Agent activity heatmaps** — deeper intermute integration showing which files agents touch most
- **Interflux integration** — feed structural data into review agent prompts for context-aware reviews
