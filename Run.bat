@echo off
rem Double-click to start Policy Lookup on Windows (64-bit Intel/AMD). Fully offline.
cd /d "%~dp0"
set "PY=runtime\windows\python\python.exe"
if not exist "%PY%" (
  echo Bundled Python not found at %PY%
  pause
  exit /b 1
)
"%PY%" launch.py
pause
