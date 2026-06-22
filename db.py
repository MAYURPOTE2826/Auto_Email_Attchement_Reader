"""
Database layer for Email Attachment Processor.

All SQLite access is funnelled through _db_conn() which guarantees every
connection is closed and every failed transaction is rolled back, even when
an unhandled exception propagates through the ``with`` block.

Keeping this module separate from main.py gives three benefits:
  1. main.py stays focused on the event loop and orchestration.
  2. DB functions are independently importable by tests without triggering
     signal registration or config validation.
  3. A future swap to PostgreSQL (or SQLAlchemy) only touches this file.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager

from config import Config
from logger import log_info, log_exception


# ---------------------------------------------------------------------------
# Connection helper
# ---------------------------------------------------------------------------

@contextmanager
def _db_conn():
    """Context manager for SQLite connections.

    Guarantees the connection is always closed — even when an exception is
    raised inside the ``with`` block — eliminating the file-handle leak that
    occurs when a manual ``conn.close()`` call is skipped on an exception path.

    Commits on clean exit; rolls back on exception so partial writes are never
    silently persisted.
    """
    conn = sqlite3.connect(Config.DATABASE_PATH)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _decode_id(e_id) -> str:
    """Normalize email ID to string — imaplib may return bytes or str."""
    return e_id.decode() if isinstance(e_id, bytes) else e_id


# ---------------------------------------------------------------------------
# Schema management
# ---------------------------------------------------------------------------

def init_db() -> None:
    """Initialize database and create / migrate table if needed."""
    try:
        with _db_conn() as conn:
            # WAL (Write-Ahead Log) mode is strongly preferred over the default
            # rollback-journal for an unattended 24/7 process:
            #   • Readers never block writers; writers never block readers.
            #   • A crash mid-write leaves the WAL file intact — SQLite replays
            #     or discards it on next open, giving much safer crash recovery.
            #   • Requires SQLite 3.7.0+ (2010), safe to assume on any modern OS.
            # Must be set before any table DDL so the pragma takes effect on the
            # first connection that creates the DB file.
            # WAL mode — capture the return value so we can warn the operator
            # if the filesystem silently fell back to DELETE mode (NFS, SMB,
            # FAT32, and some Docker volume mounts do not support WAL).
            row = conn.execute("PRAGMA journal_mode=WAL").fetchone()
            if row and row[0].lower() != 'wal':
                log_info(
                    f"SQLite WAL mode could not be enabled on this filesystem "
                    f"(got '{row[0]}' instead of 'wal'). "
                    f"The database will run in '{row[0]}' journal mode — "
                    f"crash recovery will be less robust. "
                    f"Move the database to a local filesystem to enable WAL."
                )

            conn.execute("""
                CREATE TABLE IF NOT EXISTS processed (
                    email_id    TEXT PRIMARY KEY,
                    status      TEXT    NOT NULL DEFAULT 'done',
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    last_error  TEXT,
                    updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
                )
            """)
            # Migrate existing DBs that pre-date newer columns.
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(processed)")
            existing_columns = {row[1] for row in cur.fetchall()}

            migrations = [
                ('status',
                 "ALTER TABLE processed ADD COLUMN status TEXT NOT NULL DEFAULT 'done'"),
                ('retry_count',
                 "ALTER TABLE processed ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0"),
                ('last_error',
                 "ALTER TABLE processed ADD COLUMN last_error TEXT"),
                ('updated_at',
                 "ALTER TABLE processed ADD COLUMN updated_at TEXT NOT NULL DEFAULT '1970-01-01T00:00:00'"),
            ]
            for col, ddl in migrations:
                if col not in existing_columns:
                    conn.execute(ddl)

            # Index on status: reset_failed_emails() and retry-queue scans
            # do WHERE status IN (...) — without an index that's a full table scan.
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_processed_status ON processed(status)"
            )

        log_info(f"Database initialized at {Config.DATABASE_PATH}")
    except Exception as e:
        log_exception(f"Database initialization error: {e}")
        raise


# ---------------------------------------------------------------------------
# Status queries
# ---------------------------------------------------------------------------

def get_email_record(e_id) -> tuple:
    """Return (status, retry_count) in a single query.

    Replaces the two-call pattern get_email_status() + get_retry_count() with
    one round-trip to SQLite — halves the number of file-lock acquire/release
    cycles per email and eliminates the brief window between the two reads
    where the row could theoretically be mutated by another process.

    Returns:
        (status_str, retry_count_int) if the email is in the DB.
        (None, 0)                     if not found (new email).

    Raises:
        Exception: propagated to the caller for logging; the caller should
                   skip the email rather than risk reprocessing.
    """
    try:
        with _db_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT status, retry_count FROM processed WHERE email_id = ?",
                (_decode_id(e_id),)
            )
            row = cur.fetchone()
            return (row[0], row[1]) if row else (None, 0)
    except Exception as e:
        log_exception(f"Database record read error: {e}")
        raise


def get_email_status(e_id):
    """Return the current status string, or None if the email is not in the DB."""
    try:
        with _db_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT status FROM processed WHERE email_id = ?",
                (_decode_id(e_id),)
            )
            row = cur.fetchone()
            return row[0] if row else None
    except Exception as e:
        log_exception(f"Database check error: {e}")
        raise


def get_retry_count(e_id) -> int:
    """Return the current retry_count for *e_id*, or 0 if the record does not exist."""
    try:
        with _db_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT retry_count FROM processed WHERE email_id = ?",
                (_decode_id(e_id),)
            )
            row = cur.fetchone()
            return row[0] if row else 0
    except Exception as e:
        log_exception(f"Database retry_count read error: {e}")
        return 0   # non-fatal — caller uses 0 as the safe default


# ---------------------------------------------------------------------------
# Status mutations
# ---------------------------------------------------------------------------

def mark_in_progress(e_id) -> None:
    """Record that processing has started.

    Uses an UPSERT so the function works for both new emails (INSERT) and
    emails being re-attempted after a 'retry' status (UPDATE).

    Raises:
        Exception: propagated to the caller if the DB write fails.
                   The caller MUST skip the email rather than process it
                   without an in_progress record — otherwise a mid-run crash
                   leaves a downloaded file with no crash-recovery marker.
    """
    try:
        with _db_conn() as conn:
            conn.execute(
                """INSERT INTO processed (email_id, status, retry_count, updated_at)
                   VALUES (?, 'in_progress', 0, datetime('now'))
                   ON CONFLICT(email_id) DO UPDATE SET
                       status     = 'in_progress',
                       updated_at = datetime('now')""",
                (_decode_id(e_id),)
            )
    except Exception as e:
        log_exception(f"Database mark_in_progress error: {e}")
        raise   # caller must handle — do not process without a DB record


def save_processed(e_id) -> None:
    """Mark email as fully and successfully processed."""
    try:
        with _db_conn() as conn:
            conn.execute(
                """UPDATE processed
                   SET status='done', updated_at=datetime('now')
                   WHERE email_id=?""",
                (_decode_id(e_id),)
            )
    except Exception as e:
        log_exception(f"Database save error: {e}")


def mark_retry(e_id, error_msg: str | None = None) -> None:
    """Increment retry_count and set status to 'retry'.

    The email will be re-queued for processing on the next cycle.
    The last error message is stored so operators can diagnose failures.
    """
    try:
        with _db_conn() as conn:
            conn.execute(
                """UPDATE processed
                   SET status      = 'retry',
                       retry_count = retry_count + 1,
                       last_error  = ?,
                       updated_at  = datetime('now')
                   WHERE email_id = ?""",
                (error_msg, _decode_id(e_id))
            )
    except Exception as e:
        log_exception(f"Database mark_retry error: {e}")


def mark_failed(e_id, error_msg: str | None = None) -> None:
    """Mark email as permanently failed (retries exhausted)."""
    try:
        with _db_conn() as conn:
            conn.execute(
                """UPDATE processed
                   SET status     = 'failed',
                       last_error = ?,
                       updated_at = datetime('now')
                   WHERE email_id = ?""",
                (error_msg, _decode_id(e_id))
            )
    except Exception as e:
        log_exception(f"Database mark_failed error: {e}")


def reset_failed_emails() -> None:
    """Reset all failed and retry entries so they are re-attempted on next run.

    Uses UPDATE rather than DELETE so the row's history (original email_id,
    prior retry_count) is preserved in the database.  This lets operators
    query how many total attempts have been made for a given email even after
    a --retry-failed run, and prevents phantom "not found" lookups caused by
    a missing row mid-cycle if another thread races the DELETE.
    """
    try:
        with _db_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                """UPDATE processed
                   SET status      = 'retry',
                       retry_count = 0,
                       last_error  = NULL,
                       updated_at  = datetime('now')
                   WHERE status IN ('failed', 'retry')"""
            )
            count = cur.rowcount
        log_info(
            f"Reset {count} failed/retry email(s) — they will be retried on next run"
        )
    except Exception as e:
        log_exception(f"Failed to reset emails: {e}")
        raise
