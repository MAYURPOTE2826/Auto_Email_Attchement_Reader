import os
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
    _db_conn, _decode_id, init_db, get_email_status, get_retry_count,
    mark_in_progress, save_processed, mark_retry, mark_failed, reset_failed_emails,
)
from email_reader import connect_mail, CredentialError
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

def _process_is_alive(pid: int) -> bool:
    """Return True if a process with *pid* is currently running.

    os.kill(pid, 0) checks process existence without sending a signal.
    Behaviour by platform:
      Unix  — ProcessLookupError if dead; PermissionError if alive but not ours.
      Windows — PermissionError if dead (no PROCESS_QUERY_INFORMATION rights);
                returns normally if alive.
    We treat PermissionError conservatively (assume alive) so we never
    silently stomp on a live process on either platform.
    """
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False       # Unix: process definitely gone
    except PermissionError:
        return True        # conservative — assume alive on both platforms
    except OSError:
        return False


def _acquire_instance_lock() -> None:
    """Prevent two instances of the processor from running simultaneously.

    Uses O_CREAT | O_EXCL for atomic creation.  On startup, when an existing
    lockfile is found the stored PID is checked; if that process is gone the
    stale file is removed and we proceed.  _release_instance_lock is registered
    via atexit so normal exit (and sys.exit / raise SystemExit) always cleans up.

    Raises:
        SystemExit: if another live instance is detected.
    """
    def _write_lock():
        fd = os.open(_LOCKFILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, 'w') as f:
            f.write(str(os.getpid()))

    try:
        _write_lock()
    except FileExistsError:
        # Read the PID from the existing lock file
        try:
            with open(_LOCKFILE) as fh:
                old_pid = int(fh.read().strip())
        except (OSError, ValueError):
            old_pid = None

        if old_pid and _process_is_alive(old_pid):
            raise SystemExit(
                f"Another instance of Email Assetcues is already running "
                f"(PID {old_pid}).  Stop it first, or delete {_LOCKFILE} "
                f"if you are sure it is stale."
            )

        # Stale lock — safe to remove and retry
        log_warning(
            f"Removing stale lock file (PID {old_pid} is no longer running)"
        )
        try:
            os.remove(_LOCKFILE)
        except OSError:
            pass

        try:
            _write_lock()
        except FileExistsError:
            raise SystemExit(
                "Could not acquire instance lock — please delete "
                f"{_LOCKFILE} and retry."
            )

    atexit.register(_release_instance_lock)


def _release_instance_lock() -> None:
    """Remove the PID lockfile on clean exit."""
    try:
        os.remove(_LOCKFILE)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Alerting
# ---------------------------------------------------------------------------

def send_alert(subject: str, body: str) -> None:
    """Send an alert email when the app encounters a fatal problem.

    Uses a separate try/except so a broken alert path never crashes the main
    loop.  If EMAIL credentials are invalid, this SMTP attempt will also fail
    — that failure is logged and swallowed intentionally.

    Both EMAIL_USER and EMAIL_PASS are read fresh from the environment so that
    credential rotation takes effect without a process restart.
    """
    if not Config.ALERT_EMAIL:
        return
    try:
        msg = MIMEText(body, 'plain', 'utf-8')
        msg['Subject'] = subject
        # Read both credentials fresh — consistent with connect_mail() pattern.
        _user = os.getenv("EMAIL_USER", "").strip()
        _pw   = os.getenv("EMAIL_PASS", "").strip()
        msg['From'] = _user
        msg['To']   = Config.ALERT_EMAIL
        with smtplib.SMTP_SSL(Config.ALERT_SMTP_HOST, Config.ALERT_SMTP_PORT) as smtp:
            smtp.login(_user, _pw)
            smtp.send_message(msg)
            del _pw
            del _user
        log_info(f"Alert sent to {Config.ALERT_EMAIL}")
    except Exception as e:
        log_error(f"Failed to send alert email: {e}")


# ---------------------------------------------------------------------------
# Main application loop
# ---------------------------------------------------------------------------

def main(retry_failed: bool = False) -> None:
    """Main application loop."""
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
        return

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

    while conn_error_count < Config.MAX_RETRIES and not _shutdown.is_set():
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
            email_ids = messages[0].split()

            log_info(f"Found {len(email_ids)} unseen emails")

            for e_id in email_ids:
                log_info(f"Processing Email ID: {_decode_id(e_id)}")

                try:
                    db_status = get_email_status(e_id)
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
                    retry_count = get_retry_count(e_id)
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

                # db_status is None (new email) or 'retry' (queued for re-attempt)
                mark_in_progress(e_id)

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

                if success:
                    save_processed(e_id)
                    log_info(
                        f"Email {_decode_id(e_id)} processed successfully"
                    )
                else:
                    # Honour the per-email retry budget before giving up.
                    # retry_count reflects how many attempts have already failed.
                    retry_count = get_retry_count(e_id)
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
            error_msg = str(e)
            log_error(f"Fatal credential error: {error_msg}")
            send_alert(
                "EmailAssetcues — Fatal: Invalid credentials",
                f"The email processor stopped because credentials are invalid.\n\n"
                f"Error: {error_msg}\n\n"
                f"Fix EMAIL_USER / EMAIL_PASS in your .env file and restart."
            )
            break

        except Exception as e:
            error_msg = str(e)
            log_exception(f"Connection error: {error_msg}")

            conn_error_count  += 1
            cumulative_errors += 1

            # Degraded-state alert: fires when the total lifetime failure count
            # reaches MAX_RETRIES * 2.  This catches the alternating success/fail
            # pattern that never trips the consecutive MAX_RETRIES guard but
            # still indicates a persistently unhealthy connection.
            if cumulative_errors == Config.MAX_RETRIES * 2:
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
                break

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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Email Attachment Processor")
    parser.add_argument(
        "--retry-failed",
        action="store_true",
        help="Reset all previously-failed / retry-queued emails so they are retried this run"
    )
    args = parser.parse_args()
    main(retry_failed=args.retry_failed)
