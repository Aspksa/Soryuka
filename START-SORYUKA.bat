@echo off
setlocal DisableDelayedExpansion
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 launch.py
  goto end
)
where python >nul 2>nul
if %errorlevel%==0 (
  python launch.py
  goto end
)
echo Python 3.11+ not found.
pause
exit /b 1
:end
if errorlevel 1 pause
