import os
import shutil
import email
import email.policy
import re
from email.utils import parseaddr
from config import Config
from logger import log_debug, log_info, log_warning, log_error, log_exception

# Maximum filename length (bytes) that NTFS and most filesystems support is 255.
# We cap at 200 to leave room for the counter suffix (_1, _2, …) and the extension.
_MAX_FILENAME_LEN = 200

# Chunk size for writing attachment payload to disk.
# Keeps peak in-process memory per attachment bounded to one chunk even when
# the email module has already decoded the full payload into a bytes object.
_WRITE_CHUNK_BYTES = 64 * 1024  # 64 KB

# ---------------------------------------------------------------------------
# Magic byte signatures — first-bytes check applied to every attachment payload.
#
# Defence-in-depth: an attacker who renames malware.exe → report.pdf still
# carries the MZ header.  This catches the mismatch regardless of what the
# file extension or Content-Type header claim.
# ---------------------------------------------------------------------------
_MAGIC_SIGNATURES: list[tuple[bytes, str]] = [
    (b'MZ',                  'Windows PE executable (EXE / DLL / SCR / COM)'),
    (b'\x7fELF',             'ELF Linux/Unix executable'),
    (b'#!',                  'Shell script (shebang line)'),
    # Archives — defence-in-depth: catches renamed archives (report.pdf) even
    # though archive extensions are also in BLOCKED_EXTENSIONS.
    (b'PK\x03\x04',          'ZIP archive'),
    (b'Rar!\x1a\x07',        'RAR archive'),
    (b'7z\xbc\xaf\x27\x1c', '7-Zip archive'),
]


# ---------------------------------------------------------------------------
# Filename helpers
# ---------------------------------------------------------------------------

def sanitize_filename(filename: str) -> str:
    """Return a filesystem-safe version of *filename*.

    Security measures applied in order:
    1. Take basename only  — prevents path traversal (../../etc/passwd).
    2. Strip leading dots  — prevents hidden / dot-file injection
                             (.env, .bashrc, .htaccess).
    3. Replace anything outside [a-zA-Z0-9._-] with underscores.
    4. Truncate to _MAX_FILENAME_LEN characters — avoids OS limit errors.
    5. Fall back to 'unnamed_attachment' if the result is empty.
    """
    filename = os.path.basename(filename)
    filename = filename.lstrip('.')                         # strip leading dots
    filename = re.sub(r'[^a-zA-Z0-9._-]', '_', filename)  # replace unsafe chars
    filename = filename[:_MAX_FILENAME_LEN]                 # cap length
    return filename or 'unnamed_attachment'


def get_unique_filepath(filepath: str) -> str:
    """Return a filepath that does not yet exist, creating it atomically.

    Uses ``os.O_CREAT | os.O_EXCL`` so that the existence-check and the
    file creation are a single atomic syscall — eliminating the TOCTOU
    (time-of-check / time-of-use) race condition present in a
    ``os.path.exists()`` + ``open()`` pattern.

    The returned path points to a 0-byte placeholder file.  The caller
    must open it in ``'wb'`` mode (which truncates and overwrites) to
    write the actual payload.

    Raises:
        RuntimeError: If a unique name cannot be found within
                      ``Config.MAX_FILENAME_ATTEMPTS`` iterations.
    """
    base, ext = os.path.splitext(filepath)
    for counter in range(Config.MAX_FILENAME_ATTEMPTS + 1):
        candidate = filepath if counter == 0 else f"{base}_{counter}{ext}"
        try:
            fd = os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            return candidate
        except FileExistsError:
            continue
    raise RuntimeError(
        f"Could not generate unique filepath after "
        f"{Config.MAX_FILENAME_ATTEMPTS} attempts: {filepath}"
    )


def _check_magic_bytes(payload: bytes, filename: str) -> bool:
    """Return True if the payload's magic bytes match a known executable signature.

    Only reads the first 8 bytes — O(1), no disk I/O, no full parse required.

    This is defence-in-depth against renamed executables that bypass the
    extension blocklist (e.g. ``malware.exe`` saved as ``invoice.pdf``).

    Logs a WARNING with the matched signature name so operators know precisely
    why a file was blocked.
    """
    header = payload[:8]
    for signature, description in _MAGIC_SIGNATURES:
        if header.startswith(signature):
            log_warning(
                f"[BLOCKED] '{filename}' rejected — "
                f"magic bytes identify content as: {description}"
            )
            return True
    return False


# ---------------------------------------------------------------------------
# process_email helpers — each handles one well-defined concern
# ---------------------------------------------------------------------------

def _check_disk_space() -> bool:
    """Return True if there is enough free disk space to proceed.

    Called before any network I/O so we never fetch an email we cannot save.
    """
    free_mb = shutil.disk_usage(Config.DOWNLOAD_FOLDER).free / (1024 * 1024)
    if free_mb < Config.MIN_FREE_DISK_MB:
        log_error(
            f"Not enough disk space ({free_mb:.1f} MB free, "
            f"need {Config.MIN_FREE_DISK_MB} MB). Skipping email."
        )
        return False
    return True


