#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ملخص تقلبات جلسات التأمين من /var/lib/maxcall-broker/session-events.jsonl.

لكل تأمين: عدد مرات موت الجلسة، متوسط عمر الجلسة الفعلي (من استعادة إلى
موت)، آخر قفل/فتح، آخر الأحداث. يُستخدم للتطوير ولمعرفة هوامش المهلة الحقيقية.
"""
import json
import sys
from collections import defaultdict
from pathlib import Path

P = Path("/var/lib/maxcall-broker/session-events.jsonl")

rows = []
if P.exists():
    for line in P.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
rows.sort(key=lambda r: r.get("epoch", 0))

by = defaultdict(list)
for r in rows:
    by[r.get("insurer", "?")].append(r)

def fmt_utc(ts):
    import datetime
    return datetime.datetime.utcfromtimestamp(ts).strftime("%m-%d %H:%M:%S")

for insurer in sorted(by):
    evs = by[insurer]
    restores = [e for e in evs if e.get("event") in ("restored", "restored_cold")]
    deads = [e for e in evs if e.get("event") == "dead"]
    locks = [e for e in evs if e.get("event") == "lock"]
    unlocks = [e for e in evs if e.get("event") == "unlock"]
    fails = [e for e in evs if e.get("event") == "restore_failed"]
    print(f"== {insurer} ==")
    print(f"  restores: {len(restores)}   deaths: {len(deads)}   "
          f"restore_failed: {len(fails)}   locks: {len(locks)}  unlocks: {len(unlocks)}")
    # عمر الجلسة = الفاصل بين استعادة وموت يليها (تقريبي)
    life = []
    last = None
    for e in evs:
        if e.get("event") in ("restored", "restored_cold"):
            last = e.get("epoch")
        elif e.get("event") == "dead" and last:
            life.append(e.get("epoch", 0) - last)
            last = None
    if life:
        avg = sum(life) / len(life)
        print(f"  session lifetime ≈ {avg/60:.1f} min (n={len(life)})")
    if evs:
        last_ev = evs[-1]
        print(f"  last: {fmt_utc(last_ev.get('epoch', 0))} {last_ev.get('event')} "
              f"{(last_ev.get('detail') or '')[:70]}")
    print()

print(f"events file: {P} ({sum(1 for _ in P.open('rb')) if P.exists() else 0} lines)")
