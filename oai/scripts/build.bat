@echo off
rem build.bat -- build Oai on Windows.
rem
rem   scripts\build.bat            build with whatever compiler is on PATH
rem   scripts\build.bat clean
rem
rem Works with mingw-w64 (gcc) or with MSVC when run from a Visual Studio
rem Developer Command Prompt. The Python build script does the actual work so
rem there is only one place where compiler flags live.
rem
rem Part of Oai. SPDX-License-Identifier: MIT

setlocal
cd /d "%~dp0.."

if "%1"=="clean" (
    if exist build rmdir /s /q build
    if exist dist rmdir /s /q dist
    if exist bin rmdir /s /q bin
    echo cleaned
    goto :eof
)

where python >nul 2>nul
if errorlevel 1 (
    where py >nul 2>nul
    if errorlevel 1 (
        echo build.bat: Python 3 was not found on PATH.
        echo   Install it from https://python.org and tick "Add to PATH".
        exit /b 1
    )
    set PY=py -3
) else (
    set PY=python
)

%PY% tools\build_exe.py --name Oai %*
if errorlevel 1 exit /b 1

echo.
echo Built dist\Oai.exe -- run it by double-clicking, or from this prompt:
echo     dist\Oai.exe
endlocal
