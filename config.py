"""
Configuration management for Email Attachment Processor
Reads settings from environment variables and .env file.

Precedence (highest to lowest):
  1. Environment variables set at the OS / container / systemd level
  2. Values in .env file (fallback for local development only)
"""

import os
import warnings
from dotenv import load_dotenv

# Load .env as FALLBACK only — pre-set system env vars are never overridden.
# In Docker / Kubernetes / Windows Task Scheduler, inject secrets via the
# environment directly.  Never rely on .env in production.
load_dotenv()

# Directory containing this file — used to anchor relative paths so the app
# works correctly regardless of the working directory (e.g. Task Scheduler
# launches from C:\Windows\System32, not the project folder).
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))

_VALID_LOG_LEVELS = frozenset({'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'})


def _int(name, default, *, min_val=None, max_val=None):
    """Read an integer env var with optional range validation.

    Gives a clear ValueError at import time (not deep inside the loop) if the
    value is missing, non-numeric, or outside the allowed range.
    """
    val = os.getenv(name, str(default))
    try:
        result = int(val)
    except ValueError:
        raise ValueError(f"Config error: {name}={val!r} must be a valid integer")
    if min_val is not None and result < min_val:
        raise ValueError(f"Config error: {name}={result} must be >= {min_val}")
    if max_val is not None and result > max_val:
        raise ValueError(f"Config error: {name}={result} must be <= {max_val}")
    return result


def _abspath(raw):
    """Return *raw* unchanged if already absolute; otherwise anchor it to _BASE_DIR.

    This ensures DOWNLOAD_FOLDER and DATABASE_PATH are always found at a
    predictable location even when the process working directory is unknown
    (e.g. Windows Task Scheduler, shell launched from a different directory).
    """
    return raw if os.path.isabs(raw) else os.path.join(_BASE_DIR, raw)


