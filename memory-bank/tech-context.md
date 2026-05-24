# Tech Context

## Language & Runtime
- **Python**: 3.12+ (required for modern `asyncio` and type hints)
- **Virtual Environment**: `venv` or `conda` recommended
- **Package Manager**: `pip` (requirements in `pyproject.toml` or `requirements.txt`)

## Key Dependencies

### Core Libraries
- `asyncio` - Asynchronous event loops for real-time data
- `aiohttp` - Async HTTP client for API calls
- `websockets` - WebSocket connections to data providers
- `pydantic` - Data validation and settings management
- `pyyaml` - YAML configuration parsing
- `structlog` - Structured logging (JSON format)

### Data & Analytics
- `pandas` - Data manipulation and analysis
- `numpy` - Numerical operations
- `yfinance` - Yahoo Finance historical data
- `polygon-api-client` - Polygon.io client library
- `finnhub-python` - Finnhub client library

### Backtesting
- `pandas` - Timeseries operations
- `numpy` - Performance calculations
- Custom backtester in `vibe/backtester/`

### Testing
- `pytest` - Unit and integration testing framework
- `pytest-asyncio` - Async test support
- `pytest-mock` - Mocking utilities

### Development Tools
- `black` - Code formatting
- `mypy` - Static type checking
- `ruff` - Fast linting

## Local Development Setup

### Initial Setup
```powershell
# Clone repository
cd d:\development\strategy-lab

# Create virtual environment
python -m venv venv

# Activate (Windows)
.\venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt  # or pip install -e .
```

### Environment Variables
Create `.env` file (not committed to git):
```bash
# Environment selection
ENV=local  # Options: local, dev, prod

# API Keys
POLYGON_API_KEY=your_polygon_key_here
FINNHUB_API_KEY=your_finnhub_key_here

# Discord Webhook
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...

# Optional overrides
LOG_LEVEL=INFO  # DEBUG for verbose output
```

### Configuration Files
- `config/local.yaml` - Local development settings (fast iterations)
- `config/dev.yaml` - Development environment (cloud testing)
- `config/prod.yaml` - Production settings (live trading)

**Selected via**: `ENV` environment variable

### Common Commands

#### Run Backtest
```powershell
# Single backtest
python scripts/run_backtest.py --config config/local.yaml --start 2018-01-01 --end 2024-12-31

# Parameter sensitivity sweep
python scripts/run_parameter_sensitivity.py --param stop_loss_atr --min 1.0 --max 3.0 --step 0.5
```

#### Regime Analysis
```powershell
# Analyze regime performance
python scripts/analyze_regimes.py \
    --trades reports/backtest_results.csv \
    --output reports/regime-filter/analysis.md

# Full validation (Phases 1-6)
python scripts/analyze_regimes.py --trades reports/trades_2018_2024.csv --validate-full
```

#### Trading Bot
```powershell
# Paper trading mode
python -m vibe.trading_bot.main --config config/local.yaml --mode paper

# Live trading (production)
python -m vibe.trading_bot.main --config config/prod.yaml --mode live
```

#### Testing
```powershell
# Run all tests
pytest

# Run specific test file
pytest tests/unit/test_orb_strategy.py

# Run integration tests (slower)
pytest tests/integration/

# Run with coverage
pytest --cov=vibe --cov-report=html
```

### Development Workflow

#### 1. Feature Development
```powershell
# Create feature branch
git checkout -b feature/my-feature

# Make changes, run tests locally
pytest tests/unit/

# Format and lint
black vibe/
ruff check vibe/

# Commit and push
git commit -m "Add my feature"
git push origin feature/my-feature
```

#### 2. Backtest Validation
```powershell
# Run backtest with new strategy changes
python scripts/run_backtest.py --config config/local.yaml --start 2023-01-01 --end 2025-12-31

# Check results
cat reports/backtest_results.html  # Or open in browser
```

#### 3. Regime Analysis
```powershell
# Analyze regime-specific performance
python scripts/analyze_regimes.py --trades reports/trades.csv --output reports/regime-filter/

# Validate hypothesis (e.g., H3: atr_pctile < 0.80)
python scripts/analyze_regimes.py --trades reports/trades.csv --filter "atr_pctile < 0.80" --validate-full
```

#### 4. Paper Trading Test
```powershell
# Test with local config (shorter warmup, verbose logging)
python -m vibe.trading_bot.main --config config/local.yaml --mode paper

# Monitor Discord for notifications
# Check logs: logs/trading_bot_YYYY-MM-DD.log
```

## Build & Deployment

### Version Management
- **Version File**: `vibe/trading_bot/version.py`
- **Format**: `v{major}.{minor}.{patch}` (e.g., `v1.1.0`)
- **Bump**: Update version before release, include in Discord footer

