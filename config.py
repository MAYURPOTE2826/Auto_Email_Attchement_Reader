"""
Configuration management for Email Attachment Processor
Reads settings from environment variables and .env file
"""

import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()


class Config:
    """Production configuration"""
    
    # Email Settings
    EMAIL_USER = os.getenv("EMAIL_USER")
    EMAIL_PASS = os.getenv("EMAIL_PASS")
    IMAP_SERVER = os.getenv("IMAP_SERVER", "imap.gmail.com")
    IMAP_PORT = int(os.getenv("IMAP_PORT", "993"))
    
    # Email Processing
    MAX_EMAIL_SIZE_MB = int(os.getenv("MAX_EMAIL_SIZE_MB", "20"))
    SOCKET_TIMEOUT_SEC = int(os.getenv("SOCKET_TIMEOUT_SEC", "30"))
    ATTACHMENT_INBOX = os.getenv("ATTACHMENT_INBOX", "inbox")
    
    # File Management
    DOWNLOAD_FOLDER = os.getenv("DOWNLOAD_FOLDER", "attachments")
    MAX_FILENAME_ATTEMPTS = int(os.getenv("MAX_FILENAME_ATTEMPTS", "1000"))
    
    # Database
    DATABASE_PATH = os.getenv("DATABASE_PATH", "processed_emails.db")
    
    # Logging
    LOG_FILE = os.getenv("LOG_FILE", "app.log")
    LOG_MAX_BYTES = int(os.getenv("LOG_MAX_BYTES", str(5 * 1024 * 1024)))  # 5MB
    LOG_BACKUP_COUNT = int(os.getenv("LOG_BACKUP_COUNT", "3"))
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
    
    # Application
    CHECK_INTERVAL_SEC = int(os.getenv("CHECK_INTERVAL_SEC", "30"))
    MAX_RETRIES = int(os.getenv("MAX_RETRIES", "5"))
    RETRY_WAIT_SEC = int(os.getenv("RETRY_WAIT_SEC", "10"))
    
    @staticmethod
    def validate():
        """Validate required configuration"""
        if not Config.EMAIL_USER or not Config.EMAIL_PASS:
            raise ValueError("EMAIL_USER and EMAIL_PASS must be set in .env file")
        
        if not os.path.exists(Config.DOWNLOAD_FOLDER):
            os.makedirs(Config.DOWNLOAD_FOLDER)
        
        return True
