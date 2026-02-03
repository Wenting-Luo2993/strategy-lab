# Cloud Provider Configuration Examples

## Quick Reference: Deploying to Different Clouds

This document shows how to deploy the same trading bot to different cloud providers **without any code changes** - just by changing environment variables.

---

## The Key Insight

**Cloud provider is set via environment variable, NOT hardcoded in YAML:**

```bash
# All you need to change between clouds:
STORAGE__CLOUD_PROVIDER=oracle  # or aws, azure, gcp, local
```

**Same YAML configs work everywhere!**

---

## Side-by-Side Comparison

### Oracle Cloud Free Tier

```bash
# .env
TRADING_ENV=prod
STORAGE__CLOUD_SYNC_ENABLED=true
STORAGE__CLOUD_PROVIDER=oracle

# Oracle credentials
STORAGE__ORACLE_BUCKET_NAME=trading-bot-backups
STORAGE__ORACLE_NAMESPACE=your-namespace
OCI_CONFIG_FILE=/home/ubuntu/.oci/config
OCI_CONFIG_PROFILE=DEFAULT

# Same for all providers:
DATA__FINNHUB_API_KEY=your_key
NOTIFICATION__DISCORD_WEBHOOK_URL=your_webhook
```

**Deploy:**
```bash
ssh ubuntu@oracle-vm
docker-compose up -d
```

**Cost:** $0 (Always Free tier: 20GB storage)

---

### AWS (Amazon EC2 + S3)

```bash
# .env
TRADING_ENV=prod
STORAGE__CLOUD_SYNC_ENABLED=true
STORAGE__CLOUD_PROVIDER=aws  # <-- ONLY CHANGE

# AWS credentials
STORAGE__AWS_BUCKET_NAME=trading-bot-backups
STORAGE__AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=...

# Same for all providers:
DATA__FINNHUB_API_KEY=your_key
NOTIFICATION__DISCORD_WEBHOOK_URL=your_webhook
```

**Deploy:**
```bash
ssh ubuntu@aws-ec2
docker-compose up -d  # Same command!
```

**Cost:** ~$10-15/month (t3.small + S3)

---

### Azure (VM + Blob Storage)

```bash
# .env
TRADING_ENV=prod
STORAGE__CLOUD_SYNC_ENABLED=true
STORAGE__CLOUD_PROVIDER=azure  # <-- ONLY CHANGE

# Azure credentials
STORAGE__AZURE_CONTAINER_NAME=trading-bot-backups
STORAGE__AZURE_STORAGE_ACCOUNT_NAME=mystorageaccount
AZURE_STORAGE_CONNECTION_STRING=DefaultEndpointsProtocol=https;AccountName=...

# Same for all providers:
DATA__FINNHUB_API_KEY=your_key
NOTIFICATION__DISCORD_WEBHOOK_URL=your_webhook
```

**Deploy:**
```bash
ssh azureuser@azure-vm
docker-compose up -d  # Same command!
```

**Cost:** ~$10-20/month (B1s VM + Blob Storage)

---

### Google Cloud Platform (Compute Engine + Cloud Storage)

```bash
# .env
TRADING_ENV=prod
STORAGE__CLOUD_SYNC_ENABLED=true
STORAGE__CLOUD_PROVIDER=gcp  # <-- ONLY CHANGE

# GCP credentials
STORAGE__GCP_BUCKET_NAME=trading-bot-backups
STORAGE__GCP_PROJECT_ID=my-project-123
GOOGLE_APPLICATION_CREDENTIALS=/app/config/gcp-key.json

# Same for all providers:
DATA__FINNHUB_API_KEY=your_key
NOTIFICATION__DISCORD_WEBHOOK_URL=your_webhook
```

**Deploy:**
```bash
ssh user@gcp-vm
docker-compose up -d  # Same command!
```

**Cost:** ~$10-15/month (e2-micro + Cloud Storage)

---

## Migration Scenario: Oracle → AWS

### Current Deployment (Oracle)

```bash
# Running on: oracle-vm (123.45.67.89)
# .env contains: STORAGE__CLOUD_PROVIDER=oracle
```

### Migration Steps

**1. Prepare AWS Environment**
```bash
# Create S3 bucket
aws s3 mb s3://trading-bot-backups

# Launch EC2 instance (or use existing)
# Security group: Allow 22 (SSH), 8080 (API), 8501 (Streamlit)
```

**2. Update Configuration (Only .env)**
```bash
# Create .env.aws (copy from .env.oracle)
cp .env.oracle .env.aws

# Edit .env.aws - only change these lines:
STORAGE__CLOUD_PROVIDER=aws  # Changed from oracle
STORAGE__AWS_BUCKET_NAME=trading-bot-backups
STORAGE__AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=...

# Remove Oracle-specific vars:
# STORAGE__ORACLE_BUCKET_NAME
# STORAGE__ORACLE_NAMESPACE
```

