#!/usr/bin/env bash
# Nightly backup of the SQLite database living in the `avisa-data` Docker volume.
# The whole app state (editions, sources, settings) is that one file.
#
# Install as a root cron job, e.g. `crontab -e`:
#   15 3 * * *  /opt/avisa/deploy/backup.sh >> /var/log/avisa-backup.log 2>&1
set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-/backup/avisa}"
VOLUME="${VOLUME:-avisa-data}"
KEEP_DAYS="${KEEP_DAYS:-14}"

mkdir -p "$BACKUP_DIR"
STAMP="$(date +%F)"

# Use sqlite3 .backup for a consistent copy even while the app is writing.
docker run --rm \
  -v "$VOLUME":/data \
  -v "$BACKUP_DIR":/backup \
  nouchka/sqlite3 \
  sqlite3 /data/avisa.db ".backup '/backup/avisa-$STAMP.db'"

# Prune backups older than KEEP_DAYS.
find "$BACKUP_DIR" -name 'avisa-*.db' -type f -mtime +"$KEEP_DAYS" -delete

echo "✓ Backed up to $BACKUP_DIR/avisa-$STAMP.db"
