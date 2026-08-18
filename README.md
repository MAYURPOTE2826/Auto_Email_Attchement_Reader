# 📧 Email Attachment Processor

<div align="center">

# 🚀 Email Attachment Processor

### Automated Gmail Attachment Downloader & Production-Ready Email Worker

A reliable **24/7 Windows automation service** that continuously monitors Gmail/IMAP inboxes, detects emails containing attachments, securely downloads and organizes those attachments, tracks processing state with SQLite, automatically retries failures, and recovers from crashes.

<br/>

![Python](https://img.shields.io/badge/Python-3776AB?style=for-the-badge\&logo=python\&logoColor=white)
![Gmail](https://img.shields.io/badge/Gmail-IMAP-EA4335?style=for-the-badge\&logo=gmail\&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-003B57?style=for-the-badge\&logo=sqlite\&logoColor=white)
![Windows](https://img.shields.io/badge/Windows-0078D6?style=for-the-badge\&logo=windows\&logoColor=white)

<br/>

**Automate → Download → Validate → Track → Retry → Monitor**

</div>

---

# 🌟 Overview

**Email Attachment Processor** is an automated background worker designed to continuously monitor a Gmail or other IMAP inbox and download email attachments without manual intervention.

The application is designed for **unattended 24/7 operation on Windows**.

It automatically:

* Connects to Gmail through IMAP
* Finds unseen emails containing attachments
* Validates incoming files
* Downloads attachments safely
* Prevents duplicate processing
* Tracks email processing state
* Applies Gmail labels
* Marks processed emails as read
* Retries temporary failures
* Maintains heartbeat monitoring
* Generates rotating logs
* Recovers from crashes
* Automatically restarts through a Windows watchdog

The project is designed with **reliability, security, fault tolerance, and production operation** in mind.

---

# 🎯 Problem Statement

Manually downloading email attachments becomes inefficient when an organization receives files continuously.

Typical problems include:

```text
📩 New Email
   ↓
👤 Manual Checking
   ↓
📎 Find Attachment
   ↓
⬇️ Download File
   ↓
📁 Organize File
   ↓
❌ Risk of Duplicate Download
   ↓
🔁 Repeat Manually
```

This project automates the entire workflow:

```text
📩 Email Arrives
      ↓
🤖 Processor Detects Email
      ↓
🔍 Validate Email & Attachment
      ↓
🛡️ Security Checks
      ↓
⬇️ Download Attachment
      ↓
🗄️ Store Processing Status
      ↓
🏷️ Label Email
      ↓
✅ Mark As Read
      ↓
💓 Update Heartbeat
      ↓
🔄 Check Again
```

---

# ✨ Core Features

## 📬 Automatic Email Monitoring

The worker continuously connects to Gmail or another IMAP-compatible mail server and searches for unseen emails with attachments.

The default processing cycle runs every **30 seconds**.

---

## 📎 Automatic Attachment Download

Attachments are automatically downloaded into the configured `attachments/` directory.

The system also sanitizes filenames and generates unique paths to prevent accidental overwrites.

---

## 🗄️ SQLite Processing Database

Every email is tracked using SQLite.

Processing states include:

```text
        ┌──────────────┐
        │   NEW EMAIL  │
        └──────┬───────┘
               ↓
        ┌──────────────┐
        │ IN_PROGRESS  │
        └──────┬───────┘
          ┌────┴────┐
          ↓         ↓
       SUCCESS    FAILURE
          ↓         ↓
        DONE      RETRY
                    ↓
                FAILED
```

This prevents duplicate downloads and allows the application to recover after crashes.

---

# 🛡️ Security Architecture

Security is one of the major design goals of this project.

## 🔒 File Size Protection

The system supports:

* Email size limits
* Per-attachment size limits
* Minimum free disk space checks

The default email-size limit is **20 MB**, with configurable attachment limits and a **500 MB minimum free disk-space check**.

---

## 🦠 Malware-Oriented File Validation

The application does not blindly trust file extensions.

It performs **magic-byte signature detection** to detect potentially dangerous files even when their extension or MIME type has been manipulated.

Blocked signatures include examples such as:

```text
MZ
ELF
Shell scripts
Archives
```

This helps protect against files that attempt to disguise their real format.

---

## 🚫 Extension & MIME Blocking

Configurable blocked extensions include dangerous executable/script formats such as:

```text
.exe
.bat
.cmd
.ps1
.js
.vbs
.msi
.dll
.jar
```

The application also supports MIME-type blocklists as an additional layer of defense.

---

# 🔐 Sender Authentication

For allowlisted senders, the application can enforce:

```text
DKIM
 +
SPF
 +
DMARC
```

This helps protect against sender spoofing and unauthorized email sources.

Configuration:

```env
ALLOWED_SENDERS=
REQUIRE_SENDER_AUTH=false
```

---

# 🔁 Fault Tolerance & Retry System

Temporary network or email-processing failures should not stop the worker.

The system implements:

* Connection retries
* Per-email retry budgets
* Exponential backoff
* Crash recovery
* `in_progress` protection
* Failed-email retry mode

The connection retry mechanism can be configured through:

```env
MAX_RETRIES=5
RETRY_WAIT_SEC=10
```

and per-email retry behavior through:

```env
MAX_EMAIL_RETRIES=3
```

---

# ⚡ Exponential Backoff

When consecutive connection failures occur, the application increases the waiting time between retries.

```text
Failure #1
   ↓
Wait
   ↓
Failure #2
   ↓
Longer Wait
   ↓
Failure #3
   ↓
Even Longer Wait
   ↓
Maximum 5 Minutes
```

This prevents aggressive repeated connection attempts during outages.

---

# 💓 Heartbeat Monitoring

The application writes a timestamp to:

```text
heartbeat.txt
```

after each completed processing cycle.

This allows an external monitoring system or administrator to determine whether the worker is still alive.

```text
Worker
  ↓
Process Emails
  ↓
Complete Cycle
  ↓
Update heartbeat.txt
  ↓
Wait
  ↓
Repeat
```

---

# 📊 Logging System

The application provides detailed logs through:

```text
app.log
```

Logs use rotating files to prevent unlimited disk growth.

Default configuration:

```env
LOG_FILE=app.log
LOG_MAX_BYTES=5242880
LOG_BACKUP_COUNT=3
LOG_LEVEL=INFO
```

JSON/NDJSON logging can also be enabled for log ingestion systems such as ELK, Datadog, or CloudWatch.

---

# 🚨 Alerting System

The application can send email alerts when:

* The worker stops unexpectedly
* Connection failures reach the degraded-state threshold
* Critical errors occur

A dedicated SMTP account can be configured for alerts, preventing the alerting mechanism from depending on the same IMAP credentials that may have failed.

---

# 🔐 Single Instance Protection

The application creates:

```text
app.lock
```

This prevents multiple instances of the processor from running simultaneously and competing for the same:

* SQLite database
* IMAP connection
* Email processing queue

This is particularly important for Windows auto-start environments.

---

# 🪟 Windows Auto-Start

The project is designed for continuous Windows operation.

It includes three important batch files:

| File                  | Purpose                                          |
| --------------------- | ------------------------------------------------ |
| `setup_autostart.bat` | Registers the worker with Windows Task Scheduler |
| `run_worker.bat`      | Watchdog that automatically restarts the worker  |
| `health_check.bat`    | Checks heartbeat, task status, and logs          |

---

# 🔄 Production Boot Flow

```text
                    WINDOWS BOOTS
                          │
                          ▼
                 Windows Task Scheduler
                          │
                          ▼
                 setup_autostart.bat
                          │
                          ▼
                   run_worker.bat
                          │
                          ▼
                      main.py
                          │
                 ┌────────┴────────┐
                 │                 │
              Running            Crash
                 │                 │
                 │                 ▼
                 │          Automatic Restart
                 │
                 ▼
             Email Worker
                 │
                 ▼
          Continuous Operation
```

The watchdog automatically launches `main.py` and restarts it if it crashes.

---

# 🔄 Complete Processing Workflow

Every processing cycle follows this pipeline:

```text
┌──────────────────────────┐
│ Connect to IMAP Server   │
└─────────────┬────────────┘
              ↓
┌──────────────────────────┐
│ Find Unseen Emails       │
└─────────────┬────────────┘
              ↓
┌──────────────────────────┐
│ Check Processing Status   │
└─────────────┬────────────┘
              ↓
┌──────────────────────────┐
│ Sender Authentication     │
└─────────────┬────────────┘
              ↓
┌──────────────────────────┐
│ File Security Validation  │
└─────────────┬────────────┘
              ↓
┌──────────────────────────┐
│ Download Attachment       │
└─────────────┬────────────┘
              ↓
┌──────────────────────────┐
│ Save Processing Status    │
└─────────────┬────────────┘
              ↓
┌──────────────────────────┐
│ Apply Gmail Label        │
└─────────────┬────────────┘
              ↓
┌──────────────────────────┐
│ Mark Email As Read       │
└─────────────┬────────────┘
              ↓
┌──────────────────────────┐
│ Update Heartbeat         │
└─────────────┬────────────┘
              ↓
             WAIT
              ↓
           REPEAT
```

The documented processing cycle explicitly includes IMAP connection, unseen-email search, duplicate checks, sender validation, magic-byte validation, attachment download, status updates, heartbeat writing, and repeated polling.

---

# 🧰 Technology Stack

### Programming

* Python 3.8+

### Email

* Gmail IMAP
* IMAP protocol
* SMTP for alerting

### Database

* SQLite
* SQLite WAL mode

### Automation

* Windows Task Scheduler
* Batch scripting
* Watchdog process

### Reliability

* Retry mechanism
* Exponential backoff
* Heartbeat monitoring
* Lockfile
* Graceful shutdown

### Security

* Magic-byte detection
* MIME validation
* Extension blocklists
* DKIM/SPF/DMARC verification
* Credential redaction
* File-size limits

---

# 📁 Project Structure

```text
Email_Assetcues/
│
├── main.py
│   └── Main application loop
│
├── db.py
│   └── SQLite database & retry state management
│
├── email_reader.py
│   └── Gmail / IMAP connection
│
├── attachment_handler.py
│   └── Attachment validation & downloading
│
├── logger.py
│   └── Logging configuration
│
├── config.py
│   └── Environment configuration
│
├── audit.py
│   └── Deployment readiness checker
│
├── test_email_processor.py
│   └── Tests
│
├── requirements.txt
│
├── .env.example
│
├── .gitignore
│
├── run_worker.bat
│
├── setup_autostart.bat
│
├── health_check.bat
│
├── attachments/
│   └── Downloaded files
│
├── processed_emails.db
│   └── Email processing state
│
├── app.lock
│   └── Single-instance protection
│
├── heartbeat.txt
│   └── Worker health timestamp
│
├── app.log
│   └── Application logs
│
└── worker.log
    └── Watchdog logs
```

The actual project structure and module responsibilities are documented in the source README.

---

# ⚙️ Installation

## 1️⃣ Clone the Repository

```bash
git clone <YOUR_GITHUB_REPOSITORY_URL>

cd Email_Assetcues
```

---

## 2️⃣ Install Python Dependencies

Python **3.8 or higher** is required.

```bash
pip install -r requirements.txt
```

---

# 🔐 Configure Gmail

Create a `.env` file using `.env.example`.

```bash
copy .env.example .env
```

Then configure:

```env
EMAIL_USER=your-email@gmail.com
EMAIL_PASS=your-app-password
```

The application requires a **Gmail App Password**, not the normal Google account password.

---

# 🔑 Gmail App Password Setup

1. Enable 2-Factor Authentication.
2. Open Google Account security settings.
3. Create an App Password.
4. Use the generated password in `.env`.
5. Never commit `.env` to GitHub.

---

# ▶️ Run the Application

Start the processor manually:

```bash
python main.py
```

To retry previously failed emails:

```bash
python main.py --retry-failed
```

---

# 🪟 Enable Windows Auto-Start

For production-style operation:

### Step 1

Right-click:

```text
setup_autostart.bat
```

### Step 2

Select:

```text
Run as administrator
```

### Step 3

The application will be registered with Windows Task Scheduler and automatically start when Windows boots.

---

# 🩺 Health Check

Run:

```text
health_check.bat
```

It provides information about:

* Heartbeat age
* Task Scheduler status
* Recent logs
* Worker health

This makes diagnosing an unattended worker significantly easier.

---

# ⚙️ Configuration

The application is highly configurable through `.env`.

Important settings include:

```env
# Email
EMAIL_USER=
EMAIL_PASS=

# IMAP
IMAP_SERVER=imap.gmail.com
IMAP_PORT=993

# Processing
MAX_EMAIL_SIZE_MB=20
MAX_ATTACHMENT_SIZE_MB=0
CHECK_INTERVAL_SEC=30

# Storage
DOWNLOAD_FOLDER=attachments
MIN_FREE_DISK_MB=500

# Database
DATABASE_PATH=processed_emails.db

# Retry
MAX_RETRIES=5
RETRY_WAIT_SEC=10
MAX_EMAIL_RETRIES=3

# Processing limit
MAX_EMAILS_PER_CYCLE=0

# Logging
LOG_LEVEL=INFO

# Security
ALLOWED_SENDERS=
REQUIRE_SENDER_AUTH=false
```

The complete configuration is documented in the project's `.env` settings.

---

# 📊 Performance

The project is designed to operate as a lightweight background worker.

Documented baseline characteristics:

```text
Memory  → ~50 MB
CPU     → Minimal
Polling → Every 30 seconds
Network → ~1 KB per email check + attachment size
Storage → Depends on downloaded attachments
```

---

# 🧪 Testing & Deployment Readiness

Before deploying, the project includes:

```bash
python audit.py
```

The audit checks configuration attributes, module imports, and security settings before deployment.

Possible results:

```text
READY
   or
NOT READY
```

---

# 📜 Example Logs

```text
2026-04-03T09:12:01.001 - INFO - Database initialized
2026-04-03T09:12:01.452 - INFO - Connected to imap.gmail.com:993
2026-04-03T09:12:02.103 - INFO - Found 2 unseen emails
2026-04-03T09:12:03.210 - INFO - Processing Email ID: 42
2026-04-03T09:12:04.887 - INFO - Downloaded: Invoice_April.pdf
2026-04-03T09:12:05.001 - INFO - Email marked Seen
2026-04-03T09:12:05.120 - INFO - Waiting 30 seconds
```

The project includes structured application and watchdog logging for operational visibility.

---

# 🧠 Engineering Concepts Demonstrated

This project demonstrates practical backend and automation engineering concepts:

### 🔄 Automation

Continuous background processing without manual intervention.

### 🗄️ State Management

Persistent email processing states using SQLite.

### 🔐 Security

File validation, sender authentication, credential protection, and malware-oriented checks.

### 🛡️ Fault Tolerance

Retries, backoff, crash recovery, and watchdog restart.

### 💓 Health Monitoring

Heartbeat-based worker health detection.

### 🔒 Concurrency Protection

Single-instance lockfile prevents competing workers.

### 📋 Observability

Rotating logs and health diagnostics.

### ⚙️ Production Operations

Windows Task Scheduler + watchdog architecture.

---

# 💼 Interview Explanation

### "Explain your Email Attachment Processor project."

> **I developed an automated email attachment processing system in Python that continuously monitors Gmail through IMAP and downloads attachments from unseen emails. The main challenge was making the system reliable enough to run continuously without manual supervision. I implemented SQLite-based state tracking with statuses such as `in_progress`, `done`, `retry`, and `failed` to prevent duplicate processing and recover from crashes. I also added retry logic with exponential backoff, heartbeat monitoring, rotating logs, a single-instance lockfile, and a Windows watchdog that automatically restarts the application if it crashes. From a security perspective, the system validates file extensions, MIME types, and magic-byte signatures, supports sender authentication through DKIM/SPF/DMARC, and protects credentials through environment variables.**

---

# 🔥 Strong Interview Points

If an interviewer asks **"What makes this more than a simple automation script?"**, highlight:

```text
Simple Script
     ❌
     │
     ▼
Downloads Attachments

Production Worker
     ✅
     │
     ├── State Management
     ├── Retry Logic
     ├── Crash Recovery
     ├── Security Validation
     ├── Logging
     ├── Health Monitoring
     ├── Alerting
     ├── Lockfile Protection
     ├── Watchdog
     └── Windows Auto-Start
```

That distinction makes the project much stronger as a **backend / Python / automation / DevOps-oriented portfolio project**.

---

# ⚠️ Limitations

Current limitations include:

* IMAP-based processing
* SQLite is not intended for millions of emails
* Single-threaded processing
* Windows Task Scheduler is currently used for native auto-start

These limitations are documented in the project specification.

---

# 🚀 Future Improvements

Planned improvements include:

* [ ] Multi-threaded attachment processing
* [ ] PostgreSQL support
* [ ] REST API for health/status monitoring
* [ ] Advanced email filtering
* [ ] Automatic organization by sender
* [ ] Automatic organization by date
* [ ] Automatic organization by file type
* [ ] Docker support
* [ ] Linux systemd service
* [ ] CI/CD pipeline
* [ ] Centralized monitoring dashboard

---

# 🏆 Project Highlights

<div align="center">

| Feature                  | Implementation                        |
| ------------------------ | ------------------------------------- |
| 📧 Email Monitoring      | Gmail / IMAP                          |
| 📎 Attachment Processing | Python                                |
| 🗄️ State Management     | SQLite                                |
| 🔁 Retry System          | Exponential Backoff                   |
| 🛡️ File Security        | Magic Bytes + MIME + Extension Checks |
| 🔐 Sender Verification   | DKIM / SPF / DMARC                    |
| 💓 Health Monitoring     | Heartbeat                             |
| 📊 Logging               | Rotating Logs                         |
| 🚨 Alerting              | SMTP                                  |
| 🔒 Process Protection    | Lockfile                              |
| 🔄 Crash Recovery        | Watchdog                              |
| 🪟 Auto Start            | Windows Task Scheduler                |

</div>

---

# 🌟 Why This Project Matters

This project is more than an email downloader.

It demonstrates how to turn a simple automation requirement into a **reliable, secure, continuously running production-style worker**.

The architecture focuses on:

```text
              RELIABILITY
                   ▲
                   │
        ┌──────────┼──────────┐
        │          │          │
     SECURITY   MONITORING   RECOVERY
        │          │          │
        └──────────┼──────────┘
                   │
                   ▼
          PRODUCTION WORKER
```

---

# 📌 Quick Reference

```text
Project       : Email Attachment Processor
Language      : Python
Email         : Gmail / IMAP
Database      : SQLite
Platform      : Windows
Execution     : Background Worker
Monitoring    : Heartbeat + Logs
Recovery      : Retry + Watchdog
Security      : Multi-Layer Validation
Startup       : Windows Task Scheduler
```

---

# ⭐ Support

If you find this project useful or interesting, consider giving the repository a ⭐ **Star**.

Feedback, suggestions, and contributions are welcome.

---

<div align="center">

# 📧 Automate Emails. Secure Attachments. Never Miss a File.

### Built with ❤️ using Python

**© 2026 Mayur Pote**

</div>
