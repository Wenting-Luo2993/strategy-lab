# Cloud Monitoring Implementation

**Start Date**: January 11, 2026
**Last Updated**: January 31, 2026
**Target Completion**: 4-6 weeks
**Status**: ✅ Phase 1 Complete! (100%)

---

## 📊 Progress Overview

| Phase | Status | Progress | Completed Date |
|-------|--------|----------|----------------|
| **Phase 1: SQLite Trade Database** | ✅ Complete | 3/3 tasks | Jan 31, 2026 |
| **Phase 2: Grafana Loki Logging** | ⏳ Pending | 0/3 tasks | - |
| **Phase 3: Grafana Dashboard** | ⏳ Pending | 0/4 tasks | - |
| **Phase 4: Discord Webhook Alerting** | ⏳ Pending | 0/4 tasks | - |
| **Phase 5: Testing & Optimization** | ⏳ Pending | 0/4 tasks | - |
| **Phase 6: Production Deployment** | ⏳ Pending | 0/3 tasks | - |

**Overall Progress**: 3 of 21 tasks complete (14.3%)

---

## 📋 Tech Stack

| Component | Technology | Cost | Status |
|-----------|-----------|------|--------|
| **Compute** | Oracle Cloud Always Free | $0 | ✅ Done |
| **Deployment** | Docker | $0 | ✅ Done |
| **Logging** | Grafana Loki | $0 | ⏳ Pending |
| **Dashboard** | Grafana + Grafana Cloud | $0 | ⏳ Pending |
| **Database** | SQLite | $0 | ✅ Done |
| **Alerting** | Discord Webhook | $0 | ⏳ Pending |

---

## 🎯 Implementation Phases

### Phase 1: SQLite Trade Database (Week 1)
**Goal**: Store all trade executions in a queryable database with automated backups

---

## ✅ Completed Tasks

### Task 1.1: Create Trade Store Module ✅ **COMPLETE** (Jan 26, 2026)

**What was built:**
- Production-ready `TradeStore` class with SQLite backend
- Full CRUD operations for trade data
- Thread-safe implementation with connection pooling
- 23 unit tests (all passing!)

**Key Features:**
- ✅ Record trades with symbol, side, quantity, price, strategy, P&L, metadata
- ✅ Query trades by: recent, date, symbol, strategy
- ✅ Daily summary statistics: total trades, P&L, win rate, best/worst trades
- ✅ Thread-safe for concurrent access
- ✅ WAL mode enabled for better concurrency
- ✅ Automatic schema creation
- ✅ JSON metadata storage

**Files Created:**
- `src/data/trade_store.py` (289 lines)
- `tests/data/test_trade_store.py` (362 lines)
- `examples/trade_store_example.py` (102 lines)
- `scripts/view_trades.py` (144 lines)

**Acceptance Criteria Met:**
- ✅ Can record trades with all fields
- ✅ Can query trades by various filters
- ✅ Database file created at `data/trades.db`
- ✅ Tests pass (23/23 passed!)

---

### Task 1.2: Integrate Trade Store into Trading Scripts ✅ **COMPLETE** (Jan 31, 2026)

**What was built:**
- Integrated TradeStore into orchestrator initialization
- Added trade recording in DarkTradingOrchestrator
- Proper error handling and cleanup

**Changes Made:**
- ✅ Modified `orchestrator_main.py` to initialize TradeStore
- ✅ Updated `DarkTradingOrchestrator._record_trade()` to save to SQLite
- ✅ Added TradeStore cleanup on shutdown
- ✅ Tested integration with paper trading

**Acceptance Criteria Met:**
- ✅ Every trade execution is recorded to SQLite
- ✅ No impact on trading performance
- ✅ Errors are logged but don't stop trading

---

### Task 1.2b: Add Continuous Cloud Sync ✅ **COMPLETE** (Jan 31, 2026)

**What was built:**
- Cloud-agnostic storage provider interface
- Local storage provider for testing
- Database sync daemon with background threading
- Comprehensive test suite (16 tests, all passing!)

**Files Created:**
- `src/cloud/storage_provider.py` (abstract base class)
- `src/cloud/storage_factory.py` (provider factory)
- `src/cloud/providers/local_storage.py` (local implementation)
- `src/cloud/database_sync.py` (sync daemon)
- `tests/cloud/test_storage.py` (comprehensive tests)

**Key Features:**
- ✅ Abstract provider interface (cloud-agnostic)
- ✅ Background sync every 5 minutes (configurable)
- ✅ Uploads as 'latest' and timestamped backups
- ✅ Automatic cleanup of old backups (30 days retention)
- ✅ Graceful error handling
- ✅ Non-blocking operation
- ✅ Thread-safe

**Acceptance Criteria Met:**
- ✅ Database synced to cloud every 5 minutes automatically
- ✅ `trades_latest.db` always available in cloud
- ✅ Sync runs in background (no performance impact)
- ✅ Graceful shutdown waits for final sync
- ✅ Can disable sync via env var if needed

---

## 🚧 In Progress

### Task 1.3: Cloud Provider Implementations ✅ **COMPLETE** (Jan 31, 2026)

**What was built:**
- Oracle Cloud Infrastructure (OCI) Object Storage provider
- Microsoft Azure Blob Storage provider
- Cloud provider setup documentation
- Environment variable examples

**Files Created:**
- `src/cloud/providers/oracle_storage.py` (234 lines)
- `src/cloud/providers/azure_storage.py` (196 lines)
- `docs/cloud-monitoring/CLOUD_PROVIDER_SETUP.md` (comprehensive guide)
- `.env.example` (environment variable template)

