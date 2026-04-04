# Email Attachment Processor

Automated tool to download attachments from Gmail and organize them locally. Runs continuously, checks for new emails on a configurable interval, and saves attachments with automatic deduplication. Designed for unattended 24/7 operation on Windows.

## Features

**Core Features:**
- Automatically connects to Gmail via IMAP
- Searches for unseen emails with attachments
- Downloads and saves attachments with automatic filename sanitization
- Prevents duplicate processing using SQLite with per-email status tracking (`done`, `in_progress`, `failed`)
- Labels processed emails in Gmail (`Attachment_Mails`)
- Marks emails as read after processing
- Automatic retry on connection failures (configurable, default 5 retries)
- Detailed logging to `app.log` with rotating log files
- 30-second timeout on network connections to prevent hanging
- Graceful shutdown on `Ctrl+C` or `SIGTERM` — finishes current email before stopping
- Interruptible sleep — shutdown signal wakes the wait immediately
- Heartbeat file (`heartbeat.txt`) updated each cycle for external health monitoring

**Safety Features:**
- 20MB email size limit (configurable)
- 500MB minimum free disk space check (configurable)
- Unique filenames to prevent overwrites
- Rotating log files (5MB max, 3 backups)
- Transaction-safe database operations
- `in_progress` status prevents duplicate downloads if app crashes mid-email
- Email alert when app stops unexpectedly (optional, via `ALERT_EMAIL`)

**Windows Auto-Start:**
- `run_worker.bat` — watchdog that auto-restarts `main.py` on crash
- `setup_autostart.bat` — registers the watchdog with Windows Task Scheduler (runs on boot)
- `health_check.bat` — diagnostic tool to check heartbeat, task status, and recent logs

---

## Installation

### Prerequisites
- Python 3.8 or higher
- Gmail account with IMAP enabled
- Gmail App Password (not your account password)

### Setup Steps

1. **Clone or download the project**
   ```bash
   cd Email_Assetcues
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure credentials**

   Copy `.env.example` to `.env` and fill in your credentials:
   ```bash
   copy .env.example .env
   ```
   ```env
   EMAIL_USER=your-email@gmail.com
   EMAIL_PASS=your-app-password
   ```

   **To get a Gmail App Password:**
   - Enable 2-Factor Authentication on your Google account
   - Go to https://myaccount.google.com/apppasswords
   - Select "Mail" and "Windows Computer"
   - Copy the generated 16-character password into `.env`

4. **Register for auto-start on boot (optional but recommended)**
   - Right-click `setup_autostart.bat` → **Run as administrator**
   - The processor will now start automatically every time Windows boots

---

## Usage

### Run manually
```bash
python main.py
```

### Run with failed-email retry
```bash
python main.py --retry-failed
```
Clears all `failed` entries from the database so those emails are retried this run.

### Each cycle the app will:
1. Connect to Gmail's IMAP server
2. Search for unseen emails
3. Skip already-processed emails (status `done` or `failed`)
4. Download attachments to the `attachments/` folder
5. Mark emails as processed
6. Write a heartbeat timestamp to `heartbeat.txt`
7. Wait (default 30 seconds) before checking again
8. Repeat until stopped or max retries reached

### Check logs
```bash
# Windows PowerShell
Get-Content app.log -Wait

# Windows Command Prompt
type app.log
```

---

## Windows Auto-Start (Production)

Three batch files manage the production lifecycle:

| File | Purpose | How to run |
|------|---------|------------|
| `setup_autostart.bat` | Registers the app with Task Scheduler to start on every boot | Run as Administrator (once) |
| `run_worker.bat` | Watchdog — launches `main.py` and auto-restarts it if it crashes | Launched automatically by Task Scheduler |
| `health_check.bat` | Shows heartbeat age, task status, and recent watchdog log | Double-click anytime |

**Boot flow:**
```
Windows boots
  → Task Scheduler (after 1-minute delay)
      → run_worker.bat  (watchdog)
          → main.py  (email processor)
              → auto-restarts on crash, stops on clean exit
