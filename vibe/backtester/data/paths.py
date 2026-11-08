"""Locating the market data directory.

Backtest code previously hardcoded ``Path("vibe/data/parquet")`` in roughly ten
places. That path is resolved against the *current working directory*, so it
only ever worked when a command happened to be launched from the repository
root.

It also fails completely in a git worktree. ``data/`` is gitignored, so the
parquet files exist only in the checkout that downloaded them (normally the main
one). A fresh worktree therefore starts with no market data at all, and the
failure surfaces as a bare ``FileNotFoundError`` deep inside pandas rather than
as a statement about configuration.

Resolution order, first match wins:

1. An explicit argument passed by the caller.
2. ``BACKTEST__DATA_DIR``. Absolute paths are used as-is; relative paths are
   resolved against the repository root, never the working directory.
3. ``<repo root>/vibe/data/parquet`` -- the historical default.
4. ``<main worktree>/vibe/data/parquet`` -- so worktrees share one copy of data
   that git deliberately does not track.
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = [
    "DEFAULT_DATA_SUBPATH",
    "ENV_BACKTEST_DATA_DIR",
    "MarketDataNotFoundError",
    "available_symbols",
    "main_worktree_root",
    "repo_root",
    "resolve_market_data_dir",
]

ENV_BACKTEST_DATA_DIR = "BACKTEST__DATA_DIR"

# Relative to a checkout root.
DEFAULT_DATA_SUBPATH = Path("vibe") / "data" / "parquet"


class MarketDataNotFoundError(FileNotFoundError):
    """Raised when no candidate market data directory exists.

    Carries every location that was tried, because the usual cause is a
    worktree that never received the gitignored data rather than a typo.
    """

    def __init__(self, candidates: list[tuple[str, Path]]) -> None:
        self.candidates = candidates
        lines = "\n".join(f"  - {origin}: {path}" for origin, path in candidates)
        super().__init__(
            "No market data directory found. Tried:\n"
            f"{lines}\n\n"
            f"Market data is gitignored, so a new worktree does not receive a "
            f"copy. Point {ENV_BACKTEST_DATA_DIR} at the directory holding the "
            f"parquet files (an absolute path is fine), or pass data_dir "
            f"explicitly."
        )


def repo_root() -> Path:
    """Return the root of the checkout containing this file.

    Walks up looking for ``.git``, which is a directory in a normal clone and a
    file in a linked worktree. Falls back to the package layout so that an
    installed copy without a ``.git`` entry still resolves.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / ".git").exists():
            return parent
    # vibe/backtester/data/paths.py -> repo root is four levels up.
    return here.parents[3]


def main_worktree_root() -> Path | None:
    """Return the main checkout's root when called from a linked worktree.

    In a linked worktree ``.git`` is a file containing::

        gitdir: C:/repo/.git/worktrees/<name>

    The shared git directory is therefore two levels above that, and its parent
    is the main checkout. Parsed directly instead of shelling out to git, which
    keeps this usable in test and import paths where a subprocess is unwelcome.
    Returns ``None`` when not in a worktree or the pointer is unrecognisable.
    """
    git_entry = repo_root() / ".git"
    if not git_entry.is_file():
        return None

    try:
        content = git_entry.read_text(encoding="utf-8").strip()
    except OSError:
        return None

    if not content.startswith("gitdir:"):
        return None

    gitdir = Path(content[len("gitdir:"):].strip())
    # .../.git/worktrees/<name> -> .../.git -> repo root
    if gitdir.parent.name != "worktrees":
        return None

    candidate = gitdir.parent.parent.parent
    return candidate if candidate.is_dir() else None


def _candidates(explicit: Path | str | None) -> list[tuple[str, Path]]:
    found: list[tuple[str, Path]] = []
    root = repo_root()

    if explicit is not None:
        path = Path(explicit).expanduser()
        found.append((
            "explicit data_dir argument",
            path if path.is_absolute() else (root / path),
        ))

    env = os.environ.get(ENV_BACKTEST_DATA_DIR)
    if env:
        path = Path(env).expanduser()
        found.append((
            f"{ENV_BACKTEST_DATA_DIR} environment variable",
            # Relative env values resolve against the repo, not the shell's cwd,
            # so behaviour does not depend on where a command was launched.
            path if path.is_absolute() else (root / path),
        ))

    found.append(("repository default", root / DEFAULT_DATA_SUBPATH))

    main_root = main_worktree_root()
    if main_root is not None:
        found.append((
            "main worktree (data is gitignored and not copied per worktree)",
            main_root / DEFAULT_DATA_SUBPATH,
        ))

    return found


def resolve_market_data_dir(
    explicit: Path | str | None = None, *, require_exists: bool = True
) -> Path:
    """Return the directory holding ``<SYMBOL>.parquet`` market data files.

    Args:
        explicit: Caller-supplied location. Absolute paths win outright;
            relative paths resolve against the repository root.
        require_exists: When ``True`` (default), raise
            :class:`MarketDataNotFoundError` if no candidate exists. When
            ``False``, fall back to the highest-priority candidate instead of
            raising, so callers such as test guards can report an intended
            location without the filesystem having to agree.

    Raises:
        MarketDataNotFoundError: No candidate directory exists and
            ``require_exists`` is True.
    """
    candidates = _candidates(explicit)

    for _origin, path in candidates:
        if path.is_dir():
            return path

    if not require_exists:
        return candidates[0][1]

    raise MarketDataNotFoundError(candidates)


def available_symbols(data_dir: Path | str | None = None) -> list[str]:
    """Return the symbols with parquet files present, sorted.

    Lets a caller fail with "TSLA is not available; have AMZN, GOOGL, MSFT,
    QQQ" instead of a missing-file traceback.
    """
    resolved = resolve_market_data_dir(data_dir)
    return sorted(p.stem for p in resolved.glob("*.parquet"))
