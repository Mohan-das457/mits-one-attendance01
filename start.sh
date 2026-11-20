#!/bin/bash
# MITS ONE Attendance Server Launcher
cd "$(dirname "$0")"

echo "============================================="
echo "🎓 Starting MITS ONE Attendance App..."
echo "============================================="

# Ensure virtual environment exists
if [ ! -d ".venv" ]; then
    echo "📦 Setting up Python virtual environment..."
    python3 -m venv .venv
    .venv/bin/pip install -r requirements.txt
fi

echo "🚀 Launching server..."
.venv/bin/python3 server.py
