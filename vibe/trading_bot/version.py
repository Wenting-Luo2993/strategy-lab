"""Version information for the trading bot.

Simple version tracking with manual version increments.
Captures build timestamp automatically.
"""

from datetime import datetime


# Manual version - increment this with each deployment
VERSION = "1.0.5"


def get_build_timestamp() -> str:
    """Get current timestamp in UTC.

    Returns:
        ISO format timestamp (UTC)
    """
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")


def get_version_string() -> str:
    """Get full version string with timestamp.

    Returns:
        Version string like: v1.0.1 (2026-02-26 20:45:00 UTC)
    """
    timestamp = get_build_timestamp()
    return f"v{VERSION} ({timestamp})"


def get_version_dict() -> dict:
    """Get version information as dictionary.

    Returns:
        Dictionary with version and timestamp
    """
    return {
        "version": VERSION,
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
