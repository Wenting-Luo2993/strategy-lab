"""
Finnhub Configuration Loader

Loads and validates Finnhub WebSocket configuration from JSON file or environment variables.
Environment variables take precedence over JSON configuration.
"""

import json
import os
from pathlib import Path
from typing import Dict, Any, Optional
from dataclasses import dataclass
from datetime import time

from src.utils.logger import get_logger

logger = get_logger("FinnhubConfig")


@dataclass
class MarketHours:
    """Market hours configuration."""
    timezone: str
    pre_market_start: time
    regular_start: time
    regular_end: time
    after_hours_end: time

    @classmethod
    def from_dict(cls, data: Dict[str, str]) -> 'MarketHours':
        """Create MarketHours from dictionary."""
        return cls(
            timezone=data.get("timezone", "America/New_York"),
            pre_market_start=time.fromisoformat(data.get("pre_market_start", "04:00")),
            regular_start=time.fromisoformat(data.get("regular_start", "09:30")),
            regular_end=time.fromisoformat(data.get("regular_end", "16:00")),
            after_hours_end=time.fromisoformat(data.get("after_hours_end", "20:00"))
        )


@dataclass
class ReconnectConfig:
    """Reconnection configuration."""
    enabled: bool = True
    max_attempts: int = 10
    initial_backoff_seconds: int = 1
    max_backoff_seconds: int = 60

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> 'ReconnectConfig':
        """Create ReconnectConfig from dictionary."""
        if data is None:
            return cls()
        return cls(
            enabled=data.get("enabled", True),
            max_attempts=data.get("max_attempts", 10),
            initial_backoff_seconds=data.get("initial_backoff_seconds", 1),
            max_backoff_seconds=data.get("max_backoff_seconds", 60)
        )


@dataclass
class RestApiConfig:
    """REST API configuration."""
    enabled: bool = True
    cache_ttl_seconds: int = 3600

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> 'RestApiConfig':
        """Create RestApiConfig from dictionary."""
        if data is None:
            return cls()
        return cls(
            enabled=data.get("enabled", True),
            cache_ttl_seconds=data.get("cache_ttl_seconds", 3600)
        )


@dataclass
class FinnhubConfig:
    """Finnhub WebSocket configuration."""
    api_key: str
    websocket_url: str
    bar_interval: str
    symbols: list
    market_hours: MarketHours
    bar_delay_seconds: int = 5
    filter_after_hours: bool = False
    reconnect: ReconnectConfig = None
    rest_api: RestApiConfig = None

    def __post_init__(self):
        """Set defaults for optional configs."""
        if self.reconnect is None:
            self.reconnect = ReconnectConfig()
        if self.rest_api is None:
            self.rest_api = RestApiConfig()

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'FinnhubConfig':
        """Create FinnhubConfig from dictionary."""
        # Validate required fields
        required_fields = ["api_key", "websocket_url"]
        for field in required_fields:
            if field not in data:
                raise ValueError(f"Missing required config field: {field}")

        # Parse market hours
        market_hours_data = data.get("market_hours", {})
        market_hours = MarketHours.from_dict(market_hours_data)

        # Parse reconnect config
        reconnect = ReconnectConfig.from_dict(data.get("reconnect"))

        # Parse REST API config
        rest_api = RestApiConfig.from_dict(data.get("rest_api"))

        return cls(
            api_key=data["api_key"],
            websocket_url=data.get("websocket_url", "wss://ws.finnhub.io"),
            bar_interval=data.get("bar_interval", "5m"),
            symbols=data.get("symbols", []),
            market_hours=market_hours,
            bar_delay_seconds=data.get("bar_delay_seconds", 5),
            filter_after_hours=data.get("filter_after_hours", False),
            reconnect=reconnect,
            rest_api=rest_api
        )


