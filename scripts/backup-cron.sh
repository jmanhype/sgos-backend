#!/bin/bash
# SGOS Database Backup Cron Job
# Run daily at 2 AM: 0 2 * * * /path/to/sgos-backend/scripts/backup-cron.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

# Activate venv if it exists
if [ -f ".venv/bin/activate" ]; then
    source .venv/bin/activate
fi

# Create backup
python3 scripts/backup.py

# Keep only last 30 days
python3 scripts/backup.py --prune 30

echo "$(date): Backup completed"
