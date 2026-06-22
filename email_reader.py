from __future__ import annotations

import imaplib
import os
from config import Config
from logger import log_info

# ---------------------------------------------------------------------------
# IMAP-specific exception hierarchy
#
# CredentialError  — server rejected the login explicitly (fatal, no retry).
# QuotaError       — server throttled or quota-limited the connection
#                    (transient, long back-off but keep running).
#
# Both are raised only by connect_mail() so the caller can react differently
# without parsing error strings in multiple places.
# ---------------------------------------------------------------------------

class CredentialError(Exception):
    """Raised when the IMAP server explicitly rejects the login credentials.

    Separating this from generic network / connection errors lets the main
    loop treat it as a fatal condition (stop immediately, alert operator)
    rather than a transient failure worth retrying.
    """


class QuotaError(Exception):
    """Raised when the IMAP server rate-limits or quota-throttles the connection.

    Gmail and many other providers return OVERQUOTA, [THROTTLED], or similar
    responses when too many connections are opened in a short window.  This is
    always transient — the right response is a long back-off (5 min), NOT to
    increment the consecutive-failure counter that eventually stops the app.

    Keeping this distinct from CredentialError and generic Exception lets
    main.py apply the correct handling (back-off without incrementing
    conn_error_count) from a single well-named except clause.
    """


# Keywords found in IMAP error responses that indicate rate-limiting.
# Checked case-insensitively.  Centralised here so both connect_mail()
# and any future callers share one authoritative list.
_QUOTA_KEYWORDS: frozenset[str] = frozenset({
    'OVERQUOTA',
    'THROTTLED',
    'TOO MANY',
    'TOOMANYSIMULTANEOUS',
    'RATELIMIT',
    'RATE LIMIT',
    'SLOW DOWN',
})

# Keywords that definitively indicate the server rejected the credentials.
# Only these trigger a fatal CredentialError (stop + alert).  Every other
# IMAP4.error during login — [UNAVAILABLE], [INUSE], [SERVERBUG], capacity
# errors — is transient and should be retried with exponential backoff.
#
# Why this matters: before this fix, a Gmail [UNAVAILABLE] response caused
# an immediate fatal stop and alert email at exactly the moment IMAP was
# having a hiccup.  Operators got woken for a problem that resolves itself.
_CREDENTIAL_KEYWORDS: frozenset[str] = frozenset({
    'AUTHENTICATIONFAILED',
    'AUTHENTICATION FAILED',
    'INVALID CREDENTIALS',
    'INVALID LOGIN',
    'USERNAME OR PASSWORD',
    '[AUTH]',
    'LOGIN FAILED',
    'AUTHENTICATE',
})


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
        QuotaError:      IMAP server returned a rate-limit / quota response.
        Exception:       Any other connection or mailbox-selection failure.
    """
    user = os.getenv("EMAIL_USER", "").strip()
    pw   = os.getenv("EMAIL_PASS", "").strip()

    if not user or not pw:
        raise CredentialError("Email credentials missing — set EMAIL_USER and EMAIL_PASS in .env")

    mail = imaplib.IMAP4_SSL(
        Config.IMAP_SERVER,
        Config.IMAP_PORT,
        timeout=Config.SOCKET_TIMEOUT_SEC,   # connection-scoped; no global side effect
    )
    # Connection errors (socket timeout, TLS failure, DNS) propagate to caller.

    try:
        mail.login(user, pw)
    except imaplib.IMAP4.error as exc:
        # Classify the IMAP4.error so the caller applies the right recovery path.
        exc_upper = str(exc).upper()

        if any(kw in exc_upper for kw in _QUOTA_KEYWORDS):
            raise QuotaError(str(exc)) from exc

        # Only raise CredentialError when the server explicitly says the
        # username/password is wrong.  Transient server errors ([UNAVAILABLE],
        # [INUSE], [SERVERBUG], capacity limits) must NOT be treated as fatal —
        # they should fall through as a plain IMAP4.error and be retried by the
        # main loop's exponential-backoff path.
        if any(kw in exc_upper for kw in _CREDENTIAL_KEYWORDS):
            raise CredentialError(str(exc)) from exc

        # Transient server-side error — let the main loop retry with backoff.
        raise
    finally:
        # Explicitly delete local references so the password string (and the
        # email address) have no named references remaining after login completes.
        # CPython's reference-counting GC will then deallocate them promptly.
        del pw
        del user

    status, data = mail.select(Config.ATTACHMENT_INBOX)
    if status != 'OK':
        raise Exception(f"Cannot open mailbox '{Config.ATTACHMENT_INBOX}': {data}")
    # Mailbox / connection errors propagate to caller for unified logging.

    log_info(f"Connected to {Config.IMAP_SERVER}:{Config.IMAP_PORT}")
    return mail
