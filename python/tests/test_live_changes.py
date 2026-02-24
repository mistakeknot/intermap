"""Tests for live change awareness."""

import os
import subprocess

import pytest

from intermap.live_changes import get_live_changes


# Resolve intermap root relative to this test file
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
INTERMAP_ROOT = os.path.normpath(os.path.join(_TESTS_DIR, "../.."))


# --- Fixture-based tests (synthetic git repos) ---


def _init_git_repo(path):
    """Initialize a git repo with an initial commit."""
    subprocess.run(["git", "init"], cwd=str(path), capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=str(path), capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(path), capture_output=True,
    )


def test_output_structure(tmp_path):
    """Output has required fields."""
    _init_git_repo(tmp_path)
    (tmp_path / "hello.py").write_text("def hello():\n    pass\n")
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(tmp_path), capture_output=True,
    )

    result = get_live_changes(str(tmp_path), baseline="HEAD")
    assert "project" in result
    assert "baseline" in result
    assert "changes" in result
    assert isinstance(result["changes"], list)
    assert "total_files" in result
    assert "total_symbols_affected" in result


def test_detects_modified_file(tmp_path):
    """Detects a file modified since baseline."""
    _init_git_repo(tmp_path)
    f = tmp_path / "main.py"
    f.write_text("def original():\n    pass\n")
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(tmp_path), capture_output=True,
    )
    f.write_text("def original():\n    return 42\n\ndef added():\n    pass\n")

    result = get_live_changes(str(tmp_path), baseline="HEAD")
    assert result["total_files"] == 1
    assert result["changes"][0]["file"] == "main.py"
    assert result["changes"][0]["status"] == "modified"


def test_detects_added_file(tmp_path):
    """Detects a newly added file."""
    _init_git_repo(tmp_path)
    (tmp_path / "old.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(tmp_path), capture_output=True,
    )
    (tmp_path / "new.py").write_text("def new_func():\n    pass\n")
    subprocess.run(["git", "add", "new.py"], cwd=str(tmp_path), capture_output=True)

    result = get_live_changes(str(tmp_path), baseline="HEAD")
    files = {c["file"] for c in result["changes"]}
    assert "new.py" in files


def test_symbol_annotation(tmp_path):
    """Changed function definition line should appear in symbols_affected."""
    _init_git_repo(tmp_path)
    f = tmp_path / "module.py"
    f.write_text("def foo():\n    return 1\n\ndef bar():\n    return 2\n")
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(tmp_path), capture_output=True,
    )
    # Modify the file — add a new function after bar
    f.write_text(
        "def foo():\n    return 1\n\ndef bar():\n    return 2\n\n"
        "def baz():\n    return 3\n"
    )

    result = get_live_changes(str(tmp_path), baseline="HEAD")
    change = result["changes"][0]
    symbol_names = [s["name"] for s in change["symbols_affected"]]
    # baz is new — its def line should be in the changed lines
    assert "baz" in symbol_names


def test_no_changes(tmp_path):
    """No changes returns empty list."""
    _init_git_repo(tmp_path)
    (tmp_path / "stable.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(tmp_path), capture_output=True,
    )

    result = get_live_changes(str(tmp_path), baseline="HEAD")
    assert result["total_files"] == 0
    assert result["changes"] == []


def test_change_fields(tmp_path):
    """Each change has required fields."""
    _init_git_repo(tmp_path)
    f = tmp_path / "a.py"
    f.write_text("x = 1\n")
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(tmp_path), capture_output=True,
    )
    f.write_text("x = 2\n")

    result = get_live_changes(str(tmp_path), baseline="HEAD")
    for change in result["changes"]:
        assert "file" in change
        assert "status" in change
        assert "symbols_affected" in change
