@echo off
setlocal enabledelayedexpansion

REM Create logs directory if it doesn't exist
if not exist "logs" mkdir "logs"

REM Generate a timestamp for the log file
for /f "tokens=2 delims==" %%I in ('wmic os get localdatetime /format:list 2^>nul') do set datetime=%%I
if "%datetime%"=="" (
    REM Fallback to PowerShell if wmic fails
    for /f %%I in ('powershell -NoProfile -Command "Get-Date -Format \"yyyy-MM-dd_HH-mm-ss\""') do set timestamp=%%I
) else (
    set timestamp=%datetime:~0,4%-%datetime:~4,2%-%datetime:~6,2%_%datetime:~8,2%-%datetime:~10,2%-%datetime:~12,2%
)
set LOG_FILE=logs\invoke-project-%timestamp%.log

echo Logging output to %LOG_FILE%
echo.

REM Simple wrapper to run the PowerShell task runner with the same arguments.
set SCRIPT_DIR=%~dp0
if "%~1"=="" (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%\Invoke-Project.ps1" > "%LOG_FILE%" 2>&1
) else (
    powershell -NoProfile -ExecutionPolicy Bypass -File "%SCRIPT_DIR%\Invoke-Project.ps1" -Task "%~1" > "%LOG_FILE%" 2>&1
)

if errorlevel 1 (
    echo.
    echo Error: Script failed. Please check the error messages in %LOG_FILE%.
    exit /b 1
)

echo.
echo Script completed successfully. Log file is available at %LOG_FILE%
echo.

endlocal
