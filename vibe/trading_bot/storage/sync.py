"""Cloud database synchronization service."""

import asyncio
import gzip
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any

try:
    from azure.storage.blob import BlobClient
except ImportError:
    BlobClient = None

try:
    import boto3
except ImportError:
    boto3 = None


logger = logging.getLogger(__name__)


class CloudStorageProvider:
    """Abstract base class for cloud storage providers."""

    async def upload_file(self, local_path: str, remote_path: str) -> bool:
        """Upload file to cloud storage.

        Args:
            local_path: Local file path
            remote_path: Remote path in cloud

        Returns:
            True if successful
        """
        raise NotImplementedError

    async def download_file(self, remote_path: str, local_path: str) -> bool:
        """Download file from cloud storage.

        Args:
            remote_path: Remote path in cloud
            local_path: Local file path to save

        Returns:
            True if successful
        """
        raise NotImplementedError

    async def get_file_info(self, remote_path: str) -> Optional[Dict[str, Any]]:
        """Get file metadata from cloud storage.

        Args:
            remote_path: Remote path in cloud

        Returns:
            Dict with file info (size, modified_time) or None if not found
        """
        raise NotImplementedError


class AzureBlobStorageProvider(CloudStorageProvider):
    """Azure Blob Storage provider for cloud sync."""

    def __init__(self, connection_string: str, container_name: str):
        """Initialize Azure provider.

        Args:
            connection_string: Azure connection string
            container_name: Container name

        Raises:
            ImportError: If azure-storage-blob not installed
        """
        if BlobClient is None:
            raise ImportError("azure-storage-blob required. Install with: pip install azure-storage-blob")

        self.connection_string = connection_string
        self.container_name = container_name

    async def upload_file(self, local_path: str, remote_path: str) -> bool:
        """Upload file to Azure Blob Storage."""
        try:
            with open(local_path, "rb") as data:
                blob_client = BlobClient.from_connection_string(
                    self.connection_string,
                    container_name=self.container_name,
                    blob_name=remote_path
                )
                blob_client.upload_blob(data, overwrite=True)
            logger.info(f"Uploaded to Azure: {remote_path}")
            return True
        except Exception as e:
            logger.error(f"Azure upload failed: {e}")
            return False

    async def download_file(self, remote_path: str, local_path: str) -> bool:
        """Download file from Azure Blob Storage."""
        try:
            blob_client = BlobClient.from_connection_string(
                self.connection_string,
                container_name=self.container_name,
                blob_name=remote_path
            )

            with open(local_path, "wb") as file_stream:
                file_stream.write(blob_client.download_blob().readall())

            logger.info(f"Downloaded from Azure: {remote_path}")
            return True
        except Exception as e:
            logger.error(f"Azure download failed: {e}")
            return False

    async def get_file_info(self, remote_path: str) -> Optional[Dict[str, Any]]:
        """Get Azure blob metadata."""
        try:
            blob_client = BlobClient.from_connection_string(
                self.connection_string,
                container_name=self.container_name,
                blob_name=remote_path
            )
            properties = blob_client.get_blob_properties()

            return {
                "size": properties.size,
                "modified_time": properties.last_modified,
            }
        except Exception as e:
            logger.warning(f"Failed to get Azure blob info: {e}")
            return None


class S3StorageProvider(CloudStorageProvider):
    """AWS S3 provider for cloud sync."""

    def __init__(self, bucket_name: str, region: str = "us-east-1"):
        """Initialize S3 provider.

        Args:
            bucket_name: S3 bucket name
            region: AWS region

        Raises:
            ImportError: If boto3 not installed
        """
        if boto3 is None:
            raise ImportError("boto3 required. Install with: pip install boto3")

        self.bucket_name = bucket_name
        self.s3_client = boto3.client("s3", region_name=region)

    async def upload_file(self, local_path: str, remote_path: str) -> bool:
        """Upload file to S3."""
        try:
            self.s3_client.upload_file(local_path, self.bucket_name, remote_path)
            logger.info(f"Uploaded to S3: {remote_path}")
            return True
        except Exception as e:
            logger.error(f"S3 upload failed: {e}")
            return False

    async def download_file(self, remote_path: str, local_path: str) -> bool:
        """Download file from S3."""
        try:
            self.s3_client.download_file(self.bucket_name, remote_path, local_path)
            logger.info(f"Downloaded from S3: {remote_path}")
            return True
        except Exception as e:
            logger.error(f"S3 download failed: {e}")
            return False

    async def get_file_info(self, remote_path: str) -> Optional[Dict[str, Any]]:
        """Get S3 object metadata."""
        try:
            response = self.s3_client.head_object(Bucket=self.bucket_name, Key=remote_path)
            return {
                "size": response["ContentLength"],
                "modified_time": response["LastModified"],
            }
        except Exception as e:
            logger.warning(f"Failed to get S3 object info: {e}")
            return None


