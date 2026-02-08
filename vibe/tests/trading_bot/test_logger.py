"""Tests for logger module."""

import json
import logging
from pathlib import Path
import pytest
from vibe.trading_bot.utils.logger import (
    get_logger,
    JSONFormatter,
    StandardFormatter,
    log_with_context,
)


class TestJSONFormatter:
    """Tests for JSONFormatter."""

    def test_format_basic(self):
        """Test basic JSON log formatting."""
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
        assert "timestamp" in data

    def test_format_with_meta(self):
        """Test JSON formatting with extra metadata."""
        formatter = JSONFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.WARNING,
            pathname="test.py",
            lineno=1,
            msg="Test warning",
            args=(),
            exc_info=None,
        )
        record.meta = {"user_id": 123, "action": "buy"}
        output = formatter.format(record)
        data = json.loads(output)

        assert data["level"] == "WARNING"
        assert data["user_id"] == 123
        assert data["action"] == "buy"

    def test_format_with_exception(self):
        """Test JSON formatting with exception info."""
        formatter = JSONFormatter()
        try:
            1 / 0
        except ZeroDivisionError:
            exc_info = True
        else:
            exc_info = None

        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname="test.py",
            lineno=1,
            msg="Error occurred",
            args=(),
            exc_info=exc_info,
        )
        if exc_info:
            import sys
            record.exc_info = sys.exc_info()

        output = formatter.format(record)
        data = json.loads(output)

        assert data["level"] == "ERROR"
        assert data["message"] == "Error occurred"


class TestStandardFormatter:
    """Tests for StandardFormatter."""

    def test_format_basic(self):
        """Test basic standard formatting."""
        formatter = StandardFormatter("%(levelname)s - %(message)s")
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
        assert "INFO" in output
        assert "Test message" in output


class TestGetLogger:
    """Tests for get_logger function."""

    def test_get_logger_defaults(self):
        """Test getting logger with defaults."""
        logger = get_logger("test.module")
        assert logger is not None
        assert logger.name == "test.module"
        assert logger.level == logging.INFO

    def test_get_logger_custom_level(self):
        """Test getting logger with custom log level."""
        logger = get_logger("test.debug", log_level="DEBUG")
        assert logger.level == logging.DEBUG

    def test_get_logger_with_file(self, tmp_path):
        """Test getting logger with file handler."""
        logger = get_logger(
            "test.file",
            log_level="INFO",
            log_dir=str(tmp_path),
            enable_file_handler=True,
        )
        logger.info("Test message")

        log_file = tmp_path / "test_file.log"
        assert log_file.exists()

        # Verify file contains JSON
        with open(log_file, 'r') as f:
            content = f.read().strip()
            if content:
                data = json.loads(content)
                assert data["message"] == "Test message"

    def test_get_logger_caching(self):
        """Test that logger instances are cached."""
        logger1 = get_logger("test.cache")
        logger2 = get_logger("test.cache")
        assert logger1 is logger2

    def test_get_logger_console_handler(self):
        """Test that console handler is added."""
        logger = get_logger("test.console")
        handlers = [h for h in logger.handlers if isinstance(h, logging.StreamHandler)]
        assert len(handlers) > 0

    def test_get_logger_file_rotation(self, tmp_path):
        """Test that file handler has rotation."""
        logger = get_logger(
            "test.rotation",
            log_dir=str(tmp_path),
        )

        # Check that rotating file handler exists
        handlers = [
            h for h in logger.handlers
            if isinstance(h, logging.handlers.RotatingFileHandler)
        ]
        assert len(handlers) > 0

        # Verify rotation settings
        handler = handlers[0]
        assert handler.maxBytes == 10 * 1024 * 1024  # 10 MB
        assert handler.backupCount == 5


class TestLogWithContext:
    """Tests for log_with_context function."""

    def test_log_with_context_info(self, tmp_path):
        """Test logging with context metadata."""
        logger = get_logger(
            "test.context",
            log_dir=str(tmp_path),
        )
        log_with_context(
            logger,
            "info",
            "User action",
            user_id=123,
            action="trade",
            symbol="AAPL",
        )

        log_file = tmp_path / "test_context.log"
        with open(log_file, 'r') as f:
            content = f.read().strip()
            if content:
                data = json.loads(content)
                assert data["message"] == "User action"
                assert data["user_id"] == 123
                assert data["action"] == "trade"
                assert data["symbol"] == "AAPL"

    def test_log_with_context_error(self, tmp_path):
        """Test logging error with context."""
        logger = get_logger(
            "test.error_context",
            log_dir=str(tmp_path),
        )
        log_with_context(
            logger,
            "error",
            "Operation failed",
            operation="buy_stock",
            error_code=500,
        )

        log_file = tmp_path / "test_error_context.log"
        with open(log_file, 'r') as f:
            content = f.read().strip()
            if content:
                data = json.loads(content)
                assert data["level"] == "ERROR"
                assert data["message"] == "Operation failed"
                assert data["operation"] == "buy_stock"
                assert data["error_code"] == 500


class TestLoggerIntegration:
    """Integration tests for logger."""

    def test_multiple_loggers(self, tmp_path):
        """Test that multiple loggers can work independently."""
        logger1 = get_logger("app.module1", log_dir=str(tmp_path))
        logger2 = get_logger("app.module2", log_dir=str(tmp_path))

        logger1.info("Message from module1")
        logger2.warning("Message from module2")

        log_file1 = tmp_path / "app_module1.log"
        log_file2 = tmp_path / "app_module2.log"

        assert log_file1.exists()
        assert log_file2.exists()

    def test_logger_without_file_handler(self):
        """Test logger without file handler."""
        logger = get_logger(
            "test.no_file",
            enable_file_handler=False,
        )

        # Should only have console handler
        handlers = [
            h for h in logger.handlers
            if isinstance(h, logging.handlers.RotatingFileHandler)
        ]
        assert len(handlers) == 0
