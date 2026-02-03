# Configuration and Environment Management System

## Overview

This document describes the configuration architecture that enables seamless deployment across local, development, and production environments without code changes.

## Design Principles

1. **12-Factor App**: Configuration via environment variables
2. **Environment Isolation**: Separate configs for local, dev, prod
3. **Secrets Management**: Never commit secrets to git
4. **Feature Flags**: Toggle functionality without redeployment
5. **Validation**: Type-safe configuration with Pydantic
6. **Defaults**: Sensible defaults for all settings

---

## Configuration Layers

```
┌─────────────────────────────────────────────────────────────┐
│                   Configuration Stack                        │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  1. Hardcoded Defaults (in code)                             │
│     ↓                                                        │
│  2. YAML Config File (config/{env}.yaml)                     │
│     ↓                                                        │
│  3. Environment Variables (.env file)                        │
│     ↓                                                        │
│  4. CLI Arguments (--config, --env)                          │
│                                                              │
│  Priority: CLI > Env Vars > YAML > Defaults                  │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

---

## Environment Detection

### Automatic Environment Detection

```python
# vibe/common/config/environment.py
from enum import Enum
import os
import socket

class Environment(str, Enum):
    """Deployment environment."""
    LOCAL = "local"           # Developer machine
    DEV = "dev"               # Oracle Cloud with SSH tunnel
    PROD = "prod"             # Oracle Cloud with Streamlit Cloud
    TEST = "test"             # CI/CD testing

def detect_environment() -> Environment:
    """
    Automatically detect current environment.

    Priority:
    1. TRADING_ENV environment variable
    2. Heuristics (hostname, CI variables)
    3. Default to LOCAL
    """
    # 1. Explicit environment variable
    env_var = os.getenv("TRADING_ENV", "").lower()
    if env_var in Environment.__members__.values():
        return Environment(env_var)

    # 2. CI/CD detection
    if os.getenv("CI") or os.getenv("GITHUB_ACTIONS"):
        return Environment.TEST

    # 3. Cloud detection (Oracle Cloud hostname pattern)
    hostname = socket.gethostname()
    if "oraclecloud" in hostname or "compute" in hostname:
        # Check if Streamlit Cloud is configured (means PROD)
        if os.getenv("STREAMLIT_CLOUD_API_URL"):
            return Environment.PROD
        return Environment.DEV

    # 4. Default to local development
    return Environment.LOCAL

def get_config_path(env: Environment) -> str:
    """Get config file path for environment."""
    base_dir = os.path.dirname(os.path.dirname(__file__))
    return os.path.join(base_dir, "config", f"{env.value}.yaml")
```

---

## Configuration Schema

### Main Configuration Class

```python
# vibe/common/config/settings.py
from pydantic import BaseSettings, Field, validator, SecretStr
from typing import Optional, List, Dict
from pathlib import Path
import yaml

class APIConfig(BaseSettings):
    """API server configuration."""
    host: str = "0.0.0.0"
    port: int = 8080
    base_url: str = Field(
        default="http://localhost:8080",
        description="External URL for API (used by dashboard)"
    )
    enable_auth: bool = Field(
        default=False,
        description="Enable API key authentication"
    )
    api_key: Optional[SecretStr] = Field(
        default=None,
        description="API key for authentication"
    )
    cors_origins: List[str] = Field(
        default=["*"],
        description="CORS allowed origins"
    )

    class Config:
        env_prefix = "API_"


class DashboardConfig(BaseSettings):
    """Dashboard configuration."""
    enabled: bool = Field(
        default=True,
        description="Enable dashboard API endpoints"
    )
    streamlit_mode: str = Field(
        default="local",
        description="local, remote, or disabled"
    )
    auto_refresh_seconds: int = Field(
        default=5,
        description="Dashboard auto-refresh interval"
    )

    class Config:
        env_prefix = "DASHBOARD_"


class DataProviderConfig(BaseSettings):
    """Data provider configuration."""
    historical_provider: str = Field(
        default="yahoo",
        description="yahoo, polygon, alpaca"
    )
    realtime_provider: str = Field(
        default="finnhub",
        description="finnhub, alpaca, polygon"
    )
    finnhub_api_key: Optional[SecretStr] = None
    polygon_api_key: Optional[SecretStr] = None
    alpaca_api_key: Optional[SecretStr] = None
    alpaca_secret_key: Optional[SecretStr] = None

    class Config:
        env_prefix = "DATA_"