### Cloud Deployment (Future)

**Current Status**: Local development, cloud deployment planned

**Design Principle**: Cloud-agnostic implementation for portability

#### Cloud-Agnostic Guidelines

**ALWAYS use environment variables** for cloud-specific configuration:
```python
# ✅ DO: Cloud-agnostic
bucket_name = os.getenv("STORAGE_BUCKET_NAME")
cloud_region = os.getenv("CLOUD_REGION", "us-east-1")
storage_type = os.getenv("STORAGE_TYPE", "local")  # local, s3, gcs, oci

# ❌ DON'T: Provider-specific
bucket_name = "oracle-specific-bucket-name"
region = "us-phoenix-1"  # Oracle-specific region
```

**NEVER hardcode provider specifics**:
- ❌ Import `oci.*`, `boto3.*`, `google.cloud.*` directly in core logic
- ❌ Use provider-specific resource identifiers (OCID, ARN, etc.)
- ✅ Use generic abstractions: "cloud storage", "secret management", "logging service"

**Example - Generic Abstraction**:
```python
# ✅ DO: Generic approach
from src.utils.cloud import CloudStorage
storage = CloudStorage()
storage.upload_file("results/backtest.csv", "backups/")

# ❌ DON'T: Provider-specific
from oci.object_storage import ObjectStorageClient
client = ObjectStorageClient()
```

**Rationale**: Environment variables enable easy migration between AWS, GCP, Azure, Oracle Cloud without code changes.

#### Planned Cloud Infrastructure

- **Platform**: TBD (AWS Lambda, Google Cloud Run, or dedicated VM)
- **Scheduler**: Cron job to start bot at 9:20 AM EST daily
- **Secrets**: Environment variables via cloud provider (Secrets Manager)
- **Monitoring**: Cloud Logging + Discord notifications
- **Storage**: Cloud storage for backtest results and logs

## Environment Variable Conventions

### Required
- `ENV` - Environment name (local, dev, prod)
- `POLYGON_API_KEY` - Polygon.io API key
- `DISCORD_WEBHOOK_URL` - Discord webhook for notifications

### Optional
- `FINNHUB_API_KEY` - Finnhub API key (backup provider)
- `LOG_LEVEL` - Logging verbosity (DEBUG, INFO, WARNING, ERROR)
- `DATA_DIR` - Override data directory (default: `data/`)
- `CONFIG_PATH` - Override config file path

### Config File Priority
1. Environment variable (e.g., `POLYGON_API_KEY`)
2. `.env` file (local development only)
3. YAML config file (`config/{ENV}.yaml`)
4. Defaults in code

## Logging

### Format
```json
{
  "timestamp": "2026-05-23T09:25:00-04:00",
  "level": "INFO",
  "logger": "vibe.trading_bot.orchestrator",
  "event": "warmup_started",
  "symbols": ["QQQ"],
  "version": "v1.1.0"
}
```

### Log Levels
- **DEBUG**: Verbose output (every bar, every indicator calculation)
- **INFO**: Key events (phase transitions, orders, health checks)
- **WARNING**: Recoverable issues (stale data, provider failover)
- **ERROR**: Critical failures (connection lost, order rejection)

### Log Files
- **Location**: `logs/trading_bot_{date}.log`
- **Rotation**: Daily (one file per trading day)
- **Retention**: 30 days local, unlimited in cloud storage

## Testing Strategy

### Unit Tests (`tests/unit/`)
- Individual components in isolation
- Mock external dependencies (providers, APIs)
- Fast execution (<1 second per test)

### Integration Tests (`tests/integration/`)
- End-to-end workflows (warmup → trading → cooldown)
- Real API calls (sandboxed or paper trading)
- Slower execution (5-30 seconds per test)

### Backtest Validation
- Full-history backtests (2018-2024) for regression detection
- Out-of-sample testing (2025+) for overfitting prevention
- Slippage stress tests (5, 10, 15 ticks)

## IDE Setup (VS Code)

### Recommended Extensions
- **Python** (Microsoft) - Language support
- **Pylance** - Fast IntelliSense and type checking
- **Black Formatter** - Auto-formatting on save
- **Ruff** - Fast linting

### Settings (`.vscode/settings.json`)
```json
{
  "python.defaultInterpreterPath": "${workspaceFolder}/venv/Scripts/python.exe",
  "python.testing.pytestEnabled": true,
  "python.formatting.provider": "black",
  "editor.formatOnSave": true,
  "[python]": {
    "editor.codeActionsOnSave": {
      "source.organizeImports": true
    }
  }
}
```
