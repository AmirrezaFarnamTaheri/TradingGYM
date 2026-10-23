@echo off
setlocal
REM Keep task output visible and resolve the runner independently of the caller's directory.
powershell -NoProfile -File "%~dp0Invoke-Project.ps1" %*
exit /b %errorlevel%
