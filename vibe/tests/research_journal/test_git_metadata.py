"""Tests for git metadata capture module.

Tests focus on:
- Git command execution
- Dirty state detection
- Metadata capture
- Error handling
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from vibe.research_journal.git_metadata import (
    GitNotFoundError,
    capture_execution_metadata,
    get_git_branch,
    get_git_commit_hash,
    get_python_version,
    is_git_dirty,
)
from vibe.research_journal.models import ExecutionMetadata


class TestGetGitCommitHash:
    """P0: get_git_commit_hash should return 40-character hex string."""

    def test_get_git_commit_hash_returns_40_chars(self):
        """Commit hash should be exactly 40 hex characters."""
        commit = get_git_commit_hash()
        assert len(commit) == 40
        assert all(c in "0123456789abcdef" for c in commit.lower())

    def test_get_git_commit_hash_invalid_repo(self):
        """Should raise GitNotFoundError for invalid path."""
        with pytest.raises(GitNotFoundError):
            get_git_commit_hash(Path("/nonexistent/path"))


class TestGetGitBranch:
    """P0: get_git_branch should return current branch name."""

    def test_get_git_branch_returns_string(self):
        """Branch name should be non-empty string."""
        branch = get_git_branch()
        assert isinstance(branch, str)
        assert len(branch) > 0

    def test_get_git_branch_invalid_repo(self):
        """Should raise GitNotFoundError for invalid path."""
        with pytest.raises(GitNotFoundError):
            get_git_branch(Path("/nonexistent/path"))


class TestIsGitDirty:
    """P0: is_git_dirty should detect uncommitted changes."""

    def test_is_git_dirty_returns_bool(self):
        """Should return boolean."""
        dirty = is_git_dirty()
        assert isinstance(dirty, bool)

    def test_is_git_dirty_invalid_repo(self):
        """Should raise GitNotFoundError for invalid path."""
        with pytest.raises(GitNotFoundError):
            is_git_dirty(Path("/nonexistent/path"))


class TestGetPythonVersion:
    """P1: get_python_version should return version string."""

    def test_python_version_format(self):
        """Python version should contain dots (e.g., 3.12.0)."""
        version = get_python_version()
        assert "." in version
        assert version.startswith("3.")


class TestCaptureExecutionMetadata:
    """P0: capture_execution_metadata should populate all fields."""

    def test_capture_execution_metadata_populates_all_fields(self):
        """All ExecutionMetadata fields should be non-null."""
        meta = capture_execution_metadata()
        assert meta.git_commit is not None
        assert meta.git_branch is not None
        assert isinstance(meta.git_dirty, bool)
        assert meta.executed_at is not None
        assert meta.python_version is not None

    def test_capture_execution_metadata_with_seed(self):
        """Random seed should be captured when provided."""
        meta = capture_execution_metadata(random_seed=42)
        assert meta.random_seed == 42

    def test_capture_execution_metadata_datetime_aware(self):
        """Datetime should be timezone-aware."""
        meta = capture_execution_metadata()
        assert meta.executed_at.tzinfo is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
