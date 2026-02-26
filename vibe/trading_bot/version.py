"""Version information for the trading bot.

Automatically captures git commit hash and build timestamp.
"""

import subprocess
from datetime import datetime
from typing import Optional


# Semantic version (update manually for releases)
SEMANTIC_VERSION = "1.0.0"


def get_git_commit_hash() -> str:
    """Get current git commit hash (short form).

    Returns:
        7-character commit hash, or 'unknown' if not in git repo
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short=7", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
            check=True
        )
        return result.stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        return "unknown"


def get_build_timestamp() -> str:
    """Get current timestamp in UTC.

    Returns:
        ISO format timestamp (UTC)
    """
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")


def get_version_string() -> str:
    """Get full version string with commit hash and timestamp.

    Returns:
        Version string like: v1.0.0-7789ecc (2026-02-26 20:28:50 UTC)
    """
    commit = get_git_commit_hash()
    timestamp = get_build_timestamp()
    return f"v{SEMANTIC_VERSION}-{commit} ({timestamp})"


def get_version_dict() -> dict:
    """Get version information as dictionary.

    Returns:
        Dictionary with version, commit, and timestamp
    """
    return {
        "version": SEMANTIC_VERSION,
        "commit": get_git_commit_hash(),
        "build_time": get_build_timestamp(),
        "full_version": get_version_string()
    }


# Build-time version (captured when module is imported)
BUILD_VERSION = get_version_string()
BUILD_INFO = get_version_dict()


if __name__ == "__main__":
    # Test version output
    print(f"Trading Bot Version: {BUILD_VERSION}")
    print(f"\nVersion Info:")
    for key, value in BUILD_INFO.items():
        print(f"  {key}: {value}")
