#!/usr/bin/env python3
"""Duplicate the busiest PBX week (2026-08-28 .. 2026-09-03) into 2026-09-15 .. 2026-09-21.

Day mapping (weekday-aligned, whole-day offsets):
  SRC DATE        ->  TGT DATE       OFFSET
  2026-08-28 (Fri) -> 2026-09-18      +21d
  2026-08-29 (Sat) -> 2026-09-19      +21d
  2026-08-30 (Sun) -> 2026-09-20      +21d
  2026-08-31 (Mon) -> 2026-09-21      +21d  (today)
  2026-09-01 (Tue) -> 2026-09-15      +14d
  2026-09-02 (Wed) -> 2026-09-16      +14d
  2026-09-03 (Thu) -> 2026-09-17      +14d

Copies:
  * asterisk.cdr rows        (309) -> new uniqueid/linkedid, shifted calldate, recfile -> new path
  * /var/spool/asterisk/monitor wav files referenced by the source week (real + empty)
  * maxcall_dashboard.ring_presence  rows whose linkedid belongs to the source week
  * maxcall_dashboard.presence_log   rows whose ts falls inside the source week
  * maxcall_dashboard.callers        total_calls / last_call for numbers active in the new week

Safe:
  * run with --check for a dry-run; abort if --force is not present when a manifest exists
  * every inserted id / copied file / previous caller value is written to manifest.json
  * undo_fill.py reverses everything using the manifest
"""
import argparse, json, os, re, shutil, sys, datetime, pathlib

import pymysql

SOCK = "/run/mysqld/mysqld.sock"
MONITOR = pathlib.Path("/var/spool/asterisk/monitor")
WORKDIR = pathlib.Path("/root/maxcall-datafill")
MANIFEST = WORKDIR / "manifest.json"

OFFSET_BY_SRC_DATE = {
    "2026-08-28": 21,
    "2026-08-29": 21,
    "2026-08-30": 21,
    "2026-08-31": 21,
    "2026-09-01": 14,
    "2026-09-02": 14,
    "2026-09-03": 14,
}
SRC_LO, SRC_HI = "2026-08-28", "2026-09-04"

DASH = "maxcall_dashboard"

UID_RE = re.compile(r"^(.*?)([0-9]+\.[0-9]+)\.wav$")


def conn():
    return pymysql.connect(
        unix_socket=SOCK, user="root", charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor, autocommit=False,
    )


def get(cur, q, args=(), database="asterisk"):
    cur.execute(f"USE `{database}`")
    cur.execute(q, args)
    return cur.fetchall()


def offset_for(src_date):
    return OFFSET_BY_SRC_DATE[src_date.strftime("%Y-%m-%d")]


