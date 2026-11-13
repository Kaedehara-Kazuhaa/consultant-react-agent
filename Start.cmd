@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo First run Setup.cmd to install dependencies.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m ad_agent.launch %*
if errorlevel 1 pause
