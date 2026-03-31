# Email Attachment Processor

Automated tool to download attachments from Gmail and organize them locally. Runs continuously, checks for new emails every 30 seconds, and saves attachments with automatic deduplication.

## Features

✨ **Core Features:**
- Automatically connects to Gmail via IMAP
- Searches for unseen emails with attachments
- Downloads and saves attachments with automatic filename sanitization
- Prevents duplicate processing using SQLite database
- Labels processed emails in Gmail (`Attachment_Mails`)
- Marks emails as read after processing
- Automatic retry on connection failures (max 5 retries)
- Detailed logging to `app.log` file
- 30-second timeout on network connections to prevent hanging

📊 **Safety Features:**
- 20MB email size limit (configurable)
- Unique filenames to prevent overwrites
- Rotating log files (5MB max, 3 backups)
- Transaction-safe database operations
- Graceful error handling and logging

---

## Installation

### Prerequisites
- Python 3.8 or higher
- Gmail account with IMAP enabled
- Gmail App Password (not account password)

### Setup Steps

1. **Clone or download the project**
   ```bash
   cd Email_Assetcues
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Setup Gmail credentials**
   
   Create a `.env` file in the project root:
   ```env
   EMAIL_USER=your-email@gmail.com
   EMAIL_PASS=your-app-password
   ```

   **To get Gmail App Password:**
   - Enable 2-Factor Authentication on your Gmail account
   - Go to https://myaccount.google.com/apppasswords
   - Select "Mail" and "Windows Computer"
   - Copy the generated 16-character password
   - Paste it in the `.env` file

4. **Create attachments folder (auto-created on first run)**
   ```bash
   mkdir attachments
   ```

---

## Usage

### Run the application
```bash
python main.py
```

The script will:
1. Connect to Gmail's IMAP server
2. Search for unseen emails
3. Download attachments to the `attachments/` folder
4. Mark emails as processed
5. Wait 30 seconds before checking again
6. Repeat indefinitely until stopped (Ctrl+C)

### Check logs
```bash
tail -f app.log          # Linux/Mac
Get-Content app.log      # Windows PowerShell
```

---

## Configuration

All settings can be customized via environment variables in `.env`:

```env
# Email Settings
EMAIL_USER=your-email@gmail.com
EMAIL_PASS=your-app-password
IMAP_SERVER=imap.gmail.com
IMAP_PORT=993

# Email Processing (in MB and seconds)
MAX_EMAIL_SIZE_MB=20
SOCKET_TIMEOUT_SEC=30
ATTACHMENT_INBOX=inbox

# File Storage
DOWNLOAD_FOLDER=attachments
MAX_FILENAME_ATTEMPTS=1000

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
```

---

## Project Structure

```
Email_Assetcues/
├── main.py                 # Main application loop
├── email_reader.py         # Gmail IMAP connection
├── attachment_handler.py   # Attachment processing & file handling
├── logger.py              # Logging configuration
├── config.py              # Settings management
├── requirements.txt       # Python dependencies
├── .env                   # Credentials (DO NOT COMMIT)
├── .gitignore            # Git ignore rules
├── attachments/          # Downloaded files folder
├── processed_emails.db   # SQLite database (tracks processed emails)
└── app.log              # Application logs
```

---

## Key Functions

| Module | Function | Purpose |
|--------|----------|---------|
| **main.py** | `main()` | Main loop: check emails, process, retry |
| **main.py** | `init_db()` | Create/connect to database |
| **main.py** | `is_processed()` | Check if email already processed |
| **main.py** | `save_processed()` | Mark email as processed |
| **email_reader.py** | `connect_mail()` | Connect to Gmail IMAP server |
| **attachment_handler.py** | `process_email()` | Download attachments from email |
| **attachment_handler.py** | `sanitize_filename()` | Clean invalid filename characters |
| **attachment_handler.py** | `get_unique_filepath()` | Generate unique filename if collision |
| **logger.py** | `log_info()` | Log info-level messages |
| **logger.py** | `log_error()` | Log error-level messages |

---

## Troubleshooting

### ❌ "Email credentials missing in .env"
**Solution:** Create `.env` file with `EMAIL_USER` and `EMAIL_PASS`

### ❌ "Max retries reached"
**Solution:** Check your internet connection and Gmail IMAP is enabled

### ❌ Emails not downloading
**Solution:** 
1. Check if attachments exist in the email
2. Check if email is under 20MB size limit
3. Review `app.log` for detailed errors
4. Verify Gmail IMAP is enabled (Settings > Forwarding > IMAP)

### ❌ Permission denied when saving files
**Solution:** Check write permissions on `attachments/` folder

### ❌ Duplicate emails keep processing
**Solution:** Delete `processed_emails.db` to reset (will re-download previous emails)

---

## Security Notes

⚠️ **IMPORTANT:**
- **Never hardcode credentials** in the code
- **Never commit `.env` file** to Git
- Use Gmail **App Passwords**, not your actual password
- Keep `.gitignore` updated to prevent accidental credential exposure

---

## Performance

- **Memory:** ~50MB baseline
- **CPU:** Minimal (mostly idle, wakes up every 30 seconds)
- **Network:** ~1KB per email check + attachment size
- **Storage:** Depends on attachments downloaded

---

## Logs Example

```
2026-03-31 14:23:45,123 - INFO - Connected to email server
2026-03-31 14:23:46,456 - INFO - Processing Email: Invoice #123
2026-03-31 14:23:47,789 - INFO - Downloaded: attachments/Invoice_123.pdf
2026-03-31 14:23:48,012 - INFO - Label assigned
```

---

## Limitations

- Only IMAP protocol supported (Gmail, Outlook, etc.)
- SQLite database (not scalable for millions of emails)
- Single-threaded processing
- 20MB email size limit (soft limit, can be configured)

---

## Future Improvements

- [ ] Multi-threaded attachment processing
- [ ] PostgreSQL support for scalability
- [ ] REST API for status/health checks
- [ ] Email filtering by sender/subject
- [ ] Automatic attachment organization by date/type
- [ ] Docker containerization
- [ ] Kubernetes deployment manifest
- [ ] Unit tests and integration tests
- [ ] CI/CD pipeline

---

## Support

For issues or questions, check:
1. `app.log` for detailed error messages
2. Gmail IMAP status (Settings > Forwarding and POP/IMAP)
3. Environment variables in `.env`
4. Network connectivity

---

## License

This project is provided as-is for personal use.

---

**Last Updated:** March 31, 2026
**Version:** 1.0.0-production
