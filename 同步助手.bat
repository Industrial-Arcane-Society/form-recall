@echo off
rem ============================================================
rem  Sync Assistant launcher  (PURE ASCII - do not put Chinese here!)
rem
rem  cmd.exe parses a .bat with the console code page, so Chinese
rem  text plus "chcp 65001" inside the same file shreds the script.
rem  All Chinese output comes from tools\sync_menu.py (UTF-8),
rem  printed after we switch the console to UTF-8 below.
rem
rem  Flow: cd to project root -> find python -> run the menu.
rem  Uses goto only (no parenthesised blocks: "echo." inside a block
rem  can trip cmd with ". was unexpected at this time.").
rem ============================================================
chcp 65001 >nul
title Sync Assistant
cd /d "%~dp0"
if exist "tools\sync_menu.py" goto menu_found
if not exist "..\tools\sync_menu.py" goto no_menu
cd ..

:menu_found
set "PY="
where python >nul 2>nul && set "PY=python"
if defined PY goto run_menu
if exist "%LOCALAPPDATA%\Programs\Python\Python314\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
if defined PY goto run_menu
if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
if defined PY goto run_menu
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if defined PY goto run_menu
if exist "C:\Python314\python.exe" set "PY=C:\Python314\python.exe"
if defined PY goto run_menu

echo.
echo [ERROR] python not found.
echo         Install Python 3.9+ and tick "Add Python to PATH".
goto the_end

:no_menu
echo.
echo [ERROR] tools\sync_menu.py not found.
echo         Put this .bat in the project root (next to the tools folder).
echo         Current dir: %CD%
goto the_end

:run_menu
%PY% "tools\sync_menu.py"
set "RC=%ERRORLEVEL%"
if "%RC%"=="0" goto the_end
echo.
echo [NOTE] exit code %RC% - some step did not succeed, scroll up for details.

:the_end
echo.
pause