class ExchangeConfig(BaseSettings):
    """Exchange/broker configuration."""
    exchange_type: str = Field(
        default="mock",
        description="mock, alpaca, ibkr, ccxt"
    )
    initial_capital: float = Field(
        default=10000.0,
        description="Initial capital in USD"
    )
    paper_trading: bool = Field(
        default=True,
        description="Use paper trading mode"
    )

    class Config:
        env_prefix = "EXCHANGE_"


class NotificationConfig(BaseSettings):
    """Notification service configuration."""
    discord_enabled: bool = Field(
        default=False,
        description="Enable Discord notifications"
    )
    discord_webhook_url: Optional[SecretStr] = None
    notify_on_order_sent: bool = True
    notify_on_order_filled: bool = True
    notify_on_order_cancelled: bool = True
    notify_on_error: bool = True

    class Config:
        env_prefix = "NOTIFICATION_"


class StorageConfig(BaseSettings):
    """Storage configuration."""
    database_path: Path = Field(
        default=Path("./data/trading.db"),
        description="SQLite database path"
    )
    cloud_sync_enabled: bool = Field(
        default=False,
        description="Enable cloud backup sync"
    )
    cloud_provider: str = Field(
        default="local",
        description="local, oracle, aws, azure, gcp"
    )
    cloud_sync_interval_minutes: int = Field(
        default=5,
        description="Cloud sync interval"
    )

    # Cloud provider credentials (loaded from env)
    oracle_bucket_name: Optional[str] = None
    oracle_namespace: Optional[str] = None
    aws_bucket_name: Optional[str] = None
    aws_region: Optional[str] = None

    class Config:
        env_prefix = "STORAGE_"


class FeatureFlags(BaseSettings):
    """Feature flags for toggling functionality."""
    enable_dashboard_api: bool = True
    enable_health_checks: bool = True
    enable_cloud_sync: bool = True
    enable_notifications: bool = True
    enable_mtf_validation: bool = True
    enable_risk_management: bool = True

    # Trading controls
    allow_short_selling: bool = False
    allow_options_trading: bool = False
    allow_margin_trading: bool = False

    class Config:
        env_prefix = "FEATURE_"


