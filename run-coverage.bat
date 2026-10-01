@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -STA -File "%~dp0run-coverage.ps1" %*
if errorlevel 1 (
  echo.
  echo [ERROR] See the message above.
)
pause
