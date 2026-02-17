"""Default file extractor using Python AST (stdlib, no tree-sitter required).

Falls back to basic regex parsing for non-Python files.
Tree-sitter extractors can be plugged in via the FileExtractor protocol.
"""

import ast
import re
from pathlib import Path

from .protocols import ClassInfo, FileExtractionResult, FunctionInfo


class PythonASTExtractor:
    """Extract structure from Python files using stdlib ast module."""

    def extract(self, path: str) -> FileExtractionResult:
        source = Path(path).read_text(errors="replace")
        try:
            tree = ast.parse(source, filename=path)
        except SyntaxError:
            return FileExtractionResult(language="python")

        functions = []
        classes = []
        imports = []

        # Only iterate top-level statements to avoid double-counting methods
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions.append(FunctionInfo(
                    name=node.name,
                    line_number=node.lineno,
                    params=[arg.arg for arg in node.args.args if arg.arg != "self"],
                    docstring=ast.get_docstring(node) or "",
                    language="python",
                ))
            elif isinstance(node, ast.ClassDef):
                methods = []
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        methods.append(FunctionInfo(
                            name=item.name,
                            line_number=item.lineno,
                            params=[arg.arg for arg in item.args.args if arg.arg != "self"],
                            docstring=ast.get_docstring(item) or "",
                            language="python",
                            is_method=True,
                        ))
                classes.append(ClassInfo(
                    name=node.name,
                    line_number=node.lineno,
                    methods=methods,
                    bases=[_name_from_node(b) for b in node.bases],
                    docstring=ast.get_docstring(node) or "",
                ))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.append(node.module)

        return FileExtractionResult(
            functions=functions,
            classes=classes,
            imports=imports,
            language="python",
        )


class BasicRegexExtractor:
    """Fallback extractor for non-Python files using regex patterns."""

    # Language-specific function patterns
    PATTERNS = {
        ".go": re.compile(r"^func\s+(?:\([^)]+\)\s+)?(\w+)\s*\(", re.MULTILINE),
        ".ts": re.compile(r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)", re.MULTILINE),
        ".tsx": re.compile(r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)", re.MULTILINE),
        ".js": re.compile(r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)", re.MULTILINE),
        ".rs": re.compile(r"^(?:pub\s+)?fn\s+(\w+)", re.MULTILINE),
    }

    IMPORT_PATTERNS = {
        ".go": re.compile(r'^\s*"([^"]+)"', re.MULTILINE),
        ".ts": re.compile(r"^import\s+.*from\s+['\"]([^'\"]+)['\"]", re.MULTILINE),
        ".tsx": re.compile(r"^import\s+.*from\s+['\"]([^'\"]+)['\"]", re.MULTILINE),
        ".js": re.compile(r"^(?:import|require)\s*.*['\"]([^'\"]+)['\"]", re.MULTILINE),
        ".rs": re.compile(r"^use\s+([\w:]+)", re.MULTILINE),
    }

    def extract(self, path: str) -> FileExtractionResult:
        p = Path(path)
        ext = p.suffix.lower()
        source = p.read_text(errors="replace")

        functions = []
        func_pattern = self.PATTERNS.get(ext)
        if func_pattern:
            for i, match in enumerate(func_pattern.finditer(source)):
                line = source[:match.start()].count("\n") + 1
                functions.append(FunctionInfo(name=match.group(1), line_number=line))

        imports = []
        import_pattern = self.IMPORT_PATTERNS.get(ext)
        if import_pattern:
            imports = [m.group(1) for m in import_pattern.finditer(source)]

        return FileExtractionResult(functions=functions, imports=imports)


class DefaultExtractor:
    """Routes to the appropriate extractor based on file extension."""

    def __init__(self):
        self._python = PythonASTExtractor()
        self._regex = BasicRegexExtractor()

    def extract(self, path: str) -> FileExtractionResult:
        ext = Path(path).suffix.lower()
        if ext == ".py":
            return self._python.extract(path)
        return self._regex.extract(path)


def _name_from_node(node) -> str:
    """Extract name string from an AST node."""
    if isinstance(node, ast.Name):
        return node.id
    elif isinstance(node, ast.Attribute):
        return f"{_name_from_node(node.value)}.{node.attr}"
    return ""