**3. Deploy to AWS**
```bash
# Copy files to AWS
scp .env.aws ubuntu@aws-ec2:/app/.env
scp config/prod.yaml ubuntu@aws-ec2:/app/config/  # SAME YAML!
scp docker-compose.yml ubuntu@aws-ec2:/app/

# Start on AWS
ssh ubuntu@aws-ec2
cd /app
docker-compose up -d
```

**4. Migrate Historical Data (Optional)**
```bash
# Export from Oracle SQLite
ssh ubuntu@oracle-vm
docker exec trading-bot sqlite3 /app/data/trading.db .dump > backup.sql

# Import to AWS
scp ubuntu@oracle-vm:backup.sql .
scp backup.sql ubuntu@aws-ec2:/app/
ssh ubuntu@aws-ec2
docker exec -i trading-bot sqlite3 /app/data/trading.db < /app/backup.sql
```

**5. Update Streamlit Cloud (if using)**
```toml
# .streamlit/secrets.toml
# Update API URL to AWS public IP
[api]
base_url = "https://54.123.45.67:8080"  # New AWS IP
api_key = "same-api-key"  # No change
```

**6. Verify & Cutover**
```bash
# Test AWS deployment
curl http://54.123.45.67:8080/health/live
# Dashboard: https://your-app.streamlit.app

# If working, shut down Oracle
ssh ubuntu@oracle-vm
docker-compose down
```

**Total Migration Time:** ~30 minutes
**Code Changes Required:** 0
**Config File Changes:** Just .env

---

## Multi-Region Deployment

Deploy to multiple regions for redundancy:

### Primary: Oracle Cloud (Phoenix, US)
```bash
# phoenix-vm
STORAGE__CLOUD_PROVIDER=oracle
STORAGE__ORACLE_BUCKET_NAME=trading-bot-phoenix
```

### Backup: AWS (Virginia, US)
```bash
# virginia-ec2
STORAGE__CLOUD_PROVIDER=aws
STORAGE__AWS_BUCKET_NAME=trading-bot-virginia
STORAGE__AWS_REGION=us-east-1
```

### Backup: Azure (Europe)
```bash
# europe-vm
STORAGE__CLOUD_PROVIDER=azure
STORAGE__AZURE_CONTAINER_NAME=trading-bot-europe
```

**Same code, same YAML, different clouds!**

---

## Cost Comparison (Monthly, ~720 hours)

| Provider | VM | Storage | Egress | Total | Free Tier |
|----------|-----|---------|--------|-------|-----------|
| **Oracle** | $0 | $0 | $0 | **$0** | ✅ Always Free (ARM, 24GB RAM) |
| **AWS** | $7.50 | $0.50 | $2 | **$10** | ✅ 12 months free (t2.micro) |
| **Azure** | $10 | $1 | $3 | **$14** | ✅ 12 months free (B1s) |
| **GCP** | $8 | $0.50 | $2 | **$10.50** | ✅ $300 credit (90 days) |

**Recommendation:** Start with Oracle Cloud (Always Free), keep configs cloud-agnostic, migrate easily if needed.

---

## Environment Variable Reference

### Required for All Clouds
```bash
TRADING_ENV=prod
STORAGE__CLOUD_SYNC_ENABLED=true
STORAGE__CLOUD_PROVIDER=<provider>  # Key variable!
STORAGE__CLOUD_SYNC_INTERVAL_MINUTES=5
```

### Oracle-Specific
```bash
STORAGE__ORACLE_BUCKET_NAME=bucket-name
STORAGE__ORACLE_NAMESPACE=namespace
OCI_CONFIG_FILE=/path/to/oci_config
OCI_CONFIG_PROFILE=DEFAULT
```

### AWS-Specific
```bash
STORAGE__AWS_BUCKET_NAME=bucket-name
STORAGE__AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=...
```

### Azure-Specific
```bash
STORAGE__AZURE_CONTAINER_NAME=container-name
STORAGE__AZURE_STORAGE_ACCOUNT_NAME=account-name
AZURE_STORAGE_CONNECTION_STRING=DefaultEndpoints...
```

### GCP-Specific
```bash
STORAGE__GCP_BUCKET_NAME=bucket-name
STORAGE__GCP_PROJECT_ID=project-id
GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json
```

---

## Summary

✅ **One codebase, all clouds**
✅ **Change one env var to switch providers**
✅ **No YAML config changes needed**
✅ **Easy migration between clouds**
✅ **Multi-cloud redundancy possible**

**The trading bot is truly cloud-agnostic!** 🚀
