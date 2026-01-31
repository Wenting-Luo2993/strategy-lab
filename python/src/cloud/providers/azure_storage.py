"""
Azure Storage Provider - Azure Blob Storage implementation

Implements CloudStorageProvider for Microsoft Azure Blob Storage.
Requires: pip install azure-storage-blob
"""

import os
from typing import List
from pathlib import Path

try:
    from azure.storage.blob import BlobServiceClient, BlobClient, ContainerClient
    from azure.core.exceptions import ResourceNotFoundError
except ImportError:
    raise ImportError(
        "Azure Storage SDK not installed. Install with: pip install azure-storage-blob"
    )

from src.cloud.storage_provider import CloudStorageProvider
from src.utils.logger import get_logger

logger = get_logger(__name__)


class AzureStorageProvider(CloudStorageProvider):
    """
    Microsoft Azure Blob Storage provider.

    Authentication methods:
    1. Connection string (recommended for simplicity)
    2. Account key
    3. SAS token
    4. Managed identity (if running on Azure)

    Required environment variables (choose one):
    - AZURE_STORAGE_CONNECTION_STRING: Full connection string (easiest)
      OR
    - AZURE_STORAGE_ACCOUNT_NAME + AZURE_STORAGE_ACCOUNT_KEY
      OR
    - AZURE_STORAGE_ACCOUNT_NAME + AZURE_STORAGE_SAS_TOKEN

    - AZURE_CONTAINER_NAME: Container name (required)

    Usage:
        # Set environment variables
        export AZURE_STORAGE_CONNECTION_STRING="DefaultEndpointsProtocol=https;..."
        export AZURE_CONTAINER_NAME=strategy-lab-backups

        # Use provider
        from src.cloud.storage_factory import get_storage_provider
        storage = get_storage_provider('azure')
        storage.upload_file('data/trades.db', 'backups/trades.db')
    """

    def __init__(self):
        """Initialize Azure Storage provider."""
        # Get configuration
        self.container_name = os.getenv('AZURE_CONTAINER_NAME')
        if not self.container_name:
            raise ValueError("AZURE_CONTAINER_NAME environment variable required")

        # Initialize Azure client
        self.blob_service_client = self._create_client()
        self.container_client = self.blob_service_client.get_container_client(
            self.container_name
        )

        # Ensure container exists
        try:
            self.container_client.get_container_properties()
        except ResourceNotFoundError:
            logger.info(f"Creating container: {self.container_name}")
            self.container_client.create_container()

        logger.info(
            f"AzureStorageProvider initialized: container={self.container_name}"
        )

    def _create_client(self) -> BlobServiceClient:
        """Create and configure Azure Blob Service client."""
        # Try connection string first (easiest method)
        connection_string = os.getenv('AZURE_STORAGE_CONNECTION_STRING')
        if connection_string:
            client = BlobServiceClient.from_connection_string(connection_string)
            logger.info("Using Azure connection string authentication")
            return client

        # Try account name + key
        account_name = os.getenv('AZURE_STORAGE_ACCOUNT_NAME')
        account_key = os.getenv('AZURE_STORAGE_ACCOUNT_KEY')

        if account_name and account_key:
            account_url = f"https://{account_name}.blob.core.windows.net"
            client = BlobServiceClient(account_url=account_url, credential=account_key)
            logger.info(f"Using Azure account key authentication: {account_name}")
            return client

        # Try account name + SAS token
        sas_token = os.getenv('AZURE_STORAGE_SAS_TOKEN')
        if account_name and sas_token:
            account_url = f"https://{account_name}.blob.core.windows.net"
            client = BlobServiceClient(account_url=account_url, credential=sas_token)
            logger.info(f"Using Azure SAS token authentication: {account_name}")
            return client

        raise ValueError(
            "Azure authentication failed. Set one of:\n"
            "  - AZURE_STORAGE_CONNECTION_STRING\n"
            "  - AZURE_STORAGE_ACCOUNT_NAME + AZURE_STORAGE_ACCOUNT_KEY\n"
            "  - AZURE_STORAGE_ACCOUNT_NAME + AZURE_STORAGE_SAS_TOKEN"
        )

    def upload_file(self, local_path: str, remote_path: str) -> bool:
        """Upload file to Azure Blob Storage."""
        try:
            local_file = Path(local_path)
            if not local_file.exists():
                raise FileNotFoundError(f"Local file not found: {local_path}")

            # Get blob client
            blob_client = self.blob_service_client.get_blob_client(
                container=self.container_name,
                blob=remote_path
            )

            # Upload file
            with open(local_file, 'rb') as f:
                blob_client.upload_blob(f, overwrite=True)

            logger.info(
                f"Uploaded {local_path} -> azure://{self.container_name}/{remote_path}"
            )
            return True

        except Exception as e:
            logger.error(f"Azure upload failed: {e}")
            raise

    def download_file(self, remote_path: str, local_path: str) -> bool:
        """Download file from Azure Blob Storage."""
        try:
            # Get blob client
            blob_client = self.blob_service_client.get_blob_client(
                container=self.container_name,
                blob=remote_path
            )

            # Download to local file
            local_file = Path(local_path)
            local_file.parent.mkdir(parents=True, exist_ok=True)

            with open(local_file, 'wb') as f:
                download_stream = blob_client.download_blob()
                f.write(download_stream.readall())

            logger.info(
                f"Downloaded azure://{self.container_name}/{remote_path} -> {local_path}"
            )
            return True

        except ResourceNotFoundError:
            raise FileNotFoundError(f"Remote file not found: {remote_path}")
        except Exception as e:
            logger.error(f"Azure download failed: {e}")
            raise

    def list_files(self, prefix: str = '') -> List[str]:
        """List files in Azure Blob Storage with optional prefix."""
        try:
            blobs = self.container_client.list_blobs(name_starts_with=prefix)
            files = [blob.name for blob in blobs]

            logger.debug(f"Listed {len(files)} files with prefix '{prefix}'")
            return sorted(files)

        except Exception as e:
            logger.error(f"Azure list files failed: {e}")
            raise

    def delete_file(self, remote_path: str) -> bool:
        """Delete file from Azure Blob Storage."""
        try:
            # Get blob client
            blob_client = self.blob_service_client.get_blob_client(
                container=self.container_name,
                blob=remote_path
            )

            # Delete blob
            blob_client.delete_blob()

            logger.info(f"Deleted azure://{self.container_name}/{remote_path}")
            return True

        except ResourceNotFoundError:
            logger.warning(f"File not found for deletion: {remote_path}")
            return False
        except Exception as e:
            logger.error(f"Azure delete failed: {e}")
            raise

    def file_exists(self, remote_path: str) -> bool:
        """Check if file exists in Azure Blob Storage."""
        try:
            # Get blob client
            blob_client = self.blob_service_client.get_blob_client(
                container=self.container_name,
                blob=remote_path
            )

            # Check existence
            return blob_client.exists()

        except Exception as e:
            logger.error(f"Azure file exists check failed: {e}")
            raise