class TradingBotConfig(BaseSettings):
    """Main trading bot configuration."""

    # Environment
    environment: Environment = Field(
        default_factory=detect_environment,
        description="Deployment environment"
    )

    # Sub-configurations
    api: APIConfig = Field(default_factory=APIConfig)
    dashboard: DashboardConfig = Field(default_factory=DashboardConfig)
    data: DataProviderConfig = Field(default_factory=DataProviderConfig)
    exchange: ExchangeConfig = Field(default_factory=ExchangeConfig)
    notifications: NotificationConfig = Field(default_factory=NotificationConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    features: FeatureFlags = Field(default_factory=FeatureFlags)

    # Strategy configuration
    symbols: List[str] = Field(
        default=["AAPL", "MSFT", "AMZN", "TSLA", "GOOGL"],
        description="Trading symbols"
    )
    strategy_name: str = Field(
        default="opening_range_breakout",
        description="Strategy to run"
    )

    # Logging
    log_level: str = Field(
        default="INFO",
        description="Logging level"
    )
    log_file: Optional[Path] = Field(
        default=Path("./logs/trading-bot.log"),
        description="Log file path"
    )

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        env_nested_delimiter = "__"  # API__HOST -> api.host

    @classmethod
    def from_yaml(cls, yaml_path: str) -> "TradingBotConfig":
        """Load configuration from YAML file."""
        with open(yaml_path, 'r') as f:
            yaml_data = yaml.safe_load(f)
        return cls(**yaml_data)

    @classmethod
    def load(cls, env: Optional[Environment] = None) -> "TradingBotConfig":
        """
        Load configuration for environment.

        1. Auto-detect environment if not specified
        2. Load YAML config file
        3. Override with environment variables
        4. Override with .env file
        """
        if env is None:
            env = detect_environment()

        config_path = get_config_path(env)

        if os.path.exists(config_path):
            return cls.from_yaml(config_path)
        else:
            print(f"Warning: Config file {config_path} not found, using defaults")
            return cls(environment=env)
```

---

## Environment-Specific Configuration Files

### Local Development (config/local.yaml)

```yaml
# config/local.yaml
# For local development on your laptop

environment: local

api:
  host: "127.0.0.1"
  port: 8080
  base_url: "http://localhost:8080"
  enable_auth: false  # No auth needed locally

dashboard:
  enabled: true
  streamlit_mode: "local"  # Run Streamlit locally
  auto_refresh_seconds: 5

data:
  historical_provider: "yahoo"  # Free
  realtime_provider: "finnhub"
  # finnhub_api_key loaded from .env

exchange:
  exchange_type: "mock"
  initial_capital: 10000.0
  paper_trading: true

notifications:
  discord_enabled: false  # Don't spam during development

storage:
  database_path: "./data/local/trading.db"
  cloud_sync_enabled: false  # No cloud sync locally
  cloud_provider: "local"

features:
  enable_dashboard_api: true
  enable_health_checks: true
  enable_cloud_sync: false
  enable_notifications: false  # Quiet during dev
  enable_mtf_validation: true
  enable_risk_management: true

symbols:
  - "AAPL"
  - "MSFT"

log_level: "DEBUG"
log_file: "./logs/local.log"
```

### Development on Oracle Cloud with SSH Tunnel (config/dev.yaml)

```yaml
# config/dev.yaml
# For Oracle Cloud deployment with SSH tunnel access

environment: dev

api:
  host: "0.0.0.0"
  port: 8080
  base_url: "http://localhost:8080"  # Accessed via SSH tunnel
  enable_auth: false  # SSH provides security

dashboard:
  enabled: true
  streamlit_mode: "local"  # Streamlit runs on Oracle, accessed via tunnel
  auto_refresh_seconds: 5

data:
  historical_provider: "yahoo"
  realtime_provider: "finnhub"
  # API keys loaded from .env

exchange:
  exchange_type: "mock"
  initial_capital: 10000.0
  paper_trading: true

notifications:
  discord_enabled: true  # Enable for real trading simulation
  notify_on_order_sent: true
  notify_on_order_filled: true
  notify_on_order_cancelled: true

storage:
  database_path: "/app/data/trading.db"
  cloud_sync_enabled: true  # Backup to Oracle Object Storage
  cloud_provider: "oracle"
  cloud_sync_interval_minutes: 5
  # oracle_bucket_name loaded from .env

features:
  enable_dashboard_api: true
  enable_health_checks: true
  enable_cloud_sync: true
  enable_notifications: true
  enable_mtf_validation: true
  enable_risk_management: true

symbols:
  - "AAPL"
  - "MSFT"
  - "AMZN"
  - "TSLA"
  - "GOOGL"

log_level: "INFO"
log_file: "/app/logs/trading-bot.log"
```

### Production on Oracle Cloud with Streamlit Cloud (config/prod.yaml)

```yaml
# config/prod.yaml
# For production deployment with Streamlit Cloud dashboard

environment: prod

api:
  host: "0.0.0.0"
  port: 8080
  base_url: "https://your-oracle-ip-or-domain.com:8080"  # Public URL
  enable_auth: true  # API key required
  # api_key loaded from .env
  cors_origins:
    - "https://your-trading-dashboard.streamlit.app"

dashboard:
  enabled: true
  streamlit_mode: "remote"  # Streamlit hosted on Streamlit Cloud
  auto_refresh_seconds: 10  # Slower refresh to reduce API calls

data:
  historical_provider: "yahoo"
  realtime_provider: "finnhub"

exchange:
  exchange_type: "mock"  # Still mock for MVP
  initial_capital: 10000.0
  paper_trading: true

notifications:
  discord_enabled: true
  notify_on_order_sent: true
  notify_on_order_filled: true
  notify_on_order_cancelled: true
  notify_on_error: true  # Important for production

storage:
  database_path: "/app/data/trading.db"
  cloud_sync_enabled: true
  cloud_provider: "oracle"
  cloud_sync_interval_minutes: 5

features:
  enable_dashboard_api: true
  enable_health_checks: true
  enable_cloud_sync: true
  enable_notifications: true
  enable_mtf_validation: true
  enable_risk_management: true

symbols:
  - "AAPL"
  - "MSFT"
  - "AMZN"
  - "TSLA"
  - "GOOGL"

log_level: "INFO"
log_file: "/app/logs/trading-bot.log"
```

---

## Environment Variable Files

### .env.example (Template - Commit to Git)

```bash
# .env.example
# Copy to .env and fill in your secrets
# DO NOT commit .env to git!

# ============ Environment ============
TRADING_ENV=local  # local, dev, prod

# ============ API Keys ============
DATA__FINNHUB_API_KEY=your_finnhub_key_here
DATA__POLYGON_API_KEY=your_polygon_key_here
DATA__ALPACA_API_KEY=your_alpaca_key_here
DATA__ALPACA_SECRET_KEY=your_alpaca_secret_here

# ============ API Authentication ============
API__API_KEY=your_secure_random_uuid_here

# ============ Notifications ============
NOTIFICATION__DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...

# ============ Cloud Storage ============
STORAGE__ORACLE_BUCKET_NAME=your-bucket-name
STORAGE__ORACLE_NAMESPACE=your-namespace
STORAGE__AWS_BUCKET_NAME=your-s3-bucket
STORAGE__AWS_REGION=us-east-1

# ============ Oracle Cloud Credentials ============
OCI_CONFIG_FILE=/app/config/oci_config
OCI_CONFIG_PROFILE=DEFAULT

# ============ Feature Flags (Optional Overrides) ============
# FEATURE__ENABLE_NOTIFICATIONS=true
# FEATURE__ENABLE_CLOUD_SYNC=true
```

### .env.local (Local Development)

```bash
# .env.local
TRADING_ENV=local

DATA__FINNHUB_API_KEY=your_finnhub_key

# Disable notifications locally
NOTIFICATION__DISCORD_ENABLED=false

# Local storage only
STORAGE__CLOUD_SYNC_ENABLED=false

LOG_LEVEL=DEBUG
```

### .env.prod (Production - Store Securely)

```bash
# .env.prod
# Store this in Oracle Cloud Secrets or GitHub Secrets
# Load into environment at runtime

TRADING_ENV=prod

# API
API__BASE_URL=https://123.456.789.0:8080
API__ENABLE_AUTH=true
API__API_KEY=550e8400-e29b-41d4-a716-446655440000

# Data providers
DATA__FINNHUB_API_KEY=your_production_key

# Notifications
NOTIFICATION__DISCORD_ENABLED=true
NOTIFICATION__DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...

# Cloud storage
STORAGE__CLOUD_SYNC_ENABLED=true
STORAGE__CLOUD_PROVIDER=oracle
STORAGE__ORACLE_BUCKET_NAME=trading-bot-backups
STORAGE__ORACLE_NAMESPACE=your-namespace

# Production features
FEATURE__ENABLE_NOTIFICATIONS=true
FEATURE__ENABLE_CLOUD_SYNC=true

LOG_LEVEL=INFO
```

---

## Usage in Code

### Loading Configuration

```python
# vibe/trading-bot/main.py
from vibe.common.config.settings import TradingBotConfig
from vibe.common.config.environment import Environment

def main():
    # Method 1: Auto-detect environment
    config = TradingBotConfig.load()

    # Method 2: Specify environment
    config = TradingBotConfig.load(env=Environment.PROD)

    # Method 3: Override with CLI argument
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=["local", "dev", "prod"], default=None)
    args = parser.parse_args()

    if args.env:
        config = TradingBotConfig.load(env=Environment(args.env))
    else:
        config = TradingBotConfig.load()

    print(f"Running in {config.environment} environment")
    print(f"API URL: {config.api.base_url}")
    print(f"Dashboard mode: {config.dashboard.streamlit_mode}")

    # Initialize components based on config
    service = TradingService(config)
    service.run()
