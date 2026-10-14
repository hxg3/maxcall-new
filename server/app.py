#!/usr/bin/env python3
# MaxCall Dashboard API (hardened revA - 2026-09-06)
# - All SQL parameterized (no string interpolation of user input)
# - Secrets from environment (/etc/maxcall.env), session hardened
# - Rate limits on login endpoints, recordings path contained
import pymysql
import subprocess
import os
import re
import json
import glob
import pathlib
import time
import csv
import io as _io
import wave as _wave
import contextlib as _ctx
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_file, send_from_directory, abort
from datetime import datetime
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("MAXCALL_SECRET_KEY", "MaxCall-2026-Fallback-ChangeMe")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
)
DB_CFG = {
    "host": "127.0.0.1",
    "user": os.environ.get("MAXCALL_DB_USER", "maxcall"),
    "password": os.environ.get("MAXCALL_DB_PASS", "91128088aA@"),
    "charset": "utf8mb4",
    "cursorclass": pymysql.cursors.DictCursor,
}
# Dashboard database name
DASH_DB = "maxcall_dashboard"
MONITOR_DIR = pathlib.Path("/var/spool/asterisk/monitor").resolve()

EXT_RE = re.compile(r"^\d{3,4}$")
PW_RE = re.compile(r"[^A-Za-z0-9@#_.\-]")
PHONE_RE = re.compile(r"[^\d+]")
CALLER_DISPOSITIONS = {"ANSWERED", "NO ANSWER", "BUSY", "FAILED", "CONGESTION"}
CALLER_RELATIONS = {"patient", "relative", "other"}
QUEUE_CONF = "/etc/asterisk/vitalpbx/queues__99-maxcall.conf"
QUEUE_NAME = "maxcall-q"


def db(q, params=(), database="asterisk", fetch=True):
    try:
        conn = pymysql.connect(**{**DB_CFG, "database": database})
        cur = conn.cursor()
        cur.execute(q, params or ())
        res = cur.fetchall() if fetch else None
        if not fetch:
            conn.commit()
        conn.close()
        return res
    except Exception as e:
        print(f"DB err {e}")
        return [] if fetch else None


# ---- rate limiting (in-memory, per IP) ----
_RL = {}


def _hit(key, limit, window):
    now = time.time()
    arr = [t for t in _RL.get(key, []) if now - t < window]
    arr.append(now)
    _RL[key] = arr
    return len(arr) > limit


def _client_ip():
    return request.remote_addr or "unknown"


def require_login(f):
    @wraps(f)
    def w(*a, **kw):
        if "user" not in session:
            return redirect(url_for("login"))
        return f(*a, **kw)

    return w


def _verify_dashboard_user(username, password):
    username = (username or "").strip()[:64]
    if not username or not password:
        return None
    rows = db(f"SELECT * FROM {DASH_DB}.users WHERE username=%s", (username,), database=DASH_DB)
    if not rows:
        return None
    u = rows[0]
    stored = u.get("password") or ""
    try:
        if stored and check_password_hash(stored, password):
            return u
    except Exception:
        pass
    if stored and stored == password:
        # legacy plaintext -> upgrade to hash on successful login
        try:
            db(
                f"UPDATE {DASH_DB}.users SET password=%s WHERE id=%s",
                (generate_password_hash(password), u["id"]),
                database=DASH_DB,
                fetch=False,
            )
        except Exception:
            pass
        return u
    return None


def _session_user(u):
    return {
        "id": u.get("id"),
        "username": u.get("username"),
        "role": u.get("role"),
        "display_name": u.get("display_name"),
    }


def _like_esc(s):
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


AST = ["sudo", "-n", "/usr/local/bin/maxcall-asterisk"]


def _ast_out(*args, timeout=10):
    return subprocess.check_output(AST + list(args), text=True, timeout=timeout)


def _ast_run(*args, timeout=10):
    return subprocess.run(AST + list(args), capture_output=True, text=True, timeout=timeout)


def _queue_sync():
    """Regenerate queue members from extensions (source of truth). Returns True on success."""
    try:
        exts = db("SELECT extension,name FROM ombutel.ombu_extensions ORDER BY extension", database="ombutel") or []
        lines = [
            "; MaxCall queue - members managed by dashboard API (_queue_sync), do not edit manually\n",
            "[maxcall-q]\n",
            "strategy = ringall\n",
            "timeout = 25\n",
            "retry = 2\n",
            "wrapuptime = 5\n",
            "maxlen = 0\n",
            "joinempty = yes\n",
            "leavewhenempty = no\n",
            "ringinuse = no\n",
            "musicclass = maxcall\n",
        ]
        for e in exts:
            ext = re.sub(r"\D", "", str(e.get("extension") or ""))[:4]
            nm = re.sub(r"[\r\n,]", "", str(e.get("name") or "")).strip()[:60]
            if ext:
                lines.append(f"member => PJSIP/{ext},0,{nm}\n")
        with open(QUEUE_CONF, "w") as f:
            f.writelines(lines)
        _ast_run("queue", "reload", "all")
        return True
    except Exception as ex:
        print(f"queue sync err {ex}")
        return False


def _queue_paused_map():
    """Returns {ext: bool} paused states from `queue show`."""
    try:
        out = _ast_out("queue", "show", QUEUE_NAME, timeout=5)
    except Exception:
        return {}
    paused = {}
    for line in out.splitlines():
        m = re.search(r"PJSIP/(\d+)", line)
        if m:
            paused[m.group(1)] = "paused" in line.lower()
    return paused


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if _hit("login:" + _client_ip(), 10, 300):
            return render_template("login.html", err="Too many attempts, try again later"), 429
        u = request.form.get("username", "")
        p = request.form.get("password", "")
        r = _verify_dashboard_user(u, p)
        if r:
            session["user"] = _session_user(r)
            return redirect(url_for("dashboard"))
        return render_template("login.html", err="Invalid credentials")
    return render_template("login.html")


@app.route("/api/agent/login", methods=["POST"])
def agent_login():
    if _hit("agentlogin:" + _client_ip(), 20, 300):
        return jsonify({"error": "Too many attempts, try again later"}), 429
    data = request.get_json(force=True, silent=True) or {}
    u = str(data.get("username", "")).strip()[:64]
    pw = str(data.get("password", "")).strip()[:128]
    if not u or not pw:
        return jsonify({"error": "Invalid credentials"}), 401
    r = db(
        "SELECT e.extension, e.name, d.secret FROM ombutel.ombu_extensions e "
        "JOIN ombutel.ombu_devices d ON d.extension_id=e.extension_id "
        "WHERE (e.name=%s OR e.extension=%s) AND d.secret=%s",
        (u, u, pw),
        database="ombutel",
    )
    if not r:
        r2 = _verify_dashboard_user(u, pw)
        if r2:
            host = request.headers.get("Host", "maxcall.local").split(":")[0]
            ext = r2["username"] if (r2.get("username") or "").isdigit() else "101"
            return jsonify({"ok": True, "extension": ext, "name": r2.get("display_name"), "sip_server": host})
        return jsonify({"error": "Invalid credentials"}), 401
    host = request.headers.get("Host", "maxcall.local").split(":")[0]
    return jsonify({"ok": True, "extension": r[0]["extension"], "name": r[0]["name"], "sip_server": host})


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@require_login
def dashboard():
    return render_template("dashboard.html", user=session["user"])


