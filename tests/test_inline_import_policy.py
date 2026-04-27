"""Targeted import-placement checks for high-churn entry points."""

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _nested_imports(path: Path) -> list[ast.Import | ast.ImportFrom]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    parents: dict[ast.AST, ast.AST] = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    imports: list[ast.Import | ast.ImportFrom] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Import | ast.ImportFrom):
            continue
        parent = parents.get(node)
        if not isinstance(parent, ast.Module):
            imports.append(node)
    return imports


def test_cli_app_has_no_inline_imports():
    """The CLI is the user entry point; imports should be visible up front."""
    nested = _nested_imports(ROOT / "keep" / "cli_app.py")

    assert nested == []


def test_api_has_no_inline_imports():
    """The core API should keep imports visible at module level."""
    nested = _nested_imports(ROOT / "keep" / "api.py")

    assert nested == []