```

### Accessing Configuration

```python
# vibe/trading-bot/api/dashboard.py
from vibe.common.config.settings import TradingBotConfig

config = TradingBotConfig.load()

# Use configuration
if config.api.enable_auth:
    @app.get("/api/trades", dependencies=[Depends(verify_api_key)])
    async def get_trades():
        return trade_store.get_trades()
else:
    @app.get("/api/trades")
    async def get_trades():
        return trade_store.get_trades()
```

### Feature Flags

```python
# vibe/trading-bot/infrastructure/notifications/discord.py
from vibe.common.config.settings import TradingBotConfig

config = TradingBotConfig.load()

async def send_order_notification(order: Order):
    if not config.features.enable_notifications:
        logger.debug("Notifications disabled, skipping")
        return

    if not config.notifications.discord_enabled:
        logger.debug("Discord disabled, skipping")
        return

    # Send notification
    await discord_client.send(order)
```

---

## Dashboard Configuration

### Streamlit Secrets (Streamlit Cloud)

```toml
# .streamlit/secrets.toml (for Streamlit Cloud deployment)
# Set these in Streamlit Cloud dashboard: Settings > Secrets

environment = "prod"

[api]
base_url = "https://123.456.789.0:8080"
api_key = "550e8400-e29b-41d4-a716-446655440000"

