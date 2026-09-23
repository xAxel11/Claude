@echo off
cd /d "%~dp0"
echo Removing the old environment and reinstalling packages...
if exist ".venv" rmdir /s /q ".venv"
call Jarvis.bat
