"""Git metadata capture for execution reproducibility.

Captures git state (commit hash, branch, dirty status) at experiment execution
time to enable reproducibility and debugging.
"""

import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from vibe.research_journal.models import ExecutionMetadata


class GitNotFoundError(Exception):
    """Raised when git command fails or repo not found."""

    pass


def get_git_commit_hash(repo_path: Optional[Path] = None) -> str:
    """Get current HEAD commit hash.

    Args:
        repo_path: Path to git repository (defaults to cwd)

    Returns:
        40-character hex commit hash

    Raises:
        GitNotFoundError: If not in git repo or git not available
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError, NotADirectoryError, OSError) as e:
        raise GitNotFoundError(
            f"Failed to get git commit hash (are you in a git repository?): {e}"
        )


def get_git_branch(repo_path: Optional[Path] = None) -> str:
    """Get current branch name.

    Args:
        repo_path: Path to git repository (defaults to cwd)

    Returns:
        Branch name (or "HEAD" if detached)

    Raises:
        GitNotFoundError: If not in git repo or git not available
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError, NotADirectoryError, OSError) as e:
        raise GitNotFoundError(
            f"Failed to get git branch (are you in a git repository?): {e}"
        )


def is_git_dirty(repo_path: Optional[Path] = None) -> bool:
    """Check if working tree has uncommitted changes.

    Args:
        repo_path: Path to git repository (defaults to cwd)

    Returns:
        True if working tree is dirty, False if clean

    Raises:
        GitNotFoundError: If not in git repo or git not available
    """
    try:
        # Check working tree
        result_working = subprocess.run(
            ["git", "diff", "--quiet"],
            cwd=repo_path,
            capture_output=True,
        )

        # Check staged changes
        result_staged = subprocess.run(
            ["git", "diff", "--cached", "--quiet"],
            cwd=repo_path,
            capture_output=True,
        )

        # If either command returns non-zero, there are changes
        return result_working.returncode != 0 or result_staged.returncode != 0
    except (FileNotFoundError, NotADirectoryError, OSError) as e:
        raise GitNotFoundError(f"Git not available: {e}")


def get_python_version() -> str:
    """Get current Python version string.

    Returns:
        Version string (e.g., "3.12.10")
    """
    return f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"


def capture_execution_metadata(
    repo_path: Optional[Path] = None, random_seed: Optional[int] = None
) -> ExecutionMetadata:
    """Capture complete execution metadata for reproducibility.

    Args:
        repo_path: Path to git repository (defaults to cwd)
        random_seed: Optional random seed for deterministic runs

    Returns:
        ExecutionMetadata instance with all fields populated

    Raises:
        GitNotFoundError: If git commands fail
    """
    git_commit = get_git_commit_hash(repo_path)
    git_branch = get_git_branch(repo_path)
    git_dirty = is_git_dirty(repo_path)
    python_version = get_python_version()
    executed_at = datetime.now(timezone.utc)

    if git_dirty:
        import logging
        logger = logging.getLogger(__name__)
        logger.warning(
            f"Git working tree is dirty at execution time. "
            f"Uncommitted changes: commit={git_commit}, branch={git_branch}"
        )

    return ExecutionMetadata(
        git_commit=git_commit,
        git_branch=git_branch,
        git_dirty=git_dirty,
        random_seed=random_seed,
        executed_at=executed_at,
        python_version=python_version,
    )
