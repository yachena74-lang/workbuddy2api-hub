@echo off
setlocal EnableExtensions
rem ===========================================================
rem  WorkBuddy2API-Hub - CN edition background launcher
rem
rem  Usage: double-click, or  start-cn.bat [port]
rem
rem  Starts the proxy as a detached background process, then exits.
rem  Closing this window will NOT stop the server. Use stop.bat.
rem
rem  Sets WB_PROXY_DEFAULT_REALM=cn so the gateway defaults to the
rem  domestic upstream (copilot.tencent.com / www.codebuddy.cn).
rem
rem  ASCII-only and CRLF on purpose: .bat files are read using the
rem  console code page, and non-ASCII text or LF-only line endings
rem  break the cmd.exe parser.
rem ===========================================================

set "PORT=%~1"
if "%PORT%"=="" set "PORT=8788"
set "HERE=%~dp0"
set "SCRIPT=%HERE%wb_proxy.py"
set "DAEMON=%HERE%wb_daemon.py"

rem ---- CN edition: default realm ----
set "WB_PROXY_DEFAULT_REALM=cn"

if not exist "%SCRIPT%" goto no_script
if not exist "%DAEMON%" goto no_daemon

set "PYEXE="

rem ---- 1) bundled runtime (ships with the packaged zip) ----
if not exist "%HERE%python\python.exe" goto try_managed
"%HERE%python\python.exe" --version >nul 2>nul
if errorlevel 1 goto try_managed
set "PYEXE=%HERE%python\python.exe"
goto run

:try_managed
rem ---- 2) WorkBuddy managed runtime ----
if not exist "%USERPROFILE%\.workbuddy\binaries\python\versions\3.13.12\python.exe" goto try_path
"%USERPROFILE%\.workbuddy\binaries\python\versions\3.13.12\python.exe" --version >nul 2>nul
if errorlevel 1 goto try_path
set "PYEXE=%USERPROFILE%\.workbuddy\binaries\python\versions\3.13.12\python.exe"
goto run

:try_path
rem ---- 3) python on PATH ----
python --version >nul 2>nul
if errorlevel 1 goto try_py
set "PYEXE=python"
goto run

:try_py
rem ---- 4) py launcher ----
py --version >nul 2>nul
if errorlevel 1 goto no_python
set "PYEXE=py"
goto run

:no_python
echo [ERROR] No usable Python found (need Python 3.11+).
echo         Install from https://www.python.org/downloads/
echo         and tick "Add python.exe to PATH".
echo.
pause
exit /b 1

:no_script
echo [ERROR] wb_proxy.py not found next to this script.
echo         expected: %SCRIPT%
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
echo   WorkBuddy2API-Hub  [CN edition]
echo.
echo   API      : http://127.0.0.1:%PORT%/v1
echo   Dashboard: http://127.0.0.1:%PORT%/
echo.
echo   Python   : %PYEXE%
echo   Realm    : cn  (www.codebuddy.cn / copilot.tencent.com)
echo.
echo   Panel password is "admin" on first run - change it in the panel.
echo ===========================================================
echo.

"%PYEXE%" "%DAEMON%" start --port %PORT%
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" goto start_failed

echo   The proxy is running in the BACKGROUND.
echo   Closing this window will NOT stop it.
echo   Run stop.bat when you want to stop the proxy.
echo.
echo   Logs: %HERE%run\wb-proxy.out.log
echo         %HERE%run\wb-proxy.err.log
goto done

:start_failed
echo [ERROR] Background startup failed. See the log output above.
echo         Logs: %HERE%run\

:done
echo.
pause
exit /b %RC%