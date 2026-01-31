"""
Cloud Storage Provider - Abstract base class for cloud storage implementations

This module defines the interface for cloud storage providers, enabling
cloud-agnostic backup and sync operations. Supports Oracle, AWS, GCP, Azure.
"""

from abc import ABC, abstractmethod
from typing import List, Optional


class CloudStorageProvider(ABC):
    """
    Abstract base class for cloud storage providers.

    Implementations must provide methods for uploading, downloading,
    listing, and deleting files from cloud storage.

    Usage:
        # Get provider via factory
        from src.cloud.storage_factory import get_storage_provider

        storage = get_storage_provider()  # Uses env CLOUD_STORAGE_PROVIDER
        storage.upload_file('data/trades.db', 'backups/trades_20260131.db')
        files = storage.list_files('backups/')
    """

    @abstractmethod
    def upload_file(self, local_path: str, remote_path: str) -> bool:
        """
        Upload a file to cloud storage.

        Args:
            local_path: Path to local file
            remote_path: Destination path in cloud storage

        Returns:
            bool: True if successful, False otherwise

        Raises:
            Exception: If upload fails
        """
        pass

    @abstractmethod
    def download_file(self, remote_path: str, local_path: str) -> bool:
        """
        Download a file from cloud storage.

        Args:
            remote_path: Path in cloud storage
            local_path: Destination path on local filesystem

        Returns:
            bool: True if successful, False otherwise

        Raises:
            Exception: If download fails
        """
        pass

    @abstractmethod
    def list_files(self, prefix: str = '') -> List[str]:
        """
        List files in cloud storage with optional prefix filter.

        Args:
            prefix: Optional prefix to filter results (e.g., 'backups/')

        Returns:
            List of file paths in cloud storage

        Raises:
            Exception: If listing fails
        """
        pass

    @abstractmethod
    def delete_file(self, remote_path: str) -> bool:
        """
        Delete a file from cloud storage.

        Args:
            remote_path: Path in cloud storage

        Returns:
            bool: True if successful, False otherwise

        Raises:
            Exception: If deletion fails
        """
        pass

    @abstractmethod
    def file_exists(self, remote_path: str) -> bool:
        """
        Check if a file exists in cloud storage.

        Args:
            remote_path: Path in cloud storage

        Returns:
            bool: True if file exists, False otherwise
        """
        pass
