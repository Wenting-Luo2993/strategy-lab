"""
Database Sync Daemon - Background sync of SQLite database to cloud storage

Continuously syncs the local SQLite database to cloud storage in the background.
Runs in a separate thread and handles sync errors gracefully.
"""

import threading
import time
import os
from datetime import datetime, date
from pathlib import Path
from typing import Optional

from src.cloud.storage_provider import CloudStorageProvider
from src.utils.logger import get_logger

logger = get_logger(__name__)


class DatabaseSyncDaemon:
    """
    Background daemon for syncing database to cloud storage.

    Features:
    - Runs in background thread (non-blocking)
    - Configurable sync interval
    - Uploads as 'latest' and timestamped backups
    - Automatic cleanup of old backups
    - Graceful error handling

    Usage:
        from src.cloud.storage_factory import get_storage_provider
        from src.cloud.database_sync import DatabaseSyncDaemon

        storage = get_storage_provider()
        sync = DatabaseSyncDaemon(
            db_path='data/trades.db',
            storage_provider=storage,
            sync_interval=300  # 5 minutes
        )

        sync.start()  # Start background sync
        # ... bot runs ...
        sync.stop()   # Graceful shutdown with final sync
    """

    def __init__(
        self,
        db_path: str,
        storage_provider: CloudStorageProvider,
        sync_interval: int = 300,
        backup_retention_days: int = 30
    ):
        """
        Initialize database sync daemon.

        Args:
            db_path: Path to SQLite database file
            storage_provider: CloudStorageProvider instance
            sync_interval: Seconds between syncs (default 300 = 5 minutes)
            backup_retention_days: Days to keep old backups (default 30)
        """
        self.db_path = db_path
        self.storage = storage_provider
        self.sync_interval = sync_interval
        self.backup_retention_days = backup_retention_days

        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.last_sync_time: Optional[datetime] = None
        self.last_backup_date: Optional[date] = None

        logger.info(
            f"DatabaseSyncDaemon initialized: "
            f"db={db_path}, interval={sync_interval}s"
        )

    def start(self):
        """Start background sync in separate thread."""
        if self.running:
            logger.warning("Sync daemon already running")
            return

        self.running = True
        self.thread = threading.Thread(
            target=self._sync_loop,
            daemon=True,
            name="DatabaseSyncDaemon"
        )
        self.thread.start()
        logger.info("Database sync daemon started")

    def stop(self, timeout: int = 30):
        """
        Stop background sync gracefully.

        Performs final sync before stopping.

        Args:
            timeout: Maximum seconds to wait for final sync
        """
        if not self.running:
            return

        logger.info("Stopping database sync daemon...")
        self.running = False

        # Wait for thread to finish
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=timeout)

        # Final sync
        try:
            self.sync_now()
            logger.info("Database sync daemon stopped (final sync complete)")
        except Exception as e:
            logger.error(f"Final sync failed: {e}")

    def _sync_loop(self):
        """Main sync loop (runs in background thread)."""
        while self.running:
            try:
                self.sync_now()
            except Exception as e:
                logger.error(f"Cloud sync failed: {e}", exc_info=True)

            # Sleep in small increments to allow quick shutdown
            for _ in range(self.sync_interval):
                if not self.running:
                    break
                time.sleep(1)

    def sync_now(self):
        """
        Perform immediate sync to cloud.

        Uploads database as both 'latest' and timestamped backup.
        Creates new timestamped backup once per day.

        Raises:
            FileNotFoundError: If database file doesn't exist
            Exception: If upload fails
        """
        # Check database exists
        if not os.path.exists(self.db_path):
            raise FileNotFoundError(f"Database not found: {self.db_path}")

        # Upload as latest (overwrites previous)
        logger.debug(f"Syncing database to cloud: {self.db_path}")
        self.storage.upload_file(self.db_path, 'trades_latest.db')

        # Create timestamped backup once per day
        today = date.today()
        if self._should_create_backup(today):
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            backup_path = f'backups/trades_{timestamp}.db'

            self.storage.upload_file(self.db_path, backup_path)
            self.last_backup_date = today
            logger.info(f"Created daily backup: {backup_path}")

        self.last_sync_time = datetime.now()
        logger.debug("Database synced to cloud successfully")

        # Cleanup old backups
        self._cleanup_old_backups()

    def _should_create_backup(self, today: date) -> bool:
        """Check if we should create a new timestamped backup today."""
        # Always create backup if we haven't created one yet
        if self.last_backup_date is None:
            return True

        # Create backup if it's a new day
        return today > self.last_backup_date

    def _cleanup_old_backups(self):
        """Delete backups older than retention period."""
        try:
            # List all backup files
            backups = self.storage.list_files('backups/')

            if not backups:
                return

            # Parse dates from filenames (format: trades_YYYYMMDD_HHMMSS.db)
            cutoff_date = date.today()
            deleted_count = 0

            for backup in backups:
                try:
                    # Extract date from filename
                    filename = Path(backup).name
                    if not filename.startswith('trades_'):
                        continue

                    date_str = filename.split('_')[1]  # YYYYMMDD
                    backup_date = datetime.strptime(date_str, '%Y%m%d').date()

                    # Check if too old
                    age_days = (cutoff_date - backup_date).days
                    if age_days > self.backup_retention_days:
                        self.storage.delete_file(backup)
                        deleted_count += 1
                        logger.debug(f"Deleted old backup: {backup} (age: {age_days} days)")

                except (ValueError, IndexError) as e:
                    logger.warning(f"Could not parse backup date from {backup}: {e}")
                    continue

            if deleted_count > 0:
                logger.info(f"Cleaned up {deleted_count} old backups")

        except Exception as e:
            logger.error(f"Backup cleanup failed: {e}")
            # Don't raise - cleanup failure shouldn't stop sync

    def get_status(self) -> dict:
        """
        Get sync daemon status.

        Returns:
            Dictionary with status information
        """
        return {
            'running': self.running,
            'last_sync': self.last_sync_time.isoformat() if self.last_sync_time else None,
            'last_backup': self.last_backup_date.isoformat() if self.last_backup_date else None,
            'sync_interval': self.sync_interval,
            'retention_days': self.backup_retention_days
        }
