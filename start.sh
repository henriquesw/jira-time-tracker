#!/bin/bash

# Check if pip is installed
if ! command -v pip &> /dev/null; then
    echo "pip could not be found. Please install pip."
    exit 1
fi

echo "Checking and installing requirements..."
pip install -r requirements.txt

# Store the exit code of the pip install command
if [ $? -ne 0 ]; then
    echo "Error installing requirements. Please check your Python environment."
    exit 1
fi

echo "Starting Jira Time Tracker..."
# Open webpage first
if command -v python3 &> /dev/null; then
    (sleep 2 && python3 -m webbrowser "http://127.0.0.1:5000") &
elif command -v python &> /dev/null; then
    (sleep 2 && python -m webbrowser "http://127.0.0.1:5000") &
fi

# Try running with python3 first, then python
if command -v python3 &> /dev/null; then
    python3 jira-time.py
else
    if command -v python &> /dev/null; then
        python jira-time-tracker.py
    else
        echo "Python could not be found. Please install Python."
        exit 1
    fi
fi
