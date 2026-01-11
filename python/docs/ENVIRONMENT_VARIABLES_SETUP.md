# Environment Variables Setup & Security Guide

**Purpose**: This document explains how to manage secrets and configuration for the strategy-lab project, especially for Oracle Cloud deployment.

---

## 📋 Table of Contents

1. [Overview](#overview)
2. [Secrets Identified in Project](#secrets-identified-in-project)
3. [Local Development Setup](#local-development-setup)
4. [Environment Variables Reference](#environment-variables-reference)
5. [Oracle Cloud Deployment](#oracle-cloud-deployment)
6. [Best Practices](#best-practices)

---

## Overview

### Why Environment Variables?

- **Security**: Secrets are NOT stored in version control
- **Flexibility**: Different configs for dev/staging/production
- **24/7 Operations**: Configuration managed remotely without code changes
- **Compliance**: Follows security best practices for cloud deployment

### Current State

✅ **Already Protected** (in `.gitignore`):
- `finnhub_config.json` - Finnhub API credentials
- `.env` - Environment variables file
- `credentials/` - Service account files
- `gdrive_token.json` - Google Drive OAuth tokens

⚠️ **Needs Environment Variables**:
- Finnhub API Key
- Google Drive credentials/tokens
- (Future) Polygon, Alpaca, Discord, Slack, Email configs

---

## Secrets Identified in Project

### 1. **Finnhub API Key** ⭐ CRITICAL

**Type**: API Key
**Usage**: WebSocket connection, real-time market data
**Current Storage**: `src/config/finnhub_config.json`
**Environment Variable**: `FINNHUB_API_KEY`

**Location**: [src/config/finnhub_config_loader.py](../src/config/finnhub_config_loader.py)

```python
# Now supports environment variable
config = load_finnhub_config()  # Checks FINNHUB_API_KEY env var first
```

**Action Required**:
- ✅ Updated to support `FINNHUB_API_KEY` environment variable
- Set this on Oracle Cloud during deployment

---

### 2. **Google Drive Service Account** ⭐ CRITICAL

**Type**: Service Account JSON credentials
**Usage**: Upload backtest results to Google Drive
**Current Storage**: `credentials/service_account.json` (in `.gitignore`)
**Environment Variables**:
- `GOOGLE_SERVICE_ACCOUNT_KEY` - Path to JSON file
- Alternative: `GOOGLE_SERVICE_ACCOUNT_JSON` - Full JSON as string

**Location**: [main_secondary.py:145](../main_secondary.py#L145), [src/utils/google_drive_sync.py](../src/utils/google_drive_sync.py)

```python
credentials_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_KEY")
```

**Action Required**:
- Already using env variables in code
- Copy your service account JSON to remote server
- Set `GOOGLE_SERVICE_ACCOUNT_KEY` to the path

---

### 3. **Google Drive OAuth Credentials** ⭐ CRITICAL

**Type**: OAuth Client Secret + Token
**Usage**: User-based authentication for Google Drive
**Current Storage**: `.strategy_lab/gdrive_token.json` (in `.gitignore`)
**Environment Variables**:
- `GOOGLE_OAUTH_CLIENT_SECRET` - Path to OAuth credentials JSON
- `GOOGLE_OAUTH_CLIENT_TOKEN` - Path to OAuth token file

**Location**: [main_secondary.py:150-154](../main_secondary.py#L150)

```python
oauth_client_secret_path=Path(os.getenv("GOOGLE_OAUTH_CLIENT_SECRET")).absolute(),
oauth_token_path=Path(os.getenv("GOOGLE_OAUTH_CLIENT_TOKEN")).absolute()
```

**Action Required**:
- Already using env variables in code
- Set these paths on Oracle Cloud

---

### 4. **Google Drive Folder ID** (Non-secret, but important)

**Type**: Configuration
**Usage**: Target folder for syncing backtest results
**Environment Variable**: `GOOGLE_DRIVE_ROOT_FOLDER_ID`

**Location**: [main_secondary.py:146](../main_secondary.py#L146)

```python
root_folder_id = os.getenv("GOOGLE_DRIVE_ROOT_FOLDER_ID")
```

**Action Required**:
- Get this from your Google Drive folder URL
- Format: `https://drive.google.com/drive/folders/FOLDER_ID_HERE`

---

### 5. **Finnhub Configuration Parameters**

**Type**: Configuration
**Environment Variables**:

| Variable | Default | Purpose |
|----------|---------|---------|
| `FINNHUB_SYMBOLS` | AAPL,MSFT | Comma-separated tickers to trade |
| `FINNHUB_BAR_INTERVAL` | 5m | Bar interval (1m, 5m, 15m, etc.) |
| `FINNHUB_MARKET_TIMEZONE` | America/New_York | Market timezone |
| `FINNHUB_FILTER_AFTER_HOURS` | false | Exclude after-hours trades |
| `FINNHUB_RECONNECT_ENABLED` | true | Reconnect on connection loss |
| `FINNHUB_REST_API_ENABLED` | true | Enable REST API fallback |

**Location**: [src/config/finnhub_config_loader.py:129](../src/config/finnhub_config_loader.py#L129)

---

### 6. **Future: Polygon.io API Key**

**Type**: API Key
**Purpose**: Alternative data source for historical data
**Status**: ❌ Not yet integrated
**Environment Variable**: `POLYGON_API_KEY`

**Reference**: [src/data/polygon.py](../src/data/polygon.py)

```python
@register_loader("polygon")
class PolygonDataLoader(DataLoader):
    # TODO: Implement with API key from os.getenv("POLYGON_API_KEY")
```

---

### 7. **Future: Notification Services**

**Type**: Webhook URLs / API Keys
**Status**: ❌ Not yet integrated
**Environment Variables**:
- `DISCORD_WEBHOOK_URL` - Discord trade alerts
- `SLACK_WEBHOOK_URL` - Slack trade alerts
- `SMTP_SERVER`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD` - Email alerts

---

## Local Development Setup

### Step 1: Create `.env` file

```bash
# In project root (d:\development\strategy-lab)
cp .env.template .env
```

### Step 2: Edit `.env` with your secrets

**Windows**:
```powershell
notepad .env
```

**macOS/Linux**:
```bash
nano .env
```

### Step 3: Add your credentials

```env
# Required for Finnhub WebSocket
FINNHUB_API_KEY=your_actual_api_key_from_finnhub_io

# Required for Google Drive sync
GOOGLE_SERVICE_ACCOUNT_KEY=path/to/service_account.json
GOOGLE_OAUTH_CLIENT_SECRET=path/to/oauth_client.json
GOOGLE_OAUTH_CLIENT_TOKEN=~/.strategy_lab/gdrive_token.json
GOOGLE_DRIVE_ROOT_FOLDER_ID=your_folder_id
```

### Step 4: Load environment variables

**Python**: Automatically loaded by `python-dotenv` (added to requirements)

```python
from dotenv import load_dotenv
import os

load_dotenv()
api_key = os.getenv("FINNHUB_API_KEY")
```

**Terminal** (optional):
```bash
# Windows PowerShell
Get-Content .env | ForEach-Object {
    $key, $value = $_ -split "=", 2
    if ($key -and -not $key.StartsWith("#")) {
        [Environment]::SetEnvironmentVariable($key, $value, "Process")
    }
}

# Unix/macOS
set -a
source .env
set +a
```

---

## Environment Variables Reference

### Complete List

See [.env.template](./.env.template) for the full template with all available variables.

### Finnhub Configuration

```env
# API Authentication
FINNHUB_API_KEY=your_key_here

# Connection Settings
FINNHUB_WEBSOCKET_URL=wss://ws.finnhub.io
FINNHUB_BAR_INTERVAL=5m
FINNHUB_BAR_DELAY_SECONDS=5

# Trading Configuration
FINNHUB_SYMBOLS=AAPL,MSFT,NVDA,TSLA,AMD
FINNHUB_FILTER_AFTER_HOURS=false
FINNHUB_MARKET_TIMEZONE=America/New_York
FINNHUB_REGULAR_START=09:30
FINNHUB_REGULAR_END=16:00

# Connection Resilience
FINNHUB_RECONNECT_ENABLED=true
FINNHUB_RECONNECT_MAX_ATTEMPTS=10
FINNHUB_RECONNECT_INITIAL_BACKOFF=1
FINNHUB_RECONNECT_MAX_BACKOFF=60

# API Features
FINNHUB_REST_API_ENABLED=true
FINNHUB_REST_API_CACHE_TTL=3600
```

### Google Drive Configuration

```env
# Credentials Paths
GOOGLE_SERVICE_ACCOUNT_KEY=credentials/service_account.json
GOOGLE_OAUTH_CLIENT_SECRET=credentials/oauth_client.json
GOOGLE_OAUTH_CLIENT_TOKEN=~/.strategy_lab/gdrive_token.json

# Configuration
GOOGLE_DRIVE_ROOT_FOLDER_ID=your_folder_id_here
```

### Deployment Configuration

```env
ENVIRONMENT=production  # or development, staging
LOG_LEVEL=INFO          # DEBUG, INFO, WARNING, ERROR, CRITICAL
CACHE_DIR=data_cache
RESULTS_DIR=results
```

---

## Oracle Cloud Deployment

### Architecture

```
Oracle Cloud Always Free Tier
├── Container Instance
│   ├── Environment Variables (OCI Console)
│   │   ├── FINNHUB_API_KEY
│   │   ├── GOOGLE_*
│   │   └── Other configs
│   └── Mounted Storage (optional)
│       └── credentials/
└── Object Storage (optional)
    └── Results backup
```

### Option 1: Container Instance (Recommended for Always Free)

#### Step 1: Create Container Image

```dockerfile
# Dockerfile (already exists)
FROM python:3.10-slim

WORKDIR /app

# Copy requirements and install
COPY python/requirements.txt .
RUN pip install -r requirements.txt

# Copy code
COPY python/src ./src
COPY python/main.py .
COPY python/orchestrator_main.py .

# Set up non-root user
RUN useradd -m appuser
USER appuser

# Default command
CMD ["python", "orchestrator_main.py"]
```

#### Step 2: Push to OCI Container Registry

```bash
# Authenticate with OCI
docker login ocir.io

# Tag image
docker tag strategy-lab:latest ocir.io/<region>/<tenancy>/<repo>/strategy-lab:latest

# Push
docker push ocir.io/<region>/<tenancy>/<repo>/strategy-lab:latest
```

#### Step 3: Create Container Instance in OCI Console

1. **Compute** → **Container Instances** → **Create Container Instance**

2. **Container Image Details**:
   - Image: Your pushed image URL
   - Image pull secrets: (if private repo)

3. **Environment Variables** (THIS IS KEY):
   ```
   FINNHUB_API_KEY          = your_api_key
   GOOGLE_SERVICE_ACCOUNT_KEY = /app/credentials/service_account.json
   GOOGLE_DRIVE_ROOT_FOLDER_ID = your_folder_id
   LOG_LEVEL                = INFO
   ENVIRONMENT              = production
   ```

4. **Volumes** (if needed):
   - Mount config directory for credentials
   - Mount results directory to Object Storage

5. **Resource Configuration**:
   - OCPU: 1 (Always Free)
   - Memory: 1 GB (Always Free)

6. **Networking**:
   - Subnet: Your VCN subnet
   - Public IP: Assign if needed

7. **Click Create**

#### Step 4: Manage Credentials

**Option A: Baked into Image** (⚠️ Less secure)
```dockerfile
COPY credentials/service_account.json /app/credentials/
```

**Option B: Mounted Volume** (Recommended)
- Use OCI File Storage or Object Storage
- Mount to `/app/credentials`
- Reference in env var: `/app/credentials/service_account.json`

**Option C: Environment Variable as Base64** (Most secure)
```env
# In OCI Console, encode your JSON:
GOOGLE_SERVICE_ACCOUNT_JSON=base64_encoded_json_here

# In Python:
import base64
import json
creds_json = base64.b64decode(os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON"))
```

#### Step 5: Monitor Container

```bash
# View logs in OCI Console or via CLI
oci container-instances container list --compartment-id <id>
oci container-instances logs get --container-instance-id <id>
```

### Option 2: Compute Instance (More Control)

#### Step 1: Launch Compute Instance

1. **Compute** → **Instances** → **Create Instance**
2. **Image**: Ubuntu 22.04 LTS (Always Free eligible)
3. **Shape**: VM.Standard.E2.1.Micro (Always Free)

#### Step 2: SSH into Instance

```bash
ssh ubuntu@<public_ip> -i /path/to/private/key
```

#### Step 3: Install Dependencies

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install Python and Git
sudo apt install -y python3.10 python3-pip git

# Clone repository
git clone https://github.com/your-repo/strategy-lab.git
cd strategy-lab/python

# Install Python dependencies
pip install -r requirements.txt
```

#### Step 4: Set Environment Variables

**Option A: In `.env` file**
```bash
cat > .env << EOF
FINNHUB_API_KEY=your_key_here
GOOGLE_SERVICE_ACCOUNT_KEY=/home/ubuntu/strategy-lab/credentials/service_account.json
GOOGLE_DRIVE_ROOT_FOLDER_ID=your_folder_id
LOG_LEVEL=INFO
ENVIRONMENT=production
EOF
```

**Option B: System-wide** (in `/etc/environment` or `~/.bashrc`)
```bash
sudo tee -a /etc/environment << EOF
FINNHUB_API_KEY="your_key_here"
GOOGLE_SERVICE_ACCOUNT_KEY="/home/ubuntu/strategy-lab/credentials/service_account.json"
GOOGLE_DRIVE_ROOT_FOLDER_ID="your_folder_id"
EOF
```

**Option C: Systemd Service** (Recommended for 24/7)

Create `/etc/systemd/system/strategy-lab.service`:

```ini
[Unit]
Description=Strategy Lab Trading Bot
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/strategy-lab/python

# Set environment variables
EnvironmentFile=/home/ubuntu/strategy-lab/.env

# Run command
ExecStart=/usr/bin/python3 orchestrator_main.py

# Restart on failure
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable strategy-lab
sudo systemctl start strategy-lab
sudo systemctl status strategy-lab
sudo journalctl -u strategy-lab -f  # Follow logs
```

#### Step 5: Copy Credentials

```bash
# From local machine
scp -i /path/to/key credentials/service_account.json ubuntu@<ip>:~/strategy-lab/credentials/

# Or in SSH session
# nano /home/ubuntu/strategy-lab/credentials/service_account.json
# Paste content
```

#### Step 6: Monitor & Logs

```bash
# Check running process
ps aux | grep orchestrator_main.py

# View recent logs
journalctl -u strategy-lab -n 100

# Check disk/memory
df -h
free -h
```

### Security Best Practices for Oracle Cloud

#### 1. Use OCI Vault for Secrets

```bash
# Store API keys in OCI Vault
oci vault secret create \
  --vault-id <vault_id> \
  --secret-name finnhub-api-key \
  --secret-content-details content=your_api_key
```

Reference in code:
```python
from oci.secrets import SecretsClient
secrets_client = SecretsClient(config)
api_key = secrets_client.get_secret_bundle(secret_id)['data']
```

#### 2. Use IAM Policies

Restrict who can view environment variables/secrets:
```
Allow group DevOps to manage container-instances in compartment StrategyLab
Allow group DevOps to read vaults in compartment StrategyLab
```

#### 3. Network Security

- Use Private Subnet (no public IP if possible)
- Network Security Groups restrict outbound:
  - Allow: `wss://ws.finnhub.io:443` (Finnhub WebSocket)
  - Allow: `https://www.googleapis.com` (Google Drive API)
  - Allow: DNS (port 53)
  - Deny: Everything else

#### 4. Monitoring & Alerts

```bash
# Enable audit logs
oci audit query --start-time 2025-01-01T00:00:00Z \
  --query-text 'resourceName = "/strategy-lab"'

# Set up alarms
oci monitoring alarm create \
  --compartment-id <id> \
  --display-name "High Memory Usage" \
  --metric-namespace compute \
  --metric-name MemoryUtilization \
  --threshold 80
```

#### 5. Rotate Credentials Regularly

Set reminders to:
- Regenerate Finnhub API keys (if compromised)
- Rotate Google service account keys (annually)
- Update OAuth tokens

---

## Best Practices

### Local Development ✅

1. **Never commit `.env`** to version control
   ```bash
   # Already in .gitignore
   echo ".env" >> .gitignore
   ```

2. **Use `.env.template`** as reference
   - Commit this file
   - Contains dummy values
   - Others copy and fill in

3. **Use strong credentials**
   - Long API keys (40+ characters)
   - Unique tokens per environment
   - Rotate keys regularly

4. **Log without sensitive data**
   ```python
   # Good: Mask in logs
   logger.info(f"API Key: {api_key[:8]}...{api_key[-4:]}")

   # Bad: Log full key
   logger.info(f"API Key: {api_key}")
   ```

### Production Deployment ✅

1. **Use environment variables ONLY**
   - No hardcoded secrets
   - No config files with real values
   - Use OCI Vault when available

2. **Restrict access**
   - Limited IAM policies
   - VPN or private network
   - No public IPs if possible

3. **Monitor & audit**
   - Enable logging
   - Set up alerts
   - Review access logs regularly

4. **Backup & recovery**
   - Export backups securely
   - Test recovery procedures
   - Document all credentials in secure location

5. **Incident response**
   - Have plan if credentials leak
   - Know how to rotate quickly
   - Monitor for unauthorized usage

---

## Troubleshooting

### "FINNHUB_API_KEY not found"

```python
# Solution 1: Create .env file in project root
cp .env.template .env
# Edit .env with your API key

# Solution 2: Set in environment
export FINNHUB_API_KEY="your_key_here"

# Solution 3: Check if dotenv is installed
pip install python-dotenv
```

### "GOOGLE_SERVICE_ACCOUNT_KEY path not found"

```python
# Solution 1: Verify file exists
ls -la credentials/service_account.json

# Solution 2: Check env var is set correctly
echo $GOOGLE_SERVICE_ACCOUNT_KEY

# Solution 3: Use absolute path
export GOOGLE_SERVICE_ACCOUNT_KEY="/home/ubuntu/strategy-lab/credentials/service_account.json"
```

### Oracle Cloud Container Starts But Then Stops

```bash
# 1. Check logs in OCI Console
# 2. View container exit code (check logs for error messages)
# 3. Common issues:
#    - Missing environment variables
#    - File not found (credentials path)
#    - Import error in Python code
# 4. Test locally first:
docker run -e FINNHUB_API_KEY=test strategy-lab:latest
```

### 24/7 Operation Issues

```bash
# Check if process is still running
ps aux | grep orchestrator_main

# If crashed, check logs
journalctl -u strategy-lab -n 50

# Restart if needed
sudo systemctl restart strategy-lab

# Check disk space (can cause stops)
df -h
du -sh *

# Check memory
free -h
```

---

## Summary

| Component | Status | Action |
|-----------|--------|--------|
| Finnhub API Key | ✅ Ready | Set `FINNHUB_API_KEY` on deployment |
| Google Drive | ✅ Ready | Set `GOOGLE_*` paths on deployment |
| Polygon API | ❌ Future | Create env var when implemented |
| Notifications | ❌ Future | Add webhook env vars when implemented |

**Next Steps**:
1. Copy `.env.template` to `.env` locally
2. Fill in your credentials
3. Test locally: `python scripts/test_finnhub_config.py`
4. Deploy to Oracle Cloud with env vars set

---

**Document Version**: 1.0
**Last Updated**: 2025-12-30
**Status**: Ready for Production Deployment
