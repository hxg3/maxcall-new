#!/usr/bin/env bash
# MaxCall nightly backup: databases + app + asterisk config + recordings.
# Retention: 7 days. Log: /var/log/maxcall-backup.log
set -u
DEST=/var/backups/maxcall
DAY=$(date +%F)
mkdir -p "$DEST"
{
echo "=== backup $DAY $(date +%T) ==="
for DB in maxcall_dashboard asterisk ombutel; do
  if mysqldump -u root "$DB" 2>/dev/null | gzip > "$DEST/db-$DB-$DAY.sql.gz"; then
    echo "db-$DB OK $(du -h "$DEST/db-$DB-$DAY.sql.gz" | cut -f1)"
  else
    echo "db-$DB FAILED"
  fi
done
tar czf "$DEST/app-$DAY.tgz" /opt/maxcall-dashboard 2>/dev/null && echo "app OK" || echo "app FAILED"
tar czf "$DEST/asterisk-conf-$DAY.tgz" /etc/asterisk/vitalpbx 2>/dev/null && echo "conf OK" || echo "conf FAILED"
tar czf "$DEST/recordings-$DAY.tgz" /var/spool/asterisk/monitor 2>/dev/null && echo "recordings OK" || echo "recordings FAILED"
find "$DEST" \( -name '*.gz' -o -name '*.tgz' \) -mtime +7 -delete
ls "$DEST"
echo "=== done ==="
} >> /var/log/maxcall-backup.log 2>&1
