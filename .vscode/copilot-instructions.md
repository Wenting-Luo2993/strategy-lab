# GitHub Copilot Instructions for Strategy Lab

## Cloud Infrastructure & Deployment

**Current Infrastructure**: Cloud-hosted with containerized deployment
**Design Principle**: Cloud-agnostic implementation for future portability

### Cloud-Agnostic Configuration

**ALWAYS** use environment variables for cloud-specific configuration:

- **DO**: `bucket_name = os.getenv("STORAGE_BUCKET_NAME")`
- **DON'T**: `bucket_name = "oracle-specific-bucket-name"`
- **DO**: `cloud_region = os.getenv("CLOUD_REGION", "us-east-1")`
- **DON'T**: Reference specific cloud provider regions (e.g., `us-phoenix-1`)

**Rationale**: Environment variables abstract cloud-specific details, enabling easy migration between providers without code changes. Current deployment is on Oracle Cloud, but future deployments may target AWS, GCP, Azure, or other providers.

### Avoid Provider-Specific References

**NEVER** hardcode cloud provider specifics:

- **DON'T**: Import/reference `oci.*` modules directly in core logic
- **DON'T**: Use provider-specific resource identifiers (e.g., OCID, ARN)
- **DON'T**: Reference provider-specific services (e.g., "OCI Vault", "OCI Logging")
- **DO**: Use generic abstractions (e.g., "cloud storage", "secret management", "logging service")

**Example**:

```python
# DO: Generic approach
from src.utils.cloud import CloudStorage
storage = CloudStorage()
storage.upload_file("results/backtest.csv", "backups/")

# DON'T: Provider-specific
from oci.object_storage import ObjectStorageClient
client = ObjectStorageClient()
```

### Configuration via Environment Variables

**ALWAYS** use environment variables for deployment settings:

```python
import os

# Correct: cloud-agnostic
storage_type = os.getenv("STORAGE_TYPE", "local")  # local, s3, gcs, oci
region = os.getenv("CLOUD_REGION")
credentials_path = os.getenv("CLOUD_CREDENTIALS_PATH")

# Avoid hardcoding provider specifics
```

### Documentation Guidelines

When documenting deployment or configuration:

- **DO**: "Deploy to cloud storage (S3, GCS, OCI Object Storage)"
- **DO**: "Use environment variables for credentials"
- **DON'T**: "Deploy to Oracle OCI Object Storage"
- **DO**: "Deploy to container orchestration service (Kubernetes, Container Instances, ECS)"
- **DON'T**: "Deploy to Oracle Cloud Container Instances"

## Python Code Guidelines

### File Path Resolution

**ALWAYS** use `resolve_workspace_path` from `src.utils.workspace` when constructing file paths in Python code.

- **DO**: `state_path = resolve_workspace_path(f"data_cache/{filename}")`
- **DON'T**: `state_path = Path("data_cache") / filename`

**Rationale**: `resolve_workspace_path` ensures consistent path resolution relative to the Python workspace root, regardless of where the script is executed from. This prevents path resolution errors when running code from different directories.

**Example**:

```python
from src.utils.workspace import resolve_workspace_path

# Resolve relative paths against workspace root
cache_path = resolve_workspace_path("data_cache")
config_path = resolve_workspace_path("config/settings.json")
results_path = resolve_workspace_path("results/backtest")
```

### Cache File Safety

**NEVER** suggest removing cache files (`*_rolling_cache.parquet` or `*_indicators.pkl`) to force recalculation!

- **DON'T**: `Remove-Item data_cache\AAPL_5m.parquet`
- **DON'T**: Delete cache files to trigger fresh fetches

**Rationale**: Cache files contain historical data beyond the max_lookback_days window (default 59 days). Deleting them results in **permanent data loss** of older historical data that cannot be re-fetched from data sources like Yahoo Finance.

**Safe Testing Alternatives**:

- Use a different symbol for testing
- Copy cache files before testing and restore after
- Test with cache-only mode or mock data
- Use dedicated test cache directories

## General Guidelines

- Follow existing code patterns and conventions in the repository
- Write clear, concise docstrings for all public functions
- Use type hints where appropriate
- Keep functions focused and single-purpose
- Add tests for new functionality
