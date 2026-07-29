#!/bin/bash
set -euo pipefail

DB_URL="${DATABASE_URL:-postgresql://analizador:analizador_pass@localhost:5432/analizador}"
UPLOAD_DIR="${UPLOAD_DIR:-/data/uploads}"
BACKUP_DIR="${BACKUP_DIR:-/data/backups}"
RETENTION_DAYS="${RETENTION_DAYS:-7}"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

mkdir -p "$BACKUP_DIR"

echo "Backing up database..."
pg_dump "$DB_URL" > "$BACKUP_DIR/db_$TIMESTAMP.sql"
echo "OK: $(wc -c < "$BACKUP_DIR/db_$TIMESTAMP.sql") bytes"

echo "Backing up uploads..."
tar -czf "$BACKUP_DIR/files_$TIMESTAMP.tar.gz" -C "$(dirname "$UPLOAD_DIR")" "$(basename "$UPLOAD_DIR")"
echo "OK: $(wc -c < "$BACKUP_DIR/files_$TIMESTAMP.tar.gz") bytes"

echo "Cleaning backups older than $RETENTION_DAYS days..."
find "$BACKUP_DIR" -maxdepth 1 -name "db_*.sql"      -mtime "+$RETENTION_DAYS" -delete -print
find "$BACKUP_DIR" -maxdepth 1 -name "files_*.tar.gz" -mtime "+$RETENTION_DAYS" -delete -print

echo "Backup complete: $TIMESTAMP"
