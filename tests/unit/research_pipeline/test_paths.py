"""Tests for research database path safety.

The repository lives inside a OneDrive-synced folder. Sync can corrupt an open
SQLite file, so the database must never land inside a synced tree.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vibe.research_pipeline.paths import (
    ENV_RESEARCH_DATA_DIR,
    ENV_RESEARCH_DB_PATH,
    DB_FILENAME,
    UnsafeDatabaseLocationError,
    assert_safe_db_location,
    default_data_dir,
    research_db_path,
)


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv(ENV_RESEARCH_DB_PATH, raising=False)
    monkeypatch.delenv(ENV_RESEARCH_DATA_DIR, raising=False)


@pytest.mark.parametrize(
    "unsafe",
    [
        r"C:\Users\me\OneDrive - Microsoft\repo\research.sqlite3",
        r"C:\Users\me\OneDrive\research.sqlite3",
        "/home/me/Dropbox/research.sqlite3",
        "/Users/me/Google Drive/research.sqlite3",
        "/Users/me/Box Sync/research.sqlite3",
    ],
)
def test_synced_locations_are_rejected(unsafe):
    with pytest.raises(UnsafeDatabaseLocationError, match="cloud-synced"):
        assert_safe_db_location(Path(unsafe))


def test_sync_detection_is_case_insensitive():
    with pytest.raises(UnsafeDatabaseLocationError):
        assert_safe_db_location(Path(r"C:\Users\me\onedrive\research.sqlite3"))


def test_local_appdata_location_is_accepted():
    assert_safe_db_location(
        Path(r"C:\Users\me\AppData\Local\strategy-lab\research.sqlite3")
    )


def test_explicit_override_is_still_validated(monkeypatch):
    """An override is not a licence to put the database somewhere unsafe."""
    monkeypatch.setenv(
        ENV_RESEARCH_DB_PATH, r"C:\Users\me\OneDrive\research.sqlite3"
    )
    with pytest.raises(UnsafeDatabaseLocationError):
        research_db_path()


def test_override_path_is_honoured(monkeypatch, tmp_path):
    target = tmp_path / "custom" / "my.sqlite3"
    monkeypatch.setenv(ENV_RESEARCH_DB_PATH, str(target))
    assert research_db_path() == target


def test_data_dir_override_is_honoured(monkeypatch, tmp_path):
    monkeypatch.setenv(ENV_RESEARCH_DATA_DIR, str(tmp_path))
    assert research_db_path() == tmp_path / DB_FILENAME


def test_default_db_path_is_outside_the_repository(monkeypatch, tmp_path):
    monkeypatch.setenv(ENV_RESEARCH_DATA_DIR, str(tmp_path / "appdata"))
    repo_root = Path(__file__).resolve().parents[3]
    assert repo_root not in research_db_path().parents


def test_create_parents_makes_directory(monkeypatch, tmp_path):
    target = tmp_path / "nested" / "deeper" / "research.sqlite3"
    monkeypatch.setenv(ENV_RESEARCH_DB_PATH, str(target))
    resolved = research_db_path(create_parents=True)
    assert resolved.parent.is_dir()


def test_default_data_dir_is_absolute(monkeypatch):
    monkeypatch.delenv(ENV_RESEARCH_DATA_DIR, raising=False)
    assert default_data_dir().is_absolute()


def test_error_message_points_to_the_remedy():
    with pytest.raises(UnsafeDatabaseLocationError) as excinfo:
        assert_safe_db_location(Path("/home/me/Dropbox/research.sqlite3"))
    message = str(excinfo.value)
    assert ENV_RESEARCH_DB_PATH in message
    assert "Supabase" in message
