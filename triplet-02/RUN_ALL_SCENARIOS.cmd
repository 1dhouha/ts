@echo off
cd /d "%~dp0"
title City Brain - Test Book scenarios
if not exist "brain\.venv\Scripts\python.exe" (echo Run brain\SETUP_AND_RUN.cmd once first. & pause & exit /b 1)
brain\.venv\Scripts\python.exe scenarios\run.py --all
echo.
echo Logs are in the logs folder.
pause
