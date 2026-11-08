"""Tests for market data directory resolution.

The bug being prevented: ``Path("vibe/data/parquet")`` resolved against the
working directory, so backtests only ran from the repo root and never ran at
all in a worktree, where the gitignored data is absent.
"""

from pathlib import Path

import pytest

from vibe.backtester.data.paths import (
    DEFAULT_DATA_SUBPATH,
    ENV_BACKTEST_DATA_DIR,
    MarketDataNotFoundError,
    available_symbols,
    main_worktree_root,
    repo_root,
    resolve_market_data_dir,
)


@pytest.fixture(autouse=True)
def clear_env(monkeypatch):
    """Keep the developer's own BACKTEST__DATA_DIR out of these assertions."""
    monkeypatch.delenv(ENV_BACKTEST_DATA_DIR, raising=False)


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    d = tmp_path / "parquet"
    d.mkdir()
    (d / "QQQ.parquet").write_bytes(b"")
    return d


class TestRepoRoot:
    def test_finds_a_checkout_root(self):
        assert (repo_root() / ".git").exists()

    def test_is_absolute(self):
        assert repo_root().is_absolute()


class TestPrecedence:
    def test_explicit_absolute_path_wins(self, data_dir):
        assert resolve_market_data_dir(data_dir) == data_dir

    def test_explicit_accepts_string(self, data_dir):
        assert resolve_market_data_dir(str(data_dir)) == data_dir

    def test_env_var_is_used(self, data_dir, monkeypatch):
        monkeypatch.setenv(ENV_BACKTEST_DATA_DIR, str(data_dir))
        assert resolve_market_data_dir() == data_dir

    def test_explicit_beats_env(self, data_dir, tmp_path, monkeypatch):
        other = tmp_path / "other"
        other.mkdir()
        monkeypatch.setenv(ENV_BACKTEST_DATA_DIR, str(other))
        assert resolve_market_data_dir(data_dir) == data_dir

    def test_falls_through_when_explicit_missing(self, tmp_path):
        """A stale explicit path must not strand a caller that has real data."""
        resolved = resolve_market_data_dir(tmp_path / "does-not-exist")
        assert resolved.is_dir()


class TestCwdIndependence:
    """The actual defect: resolution must not depend on where you launched."""

    def test_relative_env_resolves_against_repo_not_cwd(
        self, monkeypatch, tmp_path
    ):
        fake_repo = tmp_path / "repo"
        (fake_repo / "reldata").mkdir(parents=True)
        elsewhere = tmp_path / "elsewhere"
        (elsewhere / "reldata").mkdir(parents=True)

        monkeypatch.setattr(
            "vibe.backtester.data.paths.repo_root", lambda: fake_repo
        )
        monkeypatch.setenv(ENV_BACKTEST_DATA_DIR, "reldata")
        monkeypatch.chdir(elsewhere)

        resolved = resolve_market_data_dir()
        assert resolved == fake_repo / "reldata"
        # The decoy under the working directory must be ignored entirely.
        assert resolved != elsewhere / "reldata"

    def test_same_result_from_any_directory(self, monkeypatch, tmp_path):
        first = resolve_market_data_dir(require_exists=False)
        monkeypatch.chdir(tmp_path)
        assert resolve_market_data_dir(require_exists=False) == first

    def test_missing_env_dir_falls_through_to_real_data(
        self, monkeypatch, tmp_path
    ):
        """A worktree with no local copy still resolves to shared data."""
        monkeypatch.setenv(ENV_BACKTEST_DATA_DIR, str(tmp_path / "absent"))
        assert resolve_market_data_dir(require_exists=False).is_absolute()


class TestWorktreeFallback:
    def test_main_worktree_root_has_a_git_dir_when_present(self):
        main = main_worktree_root()
        if main is None:
            pytest.skip("not running inside a linked worktree")
        assert (main / ".git").is_dir()

    def test_worktree_can_reach_shared_data(self):
        """A worktree must find data it never received a copy of."""
        if main_worktree_root() is None:
            pytest.skip("not running inside a linked worktree")
        resolved = resolve_market_data_dir(require_exists=False)
        assert resolved.is_absolute()


class TestFailure:
    def test_raises_when_nothing_exists(self, monkeypatch, tmp_path):
        monkeypatch.setenv(ENV_BACKTEST_DATA_DIR, str(tmp_path / "nope"))
        monkeypatch.setattr(
            "vibe.backtester.data.paths.repo_root", lambda: tmp_path
        )
        monkeypatch.setattr(
            "vibe.backtester.data.paths.main_worktree_root", lambda: None
        )
        with pytest.raises(MarketDataNotFoundError) as exc:
            resolve_market_data_dir()
        assert ENV_BACKTEST_DATA_DIR in str(exc.value)

    def test_error_lists_every_location_tried(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "vibe.backtester.data.paths.repo_root", lambda: tmp_path
        )
        monkeypatch.setattr(
            "vibe.backtester.data.paths.main_worktree_root", lambda: None
        )
        with pytest.raises(MarketDataNotFoundError) as exc:
            resolve_market_data_dir()
        assert "repository default" in str(exc.value)

    def test_require_exists_false_never_raises(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "vibe.backtester.data.paths.repo_root", lambda: tmp_path
        )
        monkeypatch.setattr(
            "vibe.backtester.data.paths.main_worktree_root", lambda: None
        )
        assert resolve_market_data_dir(require_exists=False).is_absolute()


class TestAvailableSymbols:
    def test_lists_symbols_sorted(self, data_dir):
        (data_dir / "AMZN.parquet").write_bytes(b"")
        assert available_symbols(data_dir) == ["AMZN", "QQQ"]

    def test_empty_directory(self, tmp_path):
        assert available_symbols(tmp_path) == []
