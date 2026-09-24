@echo off
chcp 65001 >nul
title «–ªª DSH ƒ£–Õ
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo [¥ÌŒÛ] Œ¥’“µΩ python
  pause & exit /b 1
)
python "%~dp0tools\switch_dsh_model.py"
echo.
pause