**Oracle Cloud Provider Features:**
- ✅ Instance principal authentication (for OCI compute instances)
- ✅ Config file authentication (for local development)
- ✅ Full CRUD operations (upload, download, list, delete, exists)
- ✅ Automatic bucket verification
- ✅ Comprehensive error handling
- ✅ Environment variables: OCI_BUCKET_NAME, OCI_NAMESPACE

**Azure Storage Provider Features:**
- ✅ Connection string authentication (easiest)
- ✅ Account name + key authentication
- ✅ SAS token authentication
- ✅ Full CRUD operations (upload, download, list, delete, exists)
- ✅ Automatic container creation
- ✅ Comprehensive error handling
- ✅ Environment variable: AZURE_CONTAINER_NAME

**Acceptance Criteria Met:**
- ✅ Can switch cloud providers via environment variable
- ✅ Oracle Cloud provider supports instance principal (no credentials needed on OCI)
- ✅ Azure provider supports multiple authentication methods
- ✅ Comprehensive setup documentation created
- ✅ Environment variable examples provided
- ✅ Optional dependencies documented in requirements.txt

**Documentation:**
- ✅ Cloud provider setup guide with examples for each provider
- ✅ Authentication methods for Oracle and Azure
- ✅ Troubleshooting section
- ✅ Cost optimization tips
- ✅ Best practices

