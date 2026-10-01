@echo off
setlocal
chcp 65001>nul
set "ROOT=%~dp0.."
cd /d "%ROOT%"
echo AI Modeler legacy console launcher
where python >nul 2>nul
if errorlevel 1 (
  echo Python 3.9 or later was not found.
  pause
  exit /b 1
)
python -m ai_modeler %*
if errorlevel 1 pause
