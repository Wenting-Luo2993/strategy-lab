# src/utils/logger.py
import logging
import os
from pathlib import Path

# --- Custom logger implementation to allow per-call console output ---

class _SelectiveConsoleFilter(logging.Filter):
    """Filter that only allows records explicitly flagged for console.

    A log record passes if it has attribute `to_console` set to True.
    """
    def filter(self, record: logging.LogRecord) -> bool:  # type: ignore[override]
        return getattr(record, "to_console", False)


class StrategyLabLogger(logging.Logger):
    """Custom logger that adds a `console` boolean kwarg to level methods.

    Usage:
        logger.info("This goes to file only")
        logger.info("This also goes to console", console=True)

    The `console` flag sets a transient attribute `to_console` on the LogRecord
    so that a selective console handler (with a filter) can emit it.
    """

    def _log_with_console(self, level, msg, args, *, console=False, **kwargs):
        # Extract existing extra dict, append to_console flag if requested
        extra = kwargs.pop("extra", {}) or {}
        if console:
            # Do not overwrite user value if already present
            extra = {**extra, "to_console": True}
        kwargs["extra"] = extra
        super()._log(level, msg, args, **kwargs)

    # Override standard level helpers to accept console kwarg
    def debug(self, msg, *args, console=False, **kwargs):  # type: ignore[override]
        if self.isEnabledFor(logging.DEBUG):
            self._log_with_console(logging.DEBUG, msg, args, console=console, **kwargs)

    def info(self, msg, *args, console=False, **kwargs):  # type: ignore[override]
        if self.isEnabledFor(logging.INFO):
            self._log_with_console(logging.INFO, msg, args, console=console, **kwargs)

    def warning(self, msg, *args, console=False, **kwargs):  # type: ignore[override]
        if self.isEnabledFor(logging.WARNING):
            self._log_with_console(logging.WARNING, msg, args, console=console, **kwargs)

    warn = warning  # backward compatibility

    def error(self, msg, *args, console=False, **kwargs):  # type: ignore[override]
        if self.isEnabledFor(logging.ERROR):
            self._log_with_console(logging.ERROR, msg, args, console=console, **kwargs)

    def critical(self, msg, *args, console=False, **kwargs):  # type: ignore[override]
        if self.isEnabledFor(logging.CRITICAL):
            self._log_with_console(logging.CRITICAL, msg, args, console=console, **kwargs)

    def log(self, level, msg, *args, console=False, **kwargs):  # type: ignore[override]
        if self.isEnabledFor(level):
            self._log_with_console(level, msg, args, console=console, **kwargs)


# Register custom logger class globally (only first call has effect)
logging.setLoggerClass(StrategyLabLogger)

# Create logs directory if it doesn't exist
logs_dir = Path(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "logs"))
logs_dir.mkdir(exist_ok=True)

def get_logger(name, level=logging.DEBUG, log_to_console=False, log_to_file=True):
    """
    Returns a configured logger instance
    
    Args:
        name: Name of the logger (typically module name)
        level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_to_console: Whether to output logs to console
        log_to_file: Whether to save logs to file
        
    Returns:
        Logger instance
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)
    
    # Clear existing handlers to avoid duplicates when this function is called multiple times
    if logger.hasHandlers():
        logger.handlers.clear()
    
    # Create formatters
    file_formatter = logging.Formatter('%(asctime)s [%(name)s] [%(levelname)s] %(message)s')
    console_formatter = logging.Formatter('[%(name)s] %(message)s')
    
    # Add file handler if requested
    if log_to_file:
        log_file = logs_dir / f"{name}.log"
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(file_formatter)
        file_handler.setLevel(level)
        logger.addHandler(file_handler)

    # Two modes for console:
    # 1. Default console output (all messages) when log_to_console=True
    # 2. Selective per-call console output via console=True flag, handled by filtered handler

    # Attach always-on selective console handler first (only emits flagged records)
    selective_console = logging.StreamHandler()
    selective_console.setFormatter(console_formatter)
    selective_console.setLevel(logging.DEBUG)  # allow all levels; filtering done in filter
    selective_console.addFilter(_SelectiveConsoleFilter())
    logger.addHandler(selective_console)

    # If user wants every message also on console, add an unfiltered handler
    if log_to_console:
        default_console = logging.StreamHandler()
        default_console.setFormatter(console_formatter)
        default_console.setLevel(level)
        logger.addHandler(default_console)
    
    return logger

# Configure root logger for general application logging
def setup_root_logger(level=logging.INFO, log_to_console=False, log_to_file=True):
    """
    Configure the root logger
    
    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_to_console: Whether to output logs to console
        log_to_file: Whether to save logs to file
    
    Returns:
        Root logger instance
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    
    # Clear existing handlers to avoid duplicates
    if root_logger.hasHandlers():
        root_logger.handlers.clear()
    
    # Create formatters
    file_formatter = logging.Formatter('%(asctime)s [%(name)s] [%(levelname)s] %(message)s')
    console_formatter = logging.Formatter('[%(levelname)s] %(message)s')
    
    # Add file handler if requested
    if log_to_file:
        all_logs_file = logs_dir / "application.log"
        file_handler = logging.FileHandler(all_logs_file)
        file_handler.setFormatter(file_formatter)
        file_handler.setLevel(level)
        root_logger.addHandler(file_handler)

    # Always attach selective console handler for per-call console logging
    selective_console = logging.StreamHandler()
    selective_console.setFormatter(console_formatter)
    selective_console.setLevel(logging.DEBUG)
    selective_console.addFilter(_SelectiveConsoleFilter())
    root_logger.addHandler(selective_console)

    # Optional: default console handler for all messages
    if log_to_console:
        default_console = logging.StreamHandler()
        default_console.setFormatter(console_formatter)
        default_console.setLevel(level)
        root_logger.addHandler(default_console)
    
    return root_logger