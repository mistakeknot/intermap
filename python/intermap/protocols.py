"""Extraction protocols for intermap.

Defines the FileExtractor protocol that decouples analysis modules from
specific extraction implementations (tree-sitter, hybrid, etc.).
"""

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class FunctionInfo:
    """Lightweight function info for extraction results."""
    name: str
    line_number: int = 0
    params: list[str] = field(default_factory=list)
    return_type: str = ""
    docstring: str = ""
    language: str = ""
    is_method: bool = False
    complexity: int = 0


@dataclass
class ClassInfo:
    """Lightweight class info for extraction results."""
    name: str
    line_number: int = 0
    methods: list[FunctionInfo] = field(default_factory=list)
    bases: list[str] = field(default_factory=list)
    docstring: str = ""


@dataclass
class FileExtractionResult:
    """Result of extracting structure from a source file."""
    functions: list[FunctionInfo] = field(default_factory=list)
    classes: list[ClassInfo] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    language: str = ""

    def to_dict(self) -> dict:
        return {
            "functions": [
                {"name": f.name, "line": f.line_number, "params": f.params}
                for f in self.functions
            ],
            "classes": [
                {"name": c.name, "line": c.line_number, "methods": [m.name for m in c.methods]}
                for c in self.classes
            ],
            "imports": self.imports,
        }


class FileExtractor(Protocol):
    """Protocol for extracting code structure from files."""

    def extract(self, path: str) -> FileExtractionResult:
        """Extract functions, classes, and imports from a source file.

        Returns:
            FileExtractionResult with functions, classes, imports.
        """
        ...
