@echo off
rem ONE-TIME setup with internet (fresh copy from GitHub). Afterwards Run.bat works offline.
cd /d "%~dp0"
set "PY=runtime\windows\python\python.exe"
if not exist "%PY%" (
  echo Downloading a private copy of Python into this folder...
  if not exist runtime\windows mkdir runtime\windows
  curl.exe -L --fail -o runtime\python-win.tar.gz "https://github.com/astral-sh/python-build-standalone/releases/download/20261110/cpython-3.12.16%%2B20261110-x86_64-pc-windows-msvc-install_only.tar.gz" || goto :fail
  tar.exe -xzf runtime\python-win.tar.gz -C runtime\windows || goto :fail
  del runtime\python-win.tar.gz
)
"%PY%" setup_runtime.py || goto :fail
echo.
echo Setup finished. Double-click Run.bat to start (no internet needed).
pause
exit /b 0
:fail
echo.
echo Setup did not finish. Check the internet connection and run Setup.bat again - it continues where it stopped.
pause
exit /b 1