@app.route("/api/stats")
@require_login
def api_stats():
    total = db("SELECT COUNT(*) as c FROM cdr WHERE DATE(calldate)=CURDATE()", fetch=True)
    ans = db("SELECT COUNT(*) as c FROM cdr WHERE DATE(calldate)=CURDATE() AND disposition='ANSWERED'", fetch=True)
    avg = db("SELECT AVG(billsec) as a FROM cdr WHERE DATE(calldate)=CURDATE() AND billsec>0", fetch=True)
    week = db(
        "SELECT DATE(calldate) as d, COUNT(*) as c FROM cdr "
        "WHERE calldate >= DATE_SUB(CURDATE(), INTERVAL 7 DAY) GROUP BY DATE(calldate) ORDER BY d",
        fetch=True,
    )
    agents = db(
        "SELECT src as ext, COUNT(*) as c FROM cdr WHERE DATE(calldate)=CURDATE() "
        "AND src IN (SELECT extension FROM ombutel.ombu_extensions) GROUP BY src",
        fetch=True,
    )
    rec = db("SELECT COUNT(*) as c FROM cdr WHERE DATE(calldate)=CURDATE() AND recfile!=''", fetch=True)
    if not total or total[0]["c"] == 0:
        return jsonify({"total": 0, "answered": 0, "missed": 0, "avg_wait": 0, "week": [], "agents": {}, "rec_count": 0})
    _names = {
        r["extension"]: (r.get("name") or "")
        for r in (db("SELECT extension,name FROM ombutel.ombu_extensions", database="ombutel") or [])
    }
    agd = {r["ext"]: {"total": r["c"], "name": _names.get(r["ext"], "")} for r in (agents or [])}
    missed_list = _compute_missed()
    return jsonify(
        {
            "total": total[0]["c"],
            "answered": ans[0]["c"],
            "missed": len(missed_list),
            "avg_wait": int(avg[0]["a"] or 0) if avg else 0,
            "week": [{"d": r["d"].strftime("%Y-%m-%d"), "c": r["c"]} for r in week] if week else [],
            "agents": agd,
            "rec_count": rec[0]["c"] if rec else 0,
        }
    )


@app.route("/api/cdr")
@require_login
def api_cdr():
    q = _like_esc(request.args.get("q", "").strip()[:32])
    disp = request.args.get("disposition", "")
    ext = request.args.get("ext", "").strip()
    try:
        page = max(1, min(int(request.args.get("page", 1)), 10000))
    except Exception:
        page = 1
    per = 20
    off = (page - 1) * per
    where = []
    params = []
    if q:
        where.append("(src LIKE %s ESCAPE '\\\\' OR dst LIKE %s ESCAPE '\\\\' OR clid LIKE %s ESCAPE '\\\\')")
        params += [f"%{q}%", f"%{q}%", f"%{q}%"]
    if disp and disp != "all":
        if disp not in CALLER_DISPOSITIONS:
            return jsonify({"rows": [], "total": 0, "page": page, "per": per})
        where.append("disposition=%s")
        params.append(disp)
    if ext:
        if not EXT_RE.match(ext):
            return jsonify({"rows": [], "total": 0, "page": page, "per": per})
        where.append("(src=%s OR dst=%s)")
        params += [ext, ext]
    w = (" AND " + " AND ".join(where)) if where else ""
    rows = db(
        "SELECT calldate,clid,src,dst,duration,billsec,disposition,recfile,uniqueid,linkedid FROM cdr "
        f"WHERE 1=1 {w} ORDER BY calldate DESC LIMIT %s OFFSET %s",
        tuple(params) + (per, off),
        fetch=True,
    )
    total = db(f"SELECT COUNT(*) as c FROM cdr WHERE 1=1 {w}", tuple(params), fetch=True)
    for r in rows or []:
        r["calldate"] = r["calldate"].strftime("%Y-%m-%d %H:%M:%S") if hasattr(r["calldate"], "strftime") else str(r["calldate"])
        ph = re.sub(r"\D", "", r["src"] or "")[-8:]
        cr = db(f"SELECT name FROM {DASH_DB}.callers WHERE phone LIKE %s", (f"%{ph}",), database=DASH_DB) if ph else []
        r["caller_name"] = cr[0]["name"] if cr else ""
        r["has_rec"] = bool(r["recfile"])
        if (r.get("recfile") or "").startswith("/var/spool/asterisk/monitor/"):
            r["recfile"] = os.path.relpath(r["recfile"], "/var/spool/asterisk/monitor")
    # availability at ring time, for missed rows (one snapshot query + one per distinct time)
    missed_rows = [r for r in (rows or []) if (r.get("disposition") or "") != "ANSWERED" and (r.get("linkedid") or r.get("uniqueid"))]
    if missed_rows:
        _nmap = {x["extension"]: (x.get("name") or "")
                 for x in (db("SELECT extension,name FROM ombutel.ombu_extensions", database="ombutel") or [])}
        lids = list(dict.fromkeys([(r.get("linkedid") or r.get("uniqueid")) for r in missed_rows]))
        snap_map = {}
        try:
            sph = ",".join(["%s"] * min(len(lids), 100))
            for s in db(f"SELECT linkedid, ext FROM {DASH_DB}.ring_presence WHERE linkedid IN ({sph})",
                        tuple(lids[:100]), database=DASH_DB) or []:
                snap_map.setdefault(s["linkedid"], []).append(s["ext"])
        except Exception:
            pass
        time_cache = {}
        for r in missed_rows:
            lid = r.get("linkedid") or r.get("uniqueid")
            on = snap_map.get(lid, [])
            if not on:
                ts = str(r.get("calldate") or "")
                if ts not in time_cache:
                    time_cache[ts] = _online_at(ts)
                on = time_cache[ts]
            r["online_names"] = [(_nmap.get(e) or e) for e in on]
    return jsonify({"rows": rows or [], "total": (total[0]["c"] if total else 0), "page": page, "per": per})


@app.route("/api/agents")
@require_login
def api_agents():
    exts = db("SELECT extension,name,extension_id FROM ombutel.ombu_extensions ORDER BY extension", database="ombutel")
    try:
        out = _ast_out("pjsip", "show", "contacts", timeout=5)
    except Exception:
        out = ""
    res = []
    paused_map = _queue_paused_map()
    for e in exts or []:
        ext = e["extension"]
        stats = db(
            "SELECT COUNT(*) as total, SUM(CASE WHEN disposition='ANSWERED' THEN 1 ELSE 0 END) as ans, "
            "AVG(billsec) as avg, MAX(calldate) as last FROM cdr WHERE src=%s AND DATE(calldate)=CURDATE()",
            (ext,),
            fetch=True,
        )
        s = stats[0] if stats else {}
        status = "Available" if ("%s/sip:%s@" % (ext, ext)) in out and "Avail" in out else "Offline"
        res.append(
            {
                "extension": ext,
                "name": e["name"],
                "id": e["extension_id"],
                "status": status,
                "paused": paused_map.get(str(ext), False),
                "total": s.get("total", 0) or 0,
                "answered": s.get("ans", 0) or 0,
                "avg": int(s.get("avg", 0) or 0),
                "last": str(s.get("last", "")) if s.get("last") else "-",
            }
        )
    return jsonify(res)


