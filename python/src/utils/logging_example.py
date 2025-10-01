# Example of how to initialize logging in your application
from src.utils.logger import setup_root_logger, get_logger

def initialize_logging(enable_console=False):
    """
    Initialize the logging system for the application.
    Call this function at the start of your application.
    
    Args:
        enable_console: Whether to output logs to console (default: False)
    """
    # Setup the root logger
    setup_root_logger(log_to_console=enable_console)
    
    # Example of different logger configurations:
    
    # 1. File-only logger (default)
    file_logger = get_logger("FileOnly")
    file_logger.info("This message only goes to the log file")
    
    # 2. Console-only logger
    console_logger = get_logger("ConsoleOnly", log_to_console=True, log_to_file=False)
    console_logger.info("This message only goes to the console")
    
    # 3. Both file and console logger
    dual_logger = get_logger("DualLogger", log_to_console=True, log_to_file=True)
    dual_logger.info("This message goes to both the log file and console")
    
    # Usage example:
    # logger.debug("Debug message")
    # logger.info("Info message")
    # logger.warning("Warning message")
    # logger.error("Error message")
    # logger.critical("Critical message")o initialize logging in your main application
from src.utils.logger import setup_root_logger

def initialize_logging():
    """
    Initialize the logging system for the application.
    Call this function at the start of your application.
    """
    # Setup the root logger
    setup_root_logger()
    
    # You can also import and use the logger directly in any module:
    # from src.utils.logger import get_logger
    # logger = get_logger("ModuleName")
    # logger.debug("Debug message")
    # logger.info("Info message")
    # logger.warning("Warning message")
    # logger.error("Error message")