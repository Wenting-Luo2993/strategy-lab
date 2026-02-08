"""Constants for trading bot."""

from datetime import time
from typing import Dict, Set

# Market hours (US Eastern Time)
MARKET_OPEN = time(9, 30)  # 9:30 AM
MARKET_CLOSE = time(16, 0)  # 4:00 PM

# Trading days (Monday=0, Sunday=6)
TRADING_DAYS = {0, 1, 2, 3, 4}  # Monday through Friday

# Default symbols for trading
DEFAULT_SYMBOLS = ["AAPL", "GOOGL", "MSFT", "AMZN", "NVDA"]

# Bar intervals in seconds
BAR_INTERVALS: Dict[str, int] = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "1h": 3600,
    "1d": 86400,
}

# Database constraints
MAX_BATCH_INSERT_SIZE = 1000
DATABASE_TIMEOUT = 30.0

# Logging
LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
JSON_LOG_FORMAT = '{"timestamp": "%(asctime)s", "level": "%(levelname)s", "logger": "%(name)s", "message": "%(message)s"}'

# Log rotation
LOG_FILE_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
LOG_FILE_BACKUP_COUNT = 5

# Health check
DEFAULT_HEALTH_PORT = 8080
HEALTH_CHECK_INTERVAL_SECONDS = 10

# Metrics
METRIC_TYPES = {"trade", "performance", "health"}

# Default retention periods (days)
LOG_RETENTION_DAYS = 3
TRADE_RETENTION_DAYS = 90

# Cloud sync
DEFAULT_CLOUD_SYNC_INTERVAL = 300  # 5 minutes

# Shutdown
DEFAULT_SHUTDOWN_TIMEOUT = 30  # seconds
