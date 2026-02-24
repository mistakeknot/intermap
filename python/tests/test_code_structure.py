"""Tests for intermap code_structure module."""

import os

from intermap.code_structure import get_code_structure

# Resolve intermap root relative to this test file
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
INTERMAP_ROOT = os.path.normpath(os.path.join(_TESTS_DIR, "../.."))


def test_code_structure_python():
    result = get_code_structure(
        INTERMAP_ROOT,
        language="python",
        max_results=5,
    )
    assert result["language"] == "python"
    assert "files" in result
    assert len(result["files"]) > 0
    assert len(result["files"]) <= 5

    # Each file should have the expected keys
    for file_entry in result["files"]:
        assert "path" in file_entry
        assert "functions" in file_entry
        assert "classes" in file_entry
        assert "imports" in file_entry


def test_code_structure_max_results():
    result = get_code_structure(
        INTERMAP_ROOT,
        language="python",
        max_results=2,
    )
    assert len(result["files"]) <= 2


def test_code_structure_nonexistent_language():
    """Unknown language defaults to .py extensions, so still finds Python files."""
    result = get_code_structure(
        INTERMAP_ROOT,
        language="cobol",
        max_results=10,
    )
    # Falls back to {".py"}, so it finds Python files
    assert result["language"] == "cobol"
    assert isinstance(result["files"], list)
