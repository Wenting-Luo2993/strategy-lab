"""Workspace path utilities.

Provides helpers to determine the python workspace root directory (the top-level
`python/` folder of this project) from any module nested within it, and to
resolve relative paths against that root.

Rationale:
  Several components (e.g. caching, replay, enrichment scripts) need to anchor
  user-provided relative paths consistently. Previously each module duplicated
  logic like `Path(__file__).resolve().parents[2]` which is brittle if the
  structure changes. Centralizing this logic ensures consistency and makes it
  testable.

Detection Strategy:
  1. If a parent directory is literally named 'python', we treat that as the
     workspace root.
  2. Fallback: We look for common marker files/dirs (e.g. requirements.txt, src)
     while traversing upwards.
  3. If neither is found we return the highest directory reached (safety net).

Public API:
  get_workspace_root(start_path: Optional[Union[str, Path]] = None) -> Path
      Determine and cache the python workspace root.
  resolve_workspace_path(path: Union[str, Path], start: Optional[Union[str, Path]] = None) -> Path
      Resolve a potentially relative path against the workspace root.

Edge Cases / Guarantees:
  * Always returns an absolute Path.
  * Never raises on traversal failure; returns best-effort parent.
  * Thread-safe due to internal caching (no mutation).

Example:
  >>> from src.utils.workspace import resolve_workspace_path
  >>> resolve_workspace_path('data_cache')  # -> /abs/.../python/data_cache

"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Iterable, Optional, Sequence, Union

_MARKER_FILES: Sequence[str] = ("requirements.txt",)
_MARKER_DIRS: Sequence[str] = ("src", "tests")


def _has_markers(d: Path) -> bool:
    """Internal helper: check if directory contains any marker file/dir."""
    for f in _MARKER_FILES:
        if (d / f).exists():
            return True
    for sub in _MARKER_DIRS:
        if (d / sub).exists():
            return True
    return False


@lru_cache(maxsize=1)
def get_workspace_root(start_path: Optional[Union[str, Path]] = None) -> Path:
    """Return the python workspace root directory.

    Traverses parent directories starting from `start_path` (or this file) until
    a directory named 'python' is found OR marker heuristics succeed. Caches the
    result for subsequent calls.

    Args:
        start_path: Optional starting path (file or directory). When omitted we
                    use this module's file location.
    Returns:
        Absolute Path representing workspace root.
    """
    if start_path is None:
        current = Path(__file__).resolve()
    else:
        current = Path(start_path).resolve()
    if current.is_file():
        current = current.parent

    for parent in [current] + list(current.parents):
        # Explicit directory name match
        if parent.name == "python":
            return parent
        # Marker heuristic
        if _has_markers(parent):
            # If this parent itself contains 'python' as a child, prefer that
            python_child = parent / "python"
            if python_child.exists() and python_child.is_dir():
                return python_child.resolve()
            return parent
    # Fallback to top-most traversed directory
    return current.parents[-1]


def resolve_workspace_path(path: Union[str, Path], start: Optional[Union[str, Path]] = None) -> Path:
    """Resolve a path against the workspace root if it's relative.

    Args:
        path: Relative or absolute path.
        start: Optional start path for workspace root detection.
    Returns:
        Absolute Path.
    """
    p = Path(path)
    if p.is_absolute():
        return p.resolve()
    root = get_workspace_root(start)
    return (root / p).resolve()

__all__ = ["get_workspace_root", "resolve_workspace_path"]
