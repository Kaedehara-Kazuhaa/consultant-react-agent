@echo off
cd /d "%~dp0"
where codex >nul 2>&1
if errorlevel 1 (
  echo Official Codex CLI was not found.
  pause
  exit /b 1
)
codex login status >nul 2>&1
if errorlevel 1 (
  codex login
  if errorlevel 1 (
    pause
    exit /b 1
  )
)
call "%~dp0Start.cmd" --provider codex %*
