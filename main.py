from __future__ import annotations

import os
import re
import sys
import time
import shutil
import signal
import argparse
import smtplib
import threading
import atexit
from email.mime.text import MIMEText
from config import Config
from db import (
    # Internal helpers used by the main loop
    _db_conn, _decode_id, init_db, get_email_record,
    mark_in_progress, save_processed, mark_retry, mark_failed, reset_failed_emails,
    # Re-exported so the test suite can import them from main (backward compat)
    get_email_status, get_retry_count,
)
from email_reader import connect_mail, CredentialError, QuotaError
from attachment_handler import process_email
from logger import (
    log_debug, log_info, log_warning, log_error, log_exception,
    set_correlation_id, clear_correlation_id,
)

# ---------------------------------------------------------------------------
# Heartbeat — absolute path so the file is always found regardless of the
# working directory the process was started from (Task Scheduler, shell, etc.)
# ---------------------------------------------------------------------------
_HEARTBEAT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "heartbeat.txt")
_LOCKFILE   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.lock")

# ---------------------------------------------------------------------------
# Graceful shutdown — set by signal handler, checked in the main loop
# ---------------------------------------------------------------------------
_shutdown = threading.Event()


def _handle_signal(signum, frame):
    # Only async-signal-safe operations here.
    # log_info() is NOT safe — logging uses a threading.RLock; calling it from a
    # signal handler can deadlock if the main thread is already holding that lock.
    # print() to stdout is safe.  _shutdown.set() is safe (threading.Event).
    print("\n Shutdown requested — stopping after current email...", flush=True)
    _shutdown.set()


# ---------------------------------------------------------------------------
# Instance singleton lock — prevents two processes from running simultaneously
# ---------------------------------------------------------------------------

_lock_fd = None

