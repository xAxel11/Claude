@echo off
title J.A.R.V.I.S.
cd /d "%~dp0"

rem ---- find Python -------------------------------------------------------
set PY=
where py >nul 2>nul && set PY=py -3
if not defined PY where python >nul 2>nul && set PY=python
if not defined PY (
    echo Python is not installed.
    echo Install Python 3.10+ from https://www.python.org/downloads/
    echo and TICK "Add python.exe to PATH" during setup, then run this again.
    start https://www.python.org/downloads/
    pause
    exit /b 1
)

rem ---- first run: create venv and install packages -----------------------
if not exist ".venv\Scripts\python.exe" (
    echo First run: setting up Jarvis. This takes a few minutes...
    %PY% -m venv .venv || goto :fail
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt || goto :fail
)

rem ---- API key -----------------------------------------------------------
if not exist ".env" (
    copy ".env.example" ".env" >nul
    echo.
    echo Paste your Gemini API key after GEMINI_API_KEY= in the Notepad window,
    echo save it, close Notepad, and Jarvis will start.
    notepad ".env"
)

".venv\Scripts\python.exe" run.py
if errorlevel 1 goto :fail
exit /b 0

:fail
echo.
echo Something went wrong - see the messages above.
pause
exit /b 1
