@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"
echo ===================================
echo   EmailAssetcues Health Check
echo ===================================
echo.

:: --- Heartbeat check ---
if exist heartbeat.txt (
    set /p LAST_BEAT=<heartbeat.txt
    echo Last heartbeat : !LAST_BEAT!

    :: Check staleness — warn if heartbeat is older than 2 minutes
    powershell -NoProfile -Command ^
        "$hb = Get-Content heartbeat.txt -Raw; " ^
        "$ts = [datetime]::ParseExact($hb.Trim(), 'yyyy-MM-dd HH:mm:ss', $null); " ^
        "$age = [int](New-TimeSpan -Start $ts -End (Get-Date)).TotalSeconds; " ^
        "if ($age -gt 120) { Write-Host ('WARNING: Heartbeat is ' + $age + 's old — app may be stuck or down') } " ^
        "else { Write-Host ('Heartbeat age    : ' + $age + 's  [OK]') }"
) else (
    echo WARNING: heartbeat.txt not found — app has not completed a cycle yet or is not running.
)

echo.

:: --- Task Scheduler status ---
echo Task Scheduler:
schtasks /query /tn "EmailAssetcues" /fo LIST 2>nul | findstr /i "Status Last Run"
if %errorlevel% neq 0 (
    echo   Task not registered. Run setup_autostart.bat as Administrator to register it.
)

echo.

:: --- Worker log (last 5 lines) ---
if exist worker.log (
    echo Recent watchdog log:
    powershell -NoProfile -Command "Get-Content worker.log -Tail 5"
) else (
    echo worker.log not found — watchdog has not run yet.
)

echo.
pause
