@echo off
echo Checking and installing requirements...
pip install -r requirements.txt
if %errorlevel% neq 0 (
    echo Error installing requirements. Please check if Python and pip are installed and added to PATH.
    pause
    exit /b %errorlevel%
)

echo Starting Jira Time Tracker...
start "Jira Time Tracker" /min cmd /k python jira-time-tracker.py
timeout /t 5 > nul
start "" "http://127.0.0.1:5000"