@app.route("/api/agents/<ext>")
@require_login
def api_agent_detail(ext):
    if not EXT_RE.match(ext or ""):
        return jsonify({"error": "not found"}), 404
    info = db("SELECT * FROM ombutel.ombu_extensions WHERE extension=%s", (ext,), database="ombutel")
    if not info:
        return jsonify({"error": "not found"}), 404
    i = info[0]
    week = db(
        "SELECT DATE(calldate) as d, COUNT(*) as total, SUM(CASE WHEN disposition='ANSWERED' THEN 1 ELSE 0 END) as ans "
        "FROM cdr WHERE src=%s AND calldate >= DATE_SUB(CURDATE(), INTERVAL 7 DAY) GROUP BY DATE(calldate)",
        (ext,),
        fetch=True,
    )
    recent = db(
        "SELECT calldate,clid,src,dst,billsec,disposition FROM cdr WHERE src=%s OR dst=%s ORDER BY calldate DESC LIMIT 40",
        (ext, ext),
        fetch=True,
    )
    for r in recent or []:
        r["calldate"] = r["calldate"].strftime("%Y-%m-%d %H:%M") if hasattr(r["calldate"], "strftime") else str(r["calldate"])
    recs = db(
        "SELECT calldate,src,dst,billsec,recfile FROM cdr WHERE ((src=%s OR dst=%s) OR channel LIKE %s) "
        "AND recfile!='' GROUP BY recfile ORDER BY calldate DESC LIMIT 20",
        (ext, ext, f"PJSIP/{ext}-%"),
        fetch=True,
    )
    recs = [r for r in (recs or []) if _rec_ok(r.get("recfile") or "")]
    for r in recs or []:
        r["calldate"] = r["calldate"].strftime("%Y-%m-%d %H:%M") if hasattr(r["calldate"], "strftime") else str(r["calldate"])
        if (r.get("recfile") or "").startswith("/var/spool/asterisk/monitor/"):
            r["recfile"] = os.path.relpath(r["recfile"], "/var/spool/asterisk/monitor")
    from datetime import date as _date2, timedelta as _td2
    _d_to = _date2.today().strftime("%Y-%m-%d")
    _d_from = (_date2.today() - _td2(days=29)).strftime("%Y-%m-%d")
    _missed_while = _compute_missed_range(_d_from, _d_to, str(ext), 200)
    return jsonify({"info": i, "week": week or [], "recent": recent or [], "recs": recs or [],
                    "paused": _queue_paused_map().get(str(ext), False),
                    "missed_online": {"count": len(_missed_while), "recent": _missed_while}})


@app.route("/api/agents", methods=["POST"])
@require_login
def api_agents_add():
    data = request.get_json(force=True, silent=True) or {}
    name = re.sub(r"[\r\n\t]", "", str(data.get("name", ""))).strip()[:60]
    ext = str(data.get("extension", "")).strip()
    password = PW_RE.sub("", str(data.get("password", "") or "").strip())
    if not name:
        return jsonify({"error": "Name is required"}), 400
    if not ext:
        maxr = db(
            "SELECT MAX(CAST(extension AS UNSIGNED)) as m FROM ombutel.ombu_extensions WHERE extension REGEXP '^[0-9]+$'",
            database="ombutel",
        )
        ext = str((maxr[0]["m"] or 100) + 1)
    if not EXT_RE.match(ext):
        return jsonify({"error": "Invalid extension (3-4 digits)"}), 400
    generated = False
    if not password:
        import secrets as _secrets
        import string as _string

        alphabet = _string.ascii_letters + _string.digits
        password = "".join(_secrets.choice(alphabet) for _ in range(10))
        generated = True
    if len(password) < 4 or len(password) > 64:
        return jsonify({"error": "Password must be 4-64 chars"}), 400
    ex = db("SELECT * FROM ombutel.ombu_extensions WHERE extension=%s", (ext,), database="ombutel")
    if ex:
        return jsonify({"error": "Extension exists"}), 400
    try:
        maxid = db("SELECT MAX(extension_id) as m FROM ombutel.ombu_extensions", database="ombutel")
        nid = (maxid[0]["m"] or 0) + 1
        db(
            "INSERT INTO ombutel.ombu_extensions (extension_id,extension,name,language,class_of_service_id,tenant_id,"
            "dial_profile_id,ringtime,call_waiting,internal_rec,outgoing_rec,incoming_rec) "
            "VALUES (%s,%s,%s,'en',1,1,1,30,'yes','yes','yes','yes')",
            (nid, ext, name),
            database="ombutel",
            fetch=False,
        )
        # Device row (required for SIP/agent login) - mirrors VitalPBX defaults
        db(
            "INSERT INTO ombutel.ombu_devices (extension_id,profile_id,user,secret,description,technology,tenant_id) "
            "VALUES (%s,1,%s,%s,%s,'pjsip',1)",
            (nid, ext, password, name),
            database="ombutel",
            fetch=False,
        )
        block = (
            "\n\n[%s](p1)\ntype=endpoint\nauth=auth%s\nidentify_by=username,auth_username\n"
            "outbound_auth=auth%s\naors=%s\npermit=0.0.0.0/0\ncontext=cos-all\n\n"
            "[auth%s]\ntype=auth\nauth_type=userpass\nusername=%s\npassword=%s\n\n"
            "[%s](p1-aor)\ntype=aor\nmax_contacts=5\n" % (ext, ext, ext, ext, ext, ext, password, ext)
        )
        with open("/etc/asterisk/vitalpbx/pjsip__50-1-extensions.conf", "a") as f:
            f.write(block)
        _ast_run("module", "reload", "res_pjsip.so", timeout=5)
        _queue_sync()
        # verify both rows were really saved (grants may deny writes silently)
        chk = db("SELECT e.extension, d.secret FROM ombutel.ombu_extensions e "
                 "JOIN ombutel.ombu_devices d ON d.extension_id=e.extension_id WHERE e.extension=%s",
                 (ext,), database="ombutel")
        if not chk or (chk[0].get("secret") or "") != password:
            return jsonify({"error": "Agent not saved in database (check DB grants), retry"}), 500
        resp = {"ok": True, "extension": ext}
        if generated:
            resp["password"] = password
        return jsonify(resp)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/agents/<ext>/reset", methods=["POST"])
@require_login
def api_reset(ext):
    if not EXT_RE.match(ext or ""):
        return jsonify({"error": "not found"}), 404
    newpw = PW_RE.sub("", str(((request.get_json(force=True, silent=True) or {}).get("password", "")) or "").strip())
    if len(newpw) < 4:
        return jsonify({"error": "password too short"}), 400
    ex = db("SELECT extension_id FROM ombutel.ombu_extensions WHERE extension=%s", (ext,), database="ombutel")
    if not ex:
        return jsonify({"error": "not found"}), 404
    eid = ex[0]["extension_id"]
    db("UPDATE ombutel.ombu_devices SET secret=%s WHERE extension_id=%s", (newpw, eid), database="ombutel", fetch=False)
    dev = db("SELECT secret FROM ombutel.ombu_devices WHERE extension_id=%s", (eid,), database="ombutel")
    dev_ok = bool(dev) and all((r.get("secret") or "") == newpw for r in dev)
    conf = "/etc/asterisk/vitalpbx/pjsip__50-1-extensions.conf"
    with open(conf) as f:
        lines = f.read().splitlines(keepends=True)
    out, in_auth, changed = [], False, False
    for ln in lines:
        s2 = ln.strip()
        if s2.startswith("["):
            in_auth = s2 == "[auth%s]" % ext
        if in_auth and s2.startswith("password="):
            ln = "password=%s\n" % newpw
            changed = True
        out.append(ln)
    conf_ok = False
    if changed:
        with open(conf, "w") as f:
            f.writelines(out)
        _ast_run("module", "reload", "res_pjsip.so", timeout=10)
        with open(conf) as f:
            whole = f.read()
        m = re.search(r"\[auth%s\]\s*\n(?:[^\[]*\n)*?password=(.*)" % ext, whole)
        conf_ok = bool(m) and m.group(1).strip() == newpw
    if not (dev_ok and conf_ok):
        return jsonify({"error": "devices:%s pjsip:%s - retry" % ("ok" if dev_ok else "FAILED", "ok" if conf_ok else "FAILED")}), 500
    return jsonify({"ok": True})


@app.route("/api/agents/<ext>/pause", methods=["POST"])
@require_login
def api_pause(ext):
    # Pause/unpause a queue member (break without logging out).
    if not EXT_RE.match(ext or ""):
        return jsonify({"error": "not found"}), 404
    ex = db("SELECT extension FROM ombutel.ombu_extensions WHERE extension=%s", (ext,), database="ombutel")
    if not ex:
        return jsonify({"error": "not found"}), 404
    data = request.get_json(force=True, silent=True) or {}
    paused = bool(data.get("paused", True))
    cmd = "pause" if paused else "unpause"
    _ast_run("queue", cmd, "member", f"PJSIP/{ext}", "queue", QUEUE_NAME, timeout=10)
    state = _queue_paused_map().get(str(ext))
    if state is None or state != paused:
        return jsonify({"error": "pause not applied, retry"}), 500
    return jsonify({"ok": True, "extension": ext, "paused": paused})


