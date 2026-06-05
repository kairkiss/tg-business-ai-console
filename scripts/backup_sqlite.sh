#!/usr/bin/env bash
#
# backup_sqlite.sh — Backup the SQLite database before migration.
#
# Usage:
#   ./scripts/backup_sqlite.sh
#
# This script:
#   1. Checks if data.sqlite3 exists
#   2. Creates a timestamped backup in backups/
#   3. Sets permissions to 600 (owner-only read/write)
#
# Safety:
#   - Does NOT read .env
#   - Does NOT print any tokens or keys
#   - Exits 0 gracefully if no database exists

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DB_FILE="$PROJECT_ROOT/data.sqlite3"
BACKUP_DIR="$PROJECT_ROOT/backups"

# Check if database exists
if [ ! -f "$DB_FILE" ]; then
    echo "ℹ️  No data.sqlite3 found at $DB_FILE"
    echo "   Nothing to backup. Exiting gracefully."
    exit 0
fi

# Create backup directory if it doesn't exist
mkdir -p "$BACKUP_DIR"

# Generate timestamp
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_FILE="$BACKUP_DIR/data_v5_pre_migration_${TIMESTAMP}.sqlite3"

# Create backup
cp "$DB_FILE" "$BACKUP_FILE"

# Set restrictive permissions
chmod 600 "$BACKUP_FILE"

# Report
FILE_SIZE="$(wc -c < "$BACKUP_FILE" | tr -d ' ')"
echo "✅ Backup created: $BACKUP_FILE"
echo "   Size: $FILE_SIZE bytes"
echo "   Permissions: 600 (owner-only)"

# Also create a "latest" symlink for convenience
LATEST_LINK="$BACKUP_DIR/data_v5_latest.sqlite3"
rm -f "$LATEST_LINK"
ln -s "$(basename "$BACKUP_FILE")" "$LATEST_LINK"
echo "   Latest symlink: $LATEST_LINK"
