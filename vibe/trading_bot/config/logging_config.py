"""Logging configuration for structured JSON logging."""

import logging.config
import json
from typing import Any, Dict
from pathlib import Path


class JSONFormatter(logging.Formatter):
    """Custom formatter that outputs JSON format."""

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON."""
        log_data = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Add extra fields if present
        if hasattr(record, "meta") and record.meta:
            log_data["meta"] = record.meta

        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_data)


def get_logging_config(
    log_level: str = "INFO",
    log_dir: str = "./logs",
) -> Dict[str, Any]:
    """Get logging configuration dictionary.

    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_dir: Directory for log files

    Returns:
        Logging configuration dictionary for logging.config.dictConfig
    """
    # Create log directory if it doesn't exist
    Path(log_dir).mkdir(parents=True, exist_ok=True)

    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "standard": {
                "format": "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
            },
            "json": {
                "()": JSONFormatter,
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "level": log_level,
                "formatter": "standard",
                "stream": "ext://sys.stdout",
            },
            "file": {
                "class": "logging.handlers.RotatingFileHandler",
                "level": "DEBUG",
                "formatter": "json",
                "filename": f"{log_dir}/trading_bot.log",
                "maxBytes": 10 * 1024 * 1024,  # 10 MB
                "backupCount": 5,
            },
        },
        "loggers": {
            "vibe": {
                "level": log_level,
                "handlers": ["console", "file"],
                "propagate": False,
            },
        },
        "root": {
            "level": log_level,
            "handlers": ["console", "file"],
        },
    }


def configure_logging(log_level: str = "INFO", log_dir: str = "./logs") -> None:
    """Configure logging system.

    Args:
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_dir: Directory for log files
    """
    config = get_logging_config(log_level, log_dir)
    logging.config.dictConfig(config)
