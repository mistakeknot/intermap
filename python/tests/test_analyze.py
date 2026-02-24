"""Tests for intermap analyze dispatcher."""

import os

from intermap.analyze import dispatch

# Resolve intermap root relative to this test file
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
INTERMAP_ROOT = os.path.normpath(os.path.join(_TESTS_DIR, "../.."))


def test_dispatch_structure():
    result = dispatch(
        "structure",
        INTERMAP_ROOT,
        {"language": "python", "max_results": 3},
    )
    assert "files" in result
    assert "root" in result
    assert "language" in result
    assert len(result["files"]) <= 3


def test_dispatch_extract():
    protocols_path = os.path.join(INTERMAP_ROOT, "python/intermap/protocols.py")
    result = dispatch(
        "extract",
        ".",
        {"file": protocols_path},
    )
    assert "functions" in result
    assert "classes" in result
    assert "imports" in result
    # Should find FunctionInfo, ClassInfo, etc.
    class_names = [c["name"] for c in result["classes"]]
    assert "FunctionInfo" in class_names


def test_dispatch_unknown_command():
    result = dispatch("nonexistent", ".", {})
    assert result.get("error") == "UnknownCommand"


def test_dispatch_call_graph():
    result = dispatch(
        "call_graph",
        INTERMAP_ROOT,
        {"language": "python"},
    )
    assert "edges" in result
    assert "edge_count" in result
    assert isinstance(result["edges"], list)
