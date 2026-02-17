"""Tests for intermap extractors."""

import os
import tempfile

from intermap.extractors import DefaultExtractor, PythonASTExtractor, BasicRegexExtractor
from intermap.protocols import FunctionInfo


def test_python_extractor_functions():
    ext = PythonASTExtractor()
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write("def hello(name: str) -> str:\n    return f'Hello {name}'\n\ndef goodbye():\n    pass\n")
        f.flush()
        result = ext.extract(f.name)
    os.unlink(f.name)

    assert len(result.functions) == 2
    assert result.functions[0].name == "hello"
    assert result.functions[0].params == ["name"]
    assert result.functions[1].name == "goodbye"
    assert result.language == "python"


def test_python_extractor_classes():
    ext = PythonASTExtractor()
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write(
            'class Foo:\n    """A foo."""\n'
            "    def bar(self):\n        pass\n"
            "    def baz(self, x):\n        pass\n"
        )
        f.flush()
        result = ext.extract(f.name)
    os.unlink(f.name)

    assert len(result.classes) == 1
    assert result.classes[0].name == "Foo"
    assert result.classes[0].docstring == "A foo."
    assert len(result.classes[0].methods) == 2
    assert result.classes[0].methods[0].name == "bar"
    assert result.classes[0].methods[0].is_method is True
    # Functions list should NOT contain class methods (no double-counting)
    assert len(result.functions) == 0


def test_python_extractor_imports():
    ext = PythonASTExtractor()
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write("import os\nimport sys\nfrom pathlib import Path\nfrom typing import List, Dict\n")
        f.flush()
        result = ext.extract(f.name)
    os.unlink(f.name)

    assert "os" in result.imports
    assert "sys" in result.imports
    assert "pathlib" in result.imports
    assert "typing" in result.imports


def test_python_extractor_syntax_error():
    ext = PythonASTExtractor()
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write("def broken(:\n    pass\n")
        f.flush()
        result = ext.extract(f.name)
    os.unlink(f.name)

    assert result.functions == []
    assert result.classes == []
    assert result.language == "python"


def test_regex_extractor_go():
    ext = BasicRegexExtractor()
    with tempfile.NamedTemporaryFile(mode="w", suffix=".go", delete=False) as f:
        f.write("package main\n\nfunc main() {\n}\n\nfunc helper(x int) string {\n}\n")
        f.flush()
        result = ext.extract(f.name)
    os.unlink(f.name)

    assert len(result.functions) == 2
    assert result.functions[0].name == "main"
    assert result.functions[1].name == "helper"


def test_default_extractor_routes():
    ext = DefaultExtractor()
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write("def foo():\n    pass\n")
        f.flush()
        result = ext.extract(f.name)
    os.unlink(f.name)

    assert len(result.functions) == 1
    assert result.functions[0].name == "foo"
    assert result.language == "python"


def test_file_extraction_result_to_dict():
    ext = DefaultExtractor()
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
        f.write("import os\n\ndef greet(name):\n    pass\n\nclass Animal:\n    def speak(self):\n        pass\n")
        f.flush()
        result = ext.extract(f.name)
    os.unlink(f.name)

    d = result.to_dict()
    assert "functions" in d
    assert "classes" in d
    assert "imports" in d
    assert d["functions"][0]["name"] == "greet"
    assert d["classes"][0]["name"] == "Animal"
    assert "os" in d["imports"]