def _check_email_size(mail, e_id) -> bool:
    """Return True if the email is oversized and has already been handled.

    A True return means the caller should ``return True`` (consciously skipped,
    not a failure).  False means the size is acceptable — continue processing.

    Uses a lightweight RFC822.SIZE fetch instead of downloading the full body.
    """
    size_status, size_data = mail.uid('FETCH', e_id, '(RFC822.SIZE)')
    if size_status == 'OK' and size_data[0]:
        raw = size_data[0]
        size_str = raw.decode() if isinstance(raw, bytes) else raw
        match = re.search(r'RFC822\.SIZE (\d+)', size_str)
        if match:
            size_in_bytes = int(match.group(1))
            max_size_bytes = Config.MAX_EMAIL_SIZE_MB * 1024 * 1024
            if size_in_bytes > max_size_bytes:
                log_warning(
                    f"[SKIPPED] Email {e_id} is {size_in_bytes / 1024 / 1024:.1f} MB "
                    f"which exceeds the {Config.MAX_EMAIL_SIZE_MB} MB limit — "
                    f"marked Seen, attachment NOT downloaded. "
                    f"Raise MAX_EMAIL_SIZE_MB in .env to process this email."
                )
                seen_st, _ = mail.uid('STORE', e_id, '+FLAGS', '\\Seen')
                if seen_st != 'OK':
                    log_warning(
                        f"Could not mark oversized email {e_id} as Seen "
                        f"— it will reappear next cycle"
                    )
                return True   # consciously skipped — not a failure
    return False


def _fetch_email_body(mail, e_id):
    """Fetch the full RFC822 body.  Returns msg_data list or None on failure."""
    status, msg_data = mail.uid('FETCH', e_id, '(RFC822)')
    if status != 'OK':
        log_error(f"Failed to fetch email {e_id}: {msg_data}")
        return None
    return msg_data


def _check_sender_allowed(msg, mail, e_id) -> bool:
    """Return True to continue processing; False if sender was rejected.

    When ALLOWED_SENDERS is configured and the sender is not in the list,
    the email is marked Seen and this function returns False — the caller
    should ``return True`` (a deliberate skip, not a retry-able failure).
    """
    if not Config.ALLOWED_SENDERS:
        return True

    _, sender_addr = parseaddr(msg.get('From', ''))
    if sender_addr.lower() not in Config.ALLOWED_SENDERS:
        log_warning(
            f"[REJECTED] Email from '{sender_addr}' is not in ALLOWED_SENDERS "
            f"— marking Seen and skipping."
        )
        seen_st, _ = mail.uid('STORE', e_id, '+FLAGS', '\\Seen')
        if seen_st != 'OK':
            log_warning(
                f"Could not mark rejected email {e_id} as Seen "
                f"— it will reappear next cycle"
            )
        return False
    return True


def _save_attachment_part(part, e_id) -> str | None:
    """Run the full security pipeline on one MIME part and save it.

    Returns the saved filepath on success, or None if the attachment was
    blocked, had no payload, or had no filename.

    Security checks in order (cheapest first):
      1. Filename present
      2. Filename sanitization
      3. Extension blocklist
      4. Content-Type blocklist
      5. Atomic placeholder creation (TOCTOU-safe)
      6. Magic byte check (requires decoded payload)
    """
    filename = part.get_filename()
    if not filename:
        # Log so the operator knows an unnamed attachment was silently skipped.
        log_warning(
            f"Attachment in email {e_id} has no filename — skipping."
        )
        return None

    filename = sanitize_filename(filename)

    # --- Guard 1: extension blocklist (fast path) ---
    ext = os.path.splitext(filename)[1].lower()
    if ext in Config.BLOCKED_EXTENSIONS:
        log_warning(
            f"[BLOCKED] Attachment '{filename}' rejected — "
            f"extension '{ext}' is in BLOCKED_EXTENSIONS."
        )
        return None

    # --- Guard 2: Content-Type blocklist ---
    # Catches renamed executables that still advertise their real MIME type
    # (e.g. malware.exe → report.pdf with Content-Type: application/x-msdownload).
    content_type = part.get_content_type().lower()
    if content_type in Config.BLOCKED_MIME_TYPES:
        log_warning(
            f"[BLOCKED] Attachment '{filename}' rejected — "
            f"Content-Type '{content_type}' is in BLOCKED_MIME_TYPES."
        )
        return None

    # Atomic placeholder — no TOCTOU between existence-check and creation.
    filepath = os.path.join(Config.DOWNLOAD_FOLDER, filename)
    filepath = get_unique_filepath(filepath)

    payload = part.get_payload(decode=True)
    if not payload:
        # get_unique_filepath() created a 0-byte placeholder; clean it up
        # so the attachments folder doesn't accumulate empty ghost files.
        try:
            os.remove(filepath)
        except OSError:
            pass
        log_warning(
            f"Attachment '{filename}' has no payload — placeholder removed."
        )
        return None

    # --- Guard 3: magic bytes check ---
    # Catches executables renamed to bypass the extension blocklist
    # (e.g. malware.exe saved as invoice.pdf — MZ header gives it away).
    # Must run AFTER decoding the payload, BEFORE writing to disk.
    if _check_magic_bytes(payload, filename):
        try:
            os.remove(filepath)
        except OSError:
            pass
        return None

    # Write in fixed-size chunks so the GC can reclaim the payload
    # bytes sooner and peak RSS stays bounded per attachment.
    with open(filepath, 'wb') as f:
        for offset in range(0, len(payload), _WRITE_CHUNK_BYTES):
            f.write(payload[offset:offset + _WRITE_CHUNK_BYTES])

    # Explicitly release the payload reference so CPython's
    # reference-counting GC can reclaim it immediately.
    del payload

    log_info(f"Downloaded: {filepath}")
    return filepath