**Notes:**
- AWS and GCP providers can be added later if needed (not required for user's current setup)
- User's primary cloud: Oracle Cloud Always Free tier
- User's secondary cloud: Microsoft Azure
- Tests pending (need cloud credentials to test fully)

---

#### Task 1.2b: Add Continuous Cloud Sync (Optional but Recommended)
- [ ] Create `src/cloud/database_sync.py` - Background sync daemon:
  ```python
  import threading
  import time
  from datetime import datetime

  class DatabaseSyncDaemon:
      def __init__(self, db_path, storage_provider, sync_interval=300):
          """
          Args:
              db_path: Path to SQLite database
              storage_provider: CloudStorageProvider instance
              sync_interval: Seconds between syncs (default 300 = 5 min)
          """
          self.db_path = db_path
          self.storage = storage_provider
          self.sync_interval = sync_interval
          self.running = False
          self.thread = None

      def start(self):
          """Start background sync in separate thread"""
          self.running = True
          self.thread = threading.Thread(target=self._sync_loop, daemon=True)
          self.thread.start()

      def stop(self):
          """Stop background sync gracefully"""
          self.running = False
          if self.thread:
              self.thread.join(timeout=30)

      def _sync_loop(self):
          while self.running:
              try:
                  self.sync_now()
              except Exception as e:
                  logger.error(f"Cloud sync failed: {e}")
              time.sleep(self.sync_interval)

      def sync_now(self):
          """Perform immediate sync to cloud"""
          timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

          # Upload as latest (overwrites)
          self.storage.upload_file(self.db_path, 'trades_latest.db')

          # Also save timestamped backup (once per day)
          if self._should_create_backup():
              self.storage.upload_file(
                  self.db_path,
                  f'backups/trades_{timestamp}.db'
              )
  ```

- [ ] Integrate into `orchestrator_main.py`:
  ```python
  from src.cloud.storage_factory import get_storage_provider
  from src.cloud.database_sync import DatabaseSyncDaemon

  # At startup
  storage = get_storage_provider()
  sync_daemon = DatabaseSyncDaemon(
      db_path='data/trades.db',
      storage_provider=storage,
      sync_interval=300  # 5 minutes (configurable via env)
  )
  sync_daemon.start()

  # At shutdown
  sync_daemon.stop()  # Final sync before exit
  ```

- [ ] Add to `.env`:
  ```bash
  # Database sync settings
  DB_SYNC_ENABLED=true
  DB_SYNC_INTERVAL=300  # seconds (300 = 5 min)
  ```

- [ ] Test sync functionality:
  - [ ] Insert trade, wait 5 minutes, verify uploaded to cloud
  - [ ] Check `trades_latest.db` exists in cloud storage
  - [ ] Download and verify data integrity

**Acceptance Criteria**:
- ✅ Database synced to cloud every 5 minutes automatically
- ✅ `trades_latest.db` always available in cloud
- ✅ Sync runs in background (no performance impact)
- ✅ Graceful shutdown waits for final sync
- ✅ Can disable sync via env var if needed

**Files to Create/Modify**:
- `src/cloud/database_sync.py` (new)
- `orchestrator_main.py` (add sync daemon)
- `.env` (add sync config)

---

#### Task 1.3: Cloud-Agnostic Backup Module
- [ ] Create `src/cloud/storage_provider.py` - Abstract base class for cloud storage
  ```python
  from abc import ABC, abstractmethod

  class CloudStorageProvider(ABC):
      @abstractmethod
      def upload_file(self, local_path, remote_path):
          pass

      @abstractmethod
      def download_file(self, remote_path, local_path):
          pass

      @abstractmethod
      def list_files(self, prefix):
          pass

      @abstractmethod
      def delete_file(self, remote_path):
          pass
  ```

- [ ] Create provider implementations:
  - `src/cloud/providers/oracle_storage.py` - Oracle Object Storage
  - `src/cloud/providers/aws_storage.py` - AWS S3
  - `src/cloud/providers/gcp_storage.py` - Google Cloud Storage
  - `src/cloud/providers/azure_storage.py` - Azure Blob Storage
  - `src/cloud/providers/local_storage.py` - Local filesystem (for testing)

- [ ] Create `src/cloud/storage_factory.py` - Factory to get provider by config:
  ```python
  def get_storage_provider(provider_name=None):
      provider_name = provider_name or os.getenv('CLOUD_STORAGE_PROVIDER', 'oracle')
      if provider_name == 'oracle':
          return OracleStorageProvider()
      elif provider_name == 'aws':
          return AWSStorageProvider()
      elif provider_name == 'gcp':
          return GCPStorageProvider()
      elif provider_name == 'azure':
          return AzureStorageProvider()
      else:
          return LocalStorageProvider()
  ```

- [ ] Create `scripts/backup_trades_to_cloud.py` - Uses factory pattern:
  ```python
  from src.cloud.storage_factory import get_storage_provider

  storage = get_storage_provider()  # Automatically uses configured provider
  storage.upload_file('data/trades.db', f'backups/trades_{timestamp}.db')
  ```

- [ ] Add to `.env`:
  ```bash
  # Cloud Storage Provider (oracle, aws, gcp, azure, local)
  CLOUD_STORAGE_PROVIDER=oracle

  # Oracle Cloud (only needed if using Oracle)
  OCI_BUCKET_NAME=strategy-lab-backups
  OCI_NAMESPACE=your-namespace

  # AWS (only needed if using AWS)
  AWS_S3_BUCKET=strategy-lab-backups
  AWS_REGION=us-east-1

  # GCP (only needed if using GCP)
  GCP_BUCKET_NAME=strategy-lab-backups
  GCP_PROJECT_ID=your-project

  # Azure (only needed if using Azure)
  AZURE_STORAGE_ACCOUNT=strategylabstorage
  AZURE_CONTAINER=backups
  ```

- [ ] Install SDKs (install only what you need):
  ```bash
  # Oracle Cloud
  pip install oci

  # AWS (if switching to AWS)
  pip install boto3

  # GCP (if switching to GCP)
  pip install google-cloud-storage

  # Azure (if switching to Azure)
  pip install azure-storage-blob
  ```

- [ ] Schedule daily backup after market close (4:30 PM ET)
- [ ] Add backup to `orchestrator_main.py` shutdown routine
- [ ] Test backup and restore with current provider
- [ ] Test switching providers (change env var, verify still works)

**Acceptance Criteria**:
- ✅ Backup works with Oracle Cloud (default)
- ✅ Can switch to AWS/GCP/Azure by changing ONE env variable
- ✅ No code changes needed to switch providers
- ✅ Can manually restore from any cloud backup
- ✅ Old backups automatically cleaned up (30 days)

**Files to Create/Modify**:
- `src/cloud/__init__.py` (new)
- `src/cloud/storage_provider.py` (new - abstract base)
- `src/cloud/storage_factory.py` (new - provider factory)
- `src/cloud/providers/__init__.py` (new)
- `src/cloud/providers/oracle_storage.py` (new)
- `src/cloud/providers/aws_storage.py` (new)
- `src/cloud/providers/gcp_storage.py` (new)
- `src/cloud/providers/azure_storage.py` (new)
- `src/cloud/providers/local_storage.py` (new - for testing)
- `scripts/backup_trades_to_cloud.py` (new - uses factory)
- `orchestrator_main.py` (add backup call)
- `.env` (add cloud provider config)
- `requirements.txt` (add SDKs as optional dependencies)

---

### Phase 2: Grafana Loki Logging (Week 2)
**Goal**: Centralized log aggregation with search and filtering capabilities

#### Task 2.1: Deploy Grafana Loki Container
- [ ] Update `docker-compose.yml` to add Loki service:
  ```yaml
  loki:
    image: grafana/loki:latest
    container_name: loki
    ports:
      - "3100:3100"
    volumes:
      - loki_data:/loki
      - ./loki-config.yaml:/etc/loki/local-config.yaml
    restart: unless-stopped
  ```
- [ ] Create `loki-config.yaml` configuration:
  - Set retention period (30 days)
  - Configure storage (local filesystem)
  - Set memory limits
- [ ] Add `loki_data` volume to docker-compose
- [ ] Deploy and verify Loki is running: `curl http://localhost:3100/ready`
- [ ] Test Loki API: `curl http://localhost:3100/loki/api/v1/labels`

**Acceptance Criteria**:
- ✅ Loki container running on port 3100
- ✅ Health check returns OK
- ✅ Ready to receive logs

**Files to Create/Modify**:
- `python/docker-compose.yml` (modify)
- `python/loki-config.yaml` (new)

---

#### Task 2.2: Configure Log Shipping from Bot to Loki
- [ ] Install Python logging handler: `pip install python-logging-loki`
- [ ] Update `src/utils/logger.py` to add Loki handler:
  ```python
  from logging_loki import LokiHandler

  loki_handler = LokiHandler(
      url="http://loki:3100/loki/api/v1/push",
      tags={"application": "trading-bot"},
      version="1",
  )
  ```
- [ ] Add Loki handler to logger configuration
- [ ] Keep file logging as backup (dual logging)
- [ ] Add structured labels:
  - `level` (INFO, WARNING, ERROR)
  - `logger` (module name)
  - `strategy` (ORB, etc.)
  - `symbol` (AAPL, etc., when applicable)
- [ ] Test log shipping: generate logs and verify in Loki
- [ ] Query logs via API: `curl -G http://localhost:3100/loki/api/v1/query_range --data-urlencode 'query={application="trading-bot"}'`

**Acceptance Criteria**:
- ✅ Logs appear in Loki within seconds
- ✅ Can query logs by labels
- ✅ File logs still work (fallback)
- ✅ No performance impact on bot

**Files to Modify**:
- `src/utils/logger.py`
- `requirements.txt` (add `python-logging-loki`)

---

#### Task 2.3: Optional - Ship to Grafana Cloud (if using managed)
- [ ] Sign up for Grafana Cloud free tier (https://grafana.com/products/cloud/)
- [ ] Get Grafana Cloud Loki endpoint and credentials
- [ ] Update Loki handler URL to point to Grafana Cloud:
  ```python
  loki_handler = LokiHandler(
      url="https://<your-instance>.grafana.net/loki/api/v1/push",
      auth=("user-id", "api-key"),
      tags={"application": "trading-bot"},
  )
  ```
- [ ] Store credentials in environment variables
- [ ] Test log shipping to Grafana Cloud
- [ ] Verify logs appear in Grafana Cloud Explore

**Acceptance Criteria**:
- ✅ Logs visible in Grafana Cloud within 30 seconds
- ✅ Can access logs from anywhere (not just Oracle instance)

**Files to Modify**:
- `src/utils/logger.py`
- `.env` or environment configuration

---

### Phase 3: Grafana Dashboard (Week 3)
**Goal**: Real-time web dashboard for monitoring trades, logs, and bot health

#### Task 3.1: Deploy Grafana Container (Self-Hosted Option)
- [ ] Update `docker-compose.yml` to add Grafana service:
  ```yaml
  grafana:
    image: grafana/grafana:latest
    container_name: grafana
    ports:
      - "3000:3000"
    environment:
      - GF_SECURITY_ADMIN_PASSWORD=${GRAFANA_PASSWORD}
      - GF_INSTALL_PLUGINS=grafana-clock-panel,frser-sqlite-datasource
    volumes:
      - grafana_data:/var/lib/grafana
      - ./grafana/provisioning:/etc/grafana/provisioning
      - ./data:/data:ro
    restart: unless-stopped
  ```
- [ ] Add `grafana_data` volume
- [ ] Create `grafana/provisioning/datasources/` directory
- [ ] Create `grafana/provisioning/dashboards/` directory
- [ ] Set `GRAFANA_PASSWORD` in `.env` file
- [ ] Deploy Grafana: `docker-compose up -d grafana`
- [ ] Access Grafana at `http://your-oracle-ip:3000`
- [ ] Login with admin credentials

**Acceptance Criteria**:
- ✅ Grafana accessible via browser
- ✅ Can login successfully
- ✅ Ready to configure datasources

**Files to Create/Modify**:
- `python/docker-compose.yml` (modify)
- `python/.env` (add GRAFANA_PASSWORD)
- `python/grafana/provisioning/` (create directory structure)

---

#### Task 3.2: Configure Datasources
- [ ] Create `grafana/provisioning/datasources/loki.yaml`:
  ```yaml
  apiVersion: 1
  datasources:
    - name: Loki
      type: loki
      access: proxy
      url: http://loki:3100
      isDefault: false
  ```
- [ ] Create `grafana/provisioning/datasources/sqlite.yaml`:
  ```yaml
  apiVersion: 1
  datasources:
    - name: Trades
      type: frser-sqlite-datasource
      access: proxy
      jsonData:
        path: /data/trades.db
  ```
- [ ] Restart Grafana to load datasources
- [ ] Verify datasources in Grafana UI (Configuration > Data sources)
- [ ] Test Loki datasource: Run query `{application="trading-bot"}`
- [ ] Test SQLite datasource: Run query `SELECT * FROM trades LIMIT 10`

**Acceptance Criteria**:
- ✅ Both datasources show "Working" status
- ✅ Can query logs from Loki
- ✅ Can query trades from SQLite

**Files to Create**:
- `python/grafana/provisioning/datasources/loki.yaml`
- `python/grafana/provisioning/datasources/sqlite.yaml`

---

#### Task 3.3: Build Trading Bot Dashboard
- [ ] Create dashboard JSON: `grafana/provisioning/dashboards/trading-bot.json`
- [ ] Add dashboard config: `grafana/provisioning/dashboards/dashboard.yaml`
- [ ] Create panels:

**Panel 1: Bot Status**
  - [ ] Type: Stat
  - [ ] Query: SQLite - Check last trade timestamp (< 1 hour = Running)
  - [ ] Show: 🟢 Running / 🔴 Stopped

**Panel 2: Today's P&L**
  - [ ] Type: Stat
  - [ ] Query: `SELECT SUM(pnl) FROM trades WHERE date(timestamp) = date('now')`
  - [ ] Show: Dollar amount with +/- color

**Panel 3: Total Trades Today**
  - [ ] Type: Stat
  - [ ] Query: `SELECT COUNT(*) FROM trades WHERE date(timestamp) = date('now')`

**Panel 4: Win Rate**
  - [ ] Type: Gauge
  - [ ] Query: `SELECT (COUNT(CASE WHEN pnl > 0 THEN 1 END) * 100.0 / COUNT(*)) FROM trades WHERE date(timestamp) = date('now')`
  - [ ] Show: Percentage 0-100%

**Panel 5: P&L Over Time (Chart)**
  - [ ] Type: Time series
  - [ ] Query: `SELECT timestamp, SUM(pnl) OVER (ORDER BY timestamp) as cumulative_pnl FROM trades`
  - [ ] Show: Line chart of cumulative P&L

**Panel 6: Recent Trades Table**
  - [ ] Type: Table
  - [ ] Query: `SELECT timestamp, symbol, side, quantity, price, pnl, strategy FROM trades ORDER BY timestamp DESC LIMIT 20`
  - [ ] Format columns (timestamp, currency for price/pnl)

**Panel 7: Live Logs**
  - [ ] Type: Logs
  - [ ] Datasource: Loki
  - [ ] Query: `{application="trading-bot"} | json`
  - [ ] Show: Last 100 lines, auto-refresh every 10s

**Panel 8: Trades by Symbol (Pie Chart)**
  - [ ] Type: Pie chart
  - [ ] Query: `SELECT symbol, COUNT(*) as count FROM trades WHERE date(timestamp) = date('now') GROUP BY symbol`

- [ ] Set dashboard refresh interval: 30 seconds
- [ ] Set time range: Last 24 hours
- [ ] Add variables for symbol filtering (optional)
- [ ] Save dashboard

**Acceptance Criteria**:
- ✅ All panels display data correctly
- ✅ Dashboard auto-refreshes
- ✅ Accessible on mobile browser
- ✅ Can filter by time range

**Files to Create**:
- `python/grafana/provisioning/dashboards/dashboard.yaml`
- `python/grafana/provisioning/dashboards/trading-bot.json`

---

#### Task 3.4: Secure Remote Access
- [ ] Configure Oracle Cloud Security List:
  - [ ] Allow inbound TCP port 3000 from your IP only (or VPN)
  - [ ] Or: Allow 0.0.0.0/0 if using Grafana auth
- [ ] Test access from external network: `http://your-oracle-ip:3000`
- [ ] Optional: Set up Cloudflare Tunnel for HTTPS:
  - [ ] Install cloudflared on Oracle instance
  - [ ] Configure tunnel to Grafana (port 3000)
  - [ ] Get HTTPS URL: `https://your-tunnel.trycloudflare.com`
- [ ] Optional: Set up Nginx reverse proxy with SSL
- [ ] Test mobile access (phone browser)

**Acceptance Criteria**:
- ✅ Can access dashboard remotely while at work
- ✅ Dashboard works on mobile browser
- ✅ Connection is secure (HTTPS preferred)

**Files to Create** (if using Cloudflare/Nginx):
- `cloudflare-tunnel-config.yaml` or `nginx.conf`

---

### Phase 4: Discord Webhook Alerting (Week 4)
**Goal**: Automated notifications for critical events, warnings, and daily summaries

#### Task 4.1: Set Up Discord Webhook
- [ ] Create Discord server (or use existing)
- [ ] Create dedicated channel: `#trading-bot-alerts`
- [ ] Go to Channel Settings > Integrations > Webhooks
- [ ] Click "New Webhook"
- [ ] Name it "Trading Bot"
- [ ] Copy webhook URL
- [ ] Add to `.env`: `DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...`
- [ ] Test webhook with curl:
  ```bash
  curl -H "Content-Type: application/json" \
    -d '{"content": "Test alert from trading bot!"}' \
    $DISCORD_WEBHOOK_URL
  ```
- [ ] Verify message appears in Discord channel

**Acceptance Criteria**:
- ✅ Webhook URL saved securely in .env
- ✅ Test message received in Discord

**Files to Modify**:
- `python/.env`

---

#### Task 4.2: Create Alerting Module
- [ ] Create `src/utils/alerting.py` module
- [ ] Define `AlertLevel` enum (INFO, WARNING, CRITICAL)
- [ ] Create `AlertManager` class:
  - [ ] `__init__()` - load Discord webhook URL from env
  - [ ] `send_alert(message, level)` - main method
  - [ ] `_send_discord(message, level)` - Discord webhook call
  - [ ] `_format_message(message, level)` - add emoji and formatting
  - [ ] Error handling (don't crash if webhook fails)
  - [ ] Rate limiting (max 5 alerts/minute)
- [ ] Add emoji mapping:
  - INFO: 🔔
  - WARNING: ⚠️
  - CRITICAL: 🚨
- [ ] Add color coding (Discord embeds):
  - INFO: Blue
  - WARNING: Orange
  - CRITICAL: Red
- [ ] Write unit tests

**Acceptance Criteria**:
- ✅ Can send alerts to Discord
- ✅ Different levels have different formatting
- ✅ Graceful error handling
- ✅ Tests pass

**Files to Create**:
- `src/utils/alerting.py` (new)
- `tests/utils/test_alerting.py` (new)

---

#### Task 4.3: Integrate Alerts into Trading Bot
- [ ] Import `AlertManager` in `orchestrator_main.py`
- [ ] Initialize AlertManager at startup
- [ ] Add alert triggers:

**Startup/Shutdown Alerts (INFO)**
  - [ ] "🔔 Trading bot started successfully"
  - [ ] "🔔 Market opened. Bot is active."
  - [ ] "🔔 Market closed. Bot stopped."
  - [ ] "🔔 Trading bot shutting down gracefully"

**Trade Execution Alerts (INFO - optional, may be too noisy)**
  - [ ] "💰 Trade executed: BUY AAPL 100 @ $150.25"
  - [ ] Only send if position size > threshold (e.g., > $1000)

**Warning Alerts (WARNING)**
  - [ ] Daily loss exceeds threshold (e.g., -$500)
  - [ ] Unusual trade frequency (> 50 trades/day)
  - [ ] Connection issues (retrying Finnhub WebSocket)
  - [ ] Low data quality (missing candles)

**Critical Alerts (CRITICAL)**
  - [ ] Bot crashed (unhandled exception)
  - [ ] WebSocket connection lost (cannot reconnect)
  - [ ] Database write failure
  - [ ] Risk limit breach (max daily loss)

**Daily Summary (INFO)**
  - [ ] Send at market close (4:00 PM ET)
  - [ ] Include:
    - Total trades
    - Win rate
    - Total P&L
    - Best/worst trade
    - Strategy performance

- [ ] Test each alert type
- [ ] Add try/except around alerts (don't crash bot if alert fails)

**Acceptance Criteria**:
- ✅ Alerts sent for all defined triggers
- ✅ Daily summary sent after market close
- ✅ Critical alerts are immediately visible
- ✅ Bot continues running even if alerts fail

**Files to Modify**:
- `orchestrator_main.py`
- Trading scripts (add trade alerts)
- Strategy files (add warning alerts)

---

#### Task 4.4: Create Alert Configuration
- [ ] Create `config/alerts.yaml`:
  ```yaml
  alerts:
    enabled: true
    channels:
      discord:
        enabled: true
        webhook_url_env: DISCORD_WEBHOOK_URL

    thresholds:
      daily_loss_warning: -500      # Alert if daily loss > $500
      trade_frequency_max: 50       # Alert if > 50 trades/day
      position_size_alert: 1000     # Alert on trades > $1000
      max_daily_loss_critical: -1000  # Stop trading if loss > $1000

    daily_summary:
      enabled: true
      time: "16:05"  # 5 min after market close

    trade_alerts:
      enabled: false  # Too noisy, set true if you want all trades
      min_position_size: 1000  # Only alert large trades
  ```
- [ ] Update `AlertManager` to load config from YAML
- [ ] Add config validation
- [ ] Make thresholds configurable (no hardcoding)

**Acceptance Criteria**:
- ✅ Alert behavior controlled by config file
- ✅ Can easily adjust thresholds without code changes
- ✅ Config validated on load

**Files to Create/Modify**:
- `config/alerts.yaml` (new)
- `src/utils/alerting.py` (load config)

---

### Phase 5: Testing & Optimization (Week 5-6)
**Goal**: Ensure system reliability and performance

#### Task 5.1: Integration Testing
- [ ] Test complete flow:
  1. Bot starts → Discord alert sent
  2. Trade executed → Recorded in SQLite
  3. Log generated → Appears in Loki
  4. Dashboard shows trade → Visible in Grafana
  5. Market closes → Daily summary sent
- [ ] Test error scenarios:
  - [ ] Discord webhook down (should log error, continue)
  - [ ] SQLite locked (should retry)
  - [ ] Loki unavailable (should fallback to file)
  - [ ] Grafana down (doesn't affect bot)
- [ ] Test resource usage:
  - [ ] Monitor CPU/RAM on Oracle instance
  - [ ] Ensure bot uses < 2 GB RAM total
  - [ ] Check disk usage (logs, DB)
- [ ] Test with paper trading for 1 full market day
- [ ] Verify all components working together

**Acceptance Criteria**:
- ✅ All integrations working
- ✅ Error handling robust
- ✅ Resource usage acceptable
- ✅ No data loss during failures

---

#### Task 5.2: Performance Optimization
- [ ] Optimize SQLite writes:
  - [ ] Use WAL mode: `PRAGMA journal_mode=WAL`
  - [ ] Batch writes if needed
  - [ ] Add connection pooling
- [ ] Optimize Loki logging:
  - [ ] Set buffer size to reduce HTTP calls
  - [ ] Use async logging if needed
  - [ ] Adjust log levels (DEBUG only for development)
- [ ] Optimize Grafana queries:
  - [ ] Add indexes to frequently queried columns
  - [ ] Use query caching
  - [ ] Limit data ranges in panels
- [ ] Clean up old data:
  - [ ] Archive logs > 30 days
  - [ ] Keep trades indefinitely (compress old data)
  - [ ] Rotate log files

**Acceptance Criteria**:
- ✅ Dashboard loads in < 2 seconds
- ✅ Database queries fast (< 100ms)
- ✅ No lag in log shipping
- ✅ Disk usage under control

---

#### Task 5.3: Documentation & Runbook
- [ ] Document architecture in README
- [ ] Create deployment guide:
  - Step-by-step setup instructions
  - Environment variables needed
  - Docker commands
- [ ] Create troubleshooting guide:
  - Common issues and solutions
  - How to check logs
  - How to restart services
- [ ] Create backup/restore procedure:
  - How to backup manually
  - How to restore from backup
  - How to verify backup integrity
- [ ] Create monitoring checklist:
  - Daily: Check dashboard, verify trades recorded
  - Weekly: Review alerts, check disk usage
  - Monthly: Review performance, optimize queries

**Acceptance Criteria**:
- ✅ Documentation complete and accurate
- ✅ Anyone can deploy following the guide
- ✅ Troubleshooting guide covers common issues

**Files to Create**:
- `docs/cloud-monitoring/DEPLOYMENT_GUIDE.md`
- `docs/cloud-monitoring/TROUBLESHOOTING.md`
- `docs/cloud-monitoring/BACKUP_RESTORE.md`

---

#### Task 5.4: Disaster Recovery Testing
- [ ] Test scenarios:
  1. **Oracle instance failure**:
     - [ ] Manually stop instance
     - [ ] Verify auto-restart works
     - [ ] Check data integrity
  2. **Database corruption**:
  PyYAML for config (may already be installed)
pip install pyyaml

# Cloud Storage (install only what you need)
# Oracle Cloud (default)
pip install oci

# AWS (optional - only if switching to AWS)
pip install boto3

# GCP (optional - only if switching to GCP)
pip install google-cloud-storage

# Azure (optional - only if switching to Azure)
pip install azure-storage-blob
```

Add to `requirements.txt`:
```
python-logging-loki>=0.3.1
pyyaml>=6.0.1

# Cloud storage providers (install based on your choice)
oci>=2.112.0  # Oracle Cloud (current)
# boto3>=1.26.0  # Uncomment if using AWS
# google-cloud-storage>=2.10.0  # Uncomment if using GCP
# azure-storage-blob>=12.19.0  # Uncomment if using Azure
```

**Note**: You only need to install the SDK for the cloud provider you're using. Switch providers by changing `CLOUD_STORAGE_PROVIDER` in `.env` - no code changes needed!cceptance Criteria**:
- ✅ All failure scenarios tested
- ✅ Recovery procedures documented
- ✅ RTO/RPO targets met

---

### Phase 6: Production Deployment (Week 6)
**Goal**: Go live with full monitoring

#### Task 6.1: Pre-Production Checklist
- [ ] Code review:
  - [ ] All modules tested
  - [ ] Error handling complete
  - [ ] Logging comprehensive
  - [ ] No hardcoded secrets
- [ ] Configuration review:
  - [ ] All environment variables set
  - [ ] Alert thresholds appropriate
  - [ ] Backup schedule configured
- [ ] Security review:
  - [ ] Secrets in .env (not in code)
  - [ ] Grafana password strong
  - [ ] Discord webhook URL private
  - [ ] Oracle firewall rules configured
- [ ] Documentation review:
  - [ ] README updated
  - [ ] Deployment guide complete
  - [ ] Architecture diagram current

**Acceptance Criteria**:
- ✅ All checklist items complete
- ✅ No blockers for production

---

#### Task 6.2: Production Deployment
- [ ] Deploy to Oracle Cloud:
  ```bash
  docker-compose down
  docker-compose pull
  docker-compose up -d
  ```
- [ ] Verify all containers running: `docker ps`
- [ ] Check logs: `docker logs -f strategy-lab-bot`
- [ ] Access Grafana dashboard
- [ ] Wait for market open
- [ ] Verify bot starts trading
- [ ] Monitor first few trades closely
- [ ] Check Discord alerts arriving
- [ ] Verify trades recorded in SQLite
- [ ] Verify logs in Loki

**Acceptance Criteria**:
- ✅ Bot trading successfully
- ✅ All monitoring systems operational
- ✅ No errors in logs

---

#### Task 6.3: Post-Deployment Monitoring
- [ ] Monitor for first week:
  - [ ] Check dashboard daily
  - [ ] Review Discord alerts
  - [ ] Verify backups running
  - [ ] Check resource usage
- [ ] Fine-tune alert thresholds based on actual performance
- [ ] Optimize queries if needed
- [ ] Collect feedback and iterate

**Acceptance Criteria**:
- ✅ System stable for 1 week
- ✅ No critical issues
- ✅ Alerts actionable (not too noisy)

---

## 📦 Dependencies to Install

```bash
# SQLite (built into Python - no installation needed!)

# Loki logging
pip install python-logging-loki

# Oracle Cloud SDK (for backups)
pip install oci

# Discord alerts (uses requests - already installed)
# No additional package needed!

# PyYAML for config (may already be installed)
pip install pyyaml
```

Add rc/cloud/__init__.py` - Cloud module package
- `src/cloud/storage_provider.py` - Abstract base class for cloud storage
- `src/cloud/storage_factory.py` - Factory to create storage providers
- `src/cloud/database_sync.py` - Background database sync daemon
- `src/cloud/providers/__init__.py` - Providers package
- `src/cloud/providers/oracle_storage.py` - Oracle Cloud Storage implementation
- `src/cloud/providers/aws_storage.py` - AWS S3 implementation
- `src/cloud/providers/gcp_storage.py` - Google Cloud Storage implementation
- `src/cloud/providers/azure_storage.py` - Azure Blob Storage implementation
- `src/cloud/providers/local_storage.py` - Local filesystem (testing)
- `scripts/backup_trades_to_cloud.py` - Automated backup script
- `config/alerts.yaml` - Alert configuration
- `python/loki-config.yaml` - Loki configuration
- `python/grafana/provisioning/datasources/loki.yaml`
- `python/grafana/provisioning/datasources/sqlite.yaml`
- `python/grafana/provisioning/dashboards/dashboard.yaml`
- `python/grafana/provisioning/dashboards/trading-bot.json`
- `tests/data/test_trade_store.py`
- `tests/utils/test_alerting.py`
- `tests/cloud/test_storage_providers.py`
- `docs/cloud-monitoring/DEPLOYMENT_GUIDE.md`
- `docs/cloud-monitoring/TROUBLESHOOTING.md`
- `docs/cloud-monitoring/BACKUP_RESTORE.md`
- `docs/cloud-monitoring/CLOUD_PROVIDER_MIGRATION.md` (how to switch clouds)
- `src/data/trade_store.py` - SQLite trade database module
- `src/utils/alerting.py` - Discord webhook alerting module
- `scripts/backup_trades_to_cloud.py` - Automated backup script
- `config/alerts.yaml` - Alert configuration
- `python/loki-config.yaml` - Loki configuration
- `python/grafana/provisioning/datasources/loki.yaml`
- `python/grafana/provisioning/datasources/sqlite.yaml`
- `python/grafana/provisioning/dashboards/dashboard.yaml`
- `python/grafana/provisioning/dashboards/trading-bot.json`
- `tests/data/test_trade_store.py`
- `tests/utils/test_alerting.py`
- `docs/cloud-monitoring/DEPLOYMENT_GUIDE.md`
- `docs/cloud-monitoring/TROUBLESHOOTING.md`
- `docs/cloud-monitoring/BACKUP_RESTORE.md`

### Modified Files
- `python/docker-compose.yml` - Add Grafana and Loki services
- `python/.env` - Add Discord webhook, Grafana password, OCI config
- `python/requirements.txt` - Add new dependencies
- `orchestrator_main.py` - Integrate alerts and trade recording
- `src/utils/logger.py` - Add Loki handler
- Trading scripts - Add trade recording calls

---
Cloud Storage**: Abstract provider interface (100% cloud-agnostic!)
  - Switch clouds by changing ONE env variable: `CLOUD_STORAGE_PROVIDER`
  - No code changes needed to migrate Oracle → AWS → GCP → Azure
  - Each provider implements same interface: `upload_file()`, `download_file()`, etc.
  - Example migration: Change `.env` from `CLOUD_STORAGE_PROVIDER=oracle` to `CLOUD_STORAGE_PROVIDER=aws`

- **Database Sync**: Independent of cloud provider
  - Uses `CloudStorageProvider` interface (works with any cloud)
  - Configure sync interval via env var (default: 5 minutes)
  - Can disable sync completely if needed
  - Grafana always reads local SQLite (no cloud dependency for real-time data)

- **Logging**: Currently Loki, can swap to ELK or CloudWatch
  - Just change logger handler in `logger.py`
  - Log format stays the same

- **Dashboard**: Currently Grafana, can swap to Streamlit/custom
  - Data comes from LOCAL SQLite and Loki (real-time, no lag)
  - Cloud sync is for backup/disaster recovery only
- ✅ **Real-time visibility** into trades via Grafana dashboard
- ✅ **Searchable logs** for debugging (30 days retention)
- ✅ **Instant alerts** on Discord for critical events
- ✅ **Complete trade history** in SQLite database
- ✅ **Automated daily backups** to Oracle Cloud Storage
- ✅ **Mobile access** to dashboard from anywhere
- ✅ **< 5 minute** recovery time if instance fails
- ✅ **99%+ uptime** with auto-restart
- [ ] **Cloud-native database option**: Read trades from cloud storage directly
  - Alternative to local SQLite + sync
  - Grafana connects to cloud storage, downloads latest DB
  - Useful for multi-instance deployments or remote analysis
  - Trade-off: 5-15 minute lag vs. real-time local access

### Cloud Provider Migration Guide

**Switching from Oracle Cloud to AWS** (example):

1. Change `.env`:
   ```bash
   # Old
   CLOUD_STORAGE_PROVIDER=oracle
   OCI_BUCKET_NAME=strategy-lab-backups

   # New
   CLOUD_STORAGE_PROVIDER=aws
   AWS_S3_BUCKET=strategy-lab-backups
   AWS_REGION=us-east-1
   ```

2. Install AWS SDK:
   ```bash
   pip install boto3
   ```

3. Configure AWS credentials (one of):
   - Environment variables: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`
   - AWS CLI config: `~/.aws/credentials`
   - EC2 instance role (if running on AWS)

4. Restart bot - that's it! No code changes needed.

**Same process for GCP or Azure** - just change the env var and credentials!

---

## 📝 Notes

### Modularity & Swappability
Each component is designed to be modular:

- **Database**: Currently SQLite, can swap to PostgreSQL/TimescaleDB
  - Interface: `TradeStore` class with standard methods
  - Just implement new class with same interface

- **Logging**: Currently Loki, can swap to ELK or CloudWatch
  - Just change logger handler in `logger.py`
  - Log format stays the same

- **Dashboard**: Currently Grafana, can swap to Streamlit/custom
  - Data comes from SQLite and Loki (standard interfaces)
  - Easy to build alternative dashboard

- **Alerts**: Currently Discord, can add Slack/email/SMS
  - `AlertManager` designed to support multiple channels
  - Just add new `_send_X()` methods

### Best Practices
- **Always test in paper trading first** before going live
- **Monitor resource usage** regularly (RAM, CPU, disk)
- **Keep local backups** in addition to cloud backups
- **Review logs weekly** to catch issues early
- **Document everything** you learn along the way

### Future Enhancements
- [ ] Add Prometheus metrics for performance monitoring
- [ ] Build custom analytics dashboard (Streamlit)
- [ ] Add email digest (weekly summary)
- [ ] Integrate with Telegram bot for commands
- [ ] Add machine learning for anomaly detection
- [ ] Multi-strategy comparison dashboard
- [ ] Real-time P&L charting with live updates

---

**Last Updated**: January 11, 2026
**Maintained by**: Strategy Lab Team
**Questions?**: See [CLOUD_MONITORING_ARCHITECTURE.md](../CLOUD_MONITORING_ARCHITECTURE.md) for detailed design
