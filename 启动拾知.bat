@echo off
setlocal
title StudyLoop - Local Study Assistant
cd /d "%~dp0"

rem ============ locate Python ============
set "PY="
where py >nul 2>nul
if not errorlevel 1 set "PY=py -3"
if defined PY goto check_py
where python >nul 2>nul
if not errorlevel 1 set "PY=python"
if not defined PY goto no_py

:check_py
%PY% -c "import sys" >nul 2>nul
if errorlevel 1 goto no_py
goto run

rem ============ python missing ============
:no_py
echo.
echo   [ERROR] Python 3.10 or newer was not found on this computer.
echo.
echo   1. Download it from  https://www.python.org/downloads/
echo   2. During setup, tick  "Add python.exe to PATH"
echo   3. Then double-click this file again.
echo.
pause
exit /b 1

rem ============ start the app ============
:run
echo.
echo   Starting StudyLoop with: %PY%
echo   Keep this window open while you study. Close it to quit.
echo.
%PY% -u app.py --open
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" echo   [!] Exited with code %RC% - please read the messages above.
if "%RC%"=="0" echo   Server stopped normally. Your data is saved in the data folder.
echo   Press any key to close this window.
pause >nul
exit /b %RC%