"""Architecture pattern detection for codebases."""

import os
import re
from pathlib import Path


def detect_patterns(project_path: str, language: str = "auto") -> dict:
    """Detect architectural patterns in a project.

    Pattern types:
    - http_handlers: HTTP route registrations and handler functions
    - mcp_tools: MCP tool registrations (Go mcp-go or Python FastMCP)
    - middleware_stack: Middleware chain patterns
    - interface_impl: Interface definitions with implementations
    - cli_commands: CLI command group patterns (cobra, click)
    - plugin_skills: Claude Code skill directory patterns
    - test_suite: Test organization patterns

    Args:
        project_path: Project root directory
        language: Language hint (go, python, auto)

    Returns:
        Dict with project, language, patterns list, and count.
    """
    if language == "auto":
        language = _detect_language(project_path)

    patterns = []
    if language == "go":
        patterns.extend(_detect_go_patterns(project_path))
    elif language == "python":
        patterns.extend(_detect_python_patterns(project_path))
    # Cross-language patterns
    patterns.extend(_detect_plugin_patterns(project_path))

    return {
        "project": project_path,
        "language": language,
        "patterns": patterns,
        "total_patterns": len(patterns),
    }


def _detect_language(project_path: str) -> str:
    if os.path.isfile(os.path.join(project_path, "go.mod")):
        return "go"
    if os.path.isfile(os.path.join(project_path, "pyproject.toml")):
        return "python"
    if os.path.isfile(os.path.join(project_path, "package.json")):
        return "typescript"
    return "unknown"


def _detect_go_patterns(project_path: str) -> list[dict]:
    patterns = []
    for root, dirs, files in os.walk(project_path):
        dirs[:] = [d for d in dirs if d not in {".git", "vendor", "node_modules"}]
        for fname in files:
            if not fname.endswith(".go"):
                continue
            fpath = os.path.join(root, fname)
            rel = os.path.relpath(fpath, project_path)
            try:
                content = Path(fpath).read_text(errors="replace")
            except OSError:
                continue

            # HTTP handler registrations
            # (amendment #15: require router-like receiver prefix)
            handlers = re.findall(
                r'(?:r|router|mux|app|srv|server|e|g|api)\.'
                r'(?:HandleFunc|Handle|Get|Post|Put|Delete)\s*\(\s*"([^"]+)"',
                content,
            )
            if len(handlers) >= 2:
                patterns.append({
                    "type": "http_handlers",
                    "location": rel,
                    "confidence": min(0.9, 0.5 + len(handlers) * 0.1),
                    "description": f"{len(handlers)} HTTP routes registered",
                })

            # MCP tool registrations (mcp-go)
            tools = re.findall(r'mcp\.NewTool\s*\(\s*"([^"]+)"', content)
            if tools:
                patterns.append({
                    "type": "mcp_tools",
                    "location": rel,
                    "confidence": 0.95,
                    "description": f"{len(tools)} MCP tools: {', '.join(tools[:5])}",
                })

            # Interface definitions
            interfaces = re.findall(r'type\s+(\w+)\s+interface\s*\{', content)
            if interfaces:
                patterns.append({
                    "type": "interface_impl",
                    "location": rel,
                    "confidence": 0.85,
                    "description": f"Interfaces: {', '.join(interfaces[:5])}",
                })

            # Middleware patterns
            if re.search(r'func\s+\w+Middleware|\.Use\(|next\.ServeHTTP', content):
                patterns.append({
                    "type": "middleware_stack",
                    "location": rel,
                    "confidence": 0.8,
                    "description": "HTTP middleware chain detected",
                })

            # CLI command patterns (cobra)
            cobra_cmds = re.findall(
                r'&cobra\.Command\s*\{[^}]*Use:\s*"([^"]+)"',
                content, re.DOTALL,
            )
            if cobra_cmds:
                patterns.append({
                    "type": "cli_commands",
                    "location": rel,
                    "confidence": 0.9,
                    "description": f"Cobra commands: {', '.join(cobra_cmds[:5])}",
                })
    return patterns


def _detect_python_patterns(project_path: str) -> list[dict]:
    patterns = []
    for root, dirs, files in os.walk(project_path):
        dirs[:] = [d for d in dirs if d not in {
            ".git", "__pycache__", "venv", ".venv", "node_modules", "vendor",
        }]
        for fname in files:
            if not fname.endswith(".py"):
                continue
            fpath = os.path.join(root, fname)
            rel = os.path.relpath(fpath, project_path)
            try:
                content = Path(fpath).read_text(errors="replace")
            except OSError:
                continue

            # FastMCP tool registrations
            # (amendment #16: use \([^)]*\) to match named-argument decorators)
            tools = re.findall(
                r'@\w+\.tool\s*\([^)]*\)\s*\n\s*(?:async\s+)?def\s+(\w+)',
                content,
            )
            if tools:
                patterns.append({
                    "type": "mcp_tools",
                    "location": rel,
                    "confidence": 0.95,
                    "description": f"{len(tools)} FastMCP tools: {', '.join(tools[:5])}",
                })

            # Click CLI commands
            click_cmds = re.findall(
                r'@\w+\.command\s*\([^)]*\)\s*\n\s*def\s+(\w+)',
                content,
            )
            if click_cmds:
                patterns.append({
                    "type": "cli_commands",
                    "location": rel,
                    "confidence": 0.9,
                    "description": f"Click commands: {', '.join(click_cmds[:5])}",
                })
    return patterns


def _detect_plugin_patterns(project_path: str) -> list[dict]:
    """Detect Claude Code plugin structure patterns."""
    patterns = []
    # Skill directories
    skills_dir = os.path.join(project_path, "skills")
    if os.path.isdir(skills_dir):
        skill_dirs = [
            d for d in os.listdir(skills_dir)
            if os.path.isdir(os.path.join(skills_dir, d))
        ]
        skill_files = [f for f in os.listdir(skills_dir) if f.endswith(".md")]
        total = len(skill_dirs) + len(skill_files)
        if total > 0:
            patterns.append({
                "type": "plugin_skills",
                "location": "skills/",
                "confidence": 0.95,
                "description": f"{total} skills detected",
            })

    # Hook registrations
    hooks_json = os.path.join(project_path, "hooks", "hooks.json")
    if os.path.isfile(hooks_json):
        patterns.append({
            "type": "plugin_hooks",
            "location": "hooks/hooks.json",
            "confidence": 0.95,
            "description": "Hook registrations detected",
        })

    return patterns
