# Oracle Cloud 24/7 Deployment Guide

**Purpose**: Step-by-step guide to deploy strategy-lab on Oracle Cloud Always Free tier with 24/7 operation.

---

## 📋 Quick Reference

- **Target**: Oracle Cloud Always Free tier
- **Cost**: $0 (subject to Always Free limits)
- **Uptime**: 24/7 with automatic restart on failure
- **Compute**: 2 OCPUs, 12 GB RAM total
- **Storage**: 100 GB Object Storage, Block Storage volumes
- **Estimated Time**: 30-45 minutes setup

---

## Prerequisites

✅ **What you need**:
- OCI Account (free tier)
- Finnhub API Key (free at https://finnhub.io/register)
- Google Drive folder for results backup
- Google Service Account JSON (for Drive sync)
- Git installed locally
- Docker installed (for local testing)

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│                   Oracle Cloud                          │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  Option 1: Container Instance (Easiest)              │
│  ┌───────────────────────────────────────────────┐   │
│  │ Docker Container                              │   │
│  │ ├─ Python 3.10 runtime                        │   │
│  │ ├─ strategy-lab code                          │   │
│  │ ├─ Environment Variables (Secrets)            │   │
│  │ │  ├─ FINNHUB_API_KEY                        │   │
│  │ │  ├─ GOOGLE_SERVICE_ACCOUNT_KEY             │   │
│  │ │  └─ GOOGLE_DRIVE_ROOT_FOLDER_ID            │   │
│  │ └─ orchestrator_main.py (24/7 runner)         │   │
│  └───────────────────────────────────────────────┘   │
│                        ↓                               │
│         (or)                                          │
│                                                         │
│  Option 2: Compute Instance (More Control)           │
│  ┌───────────────────────────────────────────────┐   │
│  │ Ubuntu 22.04 LTS Instance                     │   │
│  │ ├─ Python 3.10 + pip                          │   │
│  │ ├─ Git repository                             │   │
│  │ ├─ .env file with secrets                     │   │
│  │ └─ Systemd service (auto-restart)             │   │
│  │    └─ orchestrator_main.py                    │   │
│  └───────────────────────────────────────────────┘   │
│                                                         │
│  Backup & Results:                                    │
│  ┌───────────────────────────────────────────────┐   │
│  │ Object Storage (Optional)                     │   │
│  │ └─ Backup of results/ directory               │   │
│  └───────────────────────────────────────────────┘   │
│                                                         │
└─────────────────────────────────────────────────────────┘
         ↓ Outbound Connections
    ┌────────────┬────────────────┐
    ↓            ↓                ↓
 Finnhub      Google Drive    DNS (logs)
wss://       API.googleapis  53
ws.finnhub   .com/drive
```

---

## Deployment Option 1: Container Instance (⭐ Recommended)

**Pros**:
- ✅ Simpler setup (no VM management)
- ✅ Built-in auto-restart
- ✅ Always Free eligible
- ✅ Environment variables in OCI Console UI
- ✅ No OS patching needed

**Cons**:
- ❌ Less flexibility
- ❌ Harder to debug if issues occur

### Step 1: Prepare Docker Image

#### 1.1 Build Locally (Optional, for testing)

```bash
# From project root
cd d:\development\strategy-lab

# Build image
docker build -f python/Dockerfile -t strategy-lab:latest .

# Test locally with env vars
docker run \
  -e FINNHUB_API_KEY=your_test_key \
  -e LOG_LEVEL=INFO \
  strategy-lab:latest
```

#### 1.2 Create OCI Registry Repository

```bash
# 1. In OCI Console: Containers → Container Registry
# 2. Select compartment
# 3. Click "Create Repository"
# 4. Repository name: strategy-lab
# 5. Copy the repository URL:
#    ocir.io/<region>/<tenancy>/<repo>/strategy-lab
```

#### 1.3 Push Image to OCI Registry

**Windows PowerShell**:
```powershell
# Login to OCI registry
docker login ocir.io

# When prompted:
# Username: <tenancy>/<your_username>
# Password: Your OCI user auth token (from Account Settings)

# Tag image
docker tag strategy-lab:latest ocir.io/<region>/<tenancy>/strategy-lab:latest

# Push to OCI
docker push ocir.io/<region>/<tenancy>/strategy-lab:latest

# Verify
docker image ls | grep ocir.io
```

**Get your Auth Token**:
```
OCI Console → Profile icon → User Settings → Auth Tokens → Generate Token
Copy the token and keep it secure
```

### Step 2: Create Container Instance

#### 2.1 Launch Container Instance

1. **OCI Console** → **Compute** → **Container Instances**
2. Click **"Create container instance"**

#### 2.2 Configure Basic Details

| Field | Value |
|-------|-------|
| Compartment | Your compartment |
| Display Name | `strategy-lab-trader` |
| Container Image | Your registry URL |
| Image Pull Credentials | (if private repo, set here) |

#### 2.3 Set Environment Variables ⭐ CRITICAL

In the **"Environment variables"** section, add:

```
FINNHUB_API_KEY          = <your_finnhub_api_key>
GOOGLE_SERVICE_ACCOUNT_KEY = /app/credentials/service_account.json
GOOGLE_OAUTH_CLIENT_SECRET = /app/credentials/oauth_client.json
GOOGLE_OAUTH_CLIENT_TOKEN = /app/credentials/gdrive_token.json
GOOGLE_DRIVE_ROOT_FOLDER_ID = <your_folder_id>

FINNHUB_SYMBOLS          = AAPL,MSFT,NVDA,TSLA
FINNHUB_BAR_INTERVAL     = 5m
LOG_LEVEL                = INFO
ENVIRONMENT              = production
CACHE_DIR                = /app/data_cache
RESULTS_DIR              = /app/results
```

#### 2.4 Configure Volumes (for credentials)

If your image doesn't have credentials baked in:

1. **Skip if**: You embedded credentials in the Docker image (⚠️ less secure)
2. **Do this if**: You want credentials mounted from Object Storage

```
Volume Type: Object Storage
Mount path: /app/credentials
Bucket: your-strategy-lab-bucket
```

#### 2.5 Configure Resources (Always Free)

| Setting | Value |
|---------|-------|
| OCPU | 1 |
| Memory (GB) | 1 |
| Total OCPU count | 1 |
| Total Memory | 1 GB |

#### 2.6 Configure Networking

| Setting | Value |
|---------|-------|
| Subnet | Your VCN subnet |
| Assign public IP | ❌ No (not needed for outbound) |

#### 2.7 Configure Security

Network Security Group:

```
Ingress Rules:
  (None - only outbound needed)

Egress Rules:
  Protocol: TCP
  Destination: 0.0.0.0/0
  Destination port range: 443

  Protocol: UDP
  Destination: 0.0.0.0/0
  Destination port range: 53 (DNS)
```

#### 2.8 Review and Create

- Review settings
- Scroll down
- Click **"Create container instance"**
- Wait for status: **Running** ✅

### Step 3: Monitor Container

#### 3.1 Check Logs

```bash
# In OCI Console:
# Compute → Container Instances → strategy-lab-trader → Logs

# Via CLI:
oci container-instances container-logs get \
  --container-instance-id <instance_id>
```

#### 3.2 Check Status

```bash
# OCI CLI
oci container-instances get --container-instance-id <id>

# Expected output includes:
# "lifecycle_state": "ACTIVE"
# "containers": [{"lifecycle_state": "RUNNING"}]
```

#### 3.3 Stop/Start Container

```bash
# Stop
oci container-instances stop --container-instance-id <id>

# Start
oci container-instances start --container-instance-id <id>

# Delete (if needed)
oci container-instances delete --container-instance-id <id>
```

---

## Deployment Option 2: Compute Instance (Advanced)

**Pros**:
- ✅ Full control over environment
- ✅ Easy SSH access for debugging
- ✅ Can run multiple services
- ✅ Always Free eligible (E2.1.Micro)

**Cons**:
- ❌ Need to manage OS
- ❌ Manual setup more complex
- ❌ OS patching required

### Step 1: Launch Compute Instance

#### 1.1 Create Instance

1. **OCI Console** → **Compute** → **Instances** → **Create Instance**

#### 1.2 Configure Instance Details

| Setting | Value |
|---------|-------|
| Name | `strategy-lab-compute` |
| Compartment | Your compartment |
| Image | Ubuntu 22.04 LTS |
| Shape | VM.Standard.E2.1.Micro (Always Free) |
| VCN | Your VCN |
| Subnet | Public subnet (for SSH) |
| Public IP | Assign (for SSH access) |
| SSH Key | Generate new or use existing |

#### 1.3 Review and Launch

- Download/save SSH private key
- Click **"Create"**
- Wait for state: **Running** ✅

### Step 2: SSH and Setup

#### 2.1 Connect via SSH

**Windows PowerShell**:
```powershell
# Convert .key to .ppk if needed (PuTTY)
# Or use:
ssh -i "path\to\private\key" ubuntu@<public_ip>
```

**macOS/Linux**:
```bash
chmod 600 ~/path/to/private/key
ssh -i ~/path/to/private/key ubuntu@<public_ip>
```

#### 2.2 Install Dependencies

```bash
# Update system
sudo apt update && sudo apt upgrade -y

# Install Python 3.10 and pip
sudo apt install -y python3.10 python3-pip git curl wget

# Create app directory
sudo mkdir -p /opt/strategy-lab
sudo chown ubuntu:ubuntu /opt/strategy-lab

# Create credentials directory
mkdir -p /opt/strategy-lab/credentials
```

#### 2.3 Clone Repository

```bash
cd /opt/strategy-lab
git clone https://github.com/your-username/strategy-lab.git .

# Or download ZIP and extract
wget https://github.com/your-username/strategy-lab/archive/main.zip
unzip main.zip
```

#### 2.4 Install Python Dependencies

```bash
cd /opt/strategy-lab/python

# Create virtual environment (optional but recommended)
python3 -m venv venv
source venv/bin/activate

# Install requirements
pip install -r requirements.txt
```

#### 2.5 Create `.env` File

```bash
cat > /opt/strategy-lab/python/.env << 'EOF'
# Finnhub Configuration
FINNHUB_API_KEY=your_api_key_here
FINNHUB_SYMBOLS=AAPL,MSFT,NVDA,TSLA
FINNHUB_BAR_INTERVAL=5m
LOG_LEVEL=INFO
ENVIRONMENT=production

# Google Drive
GOOGLE_SERVICE_ACCOUNT_KEY=/opt/strategy-lab/credentials/service_account.json
GOOGLE_OAUTH_CLIENT_SECRET=/opt/strategy-lab/credentials/oauth_client.json
GOOGLE_OAUTH_CLIENT_TOKEN=/opt/strategy-lab/credentials/gdrive_token.json
GOOGLE_DRIVE_ROOT_FOLDER_ID=your_folder_id_here

# Directories
CACHE_DIR=/opt/strategy-lab/python/data_cache
RESULTS_DIR=/opt/strategy-lab/python/results
EOF

chmod 600 /opt/strategy-lab/python/.env
```

#### 2.6 Upload Credentials

**From local machine**:
```bash
# Windows PowerShell
$key = "C:\path\to\private_key.key"
$user = "ubuntu"
$host_ip = "your_instance_ip"

scp -i $key "C:\path\to\service_account.json" "${user}@${host_ip}:/opt/strategy-lab/credentials/"
scp -i $key "C:\path\to\oauth_client.json" "${user}@${host_ip}:/opt/strategy-lab/credentials/"
scp -i $key "$env:USERPROFILE\.strategy_lab\gdrive_token.json" "${user}@${host_ip}:/opt/strategy-lab/credentials/"

# Make sure permissions are correct
ssh -i $key ${user}@${host_ip} chmod 600 /opt/strategy-lab/credentials/*
```

### Step 3: Create Systemd Service (Auto-restart on failure)

#### 3.1 Create Service File

```bash
sudo tee /etc/systemd/system/strategy-lab.service > /dev/null << 'EOF'
[Unit]
Description=Strategy Lab Trading Bot 24/7
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/opt/strategy-lab/python

# Load environment variables from .env
EnvironmentFile=/opt/strategy-lab/python/.env

# Python executable
ExecStart=/opt/strategy-lab/python/venv/bin/python orchestrator_main.py

# Restart policy for 24/7 operation
Restart=always
RestartSec=10
StartLimitInterval=300
StartLimitBurst=5

# Resource limits
MemoryLimit=1G

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=strategy-lab

[Install]
WantedBy=multi-user.target
EOF
```

#### 3.2 Enable and Start Service

```bash
# Reload systemd
sudo systemctl daemon-reload

# Enable auto-start on boot
sudo systemctl enable strategy-lab

# Start service
sudo systemctl start strategy-lab

# Check status
sudo systemctl status strategy-lab

# View logs
sudo journalctl -u strategy-lab -f
```

### Step 4: Monitoring (Compute Instance)

#### 4.1 View Real-time Logs

```bash
# SSH into instance and run:
sudo journalctl -u strategy-lab -f --no-pager

# Or just last 50 lines
sudo journalctl -u strategy-lab -n 50
```

#### 4.2 Check Process Health

```bash
# Is it running?
ps aux | grep orchestrator_main

# CPU and memory usage
top -p $(pgrep -f orchestrator_main)

# Disk usage
df -h /opt/strategy-lab/python/

# Free memory
free -h
```

#### 4.3 Manual Restart (if needed)

```bash
sudo systemctl restart strategy-lab

# Watch restart logs
sudo journalctl -u strategy-lab -f
```

#### 4.4 View Service Errors

```bash
# If service won't start
sudo systemctl start strategy-lab
sudo systemctl status strategy-lab -l

# Check logs for full error
sudo journalctl -u strategy-lab -x -e
```

---

## Verify Deployment Success

### For Both Options:

#### 1. Check Finnhub Connection

**Expected behavior**:
- Connection to WebSocket established
- Receiving market data for configured symbols
- No "Invalid API key" errors

**Check logs for**:
```
✅ [OK] Client created for wss://ws.finnhub.io
✅ [OK] Successfully connected!
✅ [OK] Subscribed to AAPL, MSFT, ...
```

#### 2. Check Google Drive Sync

**Expected behavior**:
- Results uploaded to Google Drive
- No "GOOGLE_SERVICE_ACCOUNT_KEY not found" errors
- New folder/files appear in Drive

**Check logs for**:
```
✅ [OK] Google Drive sync initialized
✅ Uploaded: ORB_trades_2025-12-30.csv
```

#### 3. Check Backtest Execution

**Expected behavior**:
- Backtest runs on schedule
- Results generated
- No crashes or uncaught exceptions

**Check logs for**:
```
✅ Starting backtest...
✅ Backtest completed successfully
✅ Results saved to: results/backtest/...
```

### Troubleshooting Verification

| Issue | Solution |
|-------|----------|
| Container won't start | Check logs for missing env vars |
| "API key invalid" | Verify `FINNHUB_API_KEY` is set correctly |
| Google Drive errors | Check credential file paths and permissions |
| Out of memory | Reduce number of symbols or increase instance size |
| Disk full | Clean up old results: `rm -rf results/*` |

---

## Cost Estimation

### Always Free Tier Limits

| Resource | Limit | Strategy-Lab Usage |
|----------|-------|-------------------|
| Container Instances | 2 total | 1 instance |
| OCPU per instance | 1 | 1 |
| Memory per instance | 1 GB | 1 GB |
| Block Storage | 100 GB | 0 GB (not needed) |
| Object Storage | 100 GB | 0 GB (optional backup) |
| Outbound bandwidth | 10 GB/month | ~1-2 GB/month (WebSocket + Drive sync) |

**Monthly Cost**: $0 (within Always Free limits)

**If exceeded**:
- Additional storage: ~$0.026/GB
- Additional OCPU: ~$0.01/hour

---

## Security Hardening

### Network Security

1. **Restrict Outbound Traffic**
   ```bash
   # Only allow necessary connections
   # - Finnhub: wss://ws.finnhub.io:443
   # - Google Drive: https://www.googleapis.com
   # - DNS: 53
   ```

2. **Use Private Endpoints** (if available)
   ```bash
   # If setting up VPN/bastion for access
   # Disable public IP for compute instance
   ```

### Secret Management

1. **Rotate Credentials**
   - Finnhub: Monthly
   - Google Service Account: Quarterly
   - OAuth tokens: As needed

2. **Monitor Access**
   ```bash
   # View OCI audit logs
   oci audit query --start-time 2025-01-01T00:00:00Z
   ```

3. **Enable Logging**
   - Container logs → OCI Logging
   - Application logs → CloudWatch equivalent

### OS Hardening (Compute Instance)

```bash
# Update regularly
sudo apt update && sudo apt upgrade -y

# Fail2Ban for SSH brute force protection
sudo apt install fail2ban
sudo systemctl enable fail2ban

# Firewall (UFW)
sudo ufw enable
sudo ufw allow 22/tcp
sudo ufw allow out 443/tcp
sudo ufw allow out 53/udp
```

---

## Backup & Disaster Recovery

### Backup Results

#### Option 1: OCI Object Storage

```bash
# Create bucket
oci os bucket create --compartment-id <id> --name strategy-lab-backups

# Upload results
oci os object put --bucket-name strategy-lab-backups \
  --file results/backtest/ORB_trades_2025-12-30.csv

# Enable versioning
oci os bucket update --bucket-name strategy-lab-backups --versioning-enabled
```

#### Option 2: Automated Sync

```bash
# In orchestrator_main.py or after backtest, add:
import subprocess
subprocess.run([
    "rclone", "sync",
    "/opt/strategy-lab/python/results/",
    "oci:strategy-lab-backups/results/"
])
```

#### Option 3: Manual Download

```bash
# From your local machine
oci os object list --bucket-name strategy-lab-backups
oci os object get --bucket-name strategy-lab-backups --name results/backtest/ORB_trades.csv
```

### Recovery

1. **If instance crashes**:
   - Container: Auto-restarts
   - Compute: Systemd auto-restarts

2. **If data lost**:
   - Restore from Object Storage
   - Check Google Drive for synced files

3. **Full recovery**:
   ```bash
   # Redeploy container/instance
   # Restore .env from secure location
   # Restore credentials from secure location
   # Start service
   ```

---

## 24/7 Operations Checklist

- [ ] Finnhub API key configured
- [ ] Google Drive credentials uploaded
- [ ] Container/service auto-restart enabled
- [ ] Logs being collected
- [ ] Alerts set up for failures
- [ ] Backup strategy in place
- [ ] Security hardening applied
- [ ] Network rules configured
- [ ] Monitoring dashboard set up
- [ ] Runbook documented

---

## Common Issues & Solutions

### "Connection refused: wss://ws.finnhub.io"

**Cause**: Network firewall blocking WebSocket
**Solution**:
```bash
# Check egress rules allow HTTPS/WSS
# Allow TCP:443 outbound to any destination
```

### "GOOGLE_SERVICE_ACCOUNT_KEY: No such file or directory"

**Cause**: Credentials not found at path
**Solution**:
```bash
# Verify file location
ls -la /opt/strategy-lab/credentials/service_account.json

# Or use Object Storage mount point
# Or base64 encode and use env var instead
```

### "Container exits with code 1"

**Cause**: Unhandled exception
**Solution**:
```bash
# Check logs
oci container-instances container-logs get --container-instance-id <id>

# Common causes:
# - Missing import
# - Syntax error in Python
# - Missing environment variable
# - File permissions
```

### Out of Memory Error

**Cause**: Instance only has 1 GB RAM
**Solution**:
```bash
# Reduce symbols in FINNHUB_SYMBOLS
# Clear cache: rm -rf data_cache/*
# Reduce backtest days in config
# Upgrade to larger instance (costs money)
```

### Disk Full Error

**Cause**: Results directory grew too large
**Solution**:
```bash
# Check disk usage
df -h

# Clean old results
rm -rf results/backtest/ORB_*_2025-12-2[0-9].csv

# Or upload to Object Storage first
oci os object put --bucket-name strategy-lab-backups --file results/...
```

---

## Monitoring Setup

### Optional: OCI Monitoring

```bash
# Create custom metric for "backtest_completed"
# Create alarm if backtest doesn't complete in 24 hours
# Send notification to email/Slack

# Example (via CLI):
oci monitoring alarm create \
  --compartment-id <id> \
  --display-name "Strategy Lab Backtest Failed" \
  --metric-namespace "CustomMetrics" \
  --metric-name "backtest_failed" \
  --statistic "Sum" \
  --threshold 1 \
  --comparison-operator "GREATER_THAN" \
  --notification-title "Backtest Failed!"
```

### Optional: Custom Monitoring Script

Create `/opt/strategy-lab/monitoring.py`:

```python
#!/usr/bin/env python3
import os
import json
from datetime import datetime, timedelta

# Check if backtest completed in last 24 hours
results_dir = "/opt/strategy-lab/python/results/backtest"
files = os.listdir(results_dir)
if files:
    latest = max(files, key=lambda f: os.path.getctime(os.path.join(results_dir, f)))
    mtime = os.path.getctime(os.path.join(results_dir, latest))
    age_hours = (datetime.now().timestamp() - mtime) / 3600

    if age_hours > 24:
        print("❌ ALERT: Backtest hasn't run in >24 hours")
        # Send email/Slack alert
    else:
        print(f"✅ Backtest ran {age_hours:.1f} hours ago")
```

Run in cron:
```bash
# Every 6 hours
0 */6 * * * /opt/strategy-lab/python/venv/bin/python /opt/strategy-lab/monitoring.py
```

---

## Summary

✅ **You're ready to deploy!**

**Next Steps**:
1. Gather all credentials (Finnhub, Google Drive)
2. Choose deployment option (Container or Compute)
3. Follow the deployment steps
4. Monitor logs for first 24 hours
5. Adjust symbols/intervals as needed
6. Set up backup strategy

**24/7 Running Bot on Always Free**: Mission accomplished! 🚀

---

**Document Version**: 1.0
**Last Updated**: 2025-12-30
**Status**: Ready for Deployment
