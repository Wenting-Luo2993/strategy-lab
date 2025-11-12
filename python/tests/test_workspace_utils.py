import os
from pathlib import Path

from src.utils.workspace import get_workspace_root, resolve_workspace_path


def test_get_workspace_root_detects_python_folder():
    root = get_workspace_root()
    assert root.name == "python", f"Expected root name 'python', got {root}"  # basic invariant
    assert (root / "src").exists(), "Root should contain src directory"


def test_resolve_workspace_path_absolute_round_trip():
    root = get_workspace_root()
    abs_path = root / "data_cache"
    resolved = resolve_workspace_path(abs_path)
    assert resolved == abs_path.resolve()


def test_resolve_workspace_path_relative():
    rel = "data_cache/subdir"
    resolved = resolve_workspace_path(rel)
    root = get_workspace_root()
    assert str(resolved).startswith(str(root)), "Resolved path should start with workspace root"
    assert "data_cache" in resolved.parts


def test_resolve_workspace_path_idempotent_for_absolute():
    root = get_workspace_root()
    target = root / "tests"
    first = resolve_workspace_path(target)
    second = resolve_workspace_path(first)
    assert first == second
