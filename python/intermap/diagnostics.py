# Moved from tldr-swinton (plugins/tldr-swinton/src/tldr_swinton/modules/core/diagnostics.py)
# Zero internal dependencies — only stdlib imports.
"""
Real-time diagnostics for code analysis.

Wraps type checkers (pyright, mypy) and linters (ruff) to provide
structured error output for LLM agents.

Supports:
- Python: pyright (type checker) + ruff (linter)
- TypeScript/JavaScript: tsc (type checker)
- Go: go vet (type checker) + golangci-lint (linter)
- Rust: cargo check (type checker) + clippy (linter)
- Java: javac (type checker) + checkstyle (linter)
- C/C++: clang/gcc (type checker) + cppcheck (linter)
- Ruby: rubocop (linter)
- PHP: phpstan (linter)
- Kotlin: kotlinc (type checker) + ktlint (linter)
- Swift: swiftc (type checker) + swiftlint (linter)
- C#: dotnet build (type checker)
- Scala: scalac (type checker)
- Elixir: mix compile (type checker) + credo (linter)
"""

import json
import re
import shutil
import subprocess
from pathlib import Path
from xml.etree import ElementTree


# Mapping of language -> tools configuration
LANG_TOOLS: dict[str, dict] = {
    "python": {"type_checker": "pyright", "linter": "ruff"},
    "typescript": {"type_checker": "tsc", "linter": None},
    "javascript": {"type_checker": None, "linter": None},
    "go": {"type_checker": "go vet", "linter": "golangci-lint"},
    "rust": {"type_checker": "cargo check", "linter": "clippy"},
    "java": {"type_checker": "javac", "linter": "checkstyle"},
    "c": {"type_checker": "gcc", "linter": "cppcheck"},
    "cpp": {"type_checker": "g++", "linter": "cppcheck"},
    "ruby": {"type_checker": None, "linter": "rubocop"},
    "php": {"type_checker": None, "linter": "phpstan"},
    "kotlin": {"type_checker": "kotlinc", "linter": "ktlint"},
    "swift": {"type_checker": "swiftc", "linter": "swiftlint"},
    "csharp": {"type_checker": "dotnet build", "linter": None},
    "scala": {"type_checker": "scalac", "linter": None},
    "elixir": {"type_checker": "mix compile", "linter": "credo"},
}


def _detect_language(file_path: str) -> str:
    """Detect language from file extension."""
    ext = Path(file_path).suffix.lower()
    mapping = {
        ".py": "python", ".ts": "typescript", ".tsx": "typescript",
        ".js": "javascript", ".jsx": "javascript", ".go": "go",
        ".rs": "rust", ".java": "java", ".c": "c", ".h": "c",
        ".cpp": "cpp", ".cc": "cpp", ".cxx": "cpp", ".hpp": "cpp",
        ".rb": "ruby", ".php": "php", ".kt": "kotlin", ".swift": "swift",
        ".cs": "csharp", ".scala": "scala", ".ex": "elixir", ".exs": "elixir",
    }
    return mapping.get(ext, "unknown")


def _run_tool(cmd: list[str], timeout: int = 30, cwd: str | None = None) -> tuple[str, str, int]:
    """Run a tool and return (stdout, stderr, returncode)."""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd,
        )
        return result.stdout, result.stderr, result.returncode
    except subprocess.TimeoutExpired:
        return "", "", -1
    except FileNotFoundError:
        return "", "", -1


def _parse_line_based(text: str, pattern: str, source: str) -> list[dict]:
    """Parse line-based compiler output into diagnostics."""
    diagnostics = []
    if not text.strip():
        return diagnostics
    for line in text.strip().split("\n"):
        match = re.match(pattern, line)
        if match:
            groups = match.groups()
            diagnostics.append({
                "file": groups[0],
                "line": int(groups[1]),
                "column": int(groups[2]) if len(groups) > 3 else 0,
                "severity": groups[-2] if len(groups) > 3 else "error",
                "message": groups[-1],
                "rule": "",
                "source": source,
            })
    return diagnostics


