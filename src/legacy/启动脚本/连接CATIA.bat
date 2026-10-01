@echo off
setlocal
chcp 65001>nul
set "ROOT=%~dp0.."
cd /d "%ROOT%"
where python >nul 2>nul
if errorlevel 1 (
  echo Python 3.9 or later was not found.
  pause
  exit /b 1
)
python "%ROOT%\tools\connect_catia.py" %*
pause
