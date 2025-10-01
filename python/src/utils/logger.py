# src/utils/logger.py
import logging
import os
from pathlib import Path

# Create logs directory if it doesn't exist
logs_dir = Path(os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "logs"))
logs_dir.mkdir(exist_ok=True)

def get_logger(name, level=logging.DEBUG):
    """
    Returns a configured logger instance that only logs to file (no console output)
    
    Args:
        name: Name of the logger (typically module name)
        level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        
    Returns:
        Logger instance
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)
    
    # Clear existing handlers to avoid duplicates when this function is called multiple times
    if logger.hasHandlers():
        logger.handlers.clear()
    
    # Create formatter with detailed information
    file_formatter = logging.Formatter('%(asctime)s [%(name)s] [%(levelname)s] %(message)s')
    
    # Add file handler (no console handler)
    log_file = logs_dir / f"{name}.log"
    file_handler = logging.FileHandler(log_file)
    file_handler.setFormatter(file_formatter)
    file_handler.setLevel(level)
    logger.addHandler(file_handler)
    
    return logger

# Configure root logger for general application logging
def setup_root_logger(level=logging.INFO):
    """
    Configure the root logger with only file handler (no console output)
    """
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    
    # Clear existing handlers to avoid duplicates
    if root_logger.hasHandlers():
        root_logger.handlers.clear()
    
    # File handler (for all logs)
    all_logs_file = logs_dir / "application.log"
    file_handler = logging.FileHandler(all_logs_file)
    file_handler.setFormatter(logging.Formatter('%(asctime)s [%(name)s] [%(levelname)s] %(message)s'))
    file_handler.setLevel(level)
    root_logger.addHandler(file_handler)
    
    return root_logger