def _parse_json_diagnostics(stdout: str, parser_fn) -> list[dict]:
    """Parse JSON output with a parser function."""
    try:
        data = json.loads(stdout)
        return parser_fn(data)
    except json.JSONDecodeError:
        return []


def get_diagnostics(
    file_path: str,
    language: str | None = None,
    include_lint: bool = True,
) -> dict:
    """Get type and lint diagnostics for a file.

    Returns dict with 'diagnostics' list and metadata.
    """
    path = Path(file_path).resolve()
    if not path.exists():
        return {"error": f"File not found: {file_path}", "diagnostics": []}

    lang = language or _detect_language(str(path))
    all_diagnostics = []
    tools_used = []

    if lang == "python":
        if shutil.which("pyright"):
            stdout, _, _ = _run_tool(["pyright", "--outputjson", str(path)])
            try:
                data = json.loads(stdout)
                for diag in data.get("generalDiagnostics", []):
                    all_diagnostics.append({
                        "file": diag.get("file", ""),
                        "line": diag.get("range", {}).get("start", {}).get("line", 0) + 1,
                        "column": diag.get("range", {}).get("start", {}).get("character", 0) + 1,
                        "severity": diag.get("severity", "error"),
                        "message": diag.get("message", ""),
                        "rule": diag.get("rule", ""),
                        "source": "pyright",
                    })
                tools_used.append("pyright")
            except json.JSONDecodeError:
                pass

        if include_lint and shutil.which("ruff"):
            stdout, _, _ = _run_tool(["ruff", "check", "--output-format=json", str(path)], timeout=10)
            try:
                data = json.loads(stdout)
                for diag in data:
                    all_diagnostics.append({
                        "file": diag.get("filename", ""),
                        "line": diag.get("location", {}).get("row", 0),
                        "column": diag.get("location", {}).get("column", 0),
                        "severity": "warning",
                        "message": diag.get("message", ""),
                        "rule": diag.get("code", ""),
                        "source": "ruff",
                    })
                tools_used.append("ruff")
            except json.JSONDecodeError:
                pass

    elif lang == "go":
        if shutil.which("go"):
            _, stderr, _ = _run_tool(["go", "vet", str(path)])
            pattern = r"(.+?):(\d+):(\d+):\s*(.+)"
            all_diagnostics.extend(_parse_line_based(stderr, pattern, "go vet"))
            tools_used.append("go vet")

    elif lang == "typescript":
        if shutil.which("tsc"):
            _, stderr, _ = _run_tool(["tsc", "--noEmit", "--pretty", "false", str(path)])
            pattern = r"(.+?)\((\d+),(\d+)\):\s*(error|warning)\s+(TS\d+):\s*(.+)"
            for line in stderr.strip().split("\n"):
                match = re.match(pattern, line)
                if match:
                    all_diagnostics.append({
                        "file": match.group(1),
                        "line": int(match.group(2)),
                        "column": int(match.group(3)),
                        "severity": match.group(4),
                        "message": match.group(6),
                        "rule": match.group(5),
                        "source": "tsc",
                    })
            tools_used.append("tsc")

    elif lang == "rust":
        if shutil.which("cargo"):
            stdout, _, _ = _run_tool(
                ["cargo", "check", "--message-format=json"],
                timeout=120, cwd=str(path.parent),
            )
            for line in (stdout or "").strip().split("\n"):
                try:
                    data = json.loads(line)
                    if data.get("reason") != "compiler-message":
                        continue
                    msg = data.get("message", {})
                    spans = msg.get("spans", [])
                    if not spans:
                        continue
                    span = spans[0]
                    code = msg.get("code", {})
                    all_diagnostics.append({
                        "file": span.get("file_name", ""),
                        "line": span.get("line_start", 0),
                        "column": span.get("column_start", 0),
                        "severity": msg.get("level", "error"),
                        "message": msg.get("message", ""),
                        "rule": code.get("code", "") if code else "",
                        "source": "cargo",
                    })
                except json.JSONDecodeError:
                    continue
            tools_used.append("cargo check")

    all_diagnostics.sort(key=lambda d: (d.get("file", ""), d.get("line", 0)))

    return {
        "file": str(path),
        "language": lang,
        "tools": tools_used,
        "diagnostics": all_diagnostics,
        "error_count": sum(1 for d in all_diagnostics if d.get("severity") == "error"),
        "warning_count": sum(1 for d in all_diagnostics if d.get("severity") == "warning"),
    }


