# Trading Bot MVP - Oracle Cloud Deployment Guide

## Table of Contents

1. [Overview](#overview)
2. [Prerequisites](#prerequisites)
3. [Oracle Cloud Infrastructure Setup](#oracle-cloud-infrastructure-setup)
4. [Environment Configuration](#environment-configuration)
5. [Database Setup](#database-setup)
6. [Dashboard Deployment](#dashboard-deployment)
7. [Monitoring and Health Checks](#monitoring-and-health-checks)
8. [Discord Notifications Setup](#discord-notifications-setup)
9. [Docker Deployment](#docker-deployment)
10. [Troubleshooting](#troubleshooting)
11. [Verification Steps](#verification-steps)

---

## Overview

This guide provides step-by-step instructions for deploying the trading bot MVP to Oracle Cloud. The deployment includes:

- **Trading Bot**: Core trading engine with ORB strategy
- **Dashboard**: Real-time monitoring UI (FastAPI + Streamlit)
- **WebSocket Server**: Live updates for dashboard
- **Health Monitoring**: System health checks and uptime tracking
- **Notifications**: Discord webhook integration for trade alerts
- **Database**: SQLite for MVP (upgradeable to PostgreSQL)

**Deployment Architecture:**
```
┌─────────────────────────────────────────────────────────────┐
│                    Oracle Cloud Instance                     │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │
│  │ Trading Bot  │  │  Dashboard   │  │   Database   │     │
│  │  Container   │  │  (Streamlit) │  │   (SQLite)   │     │
│  │              │  │              │  │              │     │
│  │  Port: N/A   │  │  Port: 8501  │  │ File-based   │     │
│  └──────────────┘  └──────────────┘  └──────────────┘     │
│         │                  │                  │             │
│         └──────────────────┴──────────────────┘             │
│                            │                                │
│                   ┌────────┴────────┐                       │
│                   │  Health Check   │                       │
│                   │   Port: 8080    │                       │
│                   └─────────────────┘                       │
│                                                              │
└─────────────────────────────────────────────────────────────┘
                            │
                            │ External Connections
                            ├──► Finnhub API (WebSocket)
                            ├──► Yahoo Finance (REST)
                            └──► Discord Webhook (Notifications)
```

---

## Prerequisites

### Required Accounts and API Keys

Before deployment, you must obtain the following:

#### 1. Finnhub API Key (Required)

**Purpose**: Real-time market data via WebSocket

**Steps to Obtain:**
1. Visit [https://finnhub.io/register](https://finnhub.io/register)
2. Create a free account
3. Navigate to Dashboard → API Keys
4. Copy your API key (format: `c...`)

**Free Tier Limits:**
- 60 API calls/minute
- WebSocket: 1 connection
- Real-time US stock data

**Important Notes:**
- Keep this key secret - never commit to git
- Free tier is sufficient for MVP with 3-5 symbols
- For production, consider upgrading to paid tier

#### 2. Discord Webhook URL (Optional but Recommended)

**Purpose**: Trade execution notifications and error alerts

**Steps to Obtain:**
1. Open Discord and navigate to your server
2. Go to Server Settings → Integrations → Webhooks
3. Click "New Webhook" or "Create Webhook"
4. Name it "Trading Bot Alerts"
5. Select the channel for notifications (e.g., `#trading-alerts`)
6. Click "Copy Webhook URL"
7. Save the URL (format: `https://discord.com/api/webhooks/...`)

**Notification Types:**
- Trade executions (entry/exit)
- Position updates
- Critical errors
- System health alerts

#### 3. Oracle Cloud Account

**Purpose**: Cloud compute instance for hosting

**Steps to Setup:**
1. Visit [https://www.oracle.com/cloud/free/](https://www.oracle.com/cloud/free/)
2. Sign up for Always Free tier
3. Verify email and complete registration

**Free Tier Resources:**
- 2 AMD-based Compute VMs (1/8 OCPU, 1 GB memory each)
- OR 4 Arm-based Ampere A1 cores and 24 GB memory
- 200 GB block storage
- 10 TB outbound data transfer/month

**Recommended Configuration for Trading Bot:**
- Instance type: VM.Standard.E2.1.Micro (1 OCPU, 1 GB RAM)
- OS: Ubuntu 22.04 LTS
- Storage: 50 GB boot volume

#### 4. Optional: Cloud Storage (For Advanced Deployments)

**Azure Blob Storage** (if using Azure cloud sync):
- Create Azure Storage Account
- Get connection string from "Access keys"

**AWS S3** (if using AWS cloud sync):
- Create S3 bucket
- Get AWS Access Key ID and Secret Access Key

---

## Oracle Cloud Infrastructure Setup

### Step 1: Create Compute Instance

1. **Login to Oracle Cloud Console**
   - Navigate to [https://cloud.oracle.com/](https://cloud.oracle.com/)
   - Login with your credentials

2. **Create Instance**
   ```
   Navigation: Compute → Instances → Create Instance
   ```

3. **Configure Instance**
   - **Name**: `trading-bot-mvp`
   - **Compartment**: `root` (or your compartment)
   - **Availability Domain**: Select any available
   - **Image**: `Ubuntu 22.04 LTS`
   - **Shape**: `VM.Standard.E2.1.Micro` (Always Free)
   - **Primary VNIC**: Keep defaults
   - **SSH Keys**:
     - Generate new SSH key pair OR
     - Upload your existing public key
     - **IMPORTANT**: Download and save the private key securely

4. **Boot Volume**
   - Size: `50 GB` (default is fine)
   - Performance: Balanced

5. **Click "Create"**
   - Wait 2-3 minutes for provisioning
   - Note the **Public IP address** (e.g., `150.230.x.x`)

### Step 2: Configure Firewall Rules

**Open Required Ports:**

1. **Navigate to VCN Settings**
   ```
   Compute → Instances → [Your Instance] → Primary VNIC → Subnet → Security Lists
   ```

2. **Add Ingress Rules**

   Click "Add Ingress Rules" and create the following:

   **Rule 1: SSH (Port 22)**
   ```
   Source CIDR: 0.0.0.0/0
   IP Protocol: TCP
   Source Port Range: All
   Destination Port Range: 22
   Description: SSH access
   ```

   **Rule 2: Health Check API (Port 8080)**
   ```
   Source CIDR: 0.0.0.0/0
   IP Protocol: TCP
   Source Port Range: All
   Destination Port Range: 8080
   Description: Trading bot health check
   ```

   **Rule 3: Dashboard (Port 8501)**
   ```
   Source CIDR: 0.0.0.0/0
   IP Protocol: TCP
   Source Port Range: All
   Destination Port Range: 8501
   Description: Streamlit dashboard
   ```

3. **Configure Ubuntu Firewall (UFW)**

   SSH into your instance and run:
   ```bash
   # Connect via SSH
   ssh -i /path/to/private-key.pem ubuntu@<PUBLIC_IP>

   # Enable and configure firewall
   sudo ufw allow 22/tcp    # SSH
   sudo ufw allow 8080/tcp  # Health check
   sudo ufw allow 8501/tcp  # Dashboard
   sudo ufw enable
   sudo ufw status
   ```

### Step 3: Install Dependencies

SSH into your instance and run the following commands:

```bash
# Update system packages
sudo apt-get update && sudo apt-get upgrade -y

# Install Docker
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo usermod -aG docker $USER

# Install Docker Compose
sudo apt-get install docker-compose -y

# Install Git
sudo apt-get install git -y

# Install Python (for local development/testing)
sudo apt-get install python3.11 python3.11-venv python3-pip -y

# Verify installations
docker --version
docker-compose --version
git --version
python3.11 --version
```

**Logout and login again** for Docker group permissions to take effect:
```bash
exit
ssh -i /path/to/private-key.pem ubuntu@<PUBLIC_IP>
```

### Step 4: Clone Repository

```bash
# Navigate to home directory
cd ~

# Clone the repository
git clone https://github.com/YOUR_USERNAME/strategy-lab.git
cd strategy-lab/vibe/trading_bot

# Verify structure
ls -la
# Should see: Dockerfile, docker-compose.yml, main.py, etc.
```

---

## Environment Configuration

### Step 1: Create Environment Variables File

Create a `.env` file in the `vibe/trading_bot` directory:

```bash
cd ~/strategy-lab/vibe/trading_bot
nano .env
```

**On Windows (from your workspace root):**
```powershell
cd python
# Or: cd vibe\trading_bot (depending on which trading bot you're deploying)
notepad .env
```

### Step 2: Configure Environment Variables

Add the following configuration (replace placeholders with your actual values):

**IMPORTANT:** Use comma-separated values (not JSON arrays) and NO inline comments with `#`.

```bash
# ============================================
# TRADING BOT ENVIRONMENT CONFIGURATION
# ============================================

# ------------------------------
# Application Settings
# ------------------------------
ENVIRONMENT=production
LOG_LEVEL=INFO
SHUTDOWN_TIMEOUT_SECONDS=30
HEALTH_CHECK_PORT=8080

# ------------------------------
# Trading Configuration
# ------------------------------
# Comma-separated list of symbols to trade (no spaces, no brackets)
SYMBOLS=AAPL,GOOGL,MSFT

# Initial capital in USD
INITIAL_CAPITAL=10000

# Risk management parameters
MAX_POSITION_SIZE=0.1
USE_STOP_LOSS=true
STOP_LOSS_PCT=0.02
TAKE_PROFIT_PCT=0.05

# ------------------------------
# Data Provider Configuration
# ------------------------------
YAHOO_RATE_LIMIT=5
YAHOO_RETRY_COUNT=3
DATA_CACHE_TTL_SECONDS=3600

# Bar intervals to track (comma-separated)
BAR_INTERVALS=1m,5m,15m

# ------------------------------
# Finnhub API Configuration
# ------------------------------
# REQUIRED: Get your key from https://finnhub.io/register
FINNHUB_API_KEY=your_finnhub_api_key_here

# ------------------------------
# Database Configuration
# ------------------------------
# SQLite database path (will be created automatically)
DATABASE_PATH=/app/data/trades.db

# ------------------------------
# Discord Notifications
# ------------------------------
# OPTIONAL: Get webhook URL from Discord Server Settings
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/YOUR_WEBHOOK_URL_HERE

NOTIFY_ON_TRADE=true
NOTIFY_ON_ERROR=true

# ------------------------------
# Cloud Storage (Optional)
# ------------------------------
ENABLE_CLOUD_SYNC=false
CLOUD_PROVIDER=azure
CLOUD_CONTAINER=trading-bot-backup
SYNC_INTERVAL_SECONDS=300

# ------------------------------
# Dashboard Configuration
# ------------------------------
DASHBOARD_PORT=8501
DASHBOARD_AUTO_REFRESH_SECONDS=5
```

**Save the file:**
- Linux: Press `Ctrl+X`, then `Y`, then `Enter`
- Windows: File → Save, then close Notepad

### Step 3: Secure Environment File

```bash
# Restrict permissions (only owner can read/write)
chmod 600 .env

# Verify permissions
ls -la .env
# Should show: -rw------- (600)
```

### Step 4: Validate Configuration

**IMPORTANT:** Navigate to the correct directory first:

```bash
# On Linux/Mac (Oracle Cloud)
cd ~/strategy-lab/vibe/trading_bot

# On Windows (local development)
cd d:\development\strategy-lab\python
# Or for vibe trading bot:
cd d:\development\strategy-lab\vibe\trading_bot
```

**Option 1: Validate using Docker** (recommended):
```bash
# Make sure you're in the directory with docker-compose.yml
ls docker-compose.yml  # Should show the file

# Validate configuration using Docker
docker-compose config
```

**Option 2: Quick environment check**:
```bash
# View parsed environment variables
docker-compose config | grep -A 20 environment
```

**Expected Output:**
```
INFO - Validating configuration...
INFO - Configuration is valid
INFO -   Symbols: ['AAPL', 'GOOGL', 'MSFT']
INFO -   Initial capital: $10,000.00
INFO -   Stop loss: 2.0%
INFO -   Take profit: 5.0%
```

---

## Database Setup

The trading bot uses **SQLite** for the MVP (single-file database, no separate server needed). For production, you can upgrade to PostgreSQL.

### SQLite Setup (Default - MVP)

**No manual setup required!** The database is automatically created on first run.

**Database Schema:**
- **trades**: Trade execution history (entry/exit, P&L, fees)
- **positions**: Open positions with unrealized P&L
- **account**: Account balance and equity snapshots
- **system_health**: Health check logs and error tracking

**Database Location:**
```
/app/data/trades.db  (inside Docker container)
./data/trades.db     (mapped to host volume)
```

**Automatic Initialization:**
When the bot starts for the first time, it will:
1. Create `/app/data/` directory
2. Initialize `trades.db` with schema
3. Create initial tables and indexes
4. Log: `INFO - Database initialized at /app/data/trades.db`

### PostgreSQL Setup (Optional - Production)

For production deployments with high transaction volume:

#### Step 1: Install PostgreSQL

```bash
# Install PostgreSQL
sudo apt-get install postgresql postgresql-contrib -y

# Start PostgreSQL service
sudo systemctl start postgresql
sudo systemctl enable postgresql
```

#### Step 2: Create Database and User

```bash
# Switch to postgres user
sudo -u postgres psql

# Create database and user
CREATE DATABASE trading_bot;
CREATE USER trading_admin WITH ENCRYPTED PASSWORD 'your_secure_password';
GRANT ALL PRIVILEGES ON DATABASE trading_bot TO trading_admin;
\q
```

#### Step 3: Update Environment Configuration

Edit `.env` and replace the DATABASE_PATH with DATABASE_URL:

```bash
# Comment out SQLite
# DATABASE_PATH=/app/data/trades.db

# Add PostgreSQL connection
DATABASE_URL=postgresql://trading_admin:your_secure_password@localhost:5432/trading_bot
```

#### Step 4: Install PostgreSQL Python Driver

Add to `requirements.txt`:
```
psycopg2-binary>=2.9
```

### Database Backup

**For SQLite:**
```bash
# Backup database
cp ./data/trades.db ./data/trades.db.backup.$(date +%Y%m%d_%H%M%S)

# Automate with cron (daily at 2 AM)
crontab -e
# Add line:
0 2 * * * cp ~/strategy-lab/vibe/trading_bot/data/trades.db ~/strategy-lab/vibe/trading_bot/data/trades.db.backup.$(date +\%Y\%m\%d)
```

**For PostgreSQL:**
```bash
# Backup database
pg_dump -U trading_admin trading_bot > backup_$(date +%Y%m%d_%H%M%S).sql

# Restore database
psql -U trading_admin trading_bot < backup_20240115_140530.sql
```

---

## Dashboard Deployment

The dashboard provides real-time monitoring with three components:

1. **FastAPI REST API** (Port 8080) - Data endpoints
2. **WebSocket Server** (Port 8080) - Live updates
3. **Streamlit UI** (Port 8501) - Interactive dashboard

### Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      Dashboard Stack                         │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌─────────────────────────────────────────────────────┐   │
│  │          Streamlit Dashboard (Port 8501)            │   │
│  │  - Account summary                                  │   │
│  │  - Performance metrics                              │   │
│  │  - Open positions table                             │   │
│  │  - Trade history                                    │   │
│  │  - Interactive Plotly charts                        │   │
│  └────────────────┬────────────────────────────────────┘   │
│                   │ HTTP REST + WebSocket                   │
│  ┌────────────────┴────────────────────────────────────┐   │
│  │         FastAPI Server (Port 8080)                  │   │
│  │  REST Endpoints:                                    │   │
│  │  - GET /api/account                                 │   │
│  │  - GET /api/positions                               │   │
│  │  - GET /api/trades                                  │   │
│  │  - GET /api/metrics/performance                     │   │
│  │  - GET /api/health                                  │   │
│  │  WebSocket:                                         │   │
│  │  - ws://host:8080/ws/updates                        │   │
│  └────────────────┬────────────────────────────────────┘   │
│                   │                                         │
│  ┌────────────────┴────────────────────────────────────┐   │
│  │            Trading Bot Engine                       │   │
│  │  - Executes trades                                  │   │
│  │  - Updates database                                 │   │
│  │  - Broadcasts WebSocket events                      │   │
│  └─────────────────────────────────────────────────────┘   │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

### Deployment Steps

#### Step 1: Start Dashboard with Docker

The dashboard is included in the main Docker container:

```bash
cd ~/strategy-lab/vibe/trading_bot

# Start all services
docker-compose up -d

# Check logs
docker-compose logs -f
```

You should see:
```
trading-bot | INFO - Starting trading bot (environment=production)
trading-bot | INFO - Dashboard API started on port 8080
trading-bot | INFO - Streamlit dashboard available at http://0.0.0.0:8501
trading-bot | INFO - WebSocket server listening on ws://0.0.0.0:8080/ws/updates
```

#### Step 2: Access Dashboard

**Local Access (SSH Tunnel):**
```bash
# From your local machine, create SSH tunnel
ssh -L 8501:localhost:8501 -L 8080:localhost:8080 -i /path/to/key.pem ubuntu@<ORACLE_CLOUD_IP>

# Open browser and navigate to:
http://localhost:8501
```

**Public Access:**

If you want public access (not recommended without authentication):

1. Ensure Oracle Cloud firewall allows port 8501 (see [Step 2](#step-2-configure-firewall-rules))
2. Navigate to: `http://<ORACLE_CLOUD_PUBLIC_IP>:8501`

**Security Note:** For production, implement authentication (OAuth, basic auth, etc.)

#### Step 3: Verify Dashboard Functionality

**Health Check:**
```bash
# Test health endpoint
curl http://localhost:8080/api/health

# Expected response:
{
  "status": "healthy",
  "uptime_seconds": 120,
  "total_errors": 0,
  "components": {
    "database": "healthy",
    "data_provider": "healthy",
    "websocket": "connected"
  },
  "timestamp": "2024-01-15T10:30:00Z"
}
```

**Account Data:**
```bash
curl http://localhost:8080/api/account

# Expected response:
{
  "cash": 9500.00,
  "equity": 10250.00,
  "buying_power": 9500.00,
  "portfolio_value": 10250.00,
  "total_trades": 5,
  "winning_trades": 3,
  "losing_trades": 2
}
```

**WebSocket Connection:**
```bash
# Test WebSocket (using wscat)
npm install -g wscat
wscat -c ws://localhost:8080/ws/updates

# You should receive real-time updates:
{
  "type": "trade_update",
  "timestamp": "2024-01-15T10:30:15",
  "data": { ... }
}
```

### Dashboard Features

**1. Account Summary Section**
- Current cash balance
- Total equity (cash + positions)
- Buying power
- Daily P&L with % change
- Total trades executed

**2. Performance Metrics**
- Win rate (winning trades / total trades)
- Sharpe ratio (risk-adjusted returns)
- Maximum drawdown
- Profit factor (gross profit / gross loss)
- Average trade duration

**3. System Health**
- Overall status indicator (healthy/degraded/down)
- Uptime tracking
- Error count
- Component health (database, data provider, WebSocket)

**4. Open Positions Table**
| Symbol | Quantity | Entry Price | Current Price | Unrealized P&L | % Change |
|--------|----------|-------------|---------------|----------------|----------|
| AAPL   | 100      | $150.00     | $152.50       | +$250.00       | +1.67%   |

**5. Trade History**
- Paginated table (25 trades per page)
- Filters: symbol, date range, status
- Columns: timestamp, symbol, side, quantity, price, P&L, status
- Color-coded P&L (green for profit, red for loss)

**6. Performance Charts** (Interactive Plotly)
- **Cumulative P&L**: Line chart of total P&L over time
- **Trade Distribution**: Bar chart of trades by symbol
- **Win Rate Pie Chart**: Winning vs losing vs breakeven trades
- **Drawdown Chart**: Running drawdown visualization
- **P&L by Symbol**: Total profit/loss aggregated by symbol
- **Monthly Performance**: Monthly P&L summary

### Dashboard Configuration

Edit `vibe/trading_bot/dashboard/app.py` to customize:

```python
# Auto-refresh interval (seconds)
AUTO_REFRESH_INTERVAL = 5  # Update every 5 seconds

# Chart settings
CHART_HEIGHT = 400  # Chart height in pixels
CHART_THEME = "plotly"  # or "plotly_dark"

# Table pagination
TRADES_PER_PAGE = 25

# Color scheme
PROFIT_COLOR = "#00c853"  # Green
LOSS_COLOR = "#d50000"    # Red
NEUTRAL_COLOR = "#9e9e9e"  # Gray
```

---

## Monitoring and Health Checks

### Health Check Endpoint

The trading bot exposes a health check API on port 8080:

**Endpoint:** `GET http://localhost:8080/api/health`

**Response Schema:**
```json
{
  "status": "healthy|degraded|down",
  "uptime_seconds": 3600,
  "total_errors": 0,
  "components": {
    "database": "healthy|degraded|down",
    "data_provider": "healthy|degraded|down",
    "exchange": "healthy|degraded|down",
    "websocket": "connected|disconnected"
  },
  "timestamp": "2024-01-15T10:30:00Z",
  "version": "1.0.0"
}
```

**Health Status Logic:**
- **healthy**: All components operational, no critical errors
- **degraded**: Some non-critical errors, but still functional
- **down**: Critical failure, bot cannot operate

### Docker Health Checks

The Docker container includes built-in health checks:

```yaml
# From docker-compose.yml
healthcheck:
  test: ["CMD", "curl", "-f", "http://localhost:8080/api/health"]
  interval: 30s      # Check every 30 seconds
  timeout: 10s       # Timeout after 10 seconds
  retries: 3         # Mark unhealthy after 3 failures
  start_period: 40s  # Wait 40s before starting checks
```

**Check Container Health:**
```bash
# View health status
docker ps

# Output shows health status:
CONTAINER ID   IMAGE          STATUS
abc123def456   trading-bot    Up 2 hours (healthy)

# View detailed health logs
docker inspect --format='{{json .State.Health}}' trading-bot | jq
```

### Logging

**Log Locations:**
```
./logs/trading_bot.log          # Main application logs
./logs/trading_bot_error.log    # Error logs only
./logs/trades.log               # Trade execution logs
```

**View Logs:**
```bash
# Real-time logs
docker-compose logs -f

# Last 100 lines
docker-compose logs --tail=100

# Filter by log level
docker-compose logs | grep "ERROR"

# View specific log file
tail -f ./logs/trading_bot.log
```

**Log Format:**
```
2024-01-15 10:30:00 [INFO] [trading_bot.main] Starting trading bot (environment=production)
2024-01-15 10:30:05 [INFO] [data.manager] Initialized data manager for 3 symbols
2024-01-15 10:30:10 [INFO] [strategies.orb] ORB levels calculated: AAPL high=150.50, low=148.20
2024-01-15 10:30:15 [INFO] [execution.trade_executor] Order executed: BUY 100 AAPL @ $150.25
2024-01-15 10:30:15 [ERROR] [notifications.discord] Failed to send notification: Connection timeout
```

**Log Rotation:**

Docker automatically rotates logs (configured in docker-compose.yml):
```yaml
logging:
  driver: "json-file"
  options:
    max-size: "10m"   # Max 10MB per file
    max-file: "3"     # Keep 3 files (30MB total)
```

### Monitoring with Cron Jobs

**Setup Automated Monitoring:**

1. **Create monitoring script:**
```bash
nano ~/monitor_trading_bot.sh
```

2. **Add monitoring logic:**
```bash
#!/bin/bash

# Check if container is running
if ! docker ps | grep -q trading-bot; then
    echo "$(date): Trading bot container is not running!" | tee -a ~/monitor.log
    # Restart container
    cd ~/strategy-lab/vibe/trading_bot
    docker-compose up -d
    exit 1
fi

# Check health endpoint
HEALTH=$(curl -s http://localhost:8080/api/health | jq -r '.status')

if [ "$HEALTH" != "healthy" ]; then
    echo "$(date): Trading bot health check failed: $HEALTH" | tee -a ~/monitor.log
    # Send alert (could integrate with Discord, email, etc.)
    exit 1
fi

echo "$(date): Trading bot is healthy" >> ~/monitor.log
exit 0
```

3. **Make executable:**
```bash
chmod +x ~/monitor_trading_bot.sh
```

4. **Setup cron job (check every 5 minutes):**
```bash
crontab -e

# Add line:
*/5 * * * * /home/ubuntu/monitor_trading_bot.sh
```

### External Monitoring (Optional)

**UptimeRobot Integration:**

1. Visit [https://uptimerobot.com/](https://uptimerobot.com/) and create account
2. Add new monitor:
   - Monitor Type: HTTP(s)
   - Friendly Name: Trading Bot Health
   - URL: `http://<ORACLE_CLOUD_IP>:8080/api/health`
   - Monitoring Interval: 5 minutes
3. Add alert contacts (email, Discord, SMS)

**Healthchecks.io Integration:**

```bash
# Add to cron job
*/5 * * * * /home/ubuntu/monitor_trading_bot.sh && curl -fsS -m 10 --retry 5 https://hc-ping.com/YOUR-UUID-HERE
```

---

## Discord Notifications Setup

### Step 1: Create Discord Webhook

1. **Open Discord** and navigate to your server
2. **Click Server Name** → Server Settings
3. **Navigate to** Integrations → Webhooks
4. **Click** "New Webhook" or "Create Webhook"
5. **Configure Webhook:**
   - Name: `Trading Bot Alerts`
   - Channel: Select notification channel (e.g., `#trading-alerts`)
   - Avatar: Upload bot icon (optional)
6. **Copy Webhook URL**
   - Format: `https://discord.com/api/webhooks/1234567890/AbCdEfGhIjKlMnOpQrStUvWxYz`
7. **Save Changes**

### Step 2: Add Webhook to Environment

Edit `.env` file:
```bash
nano ~/.strategy-lab/vibe/trading_bot/.env

# Update Discord configuration
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/YOUR_WEBHOOK_URL_HERE
NOTIFY_ON_TRADE=true
NOTIFY_ON_ERROR=true
```

### Step 3: Test Notification

**Manual Test:**
```bash
curl -X POST "YOUR_DISCORD_WEBHOOK_URL" \
  -H "Content-Type: application/json" \
  -d '{
    "content": "🤖 Trading Bot Test Notification",
    "embeds": [{
      "title": "System Test",
      "description": "If you receive this, Discord notifications are working!",
      "color": 5814783,
      "timestamp": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'"
    }]
  }'
```

You should receive a message in your Discord channel.

### Step 4: Restart Bot

```bash
cd ~/strategy-lab/vibe/trading_bot
docker-compose restart
```

### Notification Types

The bot sends the following notifications:

#### 1. Trade Execution Notifications

**Format:**
```
🟢 Trade Executed - BUY AAPL

Symbol: AAPL
Side: BUY
Quantity: 100
Price: $150.25
Total: $15,025.00
Timestamp: 2024-01-15 10:30:15

Strategy: ORB Breakout
Reason: Price broke above ORB high
```

#### 2. Position Exit Notifications

**Format:**
```
🔴 Position Closed - SELL AAPL

Symbol: AAPL
Side: SELL
Quantity: 100
Entry Price: $150.25
Exit Price: $152.50
P&L: +$225.00 (+1.50%)
Hold Duration: 2h 15m

Exit Reason: Take profit target reached
```

#### 3. Error Notifications

**Format:**
```
⚠️ Trading Bot Error

Error Type: DataProviderError
Message: Failed to fetch market data for AAPL
Severity: Warning

Component: YahooDataProvider
Timestamp: 2024-01-15 10:30:15

Action: Retrying in 60 seconds...
```

#### 4. Daily Summary (Optional)

**Format:**
```
📊 Daily Trading Summary - 2024-01-15

Trades Executed: 5
Winning Trades: 3 (60%)
Losing Trades: 2 (40%)

Total P&L: +$350.00 (+3.50%)
Largest Win: +$225.00 (AAPL)
Largest Loss: -$50.00 (GOOGL)

Account Balance: $10,350.00
```

### Rate Limiting

Discord webhook rate limits: **5 requests per 2 seconds**

The bot implements automatic rate limiting:
```python
# vibe/trading_bot/notifications/rate_limiter.py
rate_limiter = RateLimiter(
    rate=5,           # 5 requests
    period=2.0        # per 2 seconds
)
```

If rate limit is exceeded, notifications are queued and sent when available.

### Customize Notifications

Edit `vibe/trading_bot/notifications/formatter.py`:

```python
class NotificationFormatter:
    # Customize colors
    COLOR_SUCCESS = 0x00c853  # Green
    COLOR_ERROR = 0xd50000    # Red
    COLOR_WARNING = 0xffa000  # Orange
    COLOR_INFO = 0x2196f3     # Blue

    # Customize emojis
    EMOJI_BUY = "🟢"
    EMOJI_SELL = "🔴"
    EMOJI_PROFIT = "💰"
    EMOJI_LOSS = "📉"
    EMOJI_ERROR = "⚠️"
    EMOJI_INFO = "ℹ️"
```

---

## Docker Deployment

### Docker Architecture

The trading bot uses a **multi-stage Docker build** for optimized production images:

```dockerfile
# Stage 1: Builder (build dependencies)
FROM python:3.11-slim as builder
WORKDIR /build
RUN python -m venv /build/venv
COPY requirements.txt .
RUN /build/venv/bin/pip install --no-cache-dir -r requirements.txt

# Stage 2: Runtime (minimal final image)
FROM python:3.11-slim
RUN useradd -m -u 1000 trading  # Non-root user for security
WORKDIR /app
COPY --from=builder /build/venv /app/venv
COPY . .
USER trading
ENTRYPOINT ["python", "-m", "vibe.trading_bot.main"]
CMD ["run"]
```

**Benefits:**
- Smaller final image (~200MB vs ~600MB)
- Faster deployments
- Enhanced security (runs as non-root user)

### Deployment Commands

#### Initial Deployment

```bash
cd ~/strategy-lab/vibe/trading_bot

# Build image
docker-compose build

# Start services
docker-compose up -d

# View logs
docker-compose logs -f
```

#### Common Operations

**View Status:**
```bash
docker-compose ps

# Output:
NAME          IMAGE         STATUS        PORTS
trading-bot   trading-bot   Up 2 hours    0.0.0.0:8080->8080/tcp, 0.0.0.0:8501->8501/tcp
```

**Restart Services:**
```bash
# Restart all services
docker-compose restart

# Restart specific service
docker-compose restart trading-bot
```

**Stop Services:**
```bash
# Stop all services (data preserved)
docker-compose stop

# Stop and remove containers (data preserved in volumes)
docker-compose down
```

**Update Code:**
```bash
# Pull latest code
cd ~/strategy-lab
git pull origin main

# Rebuild and restart
cd vibe/trading_bot
docker-compose build
docker-compose up -d
```

**View Resource Usage:**
```bash
docker stats trading-bot

# Output:
CONTAINER ID   NAME          CPU %     MEM USAGE / LIMIT   NET I/O
abc123def456   trading-bot   2.5%      450MB / 512MB       1.2MB / 890KB
```

**Access Container Shell:**
```bash
docker-compose exec trading-bot bash

# Inside container:
ls /app
python -m vibe.trading_bot.main show-status
exit
```

#### Volume Management

**Data Persistence:**

The docker-compose.yml defines persistent volumes:
```yaml
volumes:
  - ./data:/app/data      # Database and cached data
  - ./logs:/app/logs      # Log files
```

**Backup Volumes:**
```bash
# Backup data directory
tar -czf backup_data_$(date +%Y%m%d).tar.gz ./data

# Backup logs
tar -czf backup_logs_$(date +%Y%m%d).tar.gz ./logs
```

**Restore Volumes:**
```bash
# Stop container
docker-compose down

# Restore data
tar -xzf backup_data_20240115.tar.gz

# Start container
docker-compose up -d
```

### Resource Limits

Configure resource limits in `docker-compose.yml`:

```yaml
services:
  trading-bot:
    # ... other config ...
    memory: 512m      # Max 512MB RAM
    cpus: '1'         # Max 1 CPU core

    # Alternative format:
    deploy:
      resources:
        limits:
          cpus: '1'
          memory: 512M
        reservations:
          cpus: '0.5'
          memory: 256M
```

**Recommended Limits for Oracle Free Tier:**
- Memory: 512MB - 768MB (instance has 1GB total)
- CPUs: 1 core
- Disk: 50GB boot volume

### Security Best Practices

1. **Non-root user**: Container runs as `trading` user (UID 1000)
2. **Read-only filesystem**: Consider adding `read_only: true` for security
3. **No privileged mode**: Never use `privileged: true`
4. **Secrets management**: Use `.env` file, never hardcode secrets
5. **Network isolation**: Use Docker networks to isolate services

---

## Troubleshooting

### Common Issues and Solutions

#### Issue 1: Container Won't Start

**Symptoms:**
```bash
docker-compose up -d
# Container starts then immediately exits
```

**Diagnosis:**
```bash
# Check logs for errors
docker-compose logs trading-bot

# Common errors:
# - "ModuleNotFoundError: No module named 'vibe'"
# - "pydantic.ValidationError: FINNHUB_API_KEY is required"
# - "OSError: [Errno 13] Permission denied: '/app/data'"
```

**Solutions:**

**A. Missing Dependencies:**
```bash
# Rebuild image
docker-compose build --no-cache
docker-compose up -d
```

**B. Missing Environment Variables:**
```bash
# Check .env file exists
ls -la .env

# Validate configuration
nano .env  # Ensure FINNHUB_API_KEY is set

# Test environment loading
docker-compose config
```

**C. Permission Issues:**
```bash
# Fix data directory permissions
chmod 755 ./data
chmod 644 ./data/trades.db

# Fix logs directory permissions
chmod 755 ./logs
```

#### Issue 2: "No module named 'vibe'" Error

**Diagnosis:**
```bash
docker-compose logs | grep "ModuleNotFoundError"
```

**Solution:**

Ensure you're running from the correct directory:
```bash
# Should be in: ~/strategy-lab/vibe/trading_bot
pwd
# Output: /home/ubuntu/strategy-lab/vibe/trading_bot

# Check Dockerfile COPY paths
cat Dockerfile | grep COPY

# Rebuild with correct context
docker-compose build --no-cache
```

#### Issue 3: Finnhub WebSocket Connection Fails

**Symptoms:**
```
ERROR - [data.providers.finnhub] WebSocket connection failed: 401 Unauthorized
ERROR - [data.providers.finnhub] Failed to connect after 3 retries
```

**Solutions:**

**A. Invalid API Key:**
```bash
# Verify API key in .env
nano .env
# FINNHUB_API_KEY should start with 'c' and be ~20 characters

# Test API key manually
curl -X GET "https://finnhub.io/api/v1/quote?symbol=AAPL&token=YOUR_API_KEY"
# Should return: {"c":150.25,"h":151.00,...}
```

**B. Free Tier Limits Exceeded:**
```
# Free tier: 60 API calls/minute
# Solution: Reduce symbols or increase cache TTL

nano .env
# Set fewer symbols:
SYMBOLS=["AAPL", "GOOGL"]  # Instead of 5+ symbols

# Increase cache TTL:
DATA_CACHE_TTL_SECONDS=7200  # 2 hours instead of 1
```

**C. WebSocket Connection Limit:**
```
# Free tier: 1 WebSocket connection
# Ensure no other instances are running:

docker ps  # Should show only 1 trading-bot container
```

#### Issue 4: Database Lock Error

**Symptoms:**
```
ERROR - [storage.database] database is locked
sqlite3.OperationalError: database is locked
```

**Solutions:**

**A. Multiple Processes Accessing Database:**
```bash
# Check for multiple running instances
docker ps
# Should show only 1 trading-bot container

# Stop all containers
docker-compose down

# Remove lock file (if exists)
rm ./data/trades.db-shm
rm ./data/trades.db-wal

# Start single instance
docker-compose up -d
```

**B. Upgrade to PostgreSQL** (for production):

See [PostgreSQL Setup](#postgresql-setup-optional---production) section.

#### Issue 5: Dashboard Not Accessible

**Symptoms:**
```bash
# Browser shows: "This site can't be reached"
# or "Connection refused" when accessing http://<IP>:8501
```

**Solutions:**

**A. Check Container Status:**
```bash
docker-compose ps
# Should show: Up X hours (healthy)
```

**B. Check Port Binding:**
```bash
docker-compose logs | grep "8501"
# Should show: Streamlit dashboard available at http://0.0.0.0:8501
```

**C. Check Firewall:**
```bash
# Oracle Cloud Security List
# Ensure ingress rule exists for port 8501

# Ubuntu UFW
sudo ufw status
# Should show: 8501/tcp ALLOW
```

**D. Use SSH Tunnel (Recommended):**
```bash
# From local machine
ssh -L 8501:localhost:8501 -i key.pem ubuntu@<ORACLE_IP>

# Access: http://localhost:8501
```

#### Issue 6: Discord Notifications Not Sending

**Symptoms:**
```
ERROR - [notifications.discord] Failed to send notification: 404 Not Found
ERROR - [notifications.discord] Failed to send notification: Connection timeout
```

**Solutions:**

**A. Invalid Webhook URL:**
```bash
# Test webhook manually
curl -X POST "YOUR_DISCORD_WEBHOOK_URL" \
  -H "Content-Type: application/json" \
  -d '{"content": "Test message"}'

# Should return: empty response (success)
# If 404: Webhook was deleted, create new one
# If 401: Invalid webhook URL
```

**B. Rate Limit Exceeded:**
```
# Discord limit: 5 requests per 2 seconds
# Solution: Bot has built-in rate limiting, but check for issues

docker-compose logs | grep "rate_limit"
# If you see warnings, notifications are queued
```

**C. Network Issues:**
```bash
# Test internet connectivity from container
docker-compose exec trading-bot bash
curl https://discord.com
exit

# If fails: Check Oracle Cloud egress rules
```

#### Issue 7: High Memory Usage

**Symptoms:**
```bash
docker stats trading-bot
# Shows: MEM USAGE: 480MB / 512MB (94%)
```

**Solutions:**

**A. Reduce Cache Size:**
```bash
nano .env

# Reduce cache TTL
DATA_CACHE_TTL_SECONDS=1800  # 30 minutes

# Reduce tracked intervals
BAR_INTERVALS=["5m"]  # Instead of ["1m", "5m", "15m"]
```

**B. Increase Memory Limit:**
```bash
nano docker-compose.yml

# Change:
memory: 768m  # Instead of 512m

# Restart:
docker-compose up -d
```

**C. Add Memory Monitoring:**
```bash
# Add to monitor script
MEMORY=$(docker stats trading-bot --no-stream --format "{{.MemPerc}}" | sed 's/%//')
if (( $(echo "$MEMORY > 80" | bc -l) )); then
    echo "$(date): High memory usage: $MEMORY%" | tee -a ~/monitor.log
    # Could trigger alert or restart
fi
```

#### Issue 8: "Permission denied" on Data Directory

**Symptoms:**
```
ERROR - OSError: [Errno 13] Permission denied: '/app/data/trades.db'
```

**Solutions:**

```bash
# Container runs as UID 1000 (trading user)
# Ensure host directories are accessible:

# Fix permissions
sudo chown -R 1000:1000 ./data
sudo chown -R 1000:1000 ./logs

# Alternative: Change container user in docker-compose.yml
nano docker-compose.yml

# Add:
services:
  trading-bot:
    user: "${UID}:${GID}"

# Then restart:
docker-compose down
docker-compose up -d
```

### Debug Mode

Enable debug logging for troubleshooting:

```bash
# Edit .env
nano .env

# Change:
LOG_LEVEL=DEBUG  # Instead of INFO

# Restart:
docker-compose restart

# View detailed logs:
docker-compose logs -f | grep DEBUG
```

### Getting Help

If issues persist:

1. **Check logs:**
   ```bash
   docker-compose logs --tail=200 > debug.log
   ```

2. **Verify configuration:**
   ```bash
   docker-compose config > config_output.yml
   ```

3. **Collect system info:**
   ```bash
   docker version
   docker-compose version
   uname -a
   free -h
   df -h
   ```

4. **Create GitHub issue:**
   - Repository: [strategy-lab](https://github.com/YOUR_USERNAME/strategy-lab/issues)
   - Include: logs, config, system info
   - Redact secrets (API keys, webhooks)

---

## Verification Steps

### Post-Deployment Checklist

After deployment, verify everything is working:

#### ✅ Step 1: Container Health

```bash
# Check container status
docker ps

# Expected output:
NAME          STATUS
trading-bot   Up X minutes (healthy)

# If status shows "unhealthy", check logs:
docker-compose logs --tail=50
```

#### ✅ Step 2: Health Endpoint

```bash
# Test health API
curl http://localhost:8080/api/health | jq

# Expected response:
{
  "status": "healthy",
  "uptime_seconds": 120,
  "total_errors": 0,
  "components": {
    "database": "healthy",
    "data_provider": "healthy",
    "exchange": "healthy",
    "websocket": "connected"
  },
  "timestamp": "2024-01-15T10:30:00Z"
}
```

#### ✅ Step 3: Database Initialization

```bash
# Check database exists
ls -lh ./data/trades.db

# Expected: File exists with size > 0 bytes

# Verify tables (optional)
docker-compose exec trading-bot python3 -c "
import sqlite3
conn = sqlite3.connect('/app/data/trades.db')
cursor = conn.cursor()
cursor.execute(\"SELECT name FROM sqlite_master WHERE type='table'\")
print('Tables:', cursor.fetchall())
conn.close()
"

# Expected output:
# Tables: [('trades',), ('positions',), ('account',), ('system_health',)]
```

#### ✅ Step 4: Data Provider Connection

```bash
# Check logs for data provider initialization
docker-compose logs | grep "data.manager"

# Expected:
INFO - [data.manager] Initialized data manager for 3 symbols
INFO - [data.manager] Connected to Yahoo Finance provider
INFO - [data.providers.finnhub] WebSocket connected successfully
```

#### ✅ Step 5: Dashboard Access

```bash
# Test Streamlit dashboard
curl -I http://localhost:8501

# Expected:
HTTP/1.1 200 OK

# Open browser:
# http://localhost:8501 (via SSH tunnel)
# or http://<ORACLE_IP>:8501 (public access)

# Verify dashboard shows:
# - Account summary data
# - Performance metrics
# - System health status
```

#### ✅ Step 6: WebSocket Connection

```bash
# Install wscat (if not installed)
npm install -g wscat

# Connect to WebSocket
wscat -c ws://localhost:8080/ws/updates

# You should see connection message:
Connected (press CTRL+C to quit)

# Wait for updates (or trigger by executing trade)
# Expected format:
{
  "type": "account_update",
  "timestamp": "2024-01-15T10:30:00",
  "data": { "cash": 10000.00, "equity": 10000.00 }
}
```

#### ✅ Step 7: Discord Notifications

```bash
# Test Discord webhook
curl -X POST "$DISCORD_WEBHOOK_URL" \
  -H "Content-Type: application/json" \
  -d '{
    "content": "✅ Trading Bot Deployment Verified",
    "embeds": [{
      "title": "Deployment Success",
      "description": "All systems operational!",
      "color": 65280,
      "timestamp": "'$(date -u +%Y-%m-%dT%H:%M:%SZ)'"
    }]
  }'

# Check Discord channel for message
```

#### ✅ Step 8: Trading Logic (Dry Run)

```bash
# Run in dry-run mode to test strategy without real trades
docker-compose exec trading-bot python -m vibe.trading_bot.main run --dry-run

# Monitor logs:
docker-compose logs -f

# Expected:
INFO - Starting trading bot (environment=production)
WARNING - DRY RUN MODE: No real orders will be executed
INFO - [strategies.orb] ORB levels calculated: AAPL high=150.50, low=148.20
INFO - [strategies.orb] Signal generated: BUY AAPL (DRY RUN)
```

#### ✅ Step 9: Backtest (Optional)

```bash
# Run backtest for past week
docker-compose exec trading-bot python -m vibe.trading_bot.main backtest \
  --start 2024-01-08 \
  --end 2024-01-15

# Expected output:
INFO - Starting backtest: 2024-01-08 to 2024-01-15
INFO - Backtest completed: status=completed
INFO - Total trades: 15
INFO - Win rate: 60.0%
INFO - Total P&L: +$450.00 (+4.5%)
```

### Monitoring Verification

#### ✅ Automated Health Checks

```bash
# Verify cron job is running
crontab -l

# Expected:
*/5 * * * * /home/ubuntu/monitor_trading_bot.sh

# Check monitoring log
tail -20 ~/monitor.log

# Expected:
2024-01-15 10:00:00: Trading bot is healthy
2024-01-15 10:05:00: Trading bot is healthy
```

#### ✅ Log Rotation

```bash
# Check Docker log files
docker inspect trading-bot | jq '.[0].HostConfig.LogConfig'

# Expected:
{
  "Type": "json-file",
  "Config": {
    "max-file": "3",
    "max-size": "10m"
  }
}
```

### Performance Verification

#### ✅ Resource Usage

```bash
# Check CPU and memory
docker stats trading-bot --no-stream

# Expected (for Oracle Free Tier):
CPU %: < 5%
MEM USAGE: < 400MB

# Check disk usage
df -h ./data
# Expected: < 1GB for MVP
```

#### ✅ Response Times

```bash
# Test API response time
time curl -s http://localhost:8080/api/health > /dev/null

# Expected: < 0.5 seconds

# Test dashboard load time
time curl -s http://localhost:8501 > /dev/null

# Expected: < 2 seconds
```

### Final Verification Summary

Run this comprehensive check script:

```bash
#!/bin/bash
echo "=== Trading Bot Verification ==="
echo ""

echo "1. Container Status:"
docker ps --filter name=trading-bot --format "table {{.Names}}\t{{.Status}}"
echo ""

echo "2. Health Check:"
curl -s http://localhost:8080/api/health | jq -r '.status'
echo ""

echo "3. Database:"
ls -lh ./data/trades.db
echo ""

echo "4. Logs (last 5 lines):"
docker-compose logs --tail=5
echo ""

echo "5. Resource Usage:"
docker stats trading-bot --no-stream --format "CPU: {{.CPUPerc}}, Memory: {{.MemUsage}}"
echo ""

echo "=== Verification Complete ==="
```

**Save as** `verify_deployment.sh`, make executable, and run:
```bash
chmod +x verify_deployment.sh
./verify_deployment.sh
```

**Expected Output:**
```
=== Trading Bot Verification ===

1. Container Status:
NAMES         STATUS
trading-bot   Up 2 hours (healthy)

2. Health Check:
healthy

3. Database:
-rw-r--r-- 1 ubuntu ubuntu 524K Jan 15 10:30 ./data/trades.db

4. Logs (last 5 lines):
trading-bot | INFO - [strategies.orb] ORB levels calculated
trading-bot | INFO - [data.manager] Market data updated
...

5. Resource Usage:
CPU: 2.5%, Memory: 380MB / 512MB

=== Verification Complete ===
```

---

## Conclusion

🎉 **Congratulations!** Your trading bot MVP is now deployed on Oracle Cloud.

### Next Steps

1. **Monitor for 24 hours** in dry-run mode to ensure stability
2. **Review logs daily** for any errors or warnings
3. **Optimize strategy parameters** based on backtest results
4. **Enable live trading** by removing `--dry-run` flag
5. **Setup automated backups** for database and logs
6. **Consider upgrading** to PostgreSQL for production
7. **Implement authentication** for dashboard public access
8. **Scale up** Oracle Cloud instance if needed

### Production Checklist

Before going live with real money:

- [ ] Tested with dry-run for at least 1 week
- [ ] Reviewed all backtest results
- [ ] Verified risk management parameters (stop loss, position size)
- [ ] Enabled Discord notifications
- [ ] Setup automated monitoring and alerts
- [ ] Documented incident response procedures
- [ ] Configured database backups
- [ ] Tested disaster recovery process
- [ ] Reviewed and understood all trading logic
- [ ] Started with small capital allocation ($100-$1000)

### Support and Resources

- **Documentation**: `docs/trading-bot-mvp/`
- **Code Repository**: [strategy-lab](https://github.com/YOUR_USERNAME/strategy-lab)
- **Issues**: [GitHub Issues](https://github.com/YOUR_USERNAME/strategy-lab/issues)
- **Dashboard README**: `vibe/trading_bot/dashboard/README.md`

### Disclaimer

⚠️ **Trading involves substantial risk of loss. This bot is for educational purposes. Always:**
- Start with paper trading (dry-run mode)
- Never risk more than you can afford to lose
- Understand the strategy logic before deploying
- Monitor the bot regularly
- Have stop-loss and risk management in place

**This software is provided "as is" without warranty of any kind.**

---

**Last Updated**: 2024-01-15
**Version**: 1.0.0
**Target Deployment**: Oracle Cloud (Always Free Tier)