class Config:
    """Production configuration — all values come from environment variables."""

    # ------------------------------------------------------------------ #
    # Email credentials (required — no defaults)
    # EMAIL_USER is kept as a class attribute (it is an email address, not a
    # secret).  EMAIL_PASS is intentionally NOT cached here — it is read fresh
    # from os.getenv() at the point of use (connect_mail, send_alert) so the
    # password string is a short-lived local variable rather than a process-
    # lifetime class attribute.
    #
    # CPython does not guarantee memory zeroing for strings, so this does not
    # eliminate the risk entirely.  The production-grade upgrade is OAuth2
    # (google-auth library), which replaces the permanent app password with
    # short-lived access tokens (~1 h TTL).
    # ------------------------------------------------------------------ #
    EMAIL_USER = os.getenv("EMAIL_USER")

    # ------------------------------------------------------------------ #
    # IMAP connection
    # ------------------------------------------------------------------ #
    IMAP_SERVER = os.getenv("IMAP_SERVER", "imap.gmail.com")
    IMAP_PORT   = _int("IMAP_PORT", 993, min_val=1, max_val=65535)

    # ------------------------------------------------------------------ #
    # Email processing
    # ------------------------------------------------------------------ #
    MAX_EMAIL_SIZE_MB  = _int("MAX_EMAIL_SIZE_MB", 20, min_val=1)
    SOCKET_TIMEOUT_SEC = _int("SOCKET_TIMEOUT_SEC", 30, min_val=1, max_val=300)
    ATTACHMENT_INBOX   = os.getenv("ATTACHMENT_INBOX", "inbox")

    # Set to "false" when using a non-Gmail IMAP server (Outlook, Yahoo,
    # self-hosted, etc.) — Gmail's proprietary X-GM-LABELS extension is
    # skipped entirely, so processing works on any RFC-compliant IMAP server.
    USE_GMAIL_LABELS = os.getenv("USE_GMAIL_LABELS", "true").lower() == "true"

    # ------------------------------------------------------------------ #
    # File management — anchored to _BASE_DIR so paths are CWD-independent
    # ------------------------------------------------------------------ #
    DOWNLOAD_FOLDER = _abspath(os.getenv("DOWNLOAD_FOLDER", "attachments"))

    # Lowered from 1000: 100 attempts is generous and avoids iterating
    # thousands of filesystem stats when many identically-named files accumulate.
    MAX_FILENAME_ATTEMPTS = _int("MAX_FILENAME_ATTEMPTS", 100, min_val=1, max_val=10000)
    MIN_FREE_DISK_MB      = _int("MIN_FREE_DISK_MB", 500, min_val=50)

    # ------------------------------------------------------------------ #
    # Database — anchored to _BASE_DIR so path is CWD-independent
    # ------------------------------------------------------------------ #
    DATABASE_PATH = _abspath(os.getenv("DATABASE_PATH", "processed_emails.db"))

    # ------------------------------------------------------------------ #
    # Logging
    # ------------------------------------------------------------------ #
    LOG_FILE         = _abspath(os.getenv("LOG_FILE", "app.log"))
    LOG_MAX_BYTES    = _int("LOG_MAX_BYTES", 5 * 1024 * 1024, min_val=65536)   # min 64 KB
    LOG_BACKUP_COUNT = _int("LOG_BACKUP_COUNT", 3, min_val=0, max_val=20)

    # Validate LOG_LEVEL at config time — invalid values silently fall back to INFO
    # so the application never fails to start over a misconfigured log level.
    # _log_level is deleted immediately to avoid polluting the class namespace.
    _log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    LOG_LEVEL  = _log_level if _log_level in _VALID_LOG_LEVELS else "INFO"
    del _log_level

    # Emit one JSON object per log line for ELK / Datadog / CloudWatch ingestion.
    # Set LOG_JSON=true in .env (or the environment) to enable.
    LOG_JSON = os.getenv("LOG_JSON", "false").lower() == "true"

    # ------------------------------------------------------------------ #
    # Application behaviour
    # ------------------------------------------------------------------ #
    CHECK_INTERVAL_SEC = _int("CHECK_INTERVAL_SEC", 30, min_val=5)
    MAX_RETRIES        = _int("MAX_RETRIES", 5, min_val=1)
    RETRY_WAIT_SEC     = _int("RETRY_WAIT_SEC", 10, min_val=1)

    # Per-email retry budget: how many additional attempts to make before
    # permanently marking an email 'failed'.  Default 3 → up to 4 total attempts.
    # Set to 0 to mark an email failed immediately on the first processing error.
    MAX_EMAIL_RETRIES = _int("MAX_EMAIL_RETRIES", 3, min_val=0, max_val=10)

    # ------------------------------------------------------------------ #
    # Alerting (optional)
    # ------------------------------------------------------------------ #
    ALERT_EMAIL     = os.getenv("ALERT_EMAIL")
    # Configurable SMTP relay — defaults to Gmail; change for non-Gmail senders
    ALERT_SMTP_HOST = os.getenv("ALERT_SMTP_HOST", "smtp.gmail.com")
    ALERT_SMTP_PORT = _int("ALERT_SMTP_PORT", 465, min_val=1, max_val=65535)

    # ------------------------------------------------------------------ #
    # MIME bomb protection
    # A legitimate email rarely has more than 20–30 MIME parts; 200 is generous.
    # Raise only if your workflow involves intentionally complex MIME structures.
    # ------------------------------------------------------------------ #
    MAX_MIME_PARTS = _int("MAX_MIME_PARTS", 200, min_val=1)

    # ------------------------------------------------------------------ #
    # Security — sender allowlist and attachment extension blocklist
    # ------------------------------------------------------------------ #
    # ALLOWED_SENDERS: comma-separated permitted sender addresses.
    # Empty (default) = accept from any sender — backward compatible.
    # Example: ALLOWED_SENDERS=boss@company.com,accounts@vendor.com
    ALLOWED_SENDERS: frozenset = frozenset(
        s.strip().lower()
        for s in os.getenv("ALLOWED_SENDERS", "").split(",")
        if s.strip()
    )

    # BLOCKED_EXTENSIONS: comma-separated extensions that are NEVER saved.
    #
    # Archive formats (.zip, .7z, .rar, etc.) are blocked by default because
    # they are the #1 ransomware delivery channel in enterprise email.
    # If your workflow genuinely requires receiving ZIP files, remove them
    # from BLOCKED_EXTENSIONS in your .env file and document why.
    #
    # Override in .env to tighten or loosen the list for your environment.
    BLOCKED_EXTENSIONS: frozenset = frozenset(
        e.strip().lower()
        for e in os.getenv("BLOCKED_EXTENSIONS", (
            ".exe,.bat,.cmd,.com,.pif,.scr,.vbs,.vbe,.js,.jse"
            ",.ws,.wsh,.hta,.ps1,.psm1,.psd1,.reg,.msi,.jar,.msc,.lnk,.dll,.cpl"
            ",.zip,.7z,.rar,.tar,.gz,.bz2,.xz,.iso,.img,.dmg"
        )).split(",")
        if e.strip()
    )

    # BLOCKED_MIME_TYPES: Content-Type values that are always rejected.
    #
    # Defence-in-depth: an attacker who renames malware.exe → report.pdf may
    # still advertise the real Content-Type.  This catches that case even when
    # the extension passes through.
    BLOCKED_MIME_TYPES: frozenset = frozenset(
        m.strip().lower()
        for m in os.getenv("BLOCKED_MIME_TYPES", (
            "application/x-msdownload,application/x-executable"
            ",application/x-sh,application/x-bat,application/x-msdos-program"
            ",application/x-dosexec,application/x-winexe,application/x-java-archive"
        )).split(",")
        if m.strip()
    )

    @staticmethod
    def validate():
        """Validate required configuration — raises ValueError on missing fields.

        Also emits a warning (not an error) for non-fatal misconfiguration such
        as an unrecognised LOG_LEVEL so the operator sees it in the log without
        the application refusing to start.
        """
        if not Config.EMAIL_USER or not os.getenv("EMAIL_PASS"):
            raise ValueError("EMAIL_USER and EMAIL_PASS must be set in .env file")

        if not (Config.ATTACHMENT_INBOX or "").strip():
            raise ValueError(
                "ATTACHMENT_INBOX cannot be empty — set it to 'inbox' or "
                "the mailbox name to select (e.g. 'INBOX')"
            )

        if not (Config.IMAP_SERVER or "").strip():
            raise ValueError(
                "IMAP_SERVER cannot be empty — set it to your IMAP hostname "
                "(e.g. 'imap.gmail.com')"
            )

        raw_level = os.getenv("LOG_LEVEL", "INFO").upper()
        if raw_level not in _VALID_LOG_LEVELS:
            warnings.warn(
                f"LOG_LEVEL={raw_level!r} is not a valid Python log level — "
                f"falling back to INFO.  Valid choices: {sorted(_VALID_LOG_LEVELS)}",
                stacklevel=2,
            )

        return True
