@echo off
setlocal
set "CITY_DASHBOARD_PY=python"
if defined CITY_PYTHON set "CITY_DASHBOARD_PY=%CITY_PYTHON%"
if not defined CITY_PYTHON if exist "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" set "CITY_DASHBOARD_PY=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
"%CITY_DASHBOARD_PY%" -X utf8 "%~dp0tools\run_dashboard.py" --open %*
if errorlevel 1 pause