@app.route("/api/recordings")
@require_login
def api_recs():
    rows = db(
        "SELECT MAX(calldate) as calldate,src,dst,MAX(billsec) as billsec,recfile FROM cdr "
        "WHERE recfile!='' GROUP BY recfile ORDER BY calldate DESC LIMIT 50",
        fetch=True,
    )
    if not rows or all(not r.get("recfile") for r in rows):
        files = glob.glob("/var/spool/asterisk/monitor/**/*.wav", recursive=True)
        files = sorted(files, key=lambda x: os.path.getmtime(x), reverse=True)[:50]
        rows = []
        for f in files:
            st = os.stat(f)
            fname = pathlib.Path(f).stem
            parts = fname.split("-")
            src = parts[3] if "-" in fname and len(parts) > 3 else "unknown"
            rows.append(
                {
                    "calldate": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
                    "src": src,
                    "dst": "-",
                    "billsec": 0,
                    "recfile": os.path.relpath(f, "/var/spool/asterisk/monitor"),
                    "exists": True,
                }
            )
        return jsonify(rows)
    _nmap = {
        r["extension"]: (r.get("name") or "")
        for r in (db("SELECT extension,name FROM ombutel.ombu_extensions", database="ombutel") or [])
    }
    kept = []
    for r in rows or []:
        if not _rec_ok(r.get("recfile") or ""):
            continue
        ag, agn = "", ""
        legs = db("SELECT src,channel FROM cdr WHERE recfile=%s AND disposition='ANSWERED'", (r["recfile"],)) or []
        for leg in legs:
            m = re.match(r"^PJSIP/(\d+)-", leg.get("channel") or "")
            if m and (m.group(1) in _nmap):
                ag, agn = m.group(1), _nmap[m.group(1)]
                break
            if (leg.get("src") or "") in _nmap:
                ag, agn = leg["src"], _nmap[leg["src"]]
                break
        r["agent"] = ag
        r["agent_name"] = agn
        kept.append(r)
    rows = kept
    for r in rows:
        if (r.get("recfile") or "").startswith("/var/spool/asterisk/monitor/"):
            r["recfile"] = os.path.relpath(r["recfile"], "/var/spool/asterisk/monitor")
        r["calldate"] = r["calldate"].strftime("%Y-%m-%d %H:%M") if hasattr(r["calldate"], "strftime") else str(r["calldate"])
        secs = int(r.get("billsec") or 0)
        if secs <= 0 and r.get("recfile"):
            secs = _wav_seconds(os.path.join("/var/spool/asterisk/monitor", r["recfile"]))
        r["dur"] = secs
        r["exists"] = True
    return jsonify(rows)


def _resolve_recording(fname):
    """Resolve a recording path strictly inside MONITOR_DIR. Returns Path or None."""
    if not fname or not isinstance(fname, str):
        return None
    if len(fname) > 256:
        return None
    rel = fname.lstrip("/")
    try:
        rp = (MONITOR_DIR / rel).resolve()
    except Exception:
        return None
    if rp != MONITOR_DIR and MONITOR_DIR not in rp.parents:
        return None
    if rp.is_file():
        return rp
    # legacy fallback: locate by basename inside the monitor tree
    base = pathlib.Path(rel).name
    if not base or base in (".", ".."):
        return None
    for g in MONITOR_DIR.rglob(base):
        try:
            rg = g.resolve()
        except Exception:
            continue
        if rg.is_file() and (rg == MONITOR_DIR or MONITOR_DIR in rg.parents):
            return rg
    return None


@app.route("/api/recordings/<path:fname>")
@require_login
def rec_file(fname):
    p = _resolve_recording(fname)
    if p is None:
        return "not found", 404
    return send_file(str(p), mimetype="audio/wav")


def _parse_caller_fields(data):
    """Validate optional caller profile fields. Returns dict."""
    file_no = re.sub(r"[^A-Za-z0-9\-/]", "", str(data.get("file_no", ""))).strip()[:30]
    phone2 = PHONE_RE.sub("", str(data.get("phone2", "")))[:20]
    relation = str(data.get("relation", "patient")).strip().lower()[:20]
    if relation not in CALLER_RELATIONS:
        relation = "patient"
    return {"file_no": file_no, "phone2": phone2, "relation": relation}


@app.route("/api/callers/<phone>")
def get_caller(phone):
    phone = PHONE_RE.sub("", phone or "")[:20]
    if len(phone) < 4:
        return jsonify({"phone": phone, "name": "", "notes": ""})
    caller = db(f"SELECT * FROM {DASH_DB}.callers WHERE phone=%s", (phone,), database=DASH_DB)
    if caller:
        c = caller[0]
        c.setdefault("file_no", "")
        c.setdefault("phone2", "")
        c.setdefault("relation", "patient")
        return jsonify(c)
    return jsonify({"phone": phone, "name": "", "notes": ""})


@app.route("/api/callers", methods=["POST"])
def save_caller():
    if _hit("savecaller:" + _client_ip(), 60, 60):
        return jsonify({"error": "Too many requests"}), 429
    data = request.get_json(force=True, silent=True) or {}
    phone = PHONE_RE.sub("", str(data.get("phone", "")))[:20]
    if len(phone) < 4:
        return jsonify({"error": "phone required"}), 400
    # Partial update: only fields present in the request are written,
    # so old clients (name/notes only) never wipe newer fields.
    sets = []
    params = []
    if "name" in data:
        sets.append("name=%s")
        params.append(re.sub(r"[\r\n]", "", str(data.get("name", ""))).strip()[:100])
    if "notes" in data:
        sets.append("notes=%s")
        params.append(str(data.get("notes", "")).strip()[:2000])
    f = _parse_caller_fields(data)
    for k in ("file_no", "phone2", "relation"):
        if k in data:
            sets.append(f"{k}=%s")
            params.append(f[k])
    if not sets:
        return jsonify({"ok": True})
    existing = db(f"SELECT phone FROM {DASH_DB}.callers WHERE phone=%s", (phone,), database=DASH_DB)
    if existing:
        db(f"UPDATE {DASH_DB}.callers SET {', '.join(sets)} WHERE phone=%s", tuple(params) + (phone,), database=DASH_DB, fetch=False)
    else:
        vals = {
            "name": re.sub(r"[\r\n]", "", str(data.get("name", ""))).strip()[:100] if "name" in data else "",
            "notes": str(data.get("notes", "")).strip()[:2000] if "notes" in data else "",
            "file_no": f["file_no"] if "file_no" in data else "",
            "phone2": f["phone2"] if "phone2" in data else "",
            "relation": f["relation"] if "relation" in data else "patient",
        }
        db(
            f"INSERT INTO {DASH_DB}.callers (phone, name, notes, file_no, phone2, relation) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (phone, vals["name"], vals["notes"], vals["file_no"], vals["phone2"], vals["relation"]),
            database=DASH_DB,
            fetch=False,
        )
    return jsonify({"ok": True})


@app.route("/maxcall-ip")
def get_server_ip():
    import netifaces

    ips = {}
    for iface in netifaces.interfaces():
        addrs = netifaces.ifaddresses(iface)
        if netifaces.AF_INET in addrs:
            for addr in addrs[netifaces.AF_INET]:
                ip = addr.get("addr")
                if ip and not ip.startswith("127."):
                    ips[iface] = ip
    return jsonify(
        {
            "hostname": "maxcall.local",
            "ips": ips,
            "sip_port": 5060,
            "dashboard_port": 3001,
            "note": "Use any non-127 IP for SIP registration from other devices",
        }
    )


