#!/bin/bash
# SGOS Backend Server Startup Script
# Starts the FastAPI backend on port 8420
# Default host is 127.0.0.1 (loopback dev mode). Set SGOS_HOST for other bindings.
# NOTE: SGOS_HOST is exported so pydantic-settings and uvicorn see the same value.

cd ~/sgos-backend

# Kill any existing instance
pkill -f "uvicorn main:app.*8420" 2>/dev/null
sleep 1

# Export canonical host — pydantic-settings reads this via env_prefix="SGOS_"
export SGOS_HOST="${SGOS_HOST:-127.0.0.1}"

# Start server
echo "🚀 Starting SGOS Backend on ${SGOS_HOST}:8420..."
uv run python -m uvicorn main:app --host "$SGOS_HOST" --port 8420