def load_finnhub_config(config_path: Optional[Path] = None) -> FinnhubConfig:
    """
    Load Finnhub configuration from JSON file or environment variables.

    Environment variables take precedence over JSON file configuration:
    - FINNHUB_API_KEY: API key for Finnhub
    - FINNHUB_WEBSOCKET_URL: WebSocket URL (default: wss://ws.finnhub.io)
    - FINNHUB_BAR_INTERVAL: Bar interval like "5m", "1m", etc. (default: 5m)
    - FINNHUB_SYMBOLS: Comma-separated symbols like "AAPL,MSFT,NVDA"
    - FINNHUB_BAR_DELAY_SECONDS: Delay after bar close (default: 5)
    - FINNHUB_FILTER_AFTER_HOURS: "true" or "false" (default: false)
    - FINNHUB_MARKET_TIMEZONE: Timezone (default: America/New_York)

    Args:
        config_path: Path to config file. If None, uses default location.

    Returns:
        FinnhubConfig: Loaded and validated configuration

    Raises:
        FileNotFoundError: If config file doesn't exist and no env vars set
        ValueError: If config is invalid or missing required fields
        json.JSONDecodeError: If JSON is malformed
    """
    # Try to load from environment variables first
    api_key_env = os.getenv("FINNHUB_API_KEY")

    # If API key is in env, load everything from environment with JSON as fallback
    if api_key_env:
        logger.info("Loading Finnhub config from environment variables")

        # Parse symbols from comma-separated string
        symbols_str = os.getenv("FINNHUB_SYMBOLS", "AAPL,MSFT")
        symbols = [s.strip() for s in symbols_str.split(",")]

        # Parse market hours from env or use defaults
        market_hours = MarketHours(
            timezone=os.getenv("FINNHUB_MARKET_TIMEZONE", "America/New_York"),
            pre_market_start=time.fromisoformat(os.getenv("FINNHUB_PRE_MARKET_START", "04:00")),
            regular_start=time.fromisoformat(os.getenv("FINNHUB_REGULAR_START", "09:30")),
            regular_end=time.fromisoformat(os.getenv("FINNHUB_REGULAR_END", "16:00")),
            after_hours_end=time.fromisoformat(os.getenv("FINNHUB_AFTER_HOURS_END", "20:00"))
        )

        # Parse reconnect config from env
        reconnect = ReconnectConfig(
            enabled=os.getenv("FINNHUB_RECONNECT_ENABLED", "true").lower() == "true",
            max_attempts=int(os.getenv("FINNHUB_RECONNECT_MAX_ATTEMPTS", "10")),
            initial_backoff_seconds=int(os.getenv("FINNHUB_RECONNECT_INITIAL_BACKOFF", "1")),
            max_backoff_seconds=int(os.getenv("FINNHUB_RECONNECT_MAX_BACKOFF", "60"))
        )

        # Parse REST API config from env
        rest_api = RestApiConfig(
            enabled=os.getenv("FINNHUB_REST_API_ENABLED", "true").lower() == "true",
            cache_ttl_seconds=int(os.getenv("FINNHUB_REST_API_CACHE_TTL", "3600"))
        )

        config = FinnhubConfig(
            api_key=api_key_env,
            websocket_url=os.getenv("FINNHUB_WEBSOCKET_URL", "wss://ws.finnhub.io"),
            bar_interval=os.getenv("FINNHUB_BAR_INTERVAL", "5m"),
            symbols=symbols,
            market_hours=market_hours,
            bar_delay_seconds=int(os.getenv("FINNHUB_BAR_DELAY_SECONDS", "5")),
            filter_after_hours=os.getenv("FINNHUB_FILTER_AFTER_HOURS", "false").lower() == "true",
            reconnect=reconnect,
            rest_api=rest_api
        )

        logger.info(f"Config loaded from env: {len(config.symbols)} symbols, interval={config.bar_interval}")
        return config

    # Fall back to JSON file loading
    if config_path is None:
        # Default location: src/config/finnhub_config.json
        config_path = Path(__file__).parent / "finnhub_config.json"

    if not config_path.exists():
        raise FileNotFoundError(
            f"Finnhub config file not found: {config_path}\n"
            f"Please create it from the example template: finnhub_config.example.json\n"
            f"OR set FINNHUB_API_KEY environment variable for environment-based configuration"
        )

    logger.info(f"Loading Finnhub config from: {config_path}")

    try:
        with open(config_path, 'r') as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in config file: {e}")

    # Validate API key
    config = FinnhubConfig.from_dict(data)

    if config.api_key == "REPLACE_WITH_YOUR_FINNHUB_API_KEY":
        raise ValueError(
            "Please set a valid Finnhub API key in finnhub_config.json\n"
            "Get your free API key at: https://finnhub.io/register\n"
            "OR set the FINNHUB_API_KEY environment variable"
        )

    logger.info(f"Config loaded successfully: {len(config.symbols)} symbols, interval={config.bar_interval}")
    return config


if __name__ == "__main__":
    # Test loading config
    try:
        config = load_finnhub_config()
        print("✅ Config loaded successfully!")
        print(f"   API Key: {'*' * len(config.api_key)}")
        print(f"   WebSocket URL: {config.websocket_url}")
        print(f"   Bar Interval: {config.bar_interval}")
        print(f"   Symbols: {', '.join(config.symbols)}")
        print(f"   Market Hours: {config.market_hours.regular_start} - {config.market_hours.regular_end} {config.market_hours.timezone}")
    except Exception as e:
        print(f"❌ Error loading config: {e}")