@app.route("/api/calls/history")
def api_calls_history():
    # Shared customer call log (all agents see the same customer calls).
    agent = (request.args.get("agent", "") or "").strip()
    try:
        limit = max(1, min(int(request.args.get("limit", 50)), 200))
    except Exception:
        limit = 50
    if not EXT_RE.match(agent):
        return jsonify({"error": "agent required"}), 400
    exts = set(r["extension"] for r in (db("SELECT extension FROM ombutel.ombu_extensions", database="ombutel") or []))

    def is_external(n):
        n = n or ""
        return bool(n) and (n not in exts) and n != "s" and len(n) > 4

    def cname(num):
        ph = re.sub(r"\D", "", num or "")[-8:]
        if not ph:
            return ""
        cr = db(f"SELECT name FROM {DASH_DB}.callers WHERE phone LIKE %s", (f"%{ph}",), database=DASH_DB)
        return cr[0]["name"] if cr else ""

    rows = db(
        "SELECT calldate,clid,src,dst,dcontext,disposition,billsec,recfile,uniqueid,linkedid,channel "
        "FROM cdr ORDER BY calldate ASC LIMIT 2000"
    ) or []
    groups = {}
    order = []
    for r in rows:
        lid = r.get("linkedid") or r.get("uniqueid") or ""
        if not lid:
            continue
        if lid not in groups:
            groups[lid] = []
            order.append(lid)
        groups[lid].append(r)
    out = []
    pending_missed = {}
    for lid in order:
        legs = groups[lid]
        legs.sort(key=lambda r: str(r["calldate"]))
        customer = ""
        inbound = False
        for leg in legs:
            s, d = leg["src"] or "", leg["dst"] or ""
            if is_external(s):
                customer, inbound = s, True
                break
            if is_external(d):
                customer, inbound = d, False
                break
        if not customer:
            continue
        t0 = legs[0]["calldate"]
        tstr = t0.strftime("%Y-%m-%d %H:%M") if hasattr(t0, "strftime") else str(t0)
        answered_by = ""
        dur = 0
        has_rec = False
        rec_path = ""
        for leg in legs:
            if leg["disposition"] == "ANSWERED":
                m = re.match(r"^PJSIP/(\d+)-", leg.get("channel") or "")
                if m and m.group(1) in exts and m.group(1) != customer:
                    answered_by = m.group(1)
                    break
        if not answered_by:
            for leg in legs:
                if leg["disposition"] == "ANSWERED":
                    d = leg["dst"] or ""
                    if d in exts:
                        answered_by = d
                        break
            else:
                for leg in legs:
                    if leg["disposition"] == "ANSWERED" and (leg["src"] or "") in exts:
                        answered_by = leg["src"]
                        break
        if not answered_by:
            answered_by = _queue_answered_by([leg.get("uniqueid") for leg in legs])
        for leg in legs:
            try:
                dur = max(dur, int(leg["billsec"] or 0))
            except Exception:
                pass
            if leg.get("recfile"):
                has_rec = True
                rec_path = rec_path or leg["recfile"]
        if inbound:
            if answered_by:
                if answered_by == agent:
                    status = "answered"
                else:
                    status = "answered-by"
            else:
                status = "missed"
            if status == "missed":
                pending_missed[customer] = True
            else:
                pending_missed.pop(customer, None)
            direction = "in"
        else:
            mine = any((leg["src"] or "") == agent for leg in legs)
            any_ans = any(leg["disposition"] == "ANSWERED" for leg in legs)
            if pending_missed.get(customer):
                status = "called-back-answered" if any_ans else "called-back-no-answer"
            else:
                status = "outgoing" if any_ans else "outgoing-no-answer"
            pending_missed.pop(customer, None)
            direction = "out"
            if not mine:
                answered_by = answered_by or ""
        out.append(
            {
                "time": tstr,
                "number": customer,
                "name": cname(customer),
                "direction": direction,
                "status": status,
                "duration": dur,
                "answered_by": _answered_by_label(answered_by) if status == "answered-by" else "",
                "has_rec": has_rec and _rec_ok(rec_path),
            }
        )
    out = out[-limit:]
    out.reverse()
    return jsonify({"rows": out, "total": len(out), "agent": agent})


@app.route("/api/callers/<phone>/history")
def api_caller_history(phone):
    # Full per-caller history across all agents/days (for the CRM popup).
    digits = re.sub(r"\D", "", phone or "")[-8:]
    if not digits:
        return jsonify({"phone": phone, "history": []})
    exts = set(r["extension"] for r in (db("SELECT extension FROM ombutel.ombu_extensions", database="ombutel") or []))

    def is_external(n):
        n = n or ""
        return bool(n) and (n not in exts) and n != "s" and len(n) > 4

    rows = db(
        "SELECT calldate,src,dst,dcontext,disposition,billsec,recfile,uniqueid,linkedid,channel "
        "FROM cdr WHERE src LIKE %s OR dst LIKE %s ORDER BY calldate ASC LIMIT 500",
        (f"%{digits}", f"%{digits}"),
    ) or []
    groups = {}
    order = []
    for r in rows:
        lid = r.get("linkedid") or r.get("uniqueid") or ""
        if not lid:
            continue
        if lid not in groups:
            groups[lid] = []
            order.append(lid)
        groups[lid].append(r)
    out = []
    pending_missed = False
    for lid in order:
        legs = sorted(groups[lid], key=lambda r: str(r["calldate"]))
        customer = ""
        inbound = False
        for leg in legs:
            s, d = leg["src"] or "", leg["dst"] or ""
            if is_external(s):
                customer, inbound = s, True
                break
            if is_external(d):
                customer, inbound = d, False
                break
        if not customer:
            continue
        t0 = legs[0]["calldate"]
        tstr = t0.strftime("%Y-%m-%d %H:%M") if hasattr(t0, "strftime") else str(t0)
        answered_by = ""
        dur = 0
        has_rec = False
        for leg in legs:
            if leg["disposition"] == "ANSWERED":
                m = re.match(r"^PJSIP/(\d+)-", leg.get("channel") or "")
                if m and m.group(1) in exts and m.group(1) != customer:
                    answered_by = m.group(1)
                    break
        if not answered_by:
            for leg in legs:
                if leg["disposition"] == "ANSWERED":
                    d = leg["dst"] or ""
                    if d in exts:
                        answered_by = d
                        break
        if not answered_by:
            answered_by = _queue_answered_by([leg.get("uniqueid") for leg in legs])
        for leg in legs:
            try:
                dur = max(dur, int(leg["billsec"] or 0))
            except Exception:
                pass
            if leg.get("recfile"):
                has_rec = True
        if inbound:
            if answered_by:
                status = "answered"
            else:
                status = "missed"
            if status == "missed":
                pending_missed = True
            else:
                pending_missed = False
        else:
            any_ans = any(leg["disposition"] == "ANSWERED" for leg in legs)
            if pending_missed:
                status = "called-back-answered" if any_ans else "called-back-no-answer"
            else:
                status = "outgoing" if any_ans else "outgoing-no-answer"
            pending_missed = False
        rec = ""
        for leg in legs:
            if leg.get("recfile"):
                rec = leg["recfile"]
                if rec.startswith("/var/spool/asterisk/monitor/"):
                    rec = os.path.relpath(rec, "/var/spool/asterisk/monitor")
                break
        real_rec = rec if _rec_ok(rec) else ""
        on = []
        try:
            snap = db(f"SELECT ext FROM {DASH_DB}.ring_presence WHERE linkedid = %s", (lid,), database=DASH_DB) or []
            on = [r["ext"] for r in snap]
        except Exception:
            on = []
        if not on:
            on = _online_at(tstr)
        out.append(
            {
                "time": tstr,
                "status": status,
                "duration": dur,
                "answered_by": _answered_by_label(answered_by),
                "has_rec": bool(real_rec),
                "rec": real_rec,
                "online": on,
            }
        )
    total = len(out)
    ans = len([x for x in out if x["status"] in ("answered", "called-back-answered")])
    miss = len([x for x in out if x["status"] == "missed"])
    cb = len([x for x in out if x["status"].startswith("called-back")])
    out = out[-500:]
    out.reverse()
    summary = {"total": total, "answered": ans, "missed": miss, "called_back": cb}
    return jsonify({"phone": phone, "history": out, "total": len(out), "summary": summary})


