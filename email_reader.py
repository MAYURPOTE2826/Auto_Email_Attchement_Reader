import imaplib
import socket
from config import Config
from logger import log_info, log_error

# Set socket timeout from config
socket.setdefaulttimeout(Config.SOCKET_TIMEOUT_SEC)


def connect_mail():
    """Connect to IMAP server with credentials from config"""
    try:
        if not Config.EMAIL_USER or not Config.EMAIL_PASS:
            raise Exception("Email credentials missing in .env")

        mail = imaplib.IMAP4_SSL(Config.IMAP_SERVER, Config.IMAP_PORT)
        mail.login(Config.EMAIL_USER.strip(), Config.EMAIL_PASS.strip())
        mail.select(Config.ATTACHMENT_INBOX)

        log_info(f"Connected to {Config.IMAP_SERVER}:{Config.IMAP_PORT}")
        return mail

    except Exception as e:
        log_error(f"Connection Error: {str(e)}")
        raise