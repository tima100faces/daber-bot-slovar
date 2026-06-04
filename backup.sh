#!/bin/bash
# DABER daily backup — keeps last 7 copies
set -e

BACKUP_DIR="/root/daber-dict/backups"
DB_HOST="127.0.0.1"
DB_PORT="5434"
DB_NAME="daber_dict"
DB_USER="postgres"
KEEP=7

mkdir -p "$BACKUP_DIR"

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
FILE="$BACKUP_DIR/daber_$TIMESTAMP.dump"

pg_dump -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
    --format=custom --compress=9 --no-owner --no-privileges \
    -f "$FILE"

# Remove old backups, keep last KEEP
ls -1t "$BACKUP_DIR"/daber_*.dump 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm -f

# Silent on success — cron no_agent=true delivers nothing when stdout is empty
# Non-zero exit = error alert to Tim
