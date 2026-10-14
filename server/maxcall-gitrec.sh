#!/usr/bin/env bash
# MaxCall recordings -> private repo hxg3/maxcall-recordings (age-encrypted).
# Rolling 30-day window + 4GB size guard + monthly history squash.
# Log: /var/log/maxcall-gitrec.log
set -u
REPO=/root/maxcall-recordings-repo
DAY=$(date +%F)
MAXBYTES=$((4 * 1024 * 1024 * 1024))
AGE_TO="age13cqchjgjr73glsutdjge830nwep96lj9lfx6gsu8k0ejvmxtdgcspfsrqh"
export GIT_SSH_COMMAND="ssh -i /root/.ssh/maxcall-rec -o BatchMode=yes"
{
echo "=== gitrec $DAY $(date +%T) ==="
cd "$REPO" || { echo "NO REPO DIR"; exit 1; }
git pull --ff-only 2>&1 | tail -1
mkdir -p rec
tar czf - /var/spool/asterisk/monitor 2>/dev/null | age -r "$AGE_TO" -o "rec/recordings-$DAY.tgz.age" && echo "archived OK $(du -h rec/recordings-$DAY.tgz.age | cut -f1)"
# 1) window: drop archives older than 30 days
for f in rec/recordings-*.tgz.age; do
  [ -e "$f" ] || continue
  d=$(basename "$f" | grep -o '[0-9]\{4\}-[0-9]\{2\}-[0-9]\{2\}' | head -1)
  if [ -n "$d" ] && [ "$d" \< "$(date -d '30 days ago' +%F)" ]; then git rm -q "$f" && echo "window-pruned $f"; fi
done
# 2) size guard: while .git > 4GB keep only newest 7 archives
size_now() { du -sb .git | cut -f1; }
while [ "$(size_now)" -gt "$MAXBYTES" ]; do
  victim=$(ls rec/recordings-*.tgz.age 2>/dev/null | sort | head -n -7 | head -1)
  [ -z "$victim" ] && { echo "SIZE GUARD: cannot shrink further"; break; }
  git rm -q "$victim" && echo "size-pruned $victim"
done
echo "repo size: $(du -sh .git | cut -f1)"
# 3) monthly squash on the 1st: collapse history so .git ~= window size
SQUASHED=0
if [ "$(date +%d)" = "01" ]; then
  git add -A && git -c user.name=maxcall-server -c user.email=maxcall-server@localhost commit -qm "squash-point $DAY" 2>/dev/null
  if git checkout -q --orphan _squash && git add -A && git -c user.name=maxcall-server -c user.email=maxcall-server@localhost commit -qm "snapshot $DAY" \
    && git branch -q -D main && git branch -q -m main; then SQUASHED=1; echo "squashed"; fi
fi
git add -A
git diff --cached --quiet || git -c user.name=maxcall-server -c user.email=maxcall-server@localhost commit -qm "recordings $DAY"
if [ "$SQUASHED" = "1" ]; then git push --force origin main 2>&1 | tail -1; else git push origin main 2>&1 | tail -1; fi
echo "=== done ==="
} >> /var/log/maxcall-gitrec.log 2>&1
