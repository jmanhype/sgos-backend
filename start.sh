#!/bin/bash
# SGOS Backend Server Startup Script
# Starts the FastAPI backend on port 8420

cd ~/sgos-backend

# Kill any existing instance
pkill -f "uvicorn main:app.*8420" 2>/dev/null
sleep 1

# Start server
echo "🚀 Starting SGOS Backend on :8420..."
uv run python -m uvicorn main:app --host 0.0.0.0 --port 8420