def _queue_answered_by(uniqueids):
    """Fallback: who answered, from queue_log CONNECT events (for queue-bridged legs
    whose CDR channel is not a plain PJSIP/ext channel). Returns ext or ''."""
    uids = [u for u in dict.fromkeys(uniqueids) if u]
    if not uids:
        return ""
    try:
        ph = ",".join(["%s"] * min(len(uids), 50))
        rows = db(
            f"SELECT agent FROM asterisk.queues_log WHERE event IN "
            f"('CONNECT','COMPLETECALLER','COMPLETEAGENT','ATTENDEDTRANSFER') AND data2 IN ({ph}) LIMIT 1",
            tuple(uids[:50]),
        ) or []
        if rows:
            m = re.match(r"^PJSIP/(\d+)-?", str(rows[0].get("agent") or ""))
            if m:
                return m.group(1)
            m2 = re.match(r"^PJSIP/(\d+)$", str(rows[0].get("agent") or ""))
            if m2:
                return m2.group(1)
    except Exception:
        pass
    return ""


_EXT_NAMES = {"t": 0, "m": {}}


def _ext_name_map():
    """{extension: user name} map from VitalPBX extensions, cached 30s."""
    now = time.time()
    if now - _EXT_NAMES.get("t", 0) > 30:
        try:
            _EXT_NAMES["m"] = {
                str(r.get("extension") or ""): str(r.get("name") or "").strip()
                for r in (db("SELECT extension,name FROM ombutel.ombu_extensions", database="ombutel") or [])
            }
            _EXT_NAMES["t"] = now
        except Exception:
            pass
    return _EXT_NAMES["m"]


def _answered_by_label(ext):
    """Display label for the answering extension: user name if known, else the extension."""
    ext = str(ext or "").strip()
    if not ext:
        return ""
    nm = _ext_name_map().get(ext, "").strip()
    return nm or ext


DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _online_at(ts):
    """Extensions online at a past moment, from presence_log change history."""
    try:
        rows = db(
            f"SELECT p.ext, p.online FROM {DASH_DB}.presence_log p INNER JOIN "
            f"(SELECT ext, MAX(ts) AS m FROM {DASH_DB}.presence_log WHERE ts <= %s GROUP BY ext) q "
            f"ON q.ext=p.ext AND q.m=p.ts",
            (ts,),
            database=DASH_DB,
        ) or []
        return [r["ext"] for r in rows if int(r.get("online") or 0) == 1]
    except Exception:
        return []


def _compute_missed(limit=50):
    # Today only. Single source of truth for the badge.
    from datetime import date as _date

    today = _date.today().strftime("%Y-%m-%d")
    return _compute_missed_range(today, today, None, limit)


def _compute_missed_range(d_from, d_to, agent=None, limit=50):
    # Missed customer calls in [d_from, d_to]: external inbound groups with no
    # ANSWERED leg by any agent. agent filters to calls missed while ext online.
    exts = set(r["extension"] for r in (db("SELECT extension FROM ombutel.ombu_extensions", database="ombutel") or []))

    def is_external(n):
        n = n or ""
        return bool(n) and (n not in exts) and n != "s" and len(n) > 4

    rows = db(
        "SELECT calldate,clid,src,dst,disposition,linkedid,uniqueid FROM cdr "
        "WHERE DATE(calldate) BETWEEN %s AND %s ORDER BY calldate DESC LIMIT 3000",
        (d_from, d_to),
    ) or []
    groups = {}
    for r in rows:
        lid = r.get("linkedid") or r.get("uniqueid") or ""
        if lid:
            groups.setdefault(lid, []).append(r)
    exts_all = [r["extension"] for r in (db("SELECT extension FROM ombutel.ombu_extensions ORDER BY extension", database="ombutel") or [])]
    _nmap = {
        r["extension"]: (r.get("name") or "")
        for r in (db("SELECT extension,name FROM ombutel.ombu_extensions", database="ombutel") or [])
    }
    out = []
    for lid, legs in groups.items():
        customer = ""
        for leg in legs:
            s = leg["src"] or ""
            if is_external(s):
                customer = s
                break
        if not customer:
            continue
        if any(leg["disposition"] == "ANSWERED" for leg in legs):
            continue
        t0 = min(str(leg["calldate"]) for leg in legs)
        ph = re.sub(r"\D", "", customer)[-8:]
        nm = ""
        if ph:
            cr = db(f"SELECT name FROM {DASH_DB}.callers WHERE phone LIKE %s", (f"%{ph}",), database=DASH_DB)
            nm = cr[0]["name"] if cr else ""
        online_at = []
        try:
            snap = db(f"SELECT ext FROM {DASH_DB}.ring_presence WHERE linkedid = %s", (lid,), database=DASH_DB) or []
            online_at = [r["ext"] for r in snap if r["ext"] in exts_all]
        except Exception:
            online_at = []
        if not online_at:
            online_at = [e for e in _online_at(t0) if e in exts_all]
        if not online_at:
            try:
                act = db(
                    "SELECT DISTINCT src as ext FROM cdr WHERE src IN "
                    "(SELECT extension FROM ombutel.ombu_extensions) AND calldate BETWEEN DATE_SUB(%s, INTERVAL 15 MINUTE) "
                    "AND DATE_ADD(%s, INTERVAL 15 MINUTE)",
                    (t0, t0),
                ) or []
                online_at = [r["ext"] for r in act]
            except Exception:
                online_at = []
        if agent and agent not in online_at:
            continue
        out.append(
            {
                "date": t0[:10],
                "time": t0[11:16],
                "number": customer,
                "name": nm,
                "clid": legs[0].get("clid") or "",
                "online": online_at,
                "online_names": [(_nmap.get(e) or e) for e in online_at],
            }
        )
        if len(out) >= limit:
            break
    return out


@app.route("/api/missed")
@require_login
def api_missed():
    from datetime import date as _date, timedelta as _td

    today = _date.today().strftime("%Y-%m-%d")
    d_from = request.args.get("from", today) or today
    d_to = request.args.get("to", today) or today
    if not DATE_RE.match(d_from):
        d_from = today
    if not DATE_RE.match(d_to):
        d_to = today
    if d_from > d_to:
        d_from, d_to = d_to, d_from
    try:
        if (_date.fromisoformat(d_to) - _date.fromisoformat(d_from)).days > 93:
            return jsonify({"error": "range too wide (max 93 days)"}), 400
    except Exception:
        d_from = d_to = today
    agent = (request.args.get("agent", "") or "").strip()
    if agent and not EXT_RE.match(agent):
        return jsonify({"error": "invalid agent"}), 400
    try:
        limit = max(1, min(int(request.args.get("limit", 50)), 200))
    except Exception:
        limit = 50
    return jsonify(_compute_missed_range(d_from, d_to, agent or None, limit))


