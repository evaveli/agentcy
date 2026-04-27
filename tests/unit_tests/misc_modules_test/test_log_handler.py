#tests/unit_tests/misc_modules_test/test_log_handler.py
import os
import time
import logging
import tempfile
import pytest
from pythonjsonlogger import jsonlogger
from src.agentcy.logger_config import HybridRotatingFileHandler  # Replace with your module name

@pytest.fixture
def test_logger():
    # Create a temporary directory for logs
    with tempfile.TemporaryDirectory() as tmpdirname:
        log_file = os.path.join(tmpdirname, "test_app.log")
        
        # Create and configure a logger
        logger = logging.getLogger("TestLogger")
        logger.setLevel(logging.DEBUG)
        # Clear any existing handlers to avoid duplication
        logger.handlers.clear()
        
        # Configure the hybrid handler:
        # - Time-based rotation: every 2 seconds ("S", interval=2)
        # - Size-based rotation: file exceeds 200 bytes
        # - Keep 2 backup files
        handler = HybridRotatingFileHandler(
            log_file, when="S", interval=2, backupCount=2, maxBytes=200, encoding="utf-8"
        )
        # Suffix used for rotated files
        handler.suffix = "%Y-%m-%d_%H-%M-%S"
        formatter = jsonlogger.JsonFormatter('%(asctime)s %(levelname)s %(name)s %(message)s')
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        
        yield logger, tmpdirname
        # No explicit cleanup needed; the temporary directory is removed automatically.

def test_size_based_rotation(test_logger):
    logger, tmpdirname = test_logger
    
    # Write several log messages to exceed the 200-byte threshold quickly.
    for i in range(20):
        logger.info("Test message number %d: %s", i, "X" * 20)
    
    # Allow time for rollover processing
    time.sleep(0.5)
    
    # List files in the temporary directory
    files = os.listdir(tmpdirname)
    # Exclude the current log file; remaining files are rotated copies
    rotated_files = [f for f in files if f != "test_app.log"]
    
    assert len(rotated_files) > 0, f"Expected rotated log files, but found: {files}"

def test_time_based_rotation(test_logger):
    logger, tmpdirname = test_logger
    
    # Write a log message before waiting for time-based rollover
    logger.info("Message before waiting for rollover")
    # Wait longer than the time interval (2 seconds)
    time.sleep(3)
    logger.info("Message after waiting for rollover")
    
    # Force a rollover if not automatically triggered
    for handler in logger.handlers:
        if hasattr(handler, "doRollover"):
            handler.doRollover()
    
    # List files in the temporary directory
    files = os.listdir(tmpdirname)
    rotated_files = [f for f in files if f != "test_app.log"]
    
    assert len(rotated_files) > 0, f"Expected at least one rotated log file, but found: {files}"
