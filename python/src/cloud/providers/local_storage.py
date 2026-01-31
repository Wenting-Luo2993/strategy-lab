"""
Local Storage Provider - For testing and local development

Simulates cloud storage using local filesystem.
"""

import os
import shutil
from pathlib import Path
from typing import List

from src.cloud.storage_provider import CloudStorageProvider
from src.utils.logger import get_logger

logger = get_logger(__name__)


class LocalStorageProvider(CloudStorageProvider):
    """
    Local filesystem storage provider for testing.

    Stores files in a local directory (default: data/cloud_storage/)
    to simulate cloud storage behavior without actual cloud credentials.
    """

    def __init__(self, base_path: str = 'data/cloud_storage'):
        """
        Initialize local storage provider.

        Args:
            base_path: Base directory for simulated cloud storage
        """
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)
        logger.info(f"LocalStorageProvider initialized at {self.base_path}")

    def _get_full_path(self, remote_path: str) -> Path:
        """Convert remote path to full local path."""
        return self.base_path / remote_path

    def upload_file(self, local_path: str, remote_path: str) -> bool:
        """Upload (copy) file to local storage."""
        try:
            local_file = Path(local_path)
            if not local_file.exists():
                raise FileNotFoundError(f"Local file not found: {local_path}")

            remote_file = self._get_full_path(remote_path)
            remote_file.parent.mkdir(parents=True, exist_ok=True)

            shutil.copy2(local_file, remote_file)
            logger.info(f"Uploaded {local_path} -> {remote_path} (local)")
            return True

        except Exception as e:
            logger.error(f"Upload failed: {e}")
            raise

    def download_file(self, remote_path: str, local_path: str) -> bool:
        """Download (copy) file from local storage."""
        try:
            remote_file = self._get_full_path(remote_path)
            if not remote_file.exists():
                raise FileNotFoundError(f"Remote file not found: {remote_path}")

            local_file = Path(local_path)
            local_file.parent.mkdir(parents=True, exist_ok=True)

            shutil.copy2(remote_file, local_file)
            logger.info(f"Downloaded {remote_path} -> {local_path} (local)")
            return True

        except Exception as e:
            logger.error(f"Download failed: {e}")
            raise

    def list_files(self, prefix: str = '') -> List[str]:
        """List files in local storage with optional prefix."""
        try:
            search_path = self._get_full_path(prefix)

            if not search_path.exists():
                return []

            files = []
            if search_path.is_file():
                files = [prefix]
            else:
                for file_path in search_path.rglob('*'):
                    if file_path.is_file():
                        relative = file_path.relative_to(self.base_path)
                        files.append(str(relative))

            logger.debug(f"Listed {len(files)} files with prefix '{prefix}'")
            return sorted(files)

        except Exception as e:
            logger.error(f"List files failed: {e}")
            raise

    def delete_file(self, remote_path: str) -> bool:
        """Delete file from local storage."""
        try:
            remote_file = self._get_full_path(remote_path)

            if not remote_file.exists():
                logger.warning(f"File not found for deletion: {remote_path}")
                return False

            remote_file.unlink()
            logger.info(f"Deleted {remote_path} (local)")
            return True

        except Exception as e:
            logger.error(f"Delete failed: {e}")
            raise

    def file_exists(self, remote_path: str) -> bool:
        """Check if file exists in local storage."""
        remote_file = self._get_full_path(remote_path)
        return remote_file.exists()
