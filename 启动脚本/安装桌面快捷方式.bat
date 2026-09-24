@echo off
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo [错误] 未找到 python, 请先安装 Python 3.9+ 并勾选 Add Python to PATH
  pause & exit /b 1
)
python "%~dp0tools\install_shortcut.py"
echo.
pause
