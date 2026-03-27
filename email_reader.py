import imaplib
import os
import socket
from dotenv import load_dotenv
from logger import log_info, log_error

# Load .env
load_dotenv()

socket.setdefaulttimeout(30)

EMAIL_USER = os.getenv("EMAIL_USER")
EMAIL_PASS = os.getenv("EMAIL_PASS")


def connect_mail():
    try:
        # ✅ validation
        if not EMAIL_USER or not EMAIL_PASS:
            raise Exception("Email credentials missing in .env")

        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(EMAIL_USER.strip(), EMAIL_PASS.strip())
        mail.select("inbox")

        log_info("Connected to email server")
        return mail

    except Exception as e:
        log_error(f"Connection Error: {str(e)}")
        raise