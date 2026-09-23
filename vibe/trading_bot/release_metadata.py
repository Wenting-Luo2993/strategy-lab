"""Immutable release metadata attached to remote operational records."""

from __future__ import annotations

import os
import subprocess
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Dict

from vibe.trading_bot.version import VERSION


_DEPLOYMENT_ID = os.getenv("TRADING_BOT_DEPLOYMENT_ID") or str(uuid.uuid4())


@lru_cache(maxsize=1)
def release_metadata() -> Dict[str, str]:
    """Return stable code and deployment identity for this process."""
    return {
        "code_version": VERSION,
        "git_commit": _git_commit(),
        "deployment_id": _DEPLOYMENT_ID,
    }


def _git_commit() -> str:
    configured = os.getenv("TRADING_BOT_GIT_SHA")
    if configured:
        return configured
    try:
        repository_root = Path(__file__).resolve().parents[2]
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=repository_root,
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=2,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"
