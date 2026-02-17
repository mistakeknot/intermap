"""Tests for intermap analyze dispatcher."""

from intermap.analyze import dispatch


def test_dispatch_structure():
    result = dispatch(
        "structure",
        "/root/projects/Interverse/plugins/intermap",
        {"language": "python", "max_results": 3},
    )
    assert "files" in result
    assert "root" in result
    assert "language" in result
    assert len(result["files"]) <= 3


def test_dispatch_extract():
    result = dispatch(
        "extract",
        ".",
        {"file": "/root/projects/Interverse/plugins/intermap/python/intermap/protocols.py"},
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
        "/root/projects/Interverse/plugins/intermap",
        {"language": "python"},
    )
    assert "edges" in result
    assert "edge_count" in result
    assert isinstance(result["edges"], list)
