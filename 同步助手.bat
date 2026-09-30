@echo off
rem ============================================================
rem  Sync Assistant launcher  (PURE ASCII - no Chinese bytes here!)
rem
rem  cmd.exe parses a .bat with the console code page; Chinese text
rem  plus "chcp 65001" inside the same file shreds the script.
rem  All Chinese output comes from tools\sync_menu.py (UTF-8).
rem
rem  This launcher prints something IMMEDIATELY (so a black window
rem  never means "silently stuck"), reports which python it uses,
rem  and points at the log file written by sync_menu.py.
rem ============================================================
chcp 65001 >nul
title Sync Assistant
cd /d "%~dp0"
echo [Sync Assistant] starting...
if not exist "tools\sync_menu.py" if exist "..\tools\sync_menu.py" cd ..
echo [Sync Assistant] dir    : %CD%

set "PY="
where python >nul 2>nul && set "PY=python"
if defined PY goto check_py
if exist "%LOCALAPPDATA%\Programs\Python\Python314\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
if defined PY goto check_py
if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if defined PY goto check_py
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if defined PY goto check_py
if exist "C:\Python314\python.exe" set "PY=C:\Python314\python.exe"
if defined PY goto check_py

echo.
echo [ERROR] python not found.
echo         Install Python 3.9+ and tick "Add Python to PATH".
goto the_end

:check_py
rem ????? python.exe ??"???"????????? -> ?????????
%PY% -c "import sys" >nul 2>nul
if errorlevel 1 goto bad_py
echo [Sync Assistant] python : %PY%
goto menu_path

:bad_py
echo.
echo [ERROR] this python cannot run: %PY%
echo         It may be the Microsoft Store alias stub.
echo         Install real Python 3.9+ (python.org) and tick "Add Python to PATH".
goto the_end

:menu_path
if not exist "tools\sync_menu.py" goto no_menu
%PY% -u "tools\sync_menu.py"
set "RC=%ERRORLEVEL%"
if "%RC%"=="0" goto the_end
echo.
echo [NOTE] exit code %RC% - some step did not succeed, scroll up for details.
echo        Full log: _sync_assistant.log in this folder.
goto the_end

:no_menu
echo.
echo [ERROR] tools\sync_menu.py not found.
echo         Put this .bat in the project root (next to the tools folder).
echo         Current dir: %CD%

:the_end
echo.
pause