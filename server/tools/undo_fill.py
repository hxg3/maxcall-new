#!/usr/bin/env python3
"""Reverse the week-fill operation using manifest.json.

Deletes the inserted cdr rows / ring_presence / presence_log rows,
restores callers totals, and removes the copied wav files.
Requires --force (the manifest is deleted afterwards).
"""
import argparse, json, pathlib, sys

import pymysql

SOCK = "/run/mysqld/mysqld.sock"
DASH = "maxcall_dashboard"
WORKDIR = pathlib.Path("/root/maxcall-datafill")
MANIFEST = WORKDIR / "manifest.json"


def conn():
    return pymysql.connect(
        unix_socket=SOCK, user="root", charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor, autocommit=False,
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--keep-files", action="store_true", help="do not delete copied wavs")
    args = ap.parse_args()

    if not MANIFEST.exists():
        print(f"[!] manifest not found: {MANIFEST}")
        sys.exit(1)
    if not args.force:
        print("[!] pass --force to confirm rollback.")
        sys.exit(1)

    m = json.loads(MANIFEST.read_text())
    db = conn()
    cur = db.cursor()

    # cdr
    ids = m.get("cdr_ids", [])
    try:
        cur.execute("USE `asterisk`")
        if ids:
            cur.execute("DELETE FROM cdr WHERE cdr_id IN ({})".format(",".join(["%s"]*len(ids))), ids)
        # ring_presence
        cur.execute(f"USE `{DASH}`")
        rp = m.get("ring_presence", {}) or {}
        for lid, arr in rp.items():
            cur.execute("DELETE FROM ring_presence WHERE linkedid=%s", (lid,))
        # presence_log
        pids = m.get("presence_log_ids", [])
        if pids:
            cur.execute("DELETE FROM presence_log WHERE id IN ({})".format(",".join(["%s"]*len(pids))), pids)
        # callers restore
        for ph, old in (m.get("callers_old", {}) or {}).items():
            if old.get("last_call"):
                cur.execute(f"USE `{DASH}`")
                cur.execute("UPDATE callers SET total_calls=%s, last_call=%s WHERE phone=%s",
                            (old["total_calls"], old["last_call"], ph))
            else:
                cur.execute(f"USE `{DASH}`")
                cur.execute("UPDATE callers SET total_calls=%s WHERE phone=%s",
                            (old["total_calls"], ph))
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"[X] FAILED (rolled back): {e}")
        sys.exit(1)

    n = len(ids)
    if not args.keep_files:
        for f in m.get("files_copied", []):
            try:
                pathlib.Path(f).unlink()
                print(f"removed: {f}")
            except FileNotFoundError:
                pass
            except Exception as e:
                print(f"WARN: could not remove {f}: {e}")

    MANIFEST.unlink(missing_ok=True)
    print(f"[+] rolled back: cdr rows={n}, ring_presence groups={len(rp or {})}, "
          f"presence_log rows={len(pids or [])}; manifest deleted.")


if __name__ == "__main__":
    main()