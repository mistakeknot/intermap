"""Cross-project dependency detection for monorepo structures."""

import os
import re
import json


def scan_cross_project_deps(root: str) -> dict:
    """Scan a monorepo root and detect cross-project dependencies.

    Detects:
    - Go module dependencies (go.mod replace directives)
    - Python path dependencies (pyproject.toml path deps)
    - Plugin dependencies (explicit env-var patterns in plugin.json)

    Args:
        root: Monorepo root directory

    Returns:
        Dict with projects, their dependencies, and edge counts.
    """
    projects = _discover_projects(root)
    # Use setdefault to handle duplicate project names (amendment #10)
    project_lookup: dict[str, str] = {}
    for p in projects:
        project_lookup.setdefault(p["name"], p["path"])

    results = []
    total_edges = 0
    for proj in projects:
        deps = []
        deps.extend(_scan_go_deps(proj["path"], project_lookup))
        deps.extend(_scan_python_deps(proj["path"], project_lookup))
        deps.extend(_scan_plugin_deps(proj["path"], project_lookup))
        # Deduplicate
        seen = set()
        unique_deps = []
        for d in deps:
            key = (d["project"], d["type"])
            if key not in seen:
                seen.add(key)
                unique_deps.append(d)
        total_edges += len(unique_deps)
        results.append({
            "project": proj["name"],
            "path": proj["path"],
            "depends_on": unique_deps,
        })

    return {
        "root": root,
        "projects": results,
        "total_projects": len(results),
        "total_edges": total_edges,
    }


def _discover_projects(root: str) -> list[dict]:
    """Find projects by walking top-level dirs for .git markers.

    Matches the Go registry.Scan() approach: walk all first-level
    subdirectories, then check for .git in second-level dirs.
    (amendment #9: use .git check, not hardcoded group names)
    """
    projects = []
    try:
        entries = sorted(os.listdir(root))
    except OSError:
        return projects

    for group_name in entries:
        group_path = os.path.join(root, group_name)
        if not os.path.isdir(group_path) or group_name.startswith("."):
            continue
        try:
            sub_entries = sorted(os.listdir(group_path))
        except OSError:
            continue
        for name in sub_entries:
            proj_path = os.path.join(group_path, name)
            if not os.path.isdir(proj_path) or name.startswith("."):
                continue
            # Only include dirs with .git marker (amendment #9)
            if not os.path.isdir(os.path.join(proj_path, ".git")):
                continue
            projects.append({"name": name, "path": proj_path, "group": group_name})
    return projects


def _scan_go_deps(project_path: str, project_lookup: dict) -> list[dict]:
    """Detect Go replace directives pointing to sibling projects."""
    gomod = os.path.join(project_path, "go.mod")
    if not os.path.isfile(gomod):
        return []
    deps = []
    with open(gomod, encoding="utf-8", errors="replace") as f:
        content = f.read()
    # Strip comment lines to avoid false edges from commented-out directives
    non_comment = "\n".join(
        l for l in content.splitlines() if not l.lstrip().startswith("//")
    )
    # Match replace directives — handles both single-line and block form
    # (amendment #3: don't anchor on 'replace' keyword, handle block form)
    for match in re.finditer(r'\S+\s+=>\s+(\.\./\S+)', non_comment):
        rel = match.group(1)
        abs_path = os.path.normpath(os.path.join(project_path, rel))
        target_name = os.path.basename(abs_path)
        if target_name in project_lookup:
            deps.append({"project": target_name, "type": "go_module", "via": f"replace => {rel}"})
    return deps


def _scan_python_deps(project_path: str, project_lookup: dict) -> list[dict]:
    """Detect Python path dependencies in pyproject.toml."""
    pyproject = os.path.join(project_path, "pyproject.toml")
    if not os.path.isfile(pyproject):
        return []
    deps = []
    with open(pyproject, encoding="utf-8", errors="replace") as f:
        content = f.read()
    # Match path dependencies: name = {path = "../sibling"}
    # (amendment #11: use [\w-]+ for hyphenated package names)
    for match in re.finditer(r'([\w-]+)\s*=\s*\{[^}]*path\s*=\s*"([^"]+)"', content):
        name, rel = match.group(1), match.group(2)
        abs_path = os.path.normpath(os.path.join(project_path, rel))
        target_name = os.path.basename(abs_path)
        if target_name in project_lookup:
            deps.append({"project": target_name, "type": "python_path", "via": f"{name} path={rel}"})
    return deps


def _scan_plugin_deps(project_path: str, project_lookup: dict) -> list[dict]:
    """Detect plugin references via explicit env-var patterns.

    Only emits edges for well-known patterns (INTERMUTE_URL, etc.)
    to avoid false positives from generic substring matching.
    (amendment #12: removed generic substring scan)
    """
    deps = []
    for pjson_path in [
        os.path.join(project_path, "plugin.json"),
        os.path.join(project_path, ".claude-plugin", "plugin.json"),
    ]:
        if not os.path.isfile(pjson_path):
            continue
        with open(pjson_path, encoding="utf-8", errors="replace") as f:
            try:
                manifest = json.load(f)
            except json.JSONDecodeError:
                continue
        for srv in (manifest.get("mcpServers") or {}).values():
            for key, _val in (srv.get("env") or {}).items():
                # Only emit for known explicit patterns
                if "INTERMUTE" in key.upper() and "intermute" in project_lookup:
                    deps.append({"project": "intermute", "type": "plugin_ref", "via": f"env.{key}"})
    return deps