[dashboard]
auto_refresh_seconds = 10
```

### Streamlit App Configuration

```python
# vibe/dashboard/app.py
import streamlit as st
import os

# Load API URL from environment
if "api" in st.secrets:
    # Running on Streamlit Cloud
    API_BASE = st.secrets["api"]["base_url"]
    API_KEY = st.secrets["api"]["api_key"]
    ENVIRONMENT = "prod"
else:
    # Running locally or via SSH tunnel
    API_BASE = os.getenv("API_BASE_URL", "http://localhost:8080")
    API_KEY = os.getenv("API_KEY", None)
    ENVIRONMENT = "local"

st.sidebar.markdown(f"**Environment:** {ENVIRONMENT}")

# Make API calls with auth if needed
headers = {"X-API-Key": API_KEY} if API_KEY else {}
response = requests.get(f"{API_BASE}/api/trades", headers=headers)
```

---

## Deployment Workflows

### Workflow 1: Local Development

```bash
# 1. Copy environment template
cp .env.example .env.local
vim .env.local  # Fill in API keys

# 2. Run locally
TRADING_ENV=local python -m vibe.trading_bot.main

# 3. Run Streamlit dashboard locally
cd vibe/dashboard
streamlit run app.py
```

### Workflow 2: Oracle Cloud with SSH Tunnel (Dev)

```bash
# 1. On your laptop - prepare configs
cp .env.example .env.dev
vim .env.dev  # Fill in API keys

# 2. Deploy to Oracle Cloud
scp .env.dev ubuntu@oracle-ip:/app/.env
scp config/dev.yaml ubuntu@oracle-ip:/app/config/

# 3. SSH into Oracle Cloud
ssh ubuntu@oracle-ip

# 4. Start trading bot
cd /app
docker-compose up -d

# 5. Exit SSH, create tunnel from laptop
ssh -L 8501:localhost:8501 -L 8080:localhost:8080 ubuntu@oracle-ip

# 6. Access dashboard
# Browser: http://localhost:8501
```

### Workflow 3: Oracle Cloud with Streamlit Cloud (Prod)

```bash
# 1. Deploy bot to Oracle Cloud
scp .env.prod ubuntu@oracle-ip:/app/.env
scp config/prod.yaml ubuntu@oracle-ip:/app/config/

ssh ubuntu@oracle-ip
cd /app
docker-compose -f docker-compose.prod.yaml up -d

# 2. Deploy dashboard to Streamlit Cloud
# - Push code to GitHub
git add vibe/dashboard/
git commit -m "Deploy dashboard"
git push origin main

# - Go to https://streamlit.io/cloud
# - Deploy vibe/dashboard/app.py
# - Set secrets (API URL, API key)

# 3. Access dashboard
# Browser: https://your-app.streamlit.app
```

---

## Cloud Provider Configuration

### Design Philosophy: Cloud-Agnostic Deployment

**Key Principle:** Cloud provider is infrastructure-specific and should be an **environment variable**, not hardcoded in YAML config files.

**Benefits:**
- ✅ Same YAML configs work across all clouds
- ✅ Switch providers by changing one env var
- ✅ No code changes when moving between clouds
- ✅ Credentials already need to be env vars anyway

### Configuration Approach

**YAML files (config/*.yaml):**
```yaml
# NO cloud_provider in YAML!
storage:
  database_path: "/app/data/trading.db"
  cloud_sync_enabled: true
  cloud_sync_interval_minutes: 5
  # cloud_provider comes from STORAGE__CLOUD_PROVIDER env var
```

**Environment variables (.env files):**
```bash
# Set provider based on deployment
STORAGE__CLOUD_PROVIDER=oracle  # or aws, azure, gcp, local
```

### Cloud Provider Examples

#### Example 1: Deploy to Oracle Cloud

```bash
# .env.prod (Oracle deployment)
TRADING_ENV=prod
STORAGE__CLOUD_SYNC_ENABLED=true
STORAGE__CLOUD_PROVIDER=oracle

# Oracle-specific credentials
STORAGE__ORACLE_BUCKET_NAME=trading-bot-backups
STORAGE__ORACLE_NAMESPACE=axnz...
OCI_CONFIG_FILE=/app/config/oci_config
OCI_CONFIG_PROFILE=DEFAULT
```

**Deploy:**
```bash
scp .env.prod oracle-vm:/app/.env
scp config/prod.yaml oracle-vm:/app/config/
ssh oracle-vm "docker-compose up -d"
```

#### Example 2: Switch to AWS (No Code Changes!)

```bash
# .env.prod (AWS deployment)
TRADING_ENV=prod
STORAGE__CLOUD_SYNC_ENABLED=true
STORAGE__CLOUD_PROVIDER=aws  # <-- Just change this!

