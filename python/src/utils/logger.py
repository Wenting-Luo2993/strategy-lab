# src/utils/logger.py
import logging
import os
from pathlib import Path

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
    
    # Add console handler if requested
    if log_to_console:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(console_formatter)
        console_handler.setLevel(level)
        logger.addHandler(console_handler)
    
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
    
    # Add console handler if requested
    if log_to_console:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(console_formatter)
        console_handler.setLevel(level)
        root_logger.addHandler(console_handler)
    
    return root_logger