#src/agentcy/logger_config.py
import logging
from logging.handlers import TimedRotatingFileHandler
import os
from pythonjsonlogger import jsonlogger

class HybridRotatingFileHandler(TimedRotatingFileHandler):

    def __init__(self, filename, when="D", interval=1, backupCount=7, maxBytes=0, encoding=None, delay=False, utc=False):
        self.maxBytes = maxBytes
        super().__init__(filename, when, interval, backupCount, encoding, delay, utc)

    def shouldRollover(self, record):
        time_rollover = super().shouldRollover(record)

        # Check size-based rollover.
        size_rollover = False
        if self.maxBytes > 0:
            if self.stream is None:  # if delay is True, ensure the stream is open
                self.stream = self._open()
            self.stream.seek(0, os.SEEK_END)
            current_size = self.stream.tell()
            size_rollover = current_size >= self.maxBytes

        return time_rollover or size_rollover


def configure_file_logger(log_level=logging.INFO, log_dir=None, log_filename="app.log", maxBytes=10*1024*1024):
    # Use the provided log directory or default to an absolute path.
    if log_dir is None:
        home_dir = os.path.expanduser("~")
        log_dir = os.path.join(home_dir, "logs", "agentcy")
    
    # Ensure the log directory exists.
    os.makedirs(log_dir, exist_ok=True)

    logger = logging.getLogger()
    logger.setLevel(log_level)

    # Prevent duplicate handlers.
    if not any(isinstance(h, logging.FileHandler) for h in logger.handlers):
        file_path = os.path.join(log_dir, log_filename)
        file_handler = logging.FileHandler(file_path)
        formatter = jsonlogger.JsonFormatter('%(asctime)s %(levelname)s %(name)s %(message)s') #type: ignore
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
    
    logger.info("File logger configured", extra={"service": "agentcy"})