def _finalize_email(mail, e_id, has_attachment_parts: bool, saved_files: list[str]) -> bool:
    """Apply Gmail label and mark email as Seen after processing.

    Label is only applied when at least one attachment was saved — we don't
    label emails whose attachments were all blocked by security policy.

    Seen is always marked when the email had attachment parts — even if all
    were blocked — so it does not reappear as unseen on the next cycle.

    Returns False if an IMAP command fails (caller should retry later).
    """
    if not has_attachment_parts:
        return True

    if Config.USE_GMAIL_LABELS and saved_files:
        label_status, label_resp = mail.uid(
            'STORE', e_id, '+X-GM-LABELS', 'Attachment_Mails'
        )
        if label_status != 'OK':
            log_error(f"Failed to apply Gmail label: {label_resp}")
            return False

    seen_status, seen_resp = mail.uid('STORE', e_id, '+FLAGS', '\\Seen')
    if seen_status != 'OK':
        log_error(f"Failed to mark as Seen: {seen_resp}")
        return False

    log_info(
        "Email marked Seen"
        + (" and Gmail label applied" if Config.USE_GMAIL_LABELS and saved_files else "")
    )
    return True


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def process_email(mail, e_id) -> bool:
    """Fetch *e_id* from *mail* and save any attachments to disk.

    Returns:
        True  — email processed (attachments saved, or no attachments present,
                or email deliberately skipped due to size / sender / policy).
        False — a recoverable error occurred; the caller should retry later.
    """
    saved_files: list[str] = []
    try:
        if not _check_disk_space():
            return False

        if _check_email_size(mail, e_id):
            return True   # consciously skipped — oversized

        msg_data = _fetch_email_body(mail, e_id)
        if msg_data is None:
            return False

        # True when the email had at least one Content-Disposition: attachment
        # part (regardless of whether it passed security checks).  Drives the
        # mark-Seen call — always mark Seen so blocked-attachment emails don't
        # reappear as unseen on every cycle.
        has_attachment_parts = False

        for response_part in msg_data:
            if not isinstance(response_part, tuple):
                continue

            # email.policy.default enables the modern EmailMessage API and
            # correctly decodes RFC 2047–encoded headers (UTF-8, ISO-8859-1,
            # etc.) in filenames and subjects.  The legacy compat32 policy
            # (default when policy= is omitted) silently mangles non-ASCII
            # filenames, producing names like =_UTF-8_B_SGVsbG8=_.pdf.
            msg = email.message_from_bytes(
                response_part[1], policy=email.policy.default
            )
            log_debug(f"Processing Email: {msg.get('Subject', '(no subject)')}")

            if not _check_sender_allowed(msg, mail, e_id):
                return True   # consciously skipped — sender not in allowlist

            # ---------------------------------------------------------------- #
            # MIME bomb protection — cap the number of parts walked.
            # A legitimate email rarely has >20–30 MIME parts.  Stopping early
            # prevents a crafted email from exhausting CPU or memory in .walk().
            # ---------------------------------------------------------------- #
            parts_walked = 0
            for part in msg.walk():
                parts_walked += 1
                if parts_walked > Config.MAX_MIME_PARTS:
                    log_warning(
                        f"[TRUNCATED] Email {e_id} has >{Config.MAX_MIME_PARTS} MIME parts "
                        f"— stopping walk to prevent MIME-bomb exhaustion. "
                        f"Raise MAX_MIME_PARTS in .env if this is a legitimate email."
                    )
                    break

                if part.get_content_disposition() != 'attachment':
                    continue

                has_attachment_parts = True
                filepath = _save_attachment_part(part, e_id)
                if filepath:
                    saved_files.append(filepath)

        return _finalize_email(mail, e_id, has_attachment_parts, saved_files)

    except Exception as e:
        log_exception(f"Processing Error: {e}")
        # Roll back any files saved before the failure so a retry starts clean.
        for f in saved_files:
            try:
                os.remove(f)
                log_info(f"Cleaned up partial download: {f}")
            except OSError as cleanup_err:
                # Log at WARNING — an orphaned file should be visible to operators.
                log_warning(f"Cleanup failed for '{f}': {cleanup_err}")
        return False
