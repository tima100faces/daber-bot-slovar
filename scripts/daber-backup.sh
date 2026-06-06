#!/bin/bash
# DABER DB backup — pg_dump to project folder
# Runs daily at 3am

BACKUP_DIR="/root/daber-dict/backups"
DB_NAME="daber_dict"
DB_PORT="5434"
TIMESTAMP=$(date +%Y-%m-%d_%H%M)

mkdir -p "$BACKUP_DIR"

PGPASSWORD=*** pg_dump -h 127.0.0.1 -p "$DB_PORT" -U postgres -Fc "$DB_NAME" \
    > "$BACKUP_DIR/daber_$TIMESTAMP.dump" 2>&1

if [ $? -eq 0 ]; then
    # Keep only last 7 backups
    ls -t "$BACKUP_DIR"/daber_*.dump 2>/dev/null | tail -n +8 | xargs rm -f 2>/dev/null
    echo "Backup saved: daber_$TIMESTAMP.dump ($(du -h "$BACKUP_DIR/daber_$TIMESTAMP.dump" | cut -f1))"
else
    echo "Backup FAILED"
    exit 1
fi