@app.route("/api/reports")
@require_login
def api_reports():
    # Management SLA reports: per-day service level/ASA/AHT/abandon (customer
    # calls only, internal excluded), today's hourly split, per-agent totals.
    # wait ~= duration - billsec. SL = answered with wait<=20s / inbound.
    try:
        days = max(1, min(int(request.args.get("days", 7)), 31))
    except Exception:
        days = 7
    exts = set(r["extension"] for r in (db("SELECT extension FROM ombutel.ombu_extensions", database="ombutel") or []))

    def is_external(n):
        n = n or ""
        return bool(n) and (n not in exts) and n != "s" and len(n) > 4

    rows = db(
        "SELECT calldate,src,dst,disposition,duration,billsec,uniqueid,linkedid,channel "
        "FROM cdr WHERE calldate >= DATE_SUB(NOW(), INTERVAL %s DAY) ORDER BY calldate ASC LIMIT 5000",
        (days,),
    ) or []
    groups = {}
    order = []
    for r in rows:
        lid = r.get("linkedid") or r.get("uniqueid") or ""
        if not lid:
            continue
        if lid not in groups:
            groups[lid] = []
            order.append(lid)
        groups[lid].append(r)
    calls = []
    for lid in order:
        legs = sorted(groups[lid], key=lambda r: str(r["calldate"]))
        customer = ""
        inbound = False
        for leg in legs:
            s, d = leg["src"] or "", leg["dst"] or ""
            if is_external(s):
                customer, inbound = s, True
                break
            if is_external(d):
                customer, inbound = d, False
                break
        if not customer:
            continue
        t0 = legs[0]["calldate"]
        day = t0.strftime("%Y-%m-%d") if hasattr(t0, "strftime") else str(t0)[:10]
        hour = t0.hour if hasattr(t0, "hour") else 0
        answered_by = ""
        for leg in legs:
            if leg["disposition"] == "ANSWERED":
                m = re.match(r"^PJSIP/(\d+)-", leg.get("channel") or "")
                if m and m.group(1) in exts and m.group(1) != customer:
                    answered_by = m.group(1)
                    break
        if not answered_by:
            for leg in legs:
                if leg["disposition"] == "ANSWERED":
                    if (leg["dst"] or "") in exts:
                        answered_by = leg["dst"]
                        break
                    if (leg["src"] or "") in exts:
                        answered_by = leg["src"]
                        break
        if not answered_by:
            answered_by = _queue_answered_by([leg.get("uniqueid") for leg in legs])
        answered = bool(answered_by) or any(leg["disposition"] == "ANSWERED" for leg in legs)
        wait = 0
        talk = 0
        for leg in legs:
            try:
                dur = int(leg["duration"] or 0)
                bill = int(leg["billsec"] or 0)
            except Exception:
                dur, bill = 0, 0
            talk = max(talk, bill)
            if leg["disposition"] == "ANSWERED":
                w = max(0, dur - bill)
                wait = w if wait == 0 else min(wait, w)
        if not answered:
            wait = max([int(leg["duration"] or 0) for leg in legs] + [0])
        calls.append({"day": day, "hour": hour, "inbound": inbound, "answered": answered,
                      "answered_by": answered_by if answered_by in exts else "", "wait": wait, "talk": talk})
    from datetime import date as _date, timedelta as _td
    today = _date.today()
    day_list = [(today - _td(days=i)).strftime("%Y-%m-%d") for i in range(days - 1, -1, -1)]
    by_day = {d: {"d": d, "total": 0, "inbound": 0, "answered": 0, "missed": 0, "sl": 0,
                  "asa": 0, "aht": 0, "_waits": [], "_talks": []} for d in day_list}
    hours = [{"h": h, "answered": 0, "missed": 0} for h in range(24)]
    ag = {}
    for c in calls:
        d = by_day.get(c["day"])
        if d is None:
            continue
        d["total"] += 1
        if c["inbound"]:
            d["inbound"] += 1
            if c["answered"]:
                d["answered"] += 1
                d["_waits"].append(c["wait"])
                d["_talks"].append(c["talk"])
            else:
                d["missed"] += 1
        if c["day"] == today.strftime("%Y-%m-%d") and c["inbound"]:
            if c["answered"]:
                hours[c["hour"]]["answered"] += 1
            else:
                hours[c["hour"]]["missed"] += 1
        ab = c["answered_by"]
        if ab:
            a = ag.setdefault(ab, {"ext": ab, "answered": 0, "_talks": []})
            if c["answered"]:
                a["answered"] += 1
                a["_talks"].append(c["talk"])
    _nmap = {r["extension"]: (r.get("name") or "")
             for r in (db("SELECT extension,name FROM ombutel.ombu_extensions", database="ombutel") or [])}
    for d in by_day.values():
        n = len(d["_waits"])
        d["sl"] = round(100.0 * len([w for w in d["_waits"] if w <= 20]) / d["inbound"], 1) if d["inbound"] else 0
        d["asa"] = round(sum(d["_waits"]) / n, 1) if n else 0
        d["aht"] = round(sum(d["_talks"]) / n, 1) if n else 0
        del d["_waits"]
        del d["_talks"]
    agents = [{"ext": e, "name": _nmap.get(e, ""), "answered": a["answered"],
               "aht": round(sum(a["_talks"]) / len(a["_talks"]), 1) if a["_talks"] else 0}
              for e, a in sorted(ag.items())]
    return jsonify({"days": [by_day[d] for d in day_list], "hours": hours, "agents": agents})


