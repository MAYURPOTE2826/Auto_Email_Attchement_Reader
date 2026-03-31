import logging
from logging.handlers import RotatingFileHandler
from config import Config

# Configure rotating file handler with settings from config
handler = RotatingFileHandler(
    Config.LOG_FILE,
    maxBytes=Config.LOG_MAX_BYTES,
    backupCount=Config.LOG_BACKUP_COUNT
)

formatter = logging.Formatter(
    "%(asctime)s - %(levelname)s - %(message)s"
)

handler.setFormatter(formatter)

logger = logging.getLogger()
logger.setLevel(Config.LOG_LEVEL)
logger.addHandler(handler)


def log_info(msg):
    logger.info(msg)


def log_error(msg):
    logger.error(msg)