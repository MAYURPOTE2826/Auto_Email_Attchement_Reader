@echo off
setlocal EnableDelayedExpansion
:: EmailAssetcues — Windows Task Scheduler Auto-Start Setup
:: Run this script as Administrator to register the app to start on boot.

NET SESSION >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Please run this script as Administrator.
    echo Right-click setup_autostart.bat and choose "Run as administrator"
    pause
    exit /b 1
)

SET TASK_NAME=EmailAssetcues
SET SCRIPT_DIR=%~dp0
SET WORKER_PATH=%SCRIPT_DIR%run_worker.bat
SET PYTHON_PATH=

:: Auto-detect Python
for /f "delims=" %%i in ('where python 2^>nul') do (
    if "!PYTHON_PATH!"=="" SET PYTHON_PATH=%%i
)

if "%PYTHON_PATH%"=="" (
    echo ERROR: Python not found on PATH.
    echo Install Python and make sure it is on your PATH, then re-run this script.
    pause
    exit /b 1
)

echo Detected Python: %PYTHON_PATH%
echo Worker path:     %WORKER_PATH%
echo Task name:       %TASK_NAME%
echo.

:: Remove existing task if present
schtasks /delete /tn "%TASK_NAME%" /f >nul 2>&1

:: Create new task — runs at system startup, continues running even if no user is logged in
schtasks /create ^
    /tn "%TASK_NAME%" ^
    /tr "\"%WORKER_PATH%\"" ^
    /sc onstart ^
    /delay 0000:01 ^
    /ru "SYSTEM" ^
    /rl HIGHEST ^
    /f

if %errorlevel% == 0 (
    echo.
    echo ✅ Auto-start registered successfully.
    echo    The processor will start automatically every time Windows boots.
    echo.
    echo Useful commands:
    echo   Start now:   schtasks /run /tn "%TASK_NAME%"
    echo   Stop:        schtasks /end /tn "%TASK_NAME%"
    echo   Remove:      schtasks /delete /tn "%TASK_NAME%" /f
    echo   Status:      schtasks /query /tn "%TASK_NAME%"
) else (
    echo.
    echo ❌ Failed to register task. Check you are running as Administrator.
)

pause
