# Intermap Roadmap

## Now (in progress)

- Complete tldr-swinton extraction — vendor modules promoted to first-class intermap code
- Fix test paths for monorepo structure
- Audit all 6 existing tools for accuracy and integration gaps

## Next

- **Cross-project dependency graph** — parse go.mod, pyproject.toml, plugin manifests to build dependency edges between monorepo projects
- **Architecture pattern detection** — extend existing `analyze_architecture` to detect MVC, event-driven, plugin system, and API patterns

## Later

- **Live change awareness** — git diff with structural annotation showing which functions/classes changed
- **Deeper intermute integration** — agent activity heatmaps overlaid on project structure
- **Multi-language expansion** — improve TypeScript, Go, and Rust analysis parity with Python
