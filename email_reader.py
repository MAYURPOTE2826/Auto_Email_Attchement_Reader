import imaplib
import os
from config import Config
from logger import log_info


class CredentialError(Exception):
    """Raised when the IMAP server explicitly rejects the login credentials.

    Separating this from generic network / connection errors lets the main
    loop treat it as a fatal condition (stop immediately, alert operator)
    rather than a transient failure worth retrying.
    """


def connect_mail() -> imaplib.IMAP4_SSL:
    """Connect to the IMAP server and select the configured mailbox.

    Credentials are read fresh from the environment on every call rather than
    from cached class attributes.  This means:
      - Rotated credentials take effect without a process restart.
      - The password string is a local variable that becomes eligible for GC
        as soon as this function returns, instead of living as a class attribute
        for the entire process lifetime.

    CPython does not guarantee memory-level zeroing of string objects, so this
    does not eliminate the risk of a memory dump recovering the password.  The
    production-grade upgrade path is OAuth2 (google-auth library), which
    replaces the permanent app password with short-lived access tokens (~1 h TTL).

    The socket timeout is set per-connection using the timeout= parameter so it
    never mutates the process-wide default socket timeout (which would silently
    affect every other socket — SMTP alerts, third-party libraries, etc.).

    Errors are NOT logged here — the caller (main.py) is the single log point
    for connection failures, which avoids duplicate log entries for the same event.

    Raises:
        CredentialError: IMAP server rejected the username / password.
        Exception: Any other connection or mailbox-selection failure.
    """
    user = os.getenv("EMAIL_USER", "").strip()
    pw   = os.getenv("EMAIL_PASS", "").strip()

    if not user or not pw:
        raise CredentialError("Email credentials missing — set EMAIL_USER and EMAIL_PASS in .env")

    try:
        mail = imaplib.IMAP4_SSL(
            Config.IMAP_SERVER,
            Config.IMAP_PORT,
            timeout=Config.SOCKET_TIMEOUT_SEC,   # connection-scoped; no global side effect
        )
    except Exception:
        raise   # caller logs with full traceback via log_exception

    try:
        mail.login(user, pw)
    except imaplib.IMAP4.error as exc:
        # IMAP4.error is raised by imaplib for protocol-level failures
        # including [AUTHENTICATIONFAILED] and [ALERT] Bad credentials.
        raise CredentialError(str(exc)) from exc
    finally:
        # Explicitly delete local references so the password string (and the
        # email address) have no named references remaining after login completes.
        # CPython's reference-counting GC will then deallocate them promptly.
        del pw
        del user

    try:
        status, data = mail.select(Config.ATTACHMENT_INBOX)
        if status != 'OK':
            raise Exception(f"Cannot open mailbox '{Config.ATTACHMENT_INBOX}': {data}")
    except Exception:
        raise   # caller logs with full traceback via log_exception

    log_info(f"Connected to {Config.IMAP_SERVER}:{Config.IMAP_PORT}")
    return mail
