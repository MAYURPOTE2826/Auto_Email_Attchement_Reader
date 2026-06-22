"""
Logging configuration for Email Attachment Processor.

Design decisions:
  - RotatingFileHandler  — log file never grows unbounded
  - Named logger         — zero third-party library noise (imaplib, smtplib, etc.)
  - stacklevel=2         — %(filename)s / %(lineno)d in format strings point to the
                           *real* caller, not to logger.py itself
  - UTF-8 encoding       — email subjects can be non-ASCII; cp1252 (Windows default)
                           would silently corrupt or crash log writes
  - delay=True           — file is only opened on the first write, so importing this
                           module before main.py creates the log directory is safe
  - stdout console       — Docker / Kubernetes log collectors expect stdout; stderr
                           is reserved for unhandled interpreter errors
  - ContextVar corr ID   — inject once per email, visible in every log line for it;
                           trivial to filter all lines for email-42 in ELK / Splunk
  - _CorrelationFilter   — injects the corr ID into every LogRecord on the logger,
                           so both plain-text and JSON formatters get it for free
  - JSON mode            — LOG_JSON=true emits newline-delimited JSON for ELK /
                           Datadog / CloudWatch; exc_info is a nested object, not a
                           raw multi-line string, to avoid log-shipper splitting bugs
  - extra={} forwarded   — any key passed via extra={"email_id": "42"} appears in
                           the JSON "extra" block; plain-text callers use it too
  - duplicate-handler guard — if the module is reloaded (common in test suites),
                           handlers are not added again; avoids N-times duplication
"""

from __future__ import annotations

import json
import logging
import os
import socket
import sys
import time
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler

try:
    from concurrent_log_handler import ConcurrentRotatingFileHandler
    _FileHandlerClass = ConcurrentRotatingFileHandler
except ImportError:
    _FileHandlerClass = RotatingFileHandler

from config import Config

# ---------------------------------------------------------------------------
# Correlation ID — one value per execution context (per email being processed).
#
# How to use in main.py:
#
#   from logger import set_correlation_id, clear_correlation_id
#
#   mark_in_progress(e_id)
#   set_correlation_id(f"email-{_decode_id(e_id)}")
#   try:
#       success = process_email(mail, e_id)
#   finally:
#       clear_correlation_id()
#
# Every log_* call made during process_email() will carry the same corr ID,
# making it trivial to filter all activity for one email in ELK / Splunk.
# ---------------------------------------------------------------------------
_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="-")


def set_correlation_id(value: str) -> None:
    """Set the correlation ID for the current execution context.

    Call this at the start of each email's processing block.  All log lines
    emitted until ``clear_correlation_id()`` is called will carry this value
    in both plain-text and JSON output.
    """
    _correlation_id.set(value)


def clear_correlation_id() -> None:
    """Reset the correlation ID back to the default placeholder '-'."""
    _correlation_id.set("-")


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------

