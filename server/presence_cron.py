#!/usr/bin/env python3
# Presence change logger for per-call "who was online" reporting.
# Runs every 2 minutes via cron. Inserts a row only on state CHANGE.
import os
import pymysql
import subprocess


def _env_file(path="/etc/maxcall.env"):
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
    except Exception:
        pass


_env_file()

DB = {"host": "127.0.0.1", "user": os.environ.get("MAXCALL_DB_USER", "maxcall"),
      "password": os.environ.get("MAXCALL_DB_PASS", ""),
      "database": "maxcall_dashboard", "charset": "utf8mb4",
      "cursorclass": pymysql.cursors.DictCursor}


def main():
    conn = pymysql.connect(**DB)
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS presence_log (
        id INT AUTO_INCREMENT PRIMARY KEY,
        ext VARCHAR(10) NOT NULL,
        online TINYINT NOT NULL,
        ts TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
        KEY(ext, ts))""")
    try:
        out = subprocess.check_output(["asterisk", "-rx", "pjsip show contacts"],
                                      text=True, timeout=10)
    except Exception:
        return
    cur.execute("SELECT extension FROM ombutel.ombu_extensions")
    exts = [r["extension"] for r in cur.fetchall()]
    for ext in exts:
        online = 1 if ("%s/sip:%s@" % (ext, ext)) in out and "Avail" in out else 0
        cur.execute("SELECT online FROM maxcall_dashboard.presence_log WHERE ext=%s ORDER BY id DESC LIMIT 1", (ext,))
        last = cur.fetchone()
        if not last or int(last["online"]) != online:
            cur.execute("INSERT INTO maxcall_dashboard.presence_log (ext, online) VALUES (%s, %s)", (ext, online))
    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
