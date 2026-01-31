# Cloud Storage Provider Configuration Guide

This guide explains how to configure each cloud storage provider for database backup and sync.

## Quick Start

Set the `CLOUD_STORAGE_PROVIDER` environment variable to choose your provider:

```bash
# Options: local, oracle, aws, gcp, azure
export CLOUD_STORAGE_PROVIDER=oracle
```

---

## Local Storage (Testing)

**Use Case**: Local development and testing without cloud credentials

**Configuration**:
```bash
export CLOUD_STORAGE_PROVIDER=local
# Optional: Custom storage path (default: data/cloud_storage)
# No credentials needed
```

**Example**:
```python
from src.cloud.storage_factory import get_storage_provider

storage = get_storage_provider('local')
storage.upload_file('data/trades.db', 'backups/trades.db')
```

Files are stored in `data/cloud_storage/` directory.

---

## Oracle Cloud Infrastructure (OCI)

**Use Case**: Running on Oracle Cloud Always Free tier

### Prerequisites
```bash
pip install oci
```

### Option 1: Instance Principal (Recommended for OCI Instances)

If running on an OCI compute instance:

```bash
export CLOUD_STORAGE_PROVIDER=oracle
export OCI_USE_INSTANCE_PRINCIPAL=true
export OCI_BUCKET_NAME=strategy-lab-backups
export OCI_NAMESPACE=your-namespace
```

### Option 2: Config File (For Local Development)

1. Create `~/.oci/config`:
```ini
[DEFAULT]
user=ocid1.user.oc1..aaaaaaaexample
fingerprint=12:34:56:78:90:ab:cd:ef:12:34:56:78:90:ab:cd:ef
tenancy=ocid1.tenancy.oc1..aaaaaaaexample
region=us-ashburn-1
key_file=~/.oci/oci_api_key.pem
```

2. Set environment variables:
```bash
export CLOUD_STORAGE_PROVIDER=oracle
export OCI_BUCKET_NAME=strategy-lab-backups
export OCI_NAMESPACE=your-namespace

# Optional
export OCI_CONFIG_FILE=~/.oci/config  # Default
export OCI_PROFILE=DEFAULT            # Default
```

### Creating an OCI Bucket

```bash
# Via OCI CLI
oci os bucket create --name strategy-lab-backups --compartment-id <compartment-ocid>

# Via Console: Storage > Buckets > Create Bucket
```

### Getting Your Namespace

```bash
# Via OCI CLI
oci os ns get

# Via Console: Tenancy Details page
```

---

## Microsoft Azure Blob Storage

**Use Case**: Running on Azure or prefer Azure services

### Prerequisites
```bash
pip install azure-storage-blob
```

### Option 1: Connection String (Easiest)

Get connection string from Azure Portal: Storage Account > Access Keys

```bash
export CLOUD_STORAGE_PROVIDER=azure
export AZURE_STORAGE_CONNECTION_STRING="DefaultEndpointsProtocol=https;AccountName=myaccount;AccountKey=...;EndpointSuffix=core.windows.net"
export AZURE_CONTAINER_NAME=strategy-lab-backups
```

### Option 2: Account Name + Key

```bash
export CLOUD_STORAGE_PROVIDER=azure
export AZURE_STORAGE_ACCOUNT_NAME=mystorageaccount
export AZURE_STORAGE_ACCOUNT_KEY=abc123...
export AZURE_CONTAINER_NAME=strategy-lab-backups
```

### Option 3: Account Name + SAS Token

```bash
export CLOUD_STORAGE_PROVIDER=azure
export AZURE_STORAGE_ACCOUNT_NAME=mystorageaccount
export AZURE_STORAGE_SAS_TOKEN=sv=2020-08-04&ss=b&srt=...
export AZURE_CONTAINER_NAME=strategy-lab-backups
```

### Creating an Azure Container

```bash
# Via Azure CLI
az storage container create --name strategy-lab-backups --account-name mystorageaccount

# Via Portal: Storage Account > Containers > + Container
```

---

## Amazon Web Services (AWS S3)

**Use Case**: Running on AWS EC2 or prefer AWS services

### Prerequisites
```bash
pip install boto3
```

### Option 1: IAM Role (Recommended for EC2)

If running on EC2 with IAM role attached:

```bash
export CLOUD_STORAGE_PROVIDER=aws
export AWS_S3_BUCKET=strategy-lab-backups
export AWS_REGION=us-east-1
```

### Option 2: Access Keys

```bash
export CLOUD_STORAGE_PROVIDER=aws
export AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE
export AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY
export AWS_S3_BUCKET=strategy-lab-backups
export AWS_REGION=us-east-1
```

### Creating an S3 Bucket

```bash
# Via AWS CLI
aws s3 mb s3://strategy-lab-backups --region us-east-1

# Via Console: S3 > Create bucket
```

---

## Google Cloud Platform (GCP)

**Use Case**: Running on GCP Compute Engine or prefer GCP services

### Prerequisites
```bash
pip install google-cloud-storage
```

### Option 1: Service Account (Recommended)

1. Create service account and download JSON key
2. Set environment variables:

```bash
export CLOUD_STORAGE_PROVIDER=gcp
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account-key.json
export GCP_BUCKET_NAME=strategy-lab-backups
export GCP_PROJECT_ID=my-project-id
```

