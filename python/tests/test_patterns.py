"""Tests for architecture pattern detection."""

import os

import pytest

from intermap.patterns import detect_patterns


# Resolve Demarch root relative to this test file
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
DEMARCH_ROOT = os.environ.get(
    "DEMARCH_ROOT",
    os.path.normpath(os.path.join(_TESTS_DIR, "../../../..")),
)


# --- Fixture-based tests (synthetic repos) ---


def test_output_structure(tmp_path):
    """Output has required fields."""
    (tmp_path / "go.mod").write_text("module example.com/test\n")
    result = detect_patterns(str(tmp_path), language="go")
    assert "patterns" in result
    assert "project" in result
    assert "language" in result
    assert "total_patterns" in result
    assert result["language"] == "go"


def test_go_mcp_tools(tmp_path):
    """Detects mcp.NewTool registrations."""
    (tmp_path / "tools.go").write_text('''
package tools

import "github.com/mark3labs/mcp-go/mcp"

func init() {
    mcp.NewTool("my_tool", mcp.WithDescription("test"))
    mcp.NewTool("other_tool", mcp.WithDescription("test2"))
}
''')
    result = detect_patterns(str(tmp_path), language="go")
    types = {p["type"] for p in result["patterns"]}
    assert "mcp_tools" in types


def test_go_http_handlers(tmp_path):
    """Detects HTTP handler registrations with router prefix."""
    (tmp_path / "routes.go").write_text('''
package main

func setupRoutes(r *mux.Router) {
    r.HandleFunc("/api/users", handleUsers)
    r.HandleFunc("/api/health", handleHealth)
    r.HandleFunc("/api/data", handleData)
}
''')
    result = detect_patterns(str(tmp_path), language="go")
    types = {p["type"] for p in result["patterns"]}
    assert "http_handlers" in types


def test_go_interfaces(tmp_path):
    """Detects Go interface definitions."""
    (tmp_path / "types.go").write_text('''
package main

type DataSource interface {
    Fetch() []byte
}
''')
    result = detect_patterns(str(tmp_path), language="go")
    types = {p["type"] for p in result["patterns"]}
    assert "interface_impl" in types


def test_python_fastmcp_tools(tmp_path):
    """Detects FastMCP tool registrations with named args."""
    (tmp_path / "server.py").write_text('''
from fastmcp import FastMCP
mcp = FastMCP("test")

@mcp.tool(name="search")
async def search_func(query: str):
    pass

@mcp.tool(name="fetch")
async def fetch_func(url: str):
    pass
''')
    result = detect_patterns(str(tmp_path), language="python")
    types = {p["type"] for p in result["patterns"]}
    assert "mcp_tools" in types


def test_plugin_skills(tmp_path):
    """Detects Claude Code skill directories."""
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "my-skill").mkdir()
    (skills / "other.md").write_text("# Skill")

    result = detect_patterns(str(tmp_path))
    types = {p["type"] for p in result["patterns"]}
    assert "plugin_skills" in types


def test_plugin_hooks(tmp_path):
    """Detects hooks.json registrations."""
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    (hooks / "hooks.json").write_text('[]')

    result = detect_patterns(str(tmp_path))
    types = {p["type"] for p in result["patterns"]}
    assert "plugin_hooks" in types


def test_auto_language_detection(tmp_path):
    """Auto-detect language from project markers."""
    (tmp_path / "go.mod").write_text("module test\n")
    result = detect_patterns(str(tmp_path), language="auto")
    assert result["language"] == "go"


def test_confidence_range(tmp_path):
    """All confidence values are 0.0-1.0."""
    (tmp_path / "tools.go").write_text('''
package tools
import "github.com/mark3labs/mcp-go/mcp"
func init() { mcp.NewTool("x", mcp.WithDescription("y")) }
''')
    result = detect_patterns(str(tmp_path), language="go")
    for p in result["patterns"]:
        assert "confidence" in p
        assert 0.0 <= p["confidence"] <= 1.0


# --- Live monorepo tests (run only when Demarch root exists) ---


@pytest.mark.skipif(
    not os.path.isdir(os.path.join(DEMARCH_ROOT, "core", "intermute")),
    reason="Demarch monorepo not found",
)
def test_live_go_project():
    """intermute should have HTTP handlers or MCP tools."""
    result = detect_patterns(
        os.path.join(DEMARCH_ROOT, "core/intermute"),
        language="go",
    )
    types = {p["type"] for p in result["patterns"]}
    assert "http_handlers" in types or "mcp_tools" in types or "interface_impl" in types