class DatabaseSync:
    """Manages periodic synchronization of trade database to cloud storage."""

    def __init__(
        self,
        storage: CloudStorageProvider,
        db_path: str,
        remote_path: str = "trades.db",
        compress: bool = True,
    ):
        """Initialize database sync service.

        Args:
            storage: Cloud storage provider
            db_path: Local database path
            remote_path: Remote path in cloud
            compress: Whether to compress before upload
        """
        self.storage = storage
        self.db_path = Path(db_path)
        self.remote_path = remote_path
        self.compress = compress

        # Metrics
        self.last_sync_time: Optional[datetime] = None
        self.last_sync_success: bool = False
        self.total_syncs: int = 0
        self.successful_syncs: int = 0

    async def upload(self) -> bool:
        """Upload database to cloud storage.

        Returns:
            True if successful
        """
        if not self.db_path.exists():
            logger.warning(f"Database file not found: {self.db_path}")
            return False

        try:
            # Compress if needed
            if self.compress:
                upload_path = await self._compress_db()
            else:
                upload_path = str(self.db_path)

            # Upload to cloud
            success = await self.storage.upload_file(upload_path, self.remote_path)

            # Track metrics
            self.total_syncs += 1
            if success:
                self.successful_syncs += 1
                self.last_sync_success = True
            else:
                self.last_sync_success = False

            self.last_sync_time = datetime.utcnow()

            # Clean up compressed file if we created one
            if self.compress:
                try:
                    os.remove(upload_path)
                except Exception as e:
                    logger.warning(f"Failed to cleanup compressed file: {e}")

            return success

        except Exception as e:
            logger.error(f"Database upload failed: {e}", exc_info=True)
            self.total_syncs += 1
            self.last_sync_success = False
            self.last_sync_time = datetime.utcnow()
            return False

    async def download(self, force: bool = False) -> bool:
        """Download database from cloud storage if newer than local.

        Args:
            force: Force download even if local is newer

        Returns:
            True if successful (or if local is already newer)
        """
        try:
            # Get remote file info
            remote_info = await self.storage.get_file_info(self.remote_path)

            if remote_info is None:
                logger.warning("Remote database file not found")
                return False

            # Check if remote is newer
            if not force and self.db_path.exists():
                local_mtime = datetime.fromtimestamp(self.db_path.stat().st_mtime)
                remote_mtime = remote_info["modified_time"]

                if isinstance(remote_mtime, int):
                    remote_mtime = datetime.fromtimestamp(remote_mtime)

                if local_mtime >= remote_mtime:
                    logger.info("Local database is up-to-date")
                    return True

            # Download from cloud
            temp_path = str(self.db_path) + ".tmp"
            success = await self.storage.download_file(self.remote_path, temp_path)

            if not success:
                return False

            # Decompress if needed
            if self.compress and self.remote_path.endswith(".gz"):
                try:
                    with gzip.open(temp_path, "rb") as f_in:
                        with open(self.db_path, "wb") as f_out:
                            f_out.write(f_in.read())
                    os.remove(temp_path)
                except Exception as e:
                    logger.error(f"Failed to decompress database: {e}")
                    return False
            else:
                # Move temp file to actual location
                try:
                    os.replace(temp_path, self.db_path)
                except Exception as e:
                    logger.error(f"Failed to move downloaded file: {e}")
                    return False

            logger.info("Database download and extract successful")
            return True

        except Exception as e:
            logger.error(f"Database download failed: {e}", exc_info=True)
            return False

    async def sync(self) -> bool:
        """Upload database to cloud (main sync operation).

        Returns:
            True if successful
        """
        return await self.upload()

    async def _compress_db(self) -> str:
        """Compress database file for upload.

        Returns:
            Path to compressed file
        """
        compressed_path = str(self.db_path) + ".gz"

        try:
            with open(self.db_path, "rb") as f_in:
                with gzip.open(compressed_path, "wb") as f_out:
                    f_out.write(f_in.read())

            logger.info(f"Database compressed: {self.db_path} -> {compressed_path}")
            return compressed_path

        except Exception as e:
            logger.error(f"Compression failed: {e}")
            raise

    def get_metrics(self) -> Dict[str, Any]:
        """Get sync metrics.

        Returns:
            Dictionary with sync statistics
        """
        success_rate = (
            (self.successful_syncs / self.total_syncs * 100)
            if self.total_syncs > 0
            else 0.0
        )

        return {
            "last_sync_time": self.last_sync_time.isoformat() if self.last_sync_time else None,
            "last_sync_success": self.last_sync_success,
            "total_syncs": self.total_syncs,
            "successful_syncs": self.successful_syncs,
            "success_rate_pct": success_rate,
        }
