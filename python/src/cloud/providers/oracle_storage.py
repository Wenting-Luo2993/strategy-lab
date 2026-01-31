"""
Oracle Cloud Storage Provider - OCI Object Storage implementation

Implements CloudStorageProvider for Oracle Cloud Infrastructure (OCI) Object Storage.
Requires: pip install oci
"""

import os
from typing import List
from pathlib import Path

try:
    import oci
    from oci.config import from_file, validate_config
    from oci.object_storage import ObjectStorageClient
except ImportError:
    raise ImportError(
        "Oracle Cloud SDK not installed. Install with: pip install oci"
    )

from src.cloud.storage_provider import CloudStorageProvider
from src.utils.logger import get_logger

logger = get_logger(__name__)


class OracleStorageProvider(CloudStorageProvider):
    """
    Oracle Cloud Infrastructure Object Storage provider.

    Authentication methods (in order of precedence):
    1. Instance principal (if running on OCI instance)
    2. Config file (~/.oci/config)
    3. Environment variables (OCI_CONFIG_FILE)

    Required environment variables:
    - OCI_BUCKET_NAME: Object storage bucket name
    - OCI_NAMESPACE: OCI namespace (tenancy namespace)

    Optional:
    - OCI_CONFIG_FILE: Path to OCI config file (default: ~/.oci/config)
    - OCI_PROFILE: Config profile to use (default: DEFAULT)
    - OCI_COMPARTMENT_ID: Compartment ID (if not using instance principal)

    Usage:
        # Set environment variables
        export OCI_BUCKET_NAME=strategy-lab-backups
        export OCI_NAMESPACE=my-namespace

        # Use provider
        from src.cloud.storage_factory import get_storage_provider
        storage = get_storage_provider('oracle')
        storage.upload_file('data/trades.db', 'backups/trades.db')
    """

    def __init__(self):
        """Initialize Oracle Cloud Storage provider."""
        # Get configuration
        self.bucket_name = os.getenv('OCI_BUCKET_NAME')
        self.namespace = os.getenv('OCI_NAMESPACE')

        if not self.bucket_name:
            raise ValueError("OCI_BUCKET_NAME environment variable required")
        if not self.namespace:
            raise ValueError("OCI_NAMESPACE environment variable required")

        # Initialize OCI client
        self.client = self._create_client()
        logger.info(
            f"OracleStorageProvider initialized: "
            f"bucket={self.bucket_name}, namespace={self.namespace}"
        )

    def _create_client(self) -> ObjectStorageClient:
        """Create and configure OCI Object Storage client."""
        try:
            # Try instance principal first (for OCI instances)
            if os.getenv('OCI_USE_INSTANCE_PRINCIPAL', 'false').lower() == 'true':
                signer = oci.auth.signers.InstancePrincipalsSecurityTokenSigner()
                client = ObjectStorageClient(config={}, signer=signer)
                logger.info("Using OCI instance principal authentication")
                return client

        except Exception as e:
            logger.debug(f"Instance principal auth failed: {e}")

        # Fall back to config file
        config_file = os.getenv('OCI_CONFIG_FILE', '~/.oci/config')
        config_profile = os.getenv('OCI_PROFILE', 'DEFAULT')

        try:
            config = from_file(
                file_location=os.path.expanduser(config_file),
                profile_name=config_profile
            )
            validate_config(config)
            client = ObjectStorageClient(config)
            logger.info(f"Using OCI config file: {config_file} (profile: {config_profile})")
            return client

        except Exception as e:
            raise RuntimeError(
                f"Failed to initialize OCI client. "
                f"Ensure ~/.oci/config exists or set OCI_USE_INSTANCE_PRINCIPAL=true. "
                f"Error: {e}"
            )

    def upload_file(self, local_path: str, remote_path: str) -> bool:
        """Upload file to OCI Object Storage."""
        try:
            local_file = Path(local_path)
            if not local_file.exists():
                raise FileNotFoundError(f"Local file not found: {local_path}")

            # Read file content
            with open(local_file, 'rb') as f:
                self.client.put_object(
                    namespace_name=self.namespace,
                    bucket_name=self.bucket_name,
                    object_name=remote_path,
                    put_object_body=f
                )

            logger.info(f"Uploaded {local_path} -> oci://{self.bucket_name}/{remote_path}")
            return True

        except Exception as e:
            logger.error(f"OCI upload failed: {e}")
            raise

    def download_file(self, remote_path: str, local_path: str) -> bool:
        """Download file from OCI Object Storage."""
        try:
            # Get object
            response = self.client.get_object(
                namespace_name=self.namespace,
                bucket_name=self.bucket_name,
                object_name=remote_path
            )

            # Save to local file
            local_file = Path(local_path)
            local_file.parent.mkdir(parents=True, exist_ok=True)

            with open(local_file, 'wb') as f:
                for chunk in response.data.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)

            logger.info(f"Downloaded oci://{self.bucket_name}/{remote_path} -> {local_path}")
            return True

        except oci.exceptions.ServiceError as e:
            if e.status == 404:
                raise FileNotFoundError(f"Remote file not found: {remote_path}")
            logger.error(f"OCI download failed: {e}")
            raise
        except Exception as e:
            logger.error(f"OCI download failed: {e}")
            raise

    def list_files(self, prefix: str = '') -> List[str]:
        """List files in OCI Object Storage with optional prefix."""
        try:
            files = []
            next_start = None

            # Paginate through all objects
            while True:
                if next_start:
                    response = self.client.list_objects(
                        namespace_name=self.namespace,
                        bucket_name=self.bucket_name,
                        prefix=prefix,
                        start=next_start
                    )
                else:
                    response = self.client.list_objects(
                        namespace_name=self.namespace,
                        bucket_name=self.bucket_name,
                        prefix=prefix
                    )

                # Add objects to list
                if response.data.objects:
                    files.extend([obj.name for obj in response.data.objects])

                # Check if there are more pages
                next_start = response.data.next_start_with
                if not next_start:
                    break

            logger.debug(f"Listed {len(files)} files with prefix '{prefix}'")
            return sorted(files)

        except Exception as e:
            logger.error(f"OCI list files failed: {e}")
            raise

    def delete_file(self, remote_path: str) -> bool:
        """Delete file from OCI Object Storage."""
        try:
            self.client.delete_object(
                namespace_name=self.namespace,
                bucket_name=self.bucket_name,
                object_name=remote_path
            )

            logger.info(f"Deleted oci://{self.bucket_name}/{remote_path}")
            return True

        except oci.exceptions.ServiceError as e:
            if e.status == 404:
                logger.warning(f"File not found for deletion: {remote_path}")
                return False
            logger.error(f"OCI delete failed: {e}")
            raise
        except Exception as e:
            logger.error(f"OCI delete failed: {e}")
            raise

    def file_exists(self, remote_path: str) -> bool:
        """Check if file exists in OCI Object Storage."""
        try:
            self.client.head_object(
                namespace_name=self.namespace,
                bucket_name=self.bucket_name,
                object_name=remote_path
            )
            return True

        except oci.exceptions.ServiceError as e:
            if e.status == 404:
                return False
            raise
        except Exception as e:
            logger.error(f"OCI file exists check failed: {e}")
            raise