@app.route("/api/callers")
@require_login
def api_callers_list():
    # Searchable caller directory for the dashboard.
    q = _like_esc(request.args.get("q", "").strip().replace("'", "")[:32])
    try:
        limit = max(1, min(int(request.args.get("limit", 100)), 500))
    except Exception:
        limit = 100
    params = []
    w = ""
    if q:
        w = ("WHERE phone LIKE %s ESCAPE '\\\\' OR name LIKE %s ESCAPE '\\\\' "
             "OR file_no LIKE %s ESCAPE '\\\\' OR phone2 LIKE %s ESCAPE '\\\\'")
        params = [f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%"]
    rows = db(
        f"SELECT phone,name,notes,file_no,phone2,relation,total_calls,last_call FROM {DASH_DB}.callers {w} ORDER BY last_call DESC LIMIT %s",
        tuple(params) + (limit,),
        database=DASH_DB,
    ) or []
    for r in rows:
        r["last_call"] = r["last_call"].strftime("%Y-%m-%d %H:%M") if hasattr(r.get("last_call"), "strftime") else str(r.get("last_call") or "")
    return jsonify(rows)


@app.route("/api/callback", methods=["POST"])
@require_login
def api_callback():
    # Real callback: ring the agent extension first, then connect the customer.
    data = request.get_json(force=True, silent=True) or {}
    agent = re.sub(r"\D", "", str(data.get("agent", "")))
    number = re.sub(r"[^\d+]", "", str(data.get("number", "")))
    if not EXT_RE.match(agent) or not (4 <= len(number) <= 16):
        return jsonify({"error": "invalid agent or number"}), 400
    ex = db("SELECT extension FROM ombutel.ombu_extensions WHERE extension=%s", (agent,), database="ombutel")
    if not ex:
        return jsonify({"error": "unknown agent"}), 404
    try:
        out = _ast_run("channel", "originate", f"PJSIP/{agent}", "extension", f"{number}@cos-all", timeout=10)
        if "successfully" in (out.stdout or "").lower() or out.returncode == 0:
            return jsonify({"ok": True})
        return jsonify({"error": (out.stdout or out.stderr or "originate failed")[:200]}), 500
    except Exception as e:
        return jsonify({"error": str(e)[:200]}), 500


def _wav_seconds(path):
    try:
        if not path or not os.path.exists(path) or os.path.getsize(path) < 4096:
            return 0
        with _ctx.closing(_wave.open(path, "rb")) as w:
            return int(w.getnframes() / float(w.getframerate() or 8000))
    except Exception:
        return 0


def _rec_ok(rel):
    # True only for listenable recordings (>=2s of audio).
    if not rel:
        return False
    full = rel if rel.startswith("/") else os.path.join("/var/spool/asterisk/monitor", rel)
    if rel.startswith("/"):
        try:
            rp = pathlib.Path(full).resolve()
        except Exception:
            return False
        if rp != MONITOR_DIR and MONITOR_DIR not in rp.parents:
            return False
        full = str(rp)
    return _wav_seconds(full) >= 2


@app.route("/api/agents/<ext>", methods=["PUT"])
@require_login
def api_agent_rename(ext):
    # Change the agent display name.
    if not EXT_RE.match(ext or ""):
        return jsonify({"error": "not found"}), 404
    data = request.get_json(force=True, silent=True) or {}
    name = re.sub(r"[\r\n]", "", str(data.get("name", ""))).strip()[:60]
    if not name:
        return jsonify({"error": "name required"}), 400
    ex = db("SELECT * FROM ombutel.ombu_extensions WHERE extension=%s", (ext,), database="ombutel")
    if not ex:
        return jsonify({"error": "not found"}), 404
    db("UPDATE ombutel.ombu_extensions SET name=%s WHERE extension=%s", (name, ext), database="ombutel", fetch=False)
    chk = db("SELECT name FROM ombutel.ombu_extensions WHERE extension=%s", (ext,), database="ombutel")
    if not chk or (chk[0].get("name") or "") != name:
        return jsonify({"error": "rename not saved, retry"}), 500
    _queue_sync()
    return jsonify({"ok": True, "extension": ext, "name": name})


@app.route("/api/agents/<ext>/export")
@require_login
def api_agent_export(ext):
    # Full agent file: info + week + recent + recordings (CSV download).
    if not EXT_RE.match(ext or ""):
        return jsonify({"error": "not found"}), 404
    info = db("SELECT extension,name FROM ombutel.ombu_extensions WHERE extension=%s", (ext,), database="ombutel")
    if not info:
        return jsonify({"error": "not found"}), 404
    buf = _io.StringIO()
    w = csv.writer(buf)
    nm = info[0].get("name") or ""
    w.writerow(["Agent file", ext, nm])
    w.writerow([])
    w.writerow(["Date", "Total", "Answered"])
    for r in db(
        "SELECT DATE(calldate) as d, COUNT(*) as total, SUM(CASE WHEN disposition='ANSWERED' THEN 1 ELSE 0 END) as ans "
        "FROM cdr WHERE src=%s AND calldate >= DATE_SUB(CURDATE(), INTERVAL 30 DAY) GROUP BY DATE(calldate) ORDER BY d",
        (ext,),
    ) or []:
        w.writerow([str(r["d"]), r["total"], r["ans"]])
    w.writerow([])
    w.writerow(["Time", "To", "Seconds", "Disposition"])
    for r in db("SELECT calldate,clid,dst,billsec,disposition FROM cdr WHERE src=%s ORDER BY calldate DESC LIMIT 200", (ext,)) or []:
        w.writerow([str(r["calldate"]), r["dst"], r["billsec"], r["disposition"]])
    w.writerow([])
    w.writerow(["Recording time", "File", "Seconds"])
    for r in db("SELECT calldate,recfile,billsec FROM cdr WHERE src=%s AND recfile!='' GROUP BY recfile ORDER BY calldate DESC LIMIT 100", (ext,)) or []:
        w.writerow([str(r["calldate"]), r["recfile"], r["billsec"]])
    out = buf.getvalue()
    return app.response_class(out, mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=agent-%s.csv" % ext})


@app.route("/api/callers/export")
@require_login
def api_callers_export():
    # Full callers file with per-caller totals (CSV download).
    q = _like_esc(request.args.get("q", "").strip().replace("'", "")[:32])
    params = []
    w = ""
    if q:
        w = ("WHERE phone LIKE %s ESCAPE '\\\\' OR name LIKE %s ESCAPE '\\\\' "
             "OR file_no LIKE %s ESCAPE '\\\\' OR phone2 LIKE %s ESCAPE '\\\\'")
        params = [f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%"]
    buf = _io.StringIO()
    wcsv = csv.writer(buf)
    wcsv.writerow(["Phone", "Name", "File no", "Alt phone", "Relation", "Notes", "Total calls", "Last call"])
    for r in db(
        f"SELECT phone,name,file_no,phone2,relation,notes,total_calls,last_call FROM {DASH_DB}.callers {w} ORDER BY last_call DESC LIMIT 1000",
        tuple(params),
        database=DASH_DB,
    ) or []:
        wcsv.writerow([r["phone"], r.get("name") or "", r.get("file_no") or "", r.get("phone2") or "",
                       r.get("relation") or "patient",
                       r.get("notes") or "", r.get("total_calls") or 0, str(r.get("last_call") or "")])
    out = buf.getvalue()
    return app.response_class(out, mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=callers.csv"})


@app.route("/api/presence")
@require_login
def api_presence():
    # Extension presence from PJSIP contacts.
    exts = [r["extension"] for r in (db("SELECT extension FROM ombutel.ombu_extensions ORDER BY extension", database="ombutel") or [])]
    try:
        out = _ast_out("pjsip", "show", "contacts", timeout=5)
    except Exception:
        out = ""
    res = []
    for ext in exts:
        online = ("%s/sip:%s@" % (ext, ext)) in out and "Avail" in out
        res.append({"extension": ext, "online": online})
    return jsonify(res)


@app.route("/api/ring/snapshot", methods=["POST"])
def api_ring_snapshot():
    # Records who is registered at ring time (per customer call).
    # Called by the inbound dialplan via curl on 127.0.0.1.
    if request.remote_addr not in ("127.0.0.1", "::1"):
        return jsonify({"error": "local only"}), 403
    data = request.get_json(force=True, silent=True) or {}
    lid = re.sub(r"[^A-Za-z0-9.\-]", "", str(data.get("linkedid", "")).strip())[:64]
    if not lid:
        return jsonify({"error": "linkedid required"}), 400
    db(
        f"CREATE TABLE IF NOT EXISTS {DASH_DB}.ring_presence (linkedid VARCHAR(32) NOT NULL, ext VARCHAR(10) NOT NULL, "
        "ts TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, PRIMARY KEY (linkedid, ext))",
        database=DASH_DB,
        fetch=False,
    )
    try:
        out = _ast_out("pjsip", "show", "contacts", timeout=5)
    except Exception:
        out = ""
    exts = [r["extension"] for r in (db("SELECT extension FROM ombutel.ombu_extensions", database="ombutel") or [])]
    got = []
    for ext in exts:
        for line in out.splitlines():
            if ("%s/sip:%s@" % (ext, ext)) in line and "Avail" in line:
                db(
                    f"INSERT IGNORE INTO {DASH_DB}.ring_presence (linkedid, ext) VALUES (%s, %s)",
                    (lid, ext),
                    database=DASH_DB,
                    fetch=False,
                )
                got.append(ext)
                break
    return jsonify({"ok": True, "online": got})


# --- update mirror --------------------------------------------------------
# MaxCall clients look here for update.json, its RSA signature and the payload.
# They cannot reach github.com over TLS from the office network, so these files
# are the only path the updater has. The set of servable names is explicit, so
# nothing else in the directory is ever exposed even if the folder grows.
UPDATE_DIR = "/opt/maxcall-updates"
_UPDATE_FILES = {
    "update.json": "application/json",
    "update.json.sig": "application/octet-stream",
    "update.json.beta": "application/json",
    "update.json.beta.sig": "application/octet-stream",
    "MaxCall.exe": "application/vnd.microsoft.portable-executable",
    "MaxCall-beta.exe": "application/vnd.microsoft.portable-executable",
}


@app.route("/updates/<path:name>")
def update_mirror(name):
    if name not in _UPDATE_FILES:
        abort(404)
    if not os.path.isfile(os.path.join(UPDATE_DIR, name)):
        abort(404)
    return send_from_directory(
        UPDATE_DIR, name, mimetype=_UPDATE_FILES[name], max_age=300
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=3001, debug=False)