def shift(dt, days):
    return dt + datetime.timedelta(days=days)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="dry-run: load, plan, no writes")
    ap.add_argument("--force", action="store_true", help="run even if manifest exists")
    args = ap.parse_args()

    WORKDIR.mkdir(parents=True, exist_ok=True)
    if MANIFEST.exists() and not args.force and not args.check:
        print(f"[!] manifest exists: {MANIFEST}\n    aborting (use --force to re-run).")
        sys.exit(1)

    db = conn()
    cur = db.cursor()

    # ---- load source CDR ------------------------------------------------
    src_rows = get(cur,
        "SELECT * FROM cdr WHERE calldate>=%s AND calldate<%s ORDER BY cdr_id",
        (SRC_LO, SRC_HI))
    if args.check:
        print(f"[check] source cdr rows      : {len(src_rows)}")
    if not src_rows:
        print("[!] no source rows")
        sys.exit(1)

    # ---- unique id map (union of uniqueid + linkedid) --------------------
    id_map = {}
    idx = 0
    first_ts = {}
    for r in src_rows:
        for key in (r["uniqueid"], r["linkedid"]):
            if not key:
                continue
            if key not in first_ts:
                first_ts[key] = r["calldate"]
    for key, ts0 in sorted(first_ts.items(), key=lambda kv: kv[1]):
        epoch = int(shift(ts0, offset_for(ts0)).timestamp())
        id_map[key] = f"{epoch}.{1000 + idx}"
        idx += 1
    if args.check:
        print(f"[check] unique id map size    : {len(id_map)}")

    # ---- plan cdr rows ---------------------------------------------------
    plan_rows = []
    for r in src_rows:
        new = dict(r)
        off = offset_for(r["calldate"])
        new["calldate"] = shift(r["calldate"], off)
        if r["uniqueid"] in id_map:
            new["uniqueid"] = id_map[r["uniqueid"]]
        if r["linkedid"] in id_map:
            new["linkedid"] = id_map[r["linkedid"]]
        plan_rows.append(new)

    # ---- recfile handling ------------------------------------------------
    rec_rows = [(i, r) for i, r in enumerate(plan_rows) if r.get("recfile")]
    copy_plan = {}
    for i, r in rec_rows:
        rel = r["recfile"]
        if not rel.startswith("/var/spool/asterisk/monitor/"):
            continue  # unusual path; keep as-is
        m = UID_RE.match(os.path.basename(rel))
        if not m:
            continue
        old_uid = m.group(2)
        if old_uid not in id_map:
            old_uid = r["uniqueid"] or old_uid
        if old_uid not in id_map:
            continue
        # The old monitor folder uses the LOCAL date of the leg. Shift that
        # local date by the same whole-day offset as calldate (avoids tz math).
        off = offset_for(src_rows[i]["calldate"])
        src_fold = pathlib.Path(rel).parent  # .../2026/08/03
        y, mo = int(src_fold.parent.parent.name), int(src_fold.parent.name)
        old_local_date = datetime.date(y, mo, int(src_fold.name))
        new_local_date = old_local_date + datetime.timedelta(days=off)
        new_folder = MONITOR / new_local_date.strftime("%Y/%m/%d")
        new_basename = re.sub(r"[0-9]+\.[0-9]+\.wav$", id_map[old_uid] + ".wav", os.path.basename(rel))
        new_rec = str(new_folder / new_basename)
        r["recfile"] = new_rec
        copy_plan[str(rel)] = new_rec
    if args.check:
        print(f"[check] recfiles to copy      : {len(copy_plan)}  ({len(set(copy_plan.values()))} target files)")

    # ---- target conflict scan ---------------------------------------------
    tgt_ids = [r["uniqueid"] for r in plan_rows]
    ex = get(cur,
        "SELECT uniqueid FROM cdr WHERE uniqueid IN ({})".format(",".join(["%s"] * len(tgt_ids))),
        tgt_ids) if tgt_ids else []
    if ex:
        print(f"[!] {len(ex)} target uniqueid(s) already exist; aborting.")
        sys.exit(1)
    new_lids = list(dict.fromkeys(r["linkedid"] for r in plan_rows if r["linkedid"]))
    ex_lids = get(cur,
        f"SELECT linkedid FROM {DASH}.ring_presence WHERE linkedid IN ({','.join(['%s']*len(new_lids))})",
        new_lids, database=DASH) if new_lids else []
    if ex_lids:
        print(f"[!] {len(ex_lids)} target linkedid(s) already in ring_presence; aborting.")
        sys.exit(1)

    # ---- check target-date occupancy --------------------------------------
    occ = get(cur,
        "SELECT DATE(calldate) d, COUNT(*) c FROM cdr "
        "WHERE DATE(calldate) BETWEEN '2026-09-15' AND '2026-09-21' GROUP BY d")
    occ = {r["d"].strftime("%Y-%m-%d"): r["c"] for r in occ}
    print("[*] existing rows in target week:", occ or "none")

    if args.check:
        from collections import Counter
        per_day = Counter(r["calldate"].strftime("%Y-%m-%d") for r in plan_rows)
        print("[*] planned rows per target day:")
        for d in sorted(per_day):
            print(f"      {d}: {per_day[d]}")
        # verify weekday alignment (source date *_weekday == target weekday)
        for sd, off in sorted(OFFSET_BY_SRC_DATE.items()):
            s = datetime.datetime.strptime(sd, "%Y-%m-%d")
            t = s + datetime.timedelta(days=off)
            print(f"      src {sd} ({s:%a}) -> tgt {t:%Y-%m-%d} ({t:%a})  +{off}d")

    # ---- ring_presence / presence_log from source window -------------------
    src_ring = get(cur,
        "SELECT linkedid, ext, ts FROM ring_presence", database=DASH)
    src_pres = get(cur,
        f"SELECT id, ext, online, ts FROM presence_log WHERE ts >= %s AND ts < %s",
        (datetime.datetime(2026, 8, 28), datetime.datetime(2026, 9, 4, 12)), database=DASH)

    src_lids_in_week = set()
    for r in src_rows:
        if r["linkedid"]:
            src_lids_in_week.add(r["linkedid"])
    ring_map = {}
    for r in src_ring:
        if r["linkedid"] in src_lids_in_week and r["linkedid"] in id_map:
            off = offset_for(first_ts[r["linkedid"]])
            ring_map.setdefault(id_map[r["linkedid"]], []).append(
                {"ext": r["ext"], "ts": shift(r["ts"] or datetime.datetime.now(), off)})
    pres_plan = []
    for r in src_pres:
        off = offset_for(r["ts"])
        pres_plan.append({"ext": r["ext"], "online": r["online"],
                          "ts": shift(r["ts"], off)})
    if args.check:
        print(f"[check] ring_presence groups   : {len(ring_map)} rows={sum(len(v) for v in ring_map.values())}")
        print(f"[check] presence_log to insert : {len(pres_plan)}")

    # ---- callers pre-state -------------------------------------------------
    callers_touch = ["77181992", "93744242"]
    callers_old = {}
    for ph in callers_touch:
        c = get(cur, f"SELECT total_calls,last_call FROM callers WHERE phone=%s", (ph,), database=DASH)
        callers_old[ph] = {"total_calls": c[0]["total_calls"] if c else 0,
                           "last_call": (c[0]["last_call"] or "").strftime("%Y-%m-%d %H:%M:%S") if c and c[0]["last_call"] else ""}

    if args.check:
        print("[check] callers pre-state      :", json.dumps(callers_old, default=str, ensure_ascii=False))
        print("[check] DRY RUN OK - nothing written.")
        sys.exit(0)

    # =====================================================================
    #                        EXECUTION
    # =====================================================================
    print("[+] copying wav files ...")
    made = []
    for src_p, dst_p in copy_plan.items():
        d = pathlib.Path(dst_p)
        d.parent.mkdir(parents=True, exist_ok=True)
        if d.exists():
            print(f"    skip (exists): {dst_p}")
            continue
        if not pathlib.Path(src_p).exists():
            print(f"    WARN missing source: {src_p}")
            continue
        shutil.copy2(src_p, dst_p)
        made.append(dst_p)
        print(f"    {src_p} -> {dst_p}")
    print(f"[+] files copied: {len(made)}")

    print("[+] inserting cdr rows ...")
    cols = ["calldate","clid","did","source","src","dst","destination","dcontext",
            "channel","dstchannel","lastapp","lastdata","duration","billsec","disposition",
            "amaflags","accountcode","auth_code","customer_code","pin_code",
            "uniqueid","linkedid","sequence","peeraccount","calltype","recfile",
            "cloud_path","tenant","trunk"]
    new_cdr_ids = []
    try:
        for r in plan_rows:
            cur.execute(f"USE `asterisk`")
            cur.execute(
                "INSERT INTO cdr (" + ",".join(cols) + ") VALUES (" + ",".join(["%s"]*len(cols)) + ")",
                tuple(r.get(c) for c in cols))
            new_cdr_ids.append(cur.lastrowid)
        print(f"[+] cdr rows inserted  : {len(new_cdr_ids)}")

        if ring_map:
            cur.execute(f"USE `{DASH}`")
            for lid, arr in ring_map.items():
                for a in arr:
                    cur.execute(
                        "INSERT INTO ring_presence (linkedid, ext, ts) VALUES (%s,%s,%s)",
                        (lid, a["ext"], a["ts"]))
            print(f"[+] ring_presence rows: {sum(len(v) for v in ring_map.values())}")

        new_pres_ids = []
        if pres_plan:
            cur.execute(f"USE `{DASH}`")
            for a in pres_plan:
                cur.execute(
                    "INSERT INTO presence_log (ext, online, ts) VALUES (%s,%s,%s)",
                    (a["ext"], a["online"], a["ts"]))
                new_pres_ids.append(cur.lastrowid)
            print(f"[+] presence_log rows : {len(new_pres_ids)}")

        for ph in callers_touch:
            cur.execute(f"USE `asterisk`")
            agg = get(cur,
                "SELECT COUNT(*) c, MAX(calldate) m FROM cdr WHERE src=%s OR dst=%s",
                (ph, ph))
            cur.execute(f"USE `{DASH}`")
            cur.execute(
                "UPDATE callers SET total_calls=%s, last_call=%s WHERE phone=%s",
                (agg[0]["c"], agg[0]["m"], ph))
            print(f"[+] callers {ph}: total_calls={agg[0]['c']} last_call={agg[0]['m']}")

        db.commit()
    except Exception as e:
        db.rollback()
        # remove files we copied (only new ones)
        for f in made:
            try:
                pathlib.Path(f).unlink()
            except Exception:
                pass
        print(f"[X] FAILED (rolled back, {len(made)} copied files removed): {e}")
        sys.exit(1)

    manifest = {
        "generated": datetime.datetime.now().isoformat(timespec="seconds"),
        "source": {"from": SRC_LO, "to": "2026-09-03", "rows": len(src_rows)},
        "cdr_ids": new_cdr_ids,
        "new_linkedids": new_lids,
        "new_uniqueids": [r["uniqueid"] for r in plan_rows],
        "files_copied": made,
        "ring_presence": {lid: arr for lid, arr in ring_map.items()},
        "presence_log_ids": new_pres_ids,
        "callers_old": callers_old,
    }
    with open(MANIFEST, "w") as f:
        json.dump(manifest, f, indent=2, default=str, ensure_ascii=False)
    print(f"[+] manifest written: {MANIFEST}")
    print("[DONE]")


if __name__ == "__main__":
    main()