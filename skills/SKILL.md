# intermap:status

Show project map — all detected projects with their language, git branch, and active agents.

Use when the user asks about project structure, what projects exist, or wants a workspace overview.

## Execution

Call the `project_registry` MCP tool to scan the workspace, then call `agent_map` to overlay active agents.

Present results as a formatted table:

```
| Project | Language | Branch | Group | Active Agents |
|---------|----------|--------|-------|---------------|
```
