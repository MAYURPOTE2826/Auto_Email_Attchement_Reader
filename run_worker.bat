@echo off
setlocal EnableDelayedExpansion
:: EmailAssetcues — Watchdog Wrapper
:: Runs main.py and automatically restarts it if it exits or crashes.
:: This script should be invoked by Task Scheduler (via setup_autostart.bat),
:: not run directly in production.

:: Always run from the script's own directory so all relative paths (.env,
:: app.log, heartbeat.txt, processed_emails.db) resolve correctly.
cd /d "%~dp0"

:: Auto-detect Python
SET PYTHON_PATH=
for /f "delims=" %%i in ('where python 2^>nul') do (
    if "!PYTHON_PATH!"=="" SET PYTHON_PATH=%%i
)

if "%PYTHON_PATH%"=="" (
    echo [%date% %time%] ERROR: Python not found on PATH. Exiting watchdog. >> worker.log
    exit /b 1
)

echo [%date% %time%] Watchdog started. Python: %PYTHON_PATH% >> worker.log

:loop
echo [%date% %time%] Starting main.py... >> worker.log
"%PYTHON_PATH%" main.py %*
SET EXIT_CODE=%errorlevel%

if %EXIT_CODE% == 0 (
    :: Clean exit — user sent shutdown signal or max retries exhausted gracefully.
    :: Do not restart; let the Task Scheduler handle the next boot start.
    echo [%date% %time%] main.py exited cleanly (code 0). Watchdog stopping. >> worker.log
    exit /b 0
)

echo [%date% %time%] main.py exited with code %EXIT_CODE% — restarting in 10 seconds... >> worker.log
timeout /t 10 /nobreak >nul
goto loop
