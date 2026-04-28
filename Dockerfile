# Multi-stage Dockerfile for Email Attachment Reader
# Deploy on: Heroku, Railway.app, Replit, or any Docker-supported platform

FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY . .

# Create directories for logs and attachments
RUN mkdir -p attachments logs

# Set environment variables with defaults
ENV PYTHONUNBUFFERED=1
ENV LOG_FILE=/app/logs/app.log
ENV DOWNLOAD_FOLDER=/app/attachments
ENV DATABASE_PATH=/app/processed_emails.db

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD test -f /app/heartbeat.txt && test $(( $(date +%s) - $(stat -c %Y /app/heartbeat.txt) )) -lt 120

# Run the application
CMD ["python", "main.py"]