# AWS-specific credentials
STORAGE__AWS_BUCKET_NAME=trading-bot-backups
STORAGE__AWS_REGION=us-east-1
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=...
```

**Deploy:**
```bash
scp .env.prod aws-ec2:/app/.env
scp config/prod.yaml aws-ec2:/app/config/  # Same YAML file!
ssh aws-ec2 "docker-compose up -d"
```

#### Example 3: Switch to Azure (Still No Code Changes!)

```bash
# .env.prod (Azure deployment)
TRADING_ENV=prod
STORAGE__CLOUD_SYNC_ENABLED=true
STORAGE__CLOUD_PROVIDER=azure  # <-- Just change this!

# Azure-specific credentials
STORAGE__AZURE_CONTAINER_NAME=trading-bot-backups
STORAGE__AZURE_STORAGE_ACCOUNT_NAME=mystorageaccount
AZURE_STORAGE_CONNECTION_STRING=DefaultEndpointsProtocol=https;...
```

**Deploy:**
```bash
scp .env.prod azure-vm:/app/.env
scp config/prod.yaml azure-vm:/app/config/  # Same YAML file!
ssh azure-vm "docker-compose up -d"
```

#### Example 4: Switch to GCP (Same Pattern!)

```bash
# .env.prod (GCP deployment)
TRADING_ENV=prod
STORAGE__CLOUD_SYNC_ENABLED=true
STORAGE__CLOUD_PROVIDER=gcp  # <-- Just change this!

# GCP-specific credentials
STORAGE__GCP_BUCKET_NAME=trading-bot-backups
STORAGE__GCP_PROJECT_ID=my-project-123
GOOGLE_APPLICATION_CREDENTIALS=/app/config/gcp-service-account.json
```

**Deploy:**
```bash
scp .env.prod gcp-vm:/app/.env
scp config/prod.yaml gcp-vm:/app/config/  # Same YAML file!
scp gcp-service-account.json gcp-vm:/app/config/
ssh gcp-vm "docker-compose up -d"
```

### Cloud Provider Factory Pattern

The bot uses a factory pattern to instantiate the correct cloud storage provider:

```python
# vibe/trading-bot/storage/cloud/factory.py
def create_cloud_storage_provider(config: StorageConfig) -> CloudStorageProvider:
    """
    Factory creates correct provider based on config.cloud_provider.

    cloud_provider comes from STORAGE__CLOUD_PROVIDER env var.
    """
    provider_map = {
        "local": LocalStorageProvider,
        "oracle": OracleObjectStorageProvider,
        "aws": S3StorageProvider,
        "azure": AzureBlobStorageProvider,
        "gcp": GCSStorageProvider,
    }

    provider_class = provider_map.get(config.cloud_provider)
    if not provider_class:
        raise ValueError(f"Unknown cloud provider: {config.cloud_provider}")

    return provider_class(config)
```

### Multi-Cloud Deployment Strategy

You can even deploy to multiple clouds simultaneously for redundancy:

```bash
# .env.prod
STORAGE__CLOUD_PROVIDER=oracle  # Primary

# Optional: Enable multi-cloud backup
STORAGE__BACKUP_PROVIDERS=aws,azure  # Secondary backups
```

### Migration Between Clouds

**Scenario:** Move from Oracle Cloud to AWS

1. **Deploy bot on AWS with same configs:**
```bash
# On AWS
STORAGE__CLOUD_PROVIDER=aws  # Changed from oracle
# All other configs stay the same!
```

2. **Sync data from Oracle to AWS:**
```bash
# One-time data migration
aws s3 sync oci://your-oracle-bucket s3://your-aws-bucket
```

3. **No code changes required!** 🎉

---

## Summary

This configuration system enables:

✅ **No code changes** between environments
✅ **Automatic environment detection**
✅ **Secure secrets management** (never commit .env)
✅ **Feature flags** for toggling functionality
✅ **Type-safe configuration** with Pydantic
✅ **Override priority**: CLI > Env Vars > YAML > Defaults

**Three deployment modes work seamlessly:**
- **Local**: `TRADING_ENV=local python main.py`
- **Dev (SSH)**: `TRADING_ENV=dev docker-compose up`
- **Prod (Streamlit Cloud)**: `TRADING_ENV=prod docker-compose up`
