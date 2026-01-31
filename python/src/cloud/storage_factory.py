"""
Storage Provider Factory - Creates cloud storage provider instances

Automatically selects the appropriate cloud storage provider based on
environment configuration. Supports Oracle, AWS, GCP, Azure, and local.
"""

import os
from typing import Optional

from src.cloud.storage_provider import CloudStorageProvider
from src.utils.logger import get_logger

logger = get_logger(__name__)


def get_storage_provider(provider_name: Optional[str] = None) -> CloudStorageProvider:
    """
    Get cloud storage provider instance.

    Provider is determined by (in order):
    1. provider_name parameter
    2. CLOUD_STORAGE_PROVIDER environment variable
    3. Default to 'local' for testing

    Args:
        provider_name: Provider name ('oracle', 'aws', 'gcp', 'azure', 'local')

    Returns:
        CloudStorageProvider instance

    Raises:
        ValueError: If provider name is invalid
        ImportError: If provider SDK is not installed

    Example:
        # Use configured provider
        storage = get_storage_provider()

        # Force specific provider
        storage = get_storage_provider('aws')
    """
    # Determine provider
    provider_name = provider_name or os.getenv('CLOUD_STORAGE_PROVIDER', 'local')
    provider_name = provider_name.lower()

    logger.info(f"Initializing cloud storage provider: {provider_name}")

    # Import and instantiate provider
    if provider_name == 'local':
        from src.cloud.providers.local_storage import LocalStorageProvider
        return LocalStorageProvider()

    elif provider_name == 'oracle':
        try:
            from src.cloud.providers.oracle_storage import OracleStorageProvider
            return OracleStorageProvider()
        except ImportError:
            raise ImportError(
                "Oracle Cloud SDK not installed. "
                "Install with: pip install oci"
            )

    elif provider_name == 'aws':
        try:
            from src.cloud.providers.aws_storage import AWSStorageProvider
            return AWSStorageProvider()
        except ImportError:
            raise ImportError(
                "AWS SDK not installed. "
                "Install with: pip install boto3"
            )

    elif provider_name == 'gcp':
        try:
            from src.cloud.providers.gcp_storage import GCPStorageProvider
            return GCPStorageProvider()
        except ImportError:
            raise ImportError(
                "Google Cloud SDK not installed. "
                "Install with: pip install google-cloud-storage"
            )

    elif provider_name == 'azure':
        try:
            from src.cloud.providers.azure_storage import AzureStorageProvider
            return AzureStorageProvider()
        except ImportError:
            raise ImportError(
                "Azure SDK not installed. "
                "Install with: pip install azure-storage-blob"
            )

    else:
        raise ValueError(
            f"Unknown cloud storage provider: {provider_name}. "
            f"Valid options: oracle, aws, gcp, azure, local"
        )
