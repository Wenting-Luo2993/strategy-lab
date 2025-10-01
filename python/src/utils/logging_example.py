# Example of how to initialize logging in your main application
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