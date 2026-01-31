"""
Unit tests for cloud storage providers and database sync
"""

import pytest
import os
import tempfile
import time
from pathlib import Path
from datetime import date

from src.cloud.storage_factory import get_storage_provider
from src.cloud.providers.local_storage import LocalStorageProvider
from src.cloud.database_sync import DatabaseSyncDaemon


@pytest.fixture
def temp_storage():
    """Create a temporary local storage directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def local_storage(temp_storage):
    """Create a local storage provider instance."""
    return LocalStorageProvider(base_path=temp_storage)


@pytest.fixture
def temp_db():
    """Create a temporary database file."""
    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.db') as f:
        f.write("test database content\n")
        db_path = f.name

    yield db_path

    # Cleanup
    if os.path.exists(db_path):
        try:
            os.remove(db_path)
        except:
            pass


class TestStorageFactory:
    """Test storage factory functionality."""

    def test_get_local_provider(self):
        """Test getting local storage provider."""
        storage = get_storage_provider('local')
        assert isinstance(storage, LocalStorageProvider)

    def test_get_default_provider(self):
        """Test default provider (local)."""
        # Save and clear env var
        old_val = os.environ.get('CLOUD_STORAGE_PROVIDER')
        if old_val:
            del os.environ['CLOUD_STORAGE_PROVIDER']

        try:
            storage = get_storage_provider()
            assert isinstance(storage, LocalStorageProvider)
        finally:
            # Restore
            if old_val:
                os.environ['CLOUD_STORAGE_PROVIDER'] = old_val

    def test_invalid_provider_raises_error(self):
        """Test that invalid provider name raises ValueError."""
        with pytest.raises(ValueError, match="Unknown cloud storage provider"):
            get_storage_provider('invalid')


class TestLocalStorageProvider:
    """Test local storage provider."""

    def test_upload_file(self, local_storage, temp_db):
        """Test file upload."""
        result = local_storage.upload_file(temp_db, 'test.db')
        assert result is True
        assert local_storage.file_exists('test.db')

    def test_upload_nonexistent_file_raises_error(self, local_storage):
        """Test uploading non-existent file raises error."""
        with pytest.raises(FileNotFoundError):
            local_storage.upload_file('nonexistent.db', 'test.db')

    def test_download_file(self, local_storage, temp_db):
        """Test file download."""
        # Upload first
        local_storage.upload_file(temp_db, 'test.db')

        # Download to new location
        download_path = temp_db + '.downloaded'
        result = local_storage.download_file('test.db', download_path)

        assert result is True
        assert os.path.exists(download_path)

        # Cleanup
        os.remove(download_path)

    def test_list_files(self, local_storage, temp_db):
        """Test listing files."""
        # Upload some files
        local_storage.upload_file(temp_db, 'file1.db')
        local_storage.upload_file(temp_db, 'backups/file2.db')
        local_storage.upload_file(temp_db, 'backups/file3.db')

        # List all files
        all_files = local_storage.list_files()
        assert len(all_files) == 3

        # List with prefix
        backup_files = local_storage.list_files('backups/')
        assert len(backup_files) == 2
        # Check that backups directory files are returned (handle both / and \ path separators)
        assert all('backups' in str(f) for f in backup_files)

    def test_delete_file(self, local_storage, temp_db):
        """Test file deletion."""
        # Upload first
        local_storage.upload_file(temp_db, 'test.db')
        assert local_storage.file_exists('test.db')

        # Delete
        result = local_storage.delete_file('test.db')
        assert result is True
        assert not local_storage.file_exists('test.db')

    def test_delete_nonexistent_file(self, local_storage):
        """Test deleting non-existent file returns False."""
        result = local_storage.delete_file('nonexistent.db')
        assert result is False

    def test_file_exists(self, local_storage, temp_db):
        """Test file existence check."""
        assert not local_storage.file_exists('test.db')

        local_storage.upload_file(temp_db, 'test.db')
        assert local_storage.file_exists('test.db')


class TestDatabaseSyncDaemon:
    """Test database sync daemon."""

    def test_initialization(self, local_storage, temp_db):
        """Test daemon initialization."""
        sync = DatabaseSyncDaemon(
            db_path=temp_db,
            storage_provider=local_storage,
            sync_interval=5
        )

        assert sync.db_path == temp_db
        assert sync.sync_interval == 5
        assert not sync.running

    def test_sync_now(self, local_storage, temp_db):
        """Test immediate sync."""
        sync = DatabaseSyncDaemon(
            db_path=temp_db,
            storage_provider=local_storage
        )

        # Perform sync
        sync.sync_now()

        # Verify files exist
        assert local_storage.file_exists('trades_latest.db')

        # Check for backup (should be created on first sync)
        backups = local_storage.list_files('backups/')
        assert len(backups) >= 1
        assert any('trades_' in b for b in backups)

    def test_sync_daemon_start_stop(self, local_storage, temp_db):
        """Test starting and stopping daemon."""
        sync = DatabaseSyncDaemon(
            db_path=temp_db,
            storage_provider=local_storage,
            sync_interval=2  # Short interval for testing
        )

        # Start daemon
        sync.start()
        assert sync.running is True
        assert sync.thread is not None

        # Wait for at least one sync
        time.sleep(3)

        # Stop daemon
        sync.stop(timeout=5)
        assert sync.running is False

        # Verify sync occurred
        assert local_storage.file_exists('trades_latest.db')

    def test_should_create_backup_logic(self, local_storage, temp_db):
        """Test backup creation logic."""
        sync = DatabaseSyncDaemon(
            db_path=temp_db,
            storage_provider=local_storage
        )

        # Should create backup on first sync
        assert sync._should_create_backup(date.today()) is True

        # After sync, same day should not create another
        sync.last_backup_date = date.today()
        assert sync._should_create_backup(date.today()) is False

    def test_get_status(self, local_storage, temp_db):
        """Test status reporting."""
        sync = DatabaseSyncDaemon(
            db_path=temp_db,
            storage_provider=local_storage,
            sync_interval=300
        )

        status = sync.get_status()

        assert status['running'] is False
        assert status['sync_interval'] == 300
        assert status['retention_days'] == 30
        assert status['last_sync'] is None

        # After sync
        sync.sync_now()
        status = sync.get_status()
        assert status['last_sync'] is not None
        assert status['last_backup'] is not None

    def test_cleanup_old_backups(self, local_storage, temp_db):
        """Test old backup cleanup."""
        sync = DatabaseSyncDaemon(
            db_path=temp_db,
            storage_provider=local_storage,
            backup_retention_days=1  # Keep only 1 day
        )

        # Create some old-looking backups
        local_storage.upload_file(temp_db, 'backups/trades_20200101_120000.db')
        local_storage.upload_file(temp_db, 'backups/trades_20200102_120000.db')

        # Run cleanup
        sync._cleanup_old_backups()

        # Old backups should be deleted
        backups = local_storage.list_files('backups/')
        old_backups = [b for b in backups if '2020' in b]
        assert len(old_backups) == 0
