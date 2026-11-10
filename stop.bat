@echo off
setlocal EnableExtensions
rem ===========================================================
rem  Stop WorkBuddy2API-Hub CN edition background service.
rem
rem  Usage: double-click, or  stop.bat [port]
rem
rem  [port] is only used to detect an older foreground instance.
rem  A background instance is stopped through run\wb-proxy.json.
rem ===========================================================

set "PORT=%~1"
if "%PORT%"=="" set "PORT=8788"
set "HERE=%~dp0"
set "DAEMON=%HERE%wb_daemon.py"

if not exist "%DAEMON%" goto no_daemon

set "PYEXE="

if not exist "%HERE%python\python.exe" goto try_managed
"%HERE%python\python.exe" --version >nul 2>nul
if errorlevel 1 goto try_managed
set "PYEXE=%HERE%python\python.exe"
goto run

:try_managed
if not exist "%USERPROFILE%\.workbuddy\binaries\python\versions\3.13.12\python.exe" goto try_path
"%USERPROFILE%\.workbuddy\binaries\python\versions\3.13.12\python.exe" --version >nul 2>nul
if errorlevel 1 goto try_path
set "PYEXE=%USERPROFILE%\.workbuddy\binaries\python\versions\3.13.12\python.exe"
goto run

:try_path
python --version >nul 2>nul
if errorlevel 1 goto try_py
set "PYEXE=python"
goto run

:try_py
py --version >nul 2>nul
if errorlevel 1 goto no_python
set "PYEXE=py"
goto run

:no_python
echo [ERROR] No usable Python found (need Python 3.11+).
echo.
pause
exit /b 1

:no_daemon
echo [ERROR] wb_daemon.py not found next to this script.
echo         expected: %DAEMON%
echo.
pause
exit /b 1

:run
echo ===========================================================
echo   Stopping WorkBuddy2API-Hub  [CN edition]
echo ===========================================================
echo.

"%PYEXE%" "%DAEMON%" stop --port %PORT%
set "RC=%ERRORLEVEL%"
echo.
pause
exit /b %RC%