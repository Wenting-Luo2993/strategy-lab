"""Tests for cloud database sync."""

import pytest
import os
import gzip
import tempfile
from pathlib import Path
from datetime import datetime
from unittest.mock import Mock, AsyncMock, MagicMock, patch

from vibe.trading_bot.storage.sync import (
    DatabaseSync,
    CloudStorageProvider,
)


class MockStorageProvider(CloudStorageProvider):
    """Mock storage provider for testing."""

    def __init__(self):
        """Initialize mock provider."""
        self.uploaded_files = {}
        self.downloaded_files = {}

    async def upload_file(self, local_path: str, remote_path: str) -> bool:
        """Mock upload."""
        with open(local_path, "rb") as f:
            self.uploaded_files[remote_path] = f.read()
        return True

    async def download_file(self, remote_path: str, local_path: str) -> bool:
        """Mock download."""
        if remote_path not in self.uploaded_files:
            return False

        with open(local_path, "wb") as f:
            f.write(self.uploaded_files[remote_path])
        return True

    async def get_file_info(self, remote_path: str):
        """Mock file info."""
        if remote_path not in self.uploaded_files:
            return None

        return {
            "size": len(self.uploaded_files[remote_path]),
            "modified_time": datetime.utcnow(),
        }


class TestDatabaseSync:
    """Test database sync service."""

    @pytest.fixture
    def temp_db(self):
        """Create temporary database file."""
        fd, path = tempfile.mkstemp(suffix=".db")
        os.write(fd, b"test database content " * 1000)
        os.close(fd)
        yield path
        if os.path.exists(path):
            os.remove(path)

    @pytest.fixture
    def storage(self):
        """Create mock storage provider."""
        return MockStorageProvider()

    @pytest.fixture
    def sync(self, temp_db, storage):
        """Create database sync service."""
        return DatabaseSync(
            storage=storage,
            db_path=temp_db,
            remote_path="trades.db",
            compress=False,
        )

    @pytest.mark.asyncio
    async def test_initialization(self, sync, temp_db):
        """Test sync service initializes correctly."""
        assert sync.db_path == Path(temp_db)
        assert sync.remote_path == "trades.db"
        assert sync.last_sync_time is None
        assert sync.total_syncs == 0

    @pytest.mark.asyncio
    async def test_upload_success(self, sync, storage):
        """Test successful database upload."""
        result = await sync.upload()

        assert result is True
        assert sync.last_sync_success is True
        assert sync.total_syncs == 1
        assert sync.successful_syncs == 1
        assert "trades.db" in storage.uploaded_files

    @pytest.mark.asyncio
    async def test_upload_missing_database(self, storage):
        """Test upload fails when database missing."""
        sync = DatabaseSync(
            storage=storage,
            db_path="/nonexistent/path/trades.db",
            compress=False,
        )

        result = await sync.upload()

        assert result is False

    @pytest.mark.asyncio
    async def test_upload_tracks_metrics(self, sync):
        """Test upload tracks sync metrics."""
        await sync.upload()
        await sync.upload()

        assert sync.total_syncs == 2
        assert sync.successful_syncs == 2
        assert sync.last_sync_time is not None

    @pytest.mark.asyncio
    async def test_get_metrics(self, sync):
        """Test getting sync metrics."""
        await sync.upload()

        metrics = sync.get_metrics()

        assert "last_sync_time" in metrics
        assert "last_sync_success" in metrics
        assert "total_syncs" in metrics
        assert "successful_syncs" in metrics
        assert "success_rate_pct" in metrics
        assert metrics["total_syncs"] == 1
        assert metrics["success_rate_pct"] == 100.0

    @pytest.mark.asyncio
    async def test_compression(self, temp_db, storage):
        """Test database compression before upload."""
        sync = DatabaseSync(
            storage=storage,
            db_path=temp_db,
            remote_path="trades.db.gz",
            compress=True,
        )

        result = await sync.upload()

        assert result is True

        # Check that uploaded file is compressed
        uploaded_data = storage.uploaded_files["trades.db.gz"]

        # Should be gzip format (starts with magic bytes)
        assert uploaded_data[:2] == b'\x1f\x8b'

    @pytest.mark.asyncio
    async def test_download_success(self, sync, temp_db, storage):
        """Test successful download."""
        # First upload
        await sync.upload()

        # Create new database file
        new_db_path = temp_db + ".new"

        sync2 = DatabaseSync(
            storage=storage,
            db_path=new_db_path,
            remote_path="trades.db",
            compress=False,
        )

        result = await sync2.download(force=True)

        assert result is True
        assert os.path.exists(new_db_path)

        # Clean up
        os.remove(new_db_path)

    @pytest.mark.asyncio
    async def test_download_missing_remote(self, sync, storage):
        """Test download fails when remote missing."""
        result = await sync.download()

        assert result is False

    @pytest.mark.asyncio
    async def test_download_respects_local_newer(self, temp_db, storage):
        """Test download skips if local is newer."""
        sync = DatabaseSync(
            storage=storage,
            db_path=temp_db,
            remote_path="trades.db",
            compress=False,
        )

        # Upload
        await sync.upload()

        # Make local file newer
        os.utime(temp_db, None)  # Touch file to update mtime

        # Download should skip
        result = await sync.download(force=False)

        assert result is True  # Success but didn't download

    @pytest.mark.asyncio
    async def test_sync_operation(self, sync):
        """Test sync operation (upload)."""
        result = await sync.sync()

        assert result is True
        assert sync.last_sync_success is True

    @pytest.mark.asyncio
    async def test_failure_tracking(self, storage):
        """Test failure tracking in metrics."""
        # Create sync with storage that fails
        failing_storage = Mock(spec=CloudStorageProvider)
        failing_storage.upload_file = AsyncMock(return_value=False)

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
            f.write(b"test data")

        try:
            sync = DatabaseSync(
                storage=failing_storage,
                db_path=db_path,
                compress=False,
            )

            await sync.upload()
            await sync.upload()

            metrics = sync.get_metrics()

            assert metrics["total_syncs"] == 2
            assert metrics["successful_syncs"] == 0
            assert metrics["success_rate_pct"] == 0.0
            assert metrics["last_sync_success"] is False

        finally:
            os.remove(db_path)

    @pytest.mark.asyncio
    async def test_download_decompression(self, temp_db, storage):
        """Test decompression on download."""
        # Create sync with compression
        sync1 = DatabaseSync(
            storage=storage,
            db_path=temp_db,
            remote_path="trades.db.gz",
            compress=True,
        )

        # Upload
        await sync1.upload()

        # Download to new location
        new_db_path = temp_db + ".new"

        sync2 = DatabaseSync(
            storage=storage,
            db_path=new_db_path,
            remote_path="trades.db.gz",
            compress=True,
        )

        result = await sync2.download(force=True)

        assert result is True
        assert os.path.exists(new_db_path)

        # Verify decompressed content matches original
        with open(temp_db, "rb") as f:
            original = f.read()

        with open(new_db_path, "rb") as f:
            downloaded = f.read()

        assert original == downloaded

        os.remove(new_db_path)

    def test_metrics_no_syncs(self, sync):
        """Test metrics with no syncs."""
        metrics = sync.get_metrics()

        assert metrics["total_syncs"] == 0
        assert metrics["successful_syncs"] == 0
        assert metrics["success_rate_pct"] == 0.0
        assert metrics["last_sync_time"] is None