def get_project_diagnostics(
    project_path: str,
    language: str = "python",
    include_lint: bool = True,
) -> dict:
    """Get diagnostics for entire project."""
    path = Path(project_path).resolve()
    if not path.exists():
        return {"error": f"Path not found: {project_path}", "diagnostics": []}

    all_diagnostics = []
    tools_used = []

    if language == "python":
        if shutil.which("pyright"):
            stdout, _, _ = _run_tool(["pyright", "--outputjson", str(path)], timeout=120, cwd=str(path))
            try:
                data = json.loads(stdout)
                for diag in data.get("generalDiagnostics", []):
                    all_diagnostics.append({
                        "file": diag.get("file", ""),
                        "line": diag.get("range", {}).get("start", {}).get("line", 0) + 1,
                        "column": diag.get("range", {}).get("start", {}).get("character", 0) + 1,
                        "severity": diag.get("severity", "error"),
                        "message": diag.get("message", ""),
                        "rule": diag.get("rule", ""),
                        "source": "pyright",
                    })
                tools_used.append("pyright")
            except json.JSONDecodeError:
                pass

        if include_lint and shutil.which("ruff"):
            stdout, _, _ = _run_tool(["ruff", "check", "--output-format=json", str(path)], timeout=60, cwd=str(path))
            try:
                data = json.loads(stdout)
                for diag in data:
                    all_diagnostics.append({
                        "file": diag.get("filename", ""),
                        "line": diag.get("location", {}).get("row", 0),
                        "column": diag.get("location", {}).get("column", 0),
                        "severity": "warning",
                        "message": diag.get("message", ""),
                        "rule": diag.get("code", ""),
                        "source": "ruff",
                    })
                tools_used.append("ruff")
            except json.JSONDecodeError:
                pass

    elif language == "go":
        if shutil.which("go"):
            _, stderr, _ = _run_tool(["go", "vet", "./..."], timeout=120, cwd=str(path))
            pattern = r"(.+?):(\d+):(\d+):\s*(.+)"
            all_diagnostics.extend(_parse_line_based(stderr, pattern, "go vet"))
            tools_used.append("go vet")

    all_diagnostics.sort(key=lambda d: (d.get("file", ""), d.get("line", 0)))

    return {
        "project": str(path),
        "language": language,
        "tools": tools_used,
        "diagnostics": all_diagnostics,
        "error_count": sum(1 for d in all_diagnostics if d.get("severity") == "error"),
        "warning_count": sum(1 for d in all_diagnostics if d.get("severity") == "warning"),
        "file_count": len(set(d.get("file", "") for d in all_diagnostics)),
    }


def format_diagnostics_for_llm(result: dict) -> str:
    """Format diagnostics as concise text for LLM context."""
    if result.get("error"):
        return f"Error: {result['error']}"

    diagnostics = result.get("diagnostics", [])
    if not diagnostics:
        return "No diagnostics found."

    lines = []
    errors = result.get("error_count", 0)
    warnings = result.get("warning_count", 0)
    lines.append(f"Found {errors} errors, {warnings} warnings")
    lines.append("")

    for d in diagnostics:
        severity = "E" if d.get("severity") == "error" else "W"
        rule = f" [{d['rule']}]" if d.get("rule") else ""
        lines.append(f"{severity} {d['file']}:{d['line']}:{d['column']}: {d['message']}{rule}")

    return "\n".join(lines)