def _acquire_instance_lock() -> None:
    """Prevent two instances of the processor from running simultaneously.

    Uses an OS-level file lock which is automatically released by the kernel
    if the process crashes or is killed. This eliminates the PID recycling
    deadlock present in the previous implementation.

    Raises:
        SystemExit: if another live instance is currently holding the lock.
    """
    global _lock_fd
    try:
        _lock_fd = os.open(_LOCKFILE, os.O_CREAT | os.O_RDWR)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(_lock_fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(_lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        raise SystemExit(
            "Another instance of Email Assetcues is already running. "
            f"If you are sure it is not, check the lock file at {_LOCKFILE}"
        )


# ---------------------------------------------------------------------------
# IMAP quota / rate-limit detection
#
# A compiled regex is used instead of a frozenset so we can match multi-word
# phrases ('TOO MANY', 'RATE LIMIT') without splitting on spaces first.
# Checked against the string representation of any unclassified Exception that
# reaches the outer except handler in the main loop — catches quota responses
# from SEARCH and FETCH operations (which happen inside the try block, not
# inside connect_mail, so QuotaError is not raised for them).
# ---------------------------------------------------------------------------
_QUOTA_RE = re.compile(
    r'OVERQUOTA|THROTTLED|TOO.MANY|TOOMANYSIMULTANEOUS|RATE.?LIMIT|SLOW.DOWN',
    re.IGNORECASE,
)

# How long to back off when a quota / throttle response is detected.
# 5 minutes is the standard recommendation for Gmail IMAP quota recovery.
_QUOTA_BACKOFF_SEC = 300

# ---------------------------------------------------------------------------
# Alert body sanitisation
#
# error_msg = str(e) is embedded verbatim in alert emails.  A future imaplib
# or smtplib version could include auth details in an exception string.
# Redact anything that looks like a Gmail app-password (four 4-letter groups
# separated by spaces) before it leaves the process in an email body.
# ---------------------------------------------------------------------------
_CRED_RE = re.compile(
    r'\b[a-z]{4}(?:\s[a-z]{4}){3}\b',
    re.IGNORECASE,
)


def _safe_error(msg: str) -> str:
    """Redact potential credential strings from an error message."""
    return _CRED_RE.sub('[REDACTED]', msg)


# ---------------------------------------------------------------------------
# Alerting
# ---------------------------------------------------------------------------

def send_alert(subject: str, body: str) -> None:
    """Send an alert email when the app encounters a fatal problem.

    Uses a separate try/except so a broken alert path never crashes the main
    loop.  SMTP failure is logged and swallowed intentionally.

    Credential resolution order (both read fresh from the environment):
      1. ALERT_SMTP_USER / ALERT_SMTP_PASS  — dedicated alert account.
      2. EMAIL_USER / EMAIL_PASS            — fallback when no dedicated
                                             account is configured.

    Using a dedicated alert account (ALERT_SMTP_USER) is strongly recommended:
    send_alert() is also called when IMAP credentials fail.  If both IMAP and
    SMTP share the same credentials, the alert silently fails at exactly the
    moment it is most needed.

    A SOCKET_TIMEOUT_SEC timeout is applied to the SMTP connection so a
    blocked or unreachable SMTP host never stalls the main loop for minutes.

    Credentials are blanked in a finally block to ensure the local references
    are removed even when smtp.send_message() raises an exception.
    """
    if not Config.ALERT_EMAIL:
        return

    # Pre-initialise to empty string so 'del' in finally always finds the names
    # regardless of whether the try body raised before the assignment.
    _user = _pw = ""
    try:
        _user = (os.getenv("ALERT_SMTP_USER") or os.getenv("EMAIL_USER", "")).strip()
        _pw   = (os.getenv("ALERT_SMTP_PASS") or os.getenv("EMAIL_PASS", "")).strip()

        msg = MIMEText(body, 'plain', 'utf-8')
        msg['Subject'] = subject
        msg['From'] = _user
        msg['To']   = Config.ALERT_EMAIL

        if Config.ALERT_USE_STARTTLS:
            with smtplib.SMTP(
                Config.ALERT_SMTP_HOST,
                Config.ALERT_SMTP_PORT,
                timeout=Config.SOCKET_TIMEOUT_SEC,
            ) as smtp:
                smtp.ehlo()
                smtp.starttls()
                smtp.login(_user, _pw)
                smtp.send_message(msg)
        else:
            with smtplib.SMTP_SSL(
                Config.ALERT_SMTP_HOST,
                Config.ALERT_SMTP_PORT,
                timeout=Config.SOCKET_TIMEOUT_SEC,
            ) as smtp:
                smtp.login(_user, _pw)
                smtp.send_message(msg)

        log_info(f"Alert sent to {Config.ALERT_EMAIL}")
    except Exception as e:
        log_error(f"Failed to send alert email: {e}")
    finally:
        # Remove local references on all exit paths — including exceptions.
        # CPython's refcount GC then deallocates the string objects promptly.
        del _pw, _user


# ---------------------------------------------------------------------------
# Main application loop
# ---------------------------------------------------------------------------

def main(retry_failed: bool = False) -> int:
    """Main application loop.

    Returns:
        0  — clean / expected exit (graceful shutdown, no emails left to retry).
        1  — fatal exit (invalid credentials, max retries exhausted).
    """
    # Reset the shutdown event so that calling main() a second time in the same
    # process (test suite, interactive shell) does not exit the loop immediately
    # because a previous Ctrl+C left the event set.
    _shutdown.clear()

    # Register signal handlers inside main() so that importing this module
    # (e.g. in the test suite) does not overwrite pytest's own SIGINT handler.
    signal.signal(signal.SIGINT, _handle_signal)
    try:
        signal.signal(signal.SIGTERM, _handle_signal)
    except (OSError, AttributeError):
        pass  # SIGTERM not fully supported on Windows — SIGINT (Ctrl+C) still works

    try:
        Config.validate()
    except ValueError as e:
        log_error(f"Configuration error: {e}")
        return 1

    # Prevent duplicate instances from competing for the same IMAP connection
    # and DB records.  Exits immediately with a clear message if another instance
    # is already running.
    _acquire_instance_lock()

    os.makedirs(Config.DOWNLOAD_FOLDER, exist_ok=True)
    init_db()

    if retry_failed:
        reset_failed_emails()

    # conn_error_count: consecutive connection failures — resets to 0 on any
    # successful cycle.  When it reaches MAX_RETRIES the loop stops.
    conn_error_count = 0

    # cumulative_errors: lifetime total connection failures this session —
    # never resets.  Used to fire a degraded-state alert when the system
    # exhibits a high overall failure rate even if no single consecutive streak
    # hits MAX_RETRIES (alternating success / fail scenario).
    cumulative_errors = 0

    # One-shot flag: the degraded-state alert fires the first time cumulative
    # errors reaches the threshold — without this, >= would spam alerts on
    # every subsequent failure.
    _degraded_alert_sent = False

    # The failure exit is an explicit `return 1` inside the except block once
    # conn_error_count reaches MAX_RETRIES — the while condition itself is only
    # responsible for honouring a graceful-shutdown signal.
    while not _shutdown.is_set():
        mail = None

        try:
            log_info("Starting email check cycle")

            mail = connect_mail()

            # ---------------------------------------------------------------- #
            # Cycle-level disk guard — skip the entire cycle when disk is full.
            # ---------------------------------------------------------------- #
            free_mb = shutil.disk_usage(Config.DOWNLOAD_FOLDER).free / (1024 * 1024)
            if free_mb < Config.MIN_FREE_DISK_MB:
                log_warning(
                    f"Disk space too low ({free_mb:.1f} MB free, "
                    f"need {Config.MIN_FREE_DISK_MB} MB) — skipping cycle, "
                    f"will retry in {Config.CHECK_INTERVAL_SEC}s"
                )
                conn_error_count = 0          # not a connection error
                _shutdown.wait(timeout=Config.CHECK_INTERVAL_SEC)
                continue                       # finally block closes the connection

            search_status, messages = mail.uid('SEARCH', None, 'UNSEEN')
            if search_status != 'OK':
                raise Exception(
                    f"IMAP SEARCH failed ({search_status}): {messages}"
                )
            # Normalise to str immediately — imaplib returns byte UIDs
            # (b'123', b'456').  Decoding once here prevents bytes vs. str
            # mismatches in SQLite queries further down the pipeline where
            # _decode_id() calls could otherwise be missed.
            email_ids = [_decode_id(uid) for uid in messages[0].split()]

            log_info(f"Found {len(email_ids)} unseen emails")

            # Per-cycle cap: prevents a burst inbox (first run, IMAP scope
            # change, backlog) from running for hours and making the heartbeat
            # go stale.  Remaining emails are picked up on subsequent cycles.
            if Config.MAX_EMAILS_PER_CYCLE and len(email_ids) > Config.MAX_EMAILS_PER_CYCLE:
                log_warning(
                    f"Inbox has {len(email_ids)} unseen emails — capping this "
                    f"cycle to {Config.MAX_EMAILS_PER_CYCLE} (MAX_EMAILS_PER_CYCLE). "
                    f"Remaining {len(email_ids) - Config.MAX_EMAILS_PER_CYCLE} "
                    f"will be processed in future cycles."
                )
                email_ids = email_ids[:Config.MAX_EMAILS_PER_CYCLE]

            for e_id in email_ids:
                log_info(f"Processing Email ID: {_decode_id(e_id)}")

                # Single query returns both status and retry_count — avoids the
                # previous two-round-trip pattern (get_email_status then
                # get_retry_count) which opened and closed two SQLite connections
                # and left a brief inconsistency window between the two reads.
                try:
                    db_status, retry_count = get_email_record(e_id)
                except Exception:
                    log_error(
                        f"DB check failed for {_decode_id(e_id)} "
                        f"— skipping to avoid reprocessing"
                    )
                    continue

                if db_status == 'done':
                    log_debug(
                        f"Email {_decode_id(e_id)} already processed — skipping"
                    )
                    continue

                if db_status == 'in_progress':
                    # -------------------------------------------------------- #
                    # Crash recovery: email was left in_progress by a previous
                    # crash.  Honour the retry budget instead of immediately
                    # marking it failed — the crash itself may have been
                    # transient (OOM, power loss, network drop).
                    # -------------------------------------------------------- #
                    if retry_count < Config.MAX_EMAIL_RETRIES:
                        mark_retry(e_id, "in_progress at startup — crash recovery")
                        log_warning(
                            f"Email {_decode_id(e_id)} was in_progress (crash recovery) "
                            f"— rescheduled for retry "
                            f"(attempt {retry_count + 1}/{Config.MAX_EMAIL_RETRIES + 1})"
                        )
                    else:
                        mark_failed(
                            e_id,
                            "in_progress at startup — crash recovery, retries exhausted"
                        )
                        log_error(
                            f"Email {_decode_id(e_id)} was in_progress (crash recovery) "
                            f"— retries exhausted, marked permanently failed. "
                            f"Use --retry-failed to requeue."
                        )
                    continue

                if db_status == 'failed':
                    log_warning(
                        f"Email {_decode_id(e_id)} previously failed — skipping. "
                        f"Use --retry-failed to requeue."
                    )
                    continue

                # db_status is None (new email) or 'retry' (queued for re-attempt).
                # Guard: if the DB write fails we must skip rather than process
                # without a record — otherwise a mid-run crash produces a duplicate
                # download with no in_progress marker for crash-recovery to detect.
                try:
                    mark_in_progress(e_id)
                except Exception:
                    log_error(
                        f"Cannot record email {_decode_id(e_id)} as in_progress "
                        f"— skipping to prevent double-processing on a DB failure"
                    )
                    continue

                # Set the correlation ID for the duration of this email's processing.
                # Every log_* call made inside process_email() (and any function it
                # calls) will carry "email-<id>" in both plain-text and JSON output,
                # making it trivial to filter all activity for one email in ELK / Splunk.
                set_correlation_id(f"email-{_decode_id(e_id)}")
                last_error_msg: str | None = None
                try:
                    success = process_email(mail, e_id)
                except Exception as exc:
                    log_exception(
                        f"Unexpected error processing email {_decode_id(e_id)}: {exc}"
                    )
                    last_error_msg = str(exc)
                    success = False
                finally:
                    # Always reset — prevents the ID from leaking into the next
                    # email's log lines if an exception skips a normal code path.
                    clear_correlation_id()

                # Populate last_error_msg for the silent-False path (process_email
                # returned False without raising).  Without this, last_error = NULL
                # in the DB gives operators no clue why the email failed.
                if not success and last_error_msg is None:
                    last_error_msg = (
                        "process_email returned False — "
                        "check attachment_handler log lines for this email-id"
                    )

                if success:
                    try:
                        save_processed(e_id)
                    except Exception as db_err:
                        # Attachment files were saved successfully; only the DB
                        # write failed.  Log prominently — crash recovery will
                        # see the email still in_progress on the next run and
                        # promote it to 'retry', burning one retry slot, but no
                        # data is lost and no duplicate download occurs.
                        log_error(
                            f"Email {_decode_id(e_id)} processed but DB mark-done "
                            f"failed ({db_err}) — crash recovery will retry next run"
                        )
                    else:
                        log_info(
                            f"Email {_decode_id(e_id)} processed successfully"
                        )
                else:
                    # retry_count was fetched at the top of this loop iteration
                    # (via get_email_record) — it reflects how many attempts have
                    # already failed, which is exactly what we need here.
                    # mark_retry increments it atomically in the DB.
                    if retry_count < Config.MAX_EMAIL_RETRIES:
                        mark_retry(e_id, last_error_msg)
                        log_warning(
                            f"Email {_decode_id(e_id)} processing failed "
                            f"(attempt {retry_count + 1}/{Config.MAX_EMAIL_RETRIES + 1}) "
                            f"— scheduled for retry next cycle."
                        )
                    else:
                        mark_failed(e_id, last_error_msg)
                        log_error(
                            f"Email {_decode_id(e_id)} exhausted all "
                            f"{Config.MAX_EMAIL_RETRIES + 1} attempts "
                            f"— marked permanently failed. "
                            f"Use --retry-failed to requeue."
                        )

            # Successful cycle — reset consecutive failure counter
            conn_error_count = 0

            # Write heartbeat to a stable absolute path so health_check.bat
            # always finds it regardless of the process working directory.
            try:
                with open(_HEARTBEAT, "w") as _hb:
                    _hb.write(time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
            except Exception:
                log_debug("Heartbeat write failed — health_check.bat may report stale")

            if _shutdown.is_set():
                # Log here (main thread) — safe, unlike inside the signal handler.
                log_info("Shutdown signal received — stopping after current email")
                break

            log_info(
                f"Waiting {Config.CHECK_INTERVAL_SEC} seconds before next check"
            )
            # Interruptible sleep — wakes immediately on shutdown signal
            _shutdown.wait(timeout=Config.CHECK_INTERVAL_SEC)

        except CredentialError as e:
            # CredentialError is raised by connect_mail() only when the IMAP
            # server explicitly rejects the login — no point retrying.
            error_msg = _safe_error(str(e))
            log_error(f"Fatal credential error: {error_msg}")
            send_alert(
                "EmailAssetcues — Fatal: Invalid credentials",
                f"The email processor stopped because credentials are invalid.\n\n"
                f"Error: {error_msg}\n\n"
                f"Fix EMAIL_USER / EMAIL_PASS in your .env file and restart."
            )
            return 1

        except QuotaError as e:
            # IMAP server is rate-limiting or quota-throttling.  This is always
            # transient — back off for _QUOTA_BACKOFF_SEC without incrementing
            # conn_error_count so a temporary throttle never stops the app.
            error_msg = _safe_error(str(e))
            log_warning(
                f"IMAP quota / throttle error — backing off "
                f"{_QUOTA_BACKOFF_SEC}s before retry: {error_msg}"
            )
            _shutdown.wait(timeout=_QUOTA_BACKOFF_SEC)
            # Loop continues — finally block closes the connection first.

        except Exception as e:
            error_msg = _safe_error(str(e))

            # ---------------------------------------------------------------- #
            # Quota / rate-limit errors that arrive as generic Exceptions
            # (e.g. SEARCH or FETCH failures that happen after a successful
            # login) are treated the same as QuotaError from connect_mail().
            # ---------------------------------------------------------------- #
            if _QUOTA_RE.search(error_msg):
                log_warning(
                    f"IMAP quota / throttle detected in operation — backing off "
                    f"{_QUOTA_BACKOFF_SEC}s before retry: {error_msg}"
                )
                _shutdown.wait(timeout=_QUOTA_BACKOFF_SEC)
                # Loop continues — finally block closes the connection first.
                continue

            log_exception(f"Connection error: {error_msg}")

            conn_error_count  += 1
            cumulative_errors += 1

            # Degraded-state alert: fires the first time cumulative errors
            # reaches MAX_RETRIES * 2.  Using >= (not ==) so the alert is not
            # skipped if the counter jumps past the threshold in one increment.
            # The one-shot flag prevents alert spam on every subsequent failure.
            if not _degraded_alert_sent and cumulative_errors >= Config.MAX_RETRIES * 2:
                _degraded_alert_sent = True
                send_alert(
                    f"EmailAssetcues — Degraded: {cumulative_errors} total connection failures",
                    f"The email processor has encountered {cumulative_errors} total "
                    f"connection failures this session (not all consecutive).\n\n"
                    f"Last error: {error_msg}\n\n"
                    f"Please check your network and IMAP server. "
                    f"The processor is still running."
                )

            if conn_error_count >= Config.MAX_RETRIES:
                log_error(
                    f"Max consecutive connection retries ({Config.MAX_RETRIES}) reached. "
                    f"Stopping."
                )
                send_alert(
                    f"EmailAssetcues — Stopped after {Config.MAX_RETRIES} failed retries",
                    f"The email processor gave up after {Config.MAX_RETRIES} consecutive "
                    f"connection failures.\n\nLast error: {error_msg}\n\n"
                    f"Please check your internet connection and IMAP server, "
                    f"then restart the app."
                )
                return 1

            # Exponential backoff — doubles on each failure, capped at 5 minutes.
            wait_time = min(
                Config.RETRY_WAIT_SEC * (2 ** (conn_error_count - 1)), 300
            )
            log_info(
                f"Connection retry {conn_error_count}/{Config.MAX_RETRIES} "
                f"— waiting {wait_time}s (exponential backoff)"
            )
            # Interruptible retry sleep — Ctrl+C / SIGTERM exits immediately
            _shutdown.wait(timeout=wait_time)

        finally:
            if mail:
                try:
                    mail.logout()
                    log_info("Connection closed successfully")
                except Exception as e:
                    log_error(f"Logout error: {e}")

    # Clean exit — either graceful shutdown or the inbox is fully processed.
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Email Attachment Processor")
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Reset all previously-failed / retry-queued emails so they are retried this run"
    )
    args = parser.parse_args()
    # sys.exit() propagates the return code to the OS so run_worker.bat
    # can distinguish a clean exit (0) from a fatal error (1) and decide
    # whether to restart.  Without this both paths look identical to the watchdog.
    sys.exit(main(retry_failed=args.retry_failed))
