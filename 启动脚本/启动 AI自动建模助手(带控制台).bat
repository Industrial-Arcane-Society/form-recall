@echo off
chcp 65001 >nul
title AI 自动建模助手
cd /d "%~dp0"
echo ============================================
echo   AI 自动建模助手
echo ============================================
echo.
where python >nul 2>nul
if errorlevel 1 (
  echo [错误] 未找到 python, 请先安装 Python 3.9+ 并勾选 Add to PATH
  pause
  exit /b 1
)
python -m ai_modeler %*
if errorlevel 1 (
  echo.
  echo [程序异常退出] 请把上面的错误信息截图反馈。
  pause
)