```

**Useful Task Scheduler commands:**
```bat
schtasks /run /tn "EmailAssetcues"       # Start now
schtasks /end /tn "EmailAssetcues"       # Stop
schtasks /delete /tn "EmailAssetcues" /f # Remove
schtasks /query /tn "EmailAssetcues"     # Check status
```

---

## Configuration

All settings are set via environment variables in `.env`. See `.env.example` for a full template.

```env
# Email Settings (required)
EMAIL_USER=your-email@gmail.com
EMAIL_PASS=your-app-password
IMAP_SERVER=imap.gmail.com
IMAP_PORT=993
ATTACHMENT_INBOX=inbox

# Email Processing
MAX_EMAIL_SIZE_MB=20
SOCKET_TIMEOUT_SEC=30

# File Storage
DOWNLOAD_FOLDER=attachments
MAX_FILENAME_ATTEMPTS=100
MIN_FREE_DISK_MB=500

# Database
DATABASE_PATH=processed_emails.db

# Logging
LOG_FILE=app.log
LOG_MAX_BYTES=5242880
LOG_BACKUP_COUNT=3
LOG_LEVEL=INFO

# Application Behavior
CHECK_INTERVAL_SEC=30
MAX_RETRIES=5
RETRY_WAIT_SEC=10

# Alerting — optional, sends an email when the app stops unexpectedly
ALERT_EMAIL=
ALERT_SMTP_HOST=smtp.gmail.com
ALERT_SMTP_PORT=465

# Logging — set LOG_JSON=true for ELK / Datadog / CloudWatch ingestion
LOG_JSON=false

# Per-email retry budget (0 = fail immediately, default 3 = up to 4 total attempts)
MAX_EMAIL_RETRIES=3

# MIME bomb protection — cap on MIME parts parsed per email
MAX_MIME_PARTS=200

# Security — sender allowlist (empty = accept from anyone)
# Example: ALLOWED_SENDERS=boss@company.com,accounts@vendor.com
ALLOWED_SENDERS=

# Security — blocked file extensions (comma-separated, with leading dot)
BLOCKED_EXTENSIONS=.exe,.bat,.cmd,.com,.pif,.scr,.vbs,.js,.ps1,.msi,.jar,.lnk,.dll,.zip,.7z,.rar,.tar,.gz,.iso,.img,.dmg

