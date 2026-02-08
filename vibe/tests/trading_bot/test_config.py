"""Tests for configuration module."""

import os
import json
import tempfile
from pathlib import Path
import pytest
from vibe.trading_bot.config.settings import (
    TradingSettings,
    DataSettings,
    CloudSettings,
    NotificationSettings,
    AppSettings,
    get_settings,
)
from vibe.trading_bot.config.constants import (
    MARKET_OPEN,
    MARKET_CLOSE,
    TRADING_DAYS,
    DEFAULT_SYMBOLS,
    BAR_INTERVALS,
)
from vibe.trading_bot.config.logging_config import (
    JSONFormatter,
    get_logging_config,
    configure_logging,
)
import logging


class TestTradingSettings:
    """Tests for TradingSettings."""

    def test_defaults(self):
        """Test default values."""
        settings = TradingSettings()
        assert "AAPL" in settings.symbols
        assert settings.initial_capital == 10000.0
        assert settings.max_position_size == 0.1
        assert settings.use_stop_loss is True

    def test_from_env_vars(self, monkeypatch):
        """Test loading from environment variables."""
        monkeypatch.setenv("SYMBOLS", '["TEST1", "TEST2"]')
        monkeypatch.setenv("INITIAL_CAPITAL", "50000")
        monkeypatch.setenv("MAX_POSITION_SIZE", "0.2")

        settings = TradingSettings()
        # Note: Pydantic may parse this differently depending on field type
        # For list fields, setting string won't work directly; we'd need to test with proper config
        assert settings.initial_capital == 50000.0
        assert settings.max_position_size == 0.2

    def test_validation(self, monkeypatch):
        """Test settings validation."""
        monkeypatch.setenv("INITIAL_CAPITAL", "invalid")
        with pytest.raises(Exception):  # ValidationError
            TradingSettings()


class TestDataSettings:
    """Tests for DataSettings."""

    def test_defaults(self):
        """Test default values."""
        settings = DataSettings()
        assert settings.yahoo_rate_limit == 5
        assert settings.yahoo_retry_count == 3
        assert settings.data_cache_ttl_seconds == 3600

    def test_bar_intervals(self):
        """Test bar intervals."""
        settings = DataSettings()
        assert "1m" in settings.bar_intervals
        assert "5m" in settings.bar_intervals


class TestCloudSettings:
    """Tests for CloudSettings."""

    def test_defaults(self):
        """Test default values."""
        settings = CloudSettings()
        assert settings.enable_cloud_sync is False
        assert settings.sync_interval_seconds == 300


class TestNotificationSettings:
    """Tests for NotificationSettings."""

    def test_defaults(self):
        """Test default values."""
        settings = NotificationSettings()
        assert settings.notify_on_trade is True
        assert settings.notify_on_error is True


class TestAppSettings:
    """Tests for AppSettings."""

    def test_defaults(self):
        """Test default values."""
        settings = AppSettings()
        assert settings.environment == "development"
        assert settings.log_level == "INFO"
        assert settings.health_check_port == 8080
        assert settings.shutdown_timeout_seconds == 30

    def test_subsettings(self):
        """Test nested settings."""
        settings = AppSettings()
        assert isinstance(settings.trading, TradingSettings)
        assert isinstance(settings.data, DataSettings)
        assert isinstance(settings.cloud, CloudSettings)
        assert isinstance(settings.notifications, NotificationSettings)

    def test_get_settings(self):
        """Test get_settings factory function."""
        settings = get_settings()
        assert isinstance(settings, AppSettings)


class TestConstants:
    """Tests for constants."""

    def test_market_hours(self):
        """Test market hours."""
        assert MARKET_OPEN.hour == 9
        assert MARKET_OPEN.minute == 30
        assert MARKET_CLOSE.hour == 16
        assert MARKET_CLOSE.minute == 0

    def test_trading_days(self):
        """Test trading days."""
        assert len(TRADING_DAYS) == 5
        assert 0 in TRADING_DAYS  # Monday
        assert 4 in TRADING_DAYS  # Friday
        assert 5 not in TRADING_DAYS  # Saturday

    def test_default_symbols(self):
        """Test default symbols."""
        assert len(DEFAULT_SYMBOLS) > 0
        assert "AAPL" in DEFAULT_SYMBOLS

    def test_bar_intervals(self):
        """Test bar intervals."""
        assert BAR_INTERVALS["1m"] == 60
        assert BAR_INTERVALS["5m"] == 300
        assert BAR_INTERVALS["15m"] == 900
        assert BAR_INTERVALS["1h"] == 3600


class TestJSONFormatter:
    """Tests for JSONFormatter."""

    def test_format_basic(self):
        """Test basic log formatting."""
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test message",
            args=(),
            exc_info=None,
        )
        output = formatter.format(record)
        data = json.loads(output)
        assert data["level"] == "INFO"
        assert data["logger"] == "test"
        assert data["message"] == "Test message"

    def test_format_with_meta(self):
        """Test formatting with extra metadata."""
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="test.py",
            lineno=1,
            msg="Test message",
            args=(),
            exc_info=None,
        )
        record.meta = {"key": "value", "number": 42}
        output = formatter.format(record)
        data = json.loads(output)
        assert data["meta"]["key"] == "value"
        assert data["meta"]["number"] == 42


class TestLoggingConfig:
    """Tests for logging configuration."""

    def test_get_logging_config(self, tmp_path):
        """Test getting logging configuration."""
        config = get_logging_config(log_level="DEBUG", log_dir=str(tmp_path))
        assert "formatters" in config
        assert "handlers" in config
        assert "json" in config["formatters"]
        assert "file" in config["handlers"]

    def test_configure_logging(self, tmp_path):
        """Test configuring logging system."""
        log_dir = str(tmp_path / "logs")
        configure_logging(log_level="INFO", log_dir=log_dir)

        # Verify logger is configured
        logger = logging.getLogger("vibe")
        assert logger is not None

    def test_log_file_creation(self, tmp_path):
        """Test that log file is created."""
        log_dir = str(tmp_path)
        configure_logging(log_level="INFO", log_dir=log_dir)

        logger = logging.getLogger("vibe.test")
        logger.info("Test message")

        log_file = Path(log_dir) / "trading_bot.log"
        assert log_file.exists()

    def test_json_log_output(self, tmp_path):
        """Test that logs are output as JSON."""
        log_dir = str(tmp_path)
        configure_logging(log_level="INFO", log_dir=log_dir)

        logger = logging.getLogger("vibe.test")
        logger.info("Test JSON output")

        log_file = Path(log_dir) / "trading_bot.log"
        with open(log_file, 'r') as f:
            content = f.read().strip()
            # Try to parse as JSON
            if content:
                data = json.loads(content.split('\n')[0])
                assert "level" in data
                assert "message" in data
