"""Tests for cross-project dependency detection."""

import os
import json

import pytest

from intermap.cross_project import scan_cross_project_deps


# Resolve Demarch root relative to this test file
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
DEMARCH_ROOT = os.environ.get(
    "DEMARCH_ROOT",
    os.path.normpath(os.path.join(_TESTS_DIR, "../../../..")),
)


# --- Fixture-based tests (synthetic repos, always reliable) ---


def test_output_structure(tmp_path):
    """Output has required fields."""
    # Create a minimal project structure
    group = tmp_path / "interverse"
    group.mkdir()
    proj = group / "testproj"
    proj.mkdir()
    (proj / ".git").mkdir()
    (proj / "go.mod").write_text("module example.com/testproj\n")

    result = scan_cross_project_deps(str(tmp_path))
    assert "projects" in result
    assert "total_projects" in result
    assert "total_edges" in result
    assert result["total_projects"] == 1
    for p in result["projects"]:
        assert "project" in p
        assert "depends_on" in p
        for dep in p["depends_on"]:
            assert "project" in dep
            assert "type" in dep
            assert "via" in dep


def test_go_module_replace(tmp_path):
    """Detects go.mod replace directives."""
    group = tmp_path / "core"
    group.mkdir()
    proj_a = group / "alpha"
    proj_a.mkdir()
    (proj_a / ".git").mkdir()
    (proj_a / "go.mod").write_text(
        "module example.com/alpha\n\n"
        "replace example.com/beta => ../beta\n"
    )
    proj_b = group / "beta"
    proj_b.mkdir()
    (proj_b / ".git").mkdir()

    result = scan_cross_project_deps(str(tmp_path))
    projects = {p["project"]: p for p in result["projects"]}
    assert "alpha" in projects
    dep_names = [d["project"] for d in projects["alpha"]["depends_on"]]
    assert "beta" in dep_names


def test_go_module_block_replace(tmp_path):
    """Detects block-form go.mod replace directives (amendment #3)."""
    group = tmp_path / "core"
    group.mkdir()
    proj_a = group / "alpha"
    proj_a.mkdir()
    (proj_a / ".git").mkdir()
    (proj_a / "go.mod").write_text(
        "module example.com/alpha\n\n"
        "replace (\n"
        "\texample.com/beta => ../beta\n"
        "\texample.com/gamma => ../gamma\n"
        ")\n"
    )
    for name in ("beta", "gamma"):
        p = group / name
        p.mkdir()
        (p / ".git").mkdir()

    result = scan_cross_project_deps(str(tmp_path))
    projects = {p["project"]: p for p in result["projects"]}
    dep_names = [d["project"] for d in projects["alpha"]["depends_on"]]
    assert "beta" in dep_names
    assert "gamma" in dep_names


def test_python_path_deps(tmp_path):
    """Detects pyproject.toml path dependencies."""
    group = tmp_path / "interverse"
    group.mkdir()
    proj_a = group / "alpha"
    proj_a.mkdir()
    (proj_a / ".git").mkdir()
    (proj_a / "pyproject.toml").write_text(
        '[tool.poetry.dependencies]\n'
        'beta-pkg = {path = "../beta"}\n'
    )
    proj_b = group / "beta"
    proj_b.mkdir()
    (proj_b / ".git").mkdir()

    result = scan_cross_project_deps(str(tmp_path))
    projects = {p["project"]: p for p in result["projects"]}
    dep_names = [d["project"] for d in projects["alpha"]["depends_on"]]
    assert "beta" in dep_names


def test_plugin_intermute_ref(tmp_path):
    """Detects INTERMUTE env var references."""
    group = tmp_path / "interverse"
    group.mkdir()
    proj = group / "myplug"
    proj.mkdir()
    (proj / ".git").mkdir()
    plugin_dir = proj / ".claude-plugin"
    plugin_dir.mkdir()
    (plugin_dir / "plugin.json").write_text(json.dumps({
        "mcpServers": {
            "myserver": {
                "env": {"INTERMUTE_URL": "http://localhost:3000"}
            }
        }
    }))
    # Need intermute to exist as a project
    intermute = group / "intermute"
    intermute.mkdir()
    (intermute / ".git").mkdir()

    result = scan_cross_project_deps(str(tmp_path))
    projects = {p["project"]: p for p in result["projects"]}
    dep_names = [d["project"] for d in projects["myplug"]["depends_on"]]
    assert "intermute" in dep_names


def test_skips_dirs_without_git(tmp_path):
    """Only includes directories with .git marker (amendment #9)."""
    group = tmp_path / "interverse"
    group.mkdir()
    proj = group / "has_git"
    proj.mkdir()
    (proj / ".git").mkdir()
    no_git = group / "no_git"
    no_git.mkdir()

    result = scan_cross_project_deps(str(tmp_path))
    names = [p["project"] for p in result["projects"]]
    assert "has_git" in names
    assert "no_git" not in names


# --- Live monorepo test (runs only when Demarch root exists) ---


@pytest.mark.skipif(
    not os.path.isdir(os.path.join(DEMARCH_ROOT, "interverse")),
    reason="Demarch monorepo not found",
)
def test_live_monorepo():
    """Smoke test against real monorepo."""
    result = scan_cross_project_deps(DEMARCH_ROOT)
    assert result["total_projects"] > 5
    projects = {p["project"]: p for p in result["projects"]}
    assert "intermap" in projects
