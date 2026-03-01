# intermap Roadmap

**Version:** 0.1.4 | **Updated:** 2026-03-01

## Now (v0.1.x — current)

All v0.1 goals achieved:
- 9 MCP tools working (project_registry, resolve_project, agent_map, code_structure, impact_analysis, change_impact, cross_project_deps, detect_patterns, live_changes)
- Python extraction from tldr-swinton complete (only vendor/dirty_flag.py remains)
- Persistent sidecar mode with crash recovery
- Full audit passed: 6 Go packages + 67 Python tests
- Tool overlap with tldr-swinton documented (coexistence, not replacement)

## Next (v0.2 — hardening)

- **Go-level result caching** for detect_patterns and cross_project_deps (currently full scan per call)
- **Symbol body-range detection** — live_changes misses body-only edits when header line unchanged
- **Performance baselines** — add benchmarks for each tool, track regressions
- **Error recovery hardening** — structured error types from Python, better timeout handling

## Later (v0.3+ — expansion)

- **Language expansion** — Rust and TypeScript AST parsing beyond Go/Python
- **Persistent index** — optional SQLite cache for large monorepos
- **Real-time filesystem watching** — inotify/fsnotify instead of git-diff polling
- **Agent activity heatmaps** — deeper intermute integration showing which files agents touch most
- **Interflux integration** — feed structural data into review agent prompts for context-aware reviews
