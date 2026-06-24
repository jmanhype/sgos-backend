#!/usr/bin/env python3
"""
SGOS Database Backup Script
Creates timestamped backups of the SQLite database with retention policy.

Usage:
    python scripts/backup.py              # Create backup
    python scripts/backup.py --list       # List backups
    python scripts/backup.py --prune 7    # Keep only last 7 days
"""
import argparse
import gzip
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path


def get_backup_dir() -> Path:
    """Get or create backup directory."""
    backup_dir = Path(__file__).parent.parent / "backups"
    backup_dir.mkdir(exist_ok=True)
    return backup_dir


def create_backup(db_path: Path) -> Path:
    """Create a compressed backup of the database."""
    if not db_path.exists():
        print(f"❌ Database not found: {db_path}")
        sys.exit(1)

    backup_dir = get_backup_dir()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_name = f"sgos_{timestamp}.db.gz"
    backup_path = backup_dir / backup_name

    # Compress database
    with open(db_path, "rb") as f_in:
        with gzip.open(backup_path, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)

    # Get sizes
    original_size = db_path.stat().st_size
    backup_size = backup_path.stat().st_size
    ratio = (backup_size / original_size * 100) if original_size > 0 else 0

    print(f"✅ Backup created: {backup_path}")
    print(f"   Original: {original_size / 1024 / 1024:.2f} MB")
    print(f"   Backup:   {backup_size / 1024 / 1024:.2f} MB ({ratio:.1f}%)")

    return backup_path


def list_backups():
    """List all backups with timestamps."""
    backup_dir = get_backup_dir()
    backups = sorted(backup_dir.glob("sgos_*.db.gz"), reverse=True)

    if not backups:
        print("No backups found.")
        return

    print(f"Found {len(backups)} backup(s):\n")
    for backup in backups:
        mtime = datetime.fromtimestamp(backup.stat().st_mtime)
        size = backup.stat().st_size
        print(f"  {backup.name:30s}  {mtime.strftime('%Y-%m-%d %H:%M')}  {size / 1024:.1f} KB")


def prune_backups(days: int):
    """Remove backups older than N days."""
    backup_dir = get_backup_dir()
    cutoff = datetime.now() - timedelta(days=days)
    removed = 0

    for backup in backup_dir.glob("sgos_*.db.gz"):
        mtime = datetime.fromtimestamp(backup.stat().st_mtime)
        if mtime < cutoff:
            backup.unlink()
            removed += 1

    print(f"🗑️  Removed {removed} backup(s) older than {days} days.")


def main():
    parser = argparse.ArgumentParser(description="SGOS Database Backup")
    parser.add_argument("--db", default="sgos.db", help="Database path (default: sgos.db)")
    parser.add_argument("--list", action="store_true", help="List all backups")
    parser.add_argument("--prune", type=int, metavar="DAYS", help="Remove backups older than N days")
    args = parser.parse_args()

    if args.list:
        list_backups()
    elif args.prune:
        prune_backups(args.prune)
    else:
        create_backup(Path(args.db))


if __name__ == "__main__":
    main()
