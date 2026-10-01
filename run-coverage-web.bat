@echo off
setlocal
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run-coverage-web.ps1" %*
if errorlevel 1 (
  echo.
  echo [ERROR] See the message above.
)
pause
