"""Centralized path constants and helpers for fixtures and snapshots.

This module consolidates directory definitions used by tests and data
extraction scripts to avoid duplication and ease future refactors.
"""
from __future__ import annotations
import os
from pathlib import Path

from src.utils.workspace import resolve_workspace_path

# Relative directories (workspace-root relative) for deterministic artifacts
SNAPSHOTS_REL_DIR = "tests/__snapshots__"
SCENARIOS_REL_DIR = "tests/__scenarios__"


def _resolve_with_env(rel_path: str, env_var: str) -> Path:
    """Resolve a workspace-relative path with optional environment override.

    If the environment variable named by ``env_var`` is set, that value is
    used directly (expanded & resolved). Otherwise the relative path is
    resolved via ``resolve_workspace_path``.
    """
    override = os.getenv(env_var)
    target = Path(override) if override else resolve_workspace_path(rel_path)
    return target.resolve()


def get_snapshot_root() -> Path:
    """Return absolute path to snapshot storage directory.

    Environment variable ``SNAPSHOT_ROOT`` may override the default relative
    directory defined by ``SNAPSHOTS_REL_DIR``.
    """
    return _resolve_with_env(SNAPSHOTS_REL_DIR, "SNAPSHOT_ROOT")


def get_scenarios_root() -> Path:
    """Return absolute path to deterministic market data fixture scenarios.

    Environment variable ``SCENARIOS_ROOT`` may override the default relative
    directory defined by ``SCENARIOS_REL_DIR``.
    """
    return _resolve_with_env(SCENARIOS_REL_DIR, "SCENARIOS_ROOT")


def get_fixture_name(start_date: str, end_date: str | None = None) -> str:
    """Generate fixture directory name from date range.

    Naming convention:
      Single day: YYYY-MM-DD
      Date range: YYYY-MM-DD_YYYY-MM-DD

    Args:
        start_date: Start date in YYYY-MM-DD format.
        end_date: Optional end date. If None or equals start_date, single day format.

    Returns:
        Fixture directory name string.
    """
    if not end_date or end_date == start_date:
        return start_date
    return f"{start_date}_{end_date}"