# Security — blocked MIME Content-Types
BLOCKED_MIME_TYPES=application/x-msdownload,application/x-executable,application/x-sh,application/x-bat
```

---

## Project Structure

```
Email_Assetcues/
├── main.py                 # Main application loop, alerting, shutdown handling
├── db.py                   # SQLite database layer (status tracking, retry logic)
├── email_reader.py         # Gmail IMAP connection
├── attachment_handler.py   # Attachment processing & file handling
├── logger.py               # Logging configuration (plain-text + JSON/NDJSON)
├── config.py               # Settings management (reads from .env)
├── requirements.txt        # Python dependencies
├── .env                    # Credentials — DO NOT COMMIT
├── .env.example            # Template for .env
├── .gitignore              # Git ignore rules
├── run_worker.bat          # Watchdog wrapper (auto-restart on crash)
├── setup_autostart.bat     # Register watchdog with Windows Task Scheduler
├── health_check.bat        # Diagnostic — heartbeat, task status, recent logs
├── attachments/            # Downloaded attachment files
├── processed_emails.db     # SQLite DB (tracks processed email status)
├── heartbeat.txt           # Timestamp of last completed cycle
├── app.log                 # Application logs
└── worker.log              # Watchdog logs (start/restart/stop events)
```

---

## Key Functions

| Module | Function | Purpose |
|--------|----------|---------|
| **main.py** | `main(retry_failed)` | Main loop: check emails, process, retry |
| **main.py** | `send_alert()` | Email alert on fatal errors |
| **db.py** | `init_db()` | Create/migrate SQLite database schema |
| **db.py** | `get_email_status()` | Returns `done`, `in_progress`, `failed`, or `None` |
| **db.py** | `mark_in_progress()` | Mark email as started (crash guard) |
| **db.py** | `save_processed()` | Mark email as fully done |
| **db.py** | `mark_failed()` | Mark email as permanently failed |
| **db.py** | `reset_failed_emails()` | Clear failed/retry entries for reprocessing |
| **email_reader.py** | `connect_mail()` | Connect to IMAP server, returns connection |
| **attachment_handler.py** | `process_email()` | Orchestrate full attachment pipeline |
| **attachment_handler.py** | `sanitize_filename()` | Sanitize filename (path traversal, dot-file safe) |
| **attachment_handler.py** | `get_unique_filepath()` | Atomic unique filename (TOCTOU-safe) |

---

## Troubleshooting

### "Email credentials missing in .env"
Copy `.env.example` to `.env` and set `EMAIL_USER` and `EMAIL_PASS`.

### "Max retries reached"
Check your internet connection and that Gmail IMAP is enabled (Gmail Settings > Forwarding and POP/IMAP).

### Emails not downloading
1. Confirm the email has attachments and is under the `MAX_EMAIL_SIZE_MB` limit
2. Review `app.log` for detailed errors
3. Verify IMAP is enabled in Gmail settings

### App is stuck / not processing
Run `health_check.bat` — it will show the heartbeat age and warn if the app is stale.

### Permission denied saving files
Check write permissions on the `attachments/` folder.

### Want to retry previously failed emails
```bash
python main.py --retry-failed
```
Or manually delete `processed_emails.db` to reset everything (will re-download all emails).

### Task Scheduler not starting the app
Run `setup_autostart.bat` as Administrator. Check status with:
```bat
schtasks /query /tn "EmailAssetcues"
```

---

## Security Notes

- **Never hardcode credentials** in source code
- **Never commit `.env`** — it is listed in `.gitignore`
- Use Gmail **App Passwords**, not your actual Google account password
- `ALERT_EMAIL` uses SMTP over SSL (port 465) — no plain-text transmission

---

## Performance

- **Memory:** ~50MB baseline
- **CPU:** Minimal — mostly idle, wakes every 30 seconds
- **Network:** ~1KB per email check + attachment size
- **Storage:** Depends on attachments downloaded; app warns if disk space falls below `MIN_FREE_DISK_MB`

---

## Logs Example

```
2026-04-03T09:12:01.001 - INFO     - main.py:197   - [-] - Database initialized at processed_emails.db
2026-04-03T09:12:01.452 - INFO     - email_reader.py:76 - [-] - Connected to imap.gmail.com:993
2026-04-03T09:12:02.103 - INFO     - main.py:405   - [-] - Found 2 unseen emails
2026-04-03T09:12:03.210 - INFO     - main.py:408   - [email-42] - Processing Email ID: 42
2026-04-03T09:12:04.887 - INFO     - attachment_handler.py:271 - [email-42] - Downloaded: attachments/Invoice_April.pdf
2026-04-03T09:12:05.001 - INFO     - attachment_handler.py:303 - [email-42] - Email marked Seen and Gmail label applied
2026-04-03T09:12:05.120 - INFO     - main.py:520   - [-] - Waiting 30 seconds before next check
```

---

## Limitations

- IMAP protocol only (works with Gmail, Outlook, and other IMAP providers)
- SQLite database — not designed for millions of emails
- Single-threaded processing
- Windows Task Scheduler auto-start only (no native Linux systemd service included)

---

## Future Improvements

- [ ] Multi-threaded attachment processing
- [ ] PostgreSQL support for scalability
- [ ] REST API for status/health checks
- [ ] Email filtering by sender/subject/date
- [ ] Automatic attachment organization by date/type/sender
- [ ] Docker containerization
- [ ] Linux systemd service unit
- [ ] CI/CD pipeline

---

## Support

For issues, check in this order:
1. Run `health_check.bat` for a quick system overview
2. Check `app.log` for detailed error messages
3. Check `worker.log` for watchdog restart history
4. Verify Gmail IMAP is enabled (Settings > Forwarding and POP/IMAP)
5. Confirm `.env` has correct credentials

---

## License

This project is provided as-is for personal use.

---

**Last Updated:** April 4, 2026
**Version:** 1.2.0-production
