#!/usr/bin/env bash
# MaxCall git backup: DB + config + app + docs -> private repo hxg3/maxcall-backup
# Encrypted with age (recipient = off-server key). Retention: 30 days. Log: /var/log/maxcall-gitbackup.log
set -u
REPO=/root/maxcall-backup-repo
DAY=$(date +%F)
AGE_TO="age13cqchjgjr73glsutdjge830nwep96lj9lfx6gsu8k0ejvmxtdgcspfsrqh"
export GIT_SSH_COMMAND="ssh -i /root/.ssh/maxcall-backup -o BatchMode=yes"
{
echo "=== gitbackup $DAY $(date +%T) ==="
cd "$REPO" || { echo "NO REPO DIR"; exit 1; }
git pull --ff-only 2>&1 | tail -1
mkdir -p db conf app docs
for DB in maxcall_dashboard asterisk ombutel; do
  if mysqldump -u root "$DB" 2>/dev/null | gzip | age -r "$AGE_TO" -o "db/$DB-$DAY.sql.gz.age"; then
    echo "db-$DB OK"
  else
    echo "db-$DB FAILED"; rm -f "db/$DB-$DAY.sql.gz.age"
  fi
done
tar czf - /opt/maxcall-dashboard 2>/dev/null | age -r "$AGE_TO" -o "app/opt-maxcall-$DAY.tgz.age" && echo "app OK"
tar czf - /etc/asterisk/vitalpbx 2>/dev/null | age -r "$AGE_TO" -o "conf/asterisk-vitalpbx-$DAY.tgz.age" && echo "conf OK"
# prune snapshots older than 30 days (keep history shallow for old days)
find db conf app -name "*-*.sql.gz.age" -o -name "*-*.tgz.age" | while read -r f; do
  d=$(basename "$f" | grep -o '[0-9]\{4\}-[0-9]\{2\}-[0-9]\{2\}' | head -1)
  if [ -n "$d" ] && [ "$d" \< "$(date -d '30 days ago' +%F)" ]; then git rm -q "$f" && echo "pruned $f"; fi
done
git add -A
if git diff --cached --quiet; then
  echo "nothing new"
else
  git -c user.name=maxcall-server -c user.email=maxcall-server@localhost commit -qm "backup $DAY" && echo "committed"
fi
git push origin main 2>&1 | tail -1
echo "=== done ==="
} >> /var/log/maxcall-gitbackup.log 2>&1
