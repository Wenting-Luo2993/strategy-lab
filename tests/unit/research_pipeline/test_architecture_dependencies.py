from __future__ import annotations

import ast
from pathlib import Path


def test_backtester_does_not_import_research_layers():
    violations = []
    for path in Path("vibe/backtester").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            if any(
                name.startswith(("vibe.research_pipeline", "vibe.research_journal"))
                for name in names
            ):
                violations.append(f"{path}:{node.lineno}")
    assert violations == []


def test_legacy_fixed_parameter_walk_forward_is_deleted():
    assert not Path("vibe/backtester/analysis/walk_forward.py").exists()
    assert not Path("vibe/backtester/optimization/pipeline.py").exists()