### Option 2: Compute Engine Default Credentials

If running on GCP Compute Engine:

```bash
export CLOUD_STORAGE_PROVIDER=gcp
export GCP_BUCKET_NAME=strategy-lab-backups
export GCP_PROJECT_ID=my-project-id
```

### Creating a GCS Bucket

```bash
# Via gcloud CLI
gsutil mb -p my-project-id -l us-central1 gs://strategy-lab-backups/

# Via Console: Cloud Storage > Create bucket
```

---

## Using in Code

### Automatic Provider Selection

```python
from src.cloud.storage_factory import get_storage_provider

# Uses CLOUD_STORAGE_PROVIDER env var
storage = get_storage_provider()

# Upload
storage.upload_file('data/trades.db', 'backups/trades_20260131.db')

# Download
storage.download_file('backups/trades_20260131.db', 'data/restored.db')

# List
files = storage.list_files('backups/')
print(f"Found {len(files)} backup files")

# Delete old backups
for file in files:
    if '202601' in file:  # January backups
        storage.delete_file(file)
```

### Force Specific Provider

```python
# Override env var
storage = get_storage_provider('azure')
```

### With Database Sync Daemon

```python
from src.cloud.storage_factory import get_storage_provider
from src.cloud.database_sync import DatabaseSyncDaemon

storage = get_storage_provider()  # Uses env CLOUD_STORAGE_PROVIDER
sync = DatabaseSyncDaemon(
    db_path='data/trades.db',
    storage_provider=storage,
    sync_interval=300  # 5 minutes
)

sync.start()  # Start background sync
# ... bot runs ...
sync.stop()   # Graceful shutdown with final sync
```

---

## Environment Variables Summary

### Common
- `CLOUD_STORAGE_PROVIDER` - Provider name (local/oracle/aws/gcp/azure)

### Oracle Cloud
- `OCI_BUCKET_NAME` - Bucket name (required)
- `OCI_NAMESPACE` - Namespace (required)
- `OCI_USE_INSTANCE_PRINCIPAL` - Use instance principal (optional, true/false)
- `OCI_CONFIG_FILE` - Config file path (optional, default: ~/.oci/config)
- `OCI_PROFILE` - Config profile (optional, default: DEFAULT)

### Azure
- `AZURE_CONTAINER_NAME` - Container name (required)
- `AZURE_STORAGE_CONNECTION_STRING` - Connection string (option 1)
- `AZURE_STORAGE_ACCOUNT_NAME` - Account name (option 2 & 3)
- `AZURE_STORAGE_ACCOUNT_KEY` - Account key (option 2)
- `AZURE_STORAGE_SAS_TOKEN` - SAS token (option 3)

### AWS
- `AWS_S3_BUCKET` - Bucket name (required)
- `AWS_REGION` - Region (required)
- `AWS_ACCESS_KEY_ID` - Access key (optional if using IAM role)
- `AWS_SECRET_ACCESS_KEY` - Secret key (optional if using IAM role)

### GCP
- `GCP_BUCKET_NAME` - Bucket name (required)
- `GCP_PROJECT_ID` - Project ID (required)
- `GOOGLE_APPLICATION_CREDENTIALS` - Service account JSON path (optional)

---

## Switching Providers

To switch from one cloud to another, just update environment variables:

```bash
# Switch from Oracle to Azure
export CLOUD_STORAGE_PROVIDER=azure
export AZURE_STORAGE_CONNECTION_STRING="..."
export AZURE_CONTAINER_NAME=strategy-lab-backups

# No code changes needed!
docker-compose restart
```

---

## Troubleshooting

### "Provider not installed" error

Install the required SDK:
```bash
pip install oci              # Oracle
pip install azure-storage-blob  # Azure
pip install boto3            # AWS
pip install google-cloud-storage  # GCP
```

### Authentication errors

1. **Oracle**: Check `~/.oci/config` file exists and is valid
2. **Azure**: Verify connection string or credentials
3. **AWS**: Check `~/.aws/credentials` or IAM role
4. **GCP**: Verify service account JSON file path

### File not found errors

- Ensure bucket/container exists
- Check bucket/container name is correct
- Verify credentials have read/write permissions

### Slow uploads/downloads

- Choose a region close to your compute instance
- Use instance credentials (faster than API keys)
- Check network connectivity

---

## Best Practices

1. **Use instance credentials** when running in cloud (faster, more secure)
2. **Test with local provider** before deploying to cloud
3. **Set up lifecycle policies** in cloud to auto-delete old backups
4. **Monitor storage costs** - enable billing alerts
5. **Encrypt sensitive data** before uploading
6. **Use dedicated buckets** for different environments (dev/staging/prod)

---

## Cost Optimization

### Oracle Cloud Always Free
- Object Storage: 10 GB free (no time limit)
- Perfect for database backups

### Azure
- Free tier: First 5 GB
- Lifecycle management: Auto-delete old backups

### AWS
- Free tier: 5 GB (12 months)
- S3 Glacier: Cheaper for long-term storage

### GCP
- Free tier: 5 GB
- Nearline/Coldline: Cheaper for infrequent access

**Recommendation**: Oracle Cloud Always Free for truly free forever storage!
