"""Filesystem locations for the research store.

The repository lives inside a OneDrive-synced folder. A SQLite database placed
under the repo would be continuously synced while open, which risks corruption
(OneDrive copying a file mid-write, or resurrecting a stale ``-wal``) and would
also push large binary churn through sync.

The research database therefore lives outside the synced tree, under the
platform's local application data directory. Sharing across devices happens via
Supabase, not via file sync.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

__all__ = [
    "APP_DIR_NAME",
    "DB_FILENAME",
    "ENV_RESEARCH_DB_PATH",
    "ENV_RESEARCH_DATA_DIR",
    "UnsafeDatabaseLocationError",
    "default_data_dir",
    "research_db_path",
    "assert_safe_db_location",
]

APP_DIR_NAME = "strategy-lab"
DB_FILENAME = "research.sqlite3"

ENV_RESEARCH_DB_PATH = "RESEARCH__DB_PATH"
ENV_RESEARCH_DATA_DIR = "RESEARCH__DATA_DIR"

# Directory names that indicate a cloud-sync root. Matched case-insensitively
# against every parent component of a candidate path.
_SYNC_MARKERS = (
    "onedrive",
    "dropbox",
    "google drive",
    "googledrive",
    "icloud drive",
    "icloud~",
    "box sync",
    "creative cloud files",
)


class UnsafeDatabaseLocationError(RuntimeError):
    """Raised when the research database would land inside a synced folder."""


def default_data_dir() -> Path:
    """Return the per-user directory that holds research state.

    Honours ``RESEARCH__DATA_DIR`` when set, otherwise falls back to the
    platform convention:
      * Windows: ``%LOCALAPPDATA%\\strategy-lab``
      * macOS:   ``~/Library/Application Support/strategy-lab``
      * Linux:   ``$XDG_DATA_HOME/strategy-lab`` or ``~/.local/share/strategy-lab``
    """
    override = os.environ.get(ENV_RESEARCH_DATA_DIR)
    if override:
        return Path(override).expanduser()

    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
        root = Path(base) if base else Path.home() / "AppData" / "Local"
        return root / APP_DIR_NAME

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_DIR_NAME

    xdg = os.environ.get("XDG_DATA_HOME")
    root = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return root / APP_DIR_NAME


def assert_safe_db_location(path: Path) -> None:
    """Reject database paths inside a known cloud-sync directory.

    This is a loud failure by design. A silently synced SQLite file produces
    intermittent, unreproducible corruption that would be blamed on the
    pipeline for weeks before anyone suspects the filesystem.
    """
    parts = [part.lower() for part in path.parts]
    for part in parts:
        for marker in _SYNC_MARKERS:
            if marker in part:
                raise UnsafeDatabaseLocationError(
                    f"Refusing to open the research database at {path}: the "
                    f"path component {part!r} indicates a cloud-synced folder. "
                    f"Sync corrupts open SQLite files. Set "
                    f"{ENV_RESEARCH_DB_PATH} or {ENV_RESEARCH_DATA_DIR} to a "
                    f"local, unsynced location. Cross-device sharing is handled "
                    f"by the Supabase publisher, not by file sync."
                )


def research_db_path(*, create_parents: bool = False) -> Path:
    """Resolve the research SQLite database path.

    ``RESEARCH__DB_PATH`` overrides the full path; otherwise the file sits in
    :func:`default_data_dir`. The result is always validated against
    :func:`assert_safe_db_location`, including explicit overrides -- an override
    is not a licence to put the database somewhere unsafe.
    """
    override = os.environ.get(ENV_RESEARCH_DB_PATH)
    path = (
        Path(override).expanduser()
        if override
        else default_data_dir() / DB_FILENAME
    )
    path = path if path.is_absolute() else path.resolve()

    assert_safe_db_location(path)

    if create_parents:
        path.parent.mkdir(parents=True, exist_ok=True)
    return path