class _CorrelationFilter(logging.Filter):
    """Inject the current correlation ID into every LogRecord.

    Adding this filter to the *logger* (not individual handlers) means it
    runs exactly once per log call, before any handler processes the record.
    Both plain-text (via %(correlation_id)s) and JSON (via record.__dict__)
    formatters then get the value without each needing to call the ContextVar.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = _correlation_id.get()  # type: ignore[attr-defined]
        return True   # never suppress — only inject


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

# ISO 8601 timestamp — no comma before milliseconds.
# Python's default produces "2026-04-04 10:22:05,123"; many log parsers
# (Filebeat, Fluentd, Splunk HEC) expect "2026-04-04T10:22:05" or the full
# RFC 3339 form.  Consistent timestamps are a prerequisite for correct log
# ordering in any time-series store.
_DATEFMT = "%Y-%m-%dT%H:%M:%S"

# Plain format includes %(filename)s:%(lineno)d so every line pinpoints its
# origin — essential for production debugging without a full-text search.
_PLAIN_FMT = (
    "%(asctime)s - %(levelname)-8s - %(filename)s:%(lineno)d"
    " - [%(correlation_id)s] - %(message)s"
)

# Standard attributes present on every LogRecord.
# Used by _JsonFormatter to separate built-ins from caller-injected extra={} keys.
_LOGRECORD_BUILTINS = frozenset({
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "message", "taskName", "correlation_id",
})

# Service envelope — read once at startup; injected into every JSON log line
# so multi-service ELK deployments can route and filter by service/env/host.
_SERVICE = os.getenv("SERVICE_NAME", "emailassetcues")
_ENV     = os.getenv("ENV", "production")
# gethostname() can block on broken/misconfigured DNS (common on Windows with VPN
# disconnected).  Fall back to a safe sentinel rather than hanging at import time.
try:
    _HOST = socket.gethostname()
except Exception:
    _HOST = "unknown-host"


def _format_time_ms(record: logging.LogRecord) -> str:
    """Return ISO 8601 timestamp with milliseconds.

    strftime has no %f / %L directive, so milliseconds are appended manually.
    Both formatters call this helper so the format is identical in plain and JSON output.
    """
    ct = time.localtime(record.created)
    return f"{time.strftime(_DATEFMT, ct)}.{int(record.msecs):03d}"


class _PlainFormatter(logging.Formatter):
    """Plain-text formatter with ISO 8601 timestamp, source location, and corr ID."""

    def __init__(self) -> None:
        super().__init__(fmt=_PLAIN_FMT, datefmt=_DATEFMT)

    def formatTime(self, record: logging.LogRecord, _datefmt=None) -> str:
        return _format_time_ms(record)


class _JsonFormatter(logging.Formatter):
    """Emit one JSON object per log line — newline-delimited JSON (NDJSON).

    Every line includes:
      Standard  : timestamp, level, logger, message, file, line, function
      Runtime   : thread_id, process_id, correlation_id
      Envelope  : service, env, host  (for ELK index routing)
      Optional  : exception (nested object, not a raw multi-line string),
                  stack_info, extra (caller-injected key=value pairs)

    Why structured exception instead of a raw string
    ─────────────────────────────────────────────────
    formatException() returns a multi-line string containing '\\n' characters.
    Several log shippers (Logstash 7.x, some Datadog agents) split input on
    newlines before JSON-parsing.  A raw multi-line value inside a JSON string
    breaks those pipelines.  Nesting it under "exception": {"text": "..."} keeps
    the outer JSON on a single line while preserving all traceback detail.

    Why default=str in json.dumps
    ──────────────────────────────
    extra={} values can be any Python object.  Passing default=str ensures
    non-serialisable types (datetime, Exception, custom objects) are rendered
    as strings rather than raising TypeError and dropping the entire log line.
    """

    def __init__(self) -> None:
        super().__init__(datefmt=_DATEFMT)

    def formatTime(self, record: logging.LogRecord, _datefmt=None) -> str:
        return _format_time_ms(record)

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            # ── Core ──────────────────────────────────────────────────────── #
            "timestamp":      self.formatTime(record, self.datefmt),
            "level":          record.levelname,
            "logger":         record.name,
            "message":        self._safe_message(record),
            # ── Source location ───────────────────────────────────────────── #
            # stacklevel=2 in every wrapper ensures these point to the real
            # caller (e.g. attachment_handler.py:205), not to logger.py.
            "file":           record.filename,
            "line":           record.lineno,
            "function":       record.funcName,
            # ── Runtime context ───────────────────────────────────────────── #
            "thread_id":      record.thread,
            "process_id":     record.process,
            "correlation_id": getattr(record, "correlation_id", "-"),
            # ── Service envelope ──────────────────────────────────────────── #
            "service":        _SERVICE,
            "env":            _ENV,
            "host":           _HOST,
        }

        # Exception — nested object so the outer JSON stays on one line
        if record.exc_info:
            payload["exception"] = {"text": self.formatException(record.exc_info)}

        # Stack info — present when the caller passes stack_info=True
        if record.stack_info:
            payload["stack_info"] = record.stack_info

        # Extra fields — caller-injected via extra={"email_id": "42", ...}
        # Silently ignored in the old implementation; now visible in JSON output.
        extras = {
            k: v
            for k, v in record.__dict__.items()
            if k not in _LOGRECORD_BUILTINS and not k.startswith("_")
        }
        if extras:
            payload["extra"] = extras

        # default=str handles datetime, Exception, and other non-JSON-serialisable
        # types without raising TypeError and silently dropping the log line.
        return json.dumps(payload, default=str)

    @staticmethod
    def _safe_message(record: logging.LogRecord) -> str:
        """Call getMessage() with a fallback.

        getMessage() calls ``str(record.msg) % record.args``.  A mismatch
        between the format string and its arguments raises TypeError, which
        in the base Formatter is caught by a surrounding try/except that we
        lose when overriding format() completely.  This method restores that
        safety net so one bad log call never silently drops a line.
        """
        try:
            return record.getMessage()
        except Exception as exc:
            return f"[log-format error: {exc}] raw={record.msg!r}"


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

_file_handler = _FileHandlerClass(
    Config.LOG_FILE,
    maxBytes=Config.LOG_MAX_BYTES,
    backupCount=Config.LOG_BACKUP_COUNT,
    encoding="utf-8",   # non-ASCII email subjects (Cyrillic, CJK, emoji) are safe
    delay=True,         # defer open to first write — import succeeds even if the
                        # log directory hasn't been created by main.py yet
)

# stdout — Docker / Kubernetes log collectors capture stdout by default.
# stderr is reserved for unhandled interpreter errors (tracebacks that bypass
# the logging system entirely) so mixing application logs there causes confusion.
_console_handler = logging.StreamHandler(sys.stdout)

# Per-handler log levels (optional).
# Uncomment to write DEBUG to file while keeping the terminal at WARNING:
#   _file_handler.setLevel(logging.DEBUG)
#   _console_handler.setLevel(logging.WARNING)

# ---------------------------------------------------------------------------
# Logger assembly
# ---------------------------------------------------------------------------

_active_formatter    = _JsonFormatter() if Config.LOG_JSON else _PlainFormatter()
_correlation_filter  = _CorrelationFilter()

for _h in (_file_handler, _console_handler):
    _h.setFormatter(_active_formatter)

logger = logging.getLogger("emailassetcues")

# Duplicate-handler guard — logging.getLogger() returns the *same* object on
# every call.  Without this check, reloading the module (pytest, importlib,
# interactive shells) adds another pair of handlers each time, causing every
# log line to be written N times — one per reload.
if not logger.handlers:
    logger.setLevel(Config.LOG_LEVEL)
    logger.propagate = False    # do not bubble up to root logger
    # Add the correlation filter to the logger (not individual handlers) so it
    # runs exactly once per log call, before any handler processes the record.
    logger.addFilter(_correlation_filter)
    logger.addHandler(_file_handler)
    logger.addHandler(_console_handler)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
# stacklevel=2 on every call tells Python's logging machinery to walk up one
# extra frame when recording %(filename)s, %(lineno)d, and %(funcName)s.
# Without it, every log line reports "logger.py" as its source — making the
# source-location fields in both plain-text and JSON output completely useless.
# ---------------------------------------------------------------------------

def log_debug(msg: object, **kwargs) -> None:
    logger.debug(msg, stacklevel=2, **kwargs)


def log_info(msg: object, **kwargs) -> None:
    logger.info(msg, stacklevel=2, **kwargs)


def log_warning(msg: object, **kwargs) -> None:
    logger.warning(msg, stacklevel=2, **kwargs)


def log_error(msg: object, **kwargs) -> None:
    """Log at ERROR level.

    Pass ``exc_info=True`` to include the current exception traceback inline,
    or use ``log_exception()`` as a shorter alternative.

    Example::

        try:
            save_file(path)
        except OSError as e:
            log_error(f"Could not save {path}: {e}", exc_info=True)
    """
    logger.error(msg, stacklevel=2, **kwargs)


def log_exception(msg: object, level: int = logging.ERROR, **kwargs) -> None:
    """Log at *level* and automatically attach the current exception traceback.

    Defaults to ERROR.  Pass ``level=logging.WARNING`` for expected / handled
    errors (e.g. connection timeouts, transient IMAP blips) that don't warrant
    waking on-call but should be visible in the log with full context.

    Must be called from inside an ``except`` block — otherwise there is no
    active exception and ``exc_info`` will be empty.

    Examples::

        # Unexpected failure — ERROR (default)
        except Exception as exc:
            log_exception(f"Unexpected error: {exc}")

        # Expected / handled failure — WARNING
        except TimeoutError as exc:
            log_exception(f"IMAP timeout (will retry): {exc}",
                          level=logging.WARNING)
    """
    logger.log(level, msg, exc_info=True, stacklevel=2, **kwargs)
