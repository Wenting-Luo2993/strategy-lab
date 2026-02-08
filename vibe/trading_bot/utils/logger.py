"""Logger utility for structured logging."""

import logging
import logging.handlers
import json
from typing import Optional, Dict, Any
from pathlib import Path


class JSONFormatter(logging.Formatter):
    """Custom formatter that outputs JSON format."""

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON.

        Args:
            record: LogRecord to format

        Returns:
            JSON-formatted log string
        """
        log_data = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Add extra fields if present
        if hasattr(record, "meta") and record.meta:
            log_data.update(record.meta)

        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_data)


class StandardFormatter(logging.Formatter):
    """Standard human-readable formatter."""

    def format(self, record: logging.LogRecord) -> str:
        """Format log record in standard format.

        Args:
            record: LogRecord to format

        Returns:
            Formatted log string
        """
        return super().format(record)


_loggers: Dict[str, logging.Logger] = {}


def get_logger(
    name: str,
    log_level: str = "INFO",
    log_dir: Optional[str] = None,
    enable_file_handler: bool = True,
) -> logging.Logger:
    """Get or create a logger with JSON file handler and console handler.

    Args:
        name: Logger name (typically __name__)
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_dir: Directory for log files (if None, no file handler)
        enable_file_handler: Whether to add file handler

    Returns:
        Configured logger instance
    """
    if name in _loggers:
        return _loggers[name]

    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, log_level.upper()))

    # Avoid adding duplicate handlers
    if not logger.handlers:
        # Console handler with standard format
        console_handler = logging.StreamHandler()
        console_handler.setLevel(getattr(logging, log_level.upper()))
        console_formatter = StandardFormatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        console_handler.setFormatter(console_formatter)
        logger.addHandler(console_handler)

        # File handler with JSON format if log_dir is specified
        if enable_file_handler and log_dir:
            log_path = Path(log_dir)
            log_path.mkdir(parents=True, exist_ok=True)

            file_handler = logging.handlers.RotatingFileHandler(
                filename=str(log_path / f"{name.replace('.', '_')}.log"),
                maxBytes=10 * 1024 * 1024,  # 10 MB
                backupCount=5,
            )
            file_handler.setLevel(logging.DEBUG)
            file_formatter = JSONFormatter()
            file_handler.setFormatter(file_formatter)
            logger.addHandler(file_handler)

    _loggers[name] = logger
    return logger


def log_with_context(
    logger: logging.Logger,
    level: str,
    message: str,
    **context: Any,
) -> None:
    """Log a message with additional context metadata.

    Args:
        logger: Logger instance
        level: Log level (debug, info, warning, error, critical)
        message: Log message
        **context: Additional context to include in JSON output
    """
    log_func = getattr(logger, level.lower())

    # Create a LogRecord and add meta attribute
    record = logger.makeRecord(
        logger.name,
        getattr(logging, level.upper()),
        "(unknown file)",
        0,
        message,
        (),
        None,
    )
    record.meta = context
    logger.handle(record)
