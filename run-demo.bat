@echo off
REM Starts JobPing on your own computer with fake data. Nothing is sent to anyone.
cd /d "%~dp0"

python --version >nul 2>nul
if errorlevel 1 (
  echo Python is not installed yet.
  echo 1. Download it from https://www.python.org/downloads/
  echo 2. Run the installer and TICK "Add python.exe to PATH"
  echo 3. Double-click this file again.
  pause
  exit /b 1
)

if not exist .venv (
  echo Setting up for the first time. This takes about a minute...
  python -m venv .venv
)
call .venv\Scripts\activate.bat
python -m pip install --quiet -r requirements.txt
if errorlevel 1 (
  echo Could not install the needed pieces. Check your internet connection and try again.
  pause
  exit /b 1
)

set DATABASE_PATH=%~dp0demo.db
python -m flask --app wsgi seed-demo
echo.
echo JobPing is starting. Your browser will open in a moment.
echo Log in with the email and password shown above. Close this window to stop.
start "" http://localhost:5000/login
python -m flask --app wsgi run
pause
