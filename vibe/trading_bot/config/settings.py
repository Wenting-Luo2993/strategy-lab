"""Configuration settings for trading bot using Pydantic."""

from typing import Optional, List
from pydantic_settings import BaseSettings
from pydantic import Field


class TradingSettings(BaseSettings):
    """Trading configuration."""

    symbols: List[str] = Field(default=["AAPL", "GOOGL", "MSFT"], description="Trading symbols")
    initial_capital: float = Field(default=10000.0, description="Initial capital in USD")
    max_position_size: float = Field(default=0.1, description="Max position size as % of capital")
    use_stop_loss: bool = Field(default=True, description="Enable stop loss")
    stop_loss_pct: float = Field(default=0.02, description="Stop loss percentage")
    take_profit_pct: float = Field(default=0.05, description="Take profit percentage")
    finnhub_api_key: Optional[str] = Field(default=None, description="Finnhub API key")

    class Config:
        env_prefix = ""
        case_sensitive = False


class DataSettings(BaseSettings):
    """Data provider configuration."""

    yahoo_rate_limit: int = Field(default=5, description="Yahoo Finance requests per second")
    yahoo_retry_count: int = Field(default=3, description="Yahoo Finance retry attempts")
    data_cache_ttl_seconds: int = Field(default=3600, description="Cache TTL in seconds")
    bar_intervals: List[str] = Field(default=["1m", "5m", "15m"], description="Bar intervals to track")

    class Config:
        env_prefix = ""
        case_sensitive = False


class CloudSettings(BaseSettings):
    """Cloud storage configuration."""

    cloud_provider: Optional[str] = Field(default=None, description="Cloud provider (azure, aws)")
    cloud_container: Optional[str] = Field(default=None, description="Cloud storage container/bucket")
    sync_interval_seconds: int = Field(default=300, description="Cloud sync interval")
    enable_cloud_sync: bool = Field(default=False, description="Enable cloud sync")

    class Config:
        env_prefix = ""
        case_sensitive = False


class NotificationSettings(BaseSettings):
    """Notification configuration."""

    discord_webhook_url: Optional[str] = Field(default=None, description="Discord webhook URL")
    notify_on_trade: bool = Field(default=True, description="Notify on trade execution")
    notify_on_error: bool = Field(default=True, description="Notify on error")

    class Config:
        env_prefix = ""
        case_sensitive = False


class AppSettings(BaseSettings):
    """Main application settings combining all subsettings."""

    environment: str = Field(default="development", description="Environment (development/production)")
    log_level: str = Field(default="INFO", description="Logging level")
    health_check_port: int = Field(default=8080, description="Health check server port")
    shutdown_timeout_seconds: int = Field(default=30, description="Graceful shutdown timeout")
    database_path: str = Field(default="./data/trades.db", description="SQLite database path")

    trading: TradingSettings = Field(default_factory=TradingSettings)
    data: DataSettings = Field(default_factory=DataSettings)
    cloud: CloudSettings = Field(default_factory=CloudSettings)
    notifications: NotificationSettings = Field(default_factory=NotificationSettings)

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


def get_settings() -> AppSettings:
    """Get application settings."""
    return AppSettings()
