@echo off
chcp 65001 >nul
title 一键连接 CATIA
cd /d "%~dp0"
set PY=
where python >nul 2>nul && set PY=python
if "%PY%"=="" if exist "%LOCALAPPDATA%\Programs\Python\Python314\python.exe" set PY="%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
if "%PY%"=="" if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set PY="%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if "%PY%"=="" if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set PY="%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if "%PY%"=="" (
  echo [错误] 未找到 python, 请先安装 Python 3.9+ 并勾选 Add Python to PATH
  pause & exit /b 1
)
%PY% "%~dp0tools\connect_catia.py" %*
echo.
pause
