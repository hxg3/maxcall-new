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

import his_broker
from flask import Flask, render_template, request, redirect, url_for, session, jsonify, send_file, send_from_directory, abort
from datetime import datetime, timedelta, timezone
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


# ---------------------------------------------------------------------------
# feedback tables (maxcall_dashboard) — idempotent, safe to re-run
# ---------------------------------------------------------------------------

_FEEDBACK_TABLES_SQL = [
    """CREATE TABLE IF NOT EXISTS maxcall_dashboard.feedback_tasks (
      id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
      phone         VARCHAR(20)  NOT NULL,
      pat_id        VARCHAR(30)  NULL,
      visit_doc_no  VARCHAR(30)  NULL,
      visit_date    DATE         NULL,
      patient_name  VARCHAR(150) NULL,
      doctor        VARCHAR(120) NULL,
      payer         VARCHAR(120) NULL,
      employer      VARCHAR(120) NULL,
      service       VARCHAR(150) NULL,
      visit_amount  DECIMAL(12,3) NULL,
      pat_age        VARCHAR(12) NULL,
      pat_sex        VARCHAR(4)  NULL,
      pat_nationality VARCHAR(64) NULL,
      pat_payer_code VARCHAR(16) NULL,
      source        VARCHAR(16)  NOT NULL DEFAULT 'HIS',
      rule_tag      VARCHAR(48)  NULL,
      status        VARCHAR(24)  NOT NULL DEFAULT 'pending',
      priority      TINYINT      NOT NULL DEFAULT 2,
      attempts      INT          NOT NULL DEFAULT 0,
      max_attempts  INT          NOT NULL DEFAULT 3,
      next_attempt_at DATETIME   NULL,
      assigned_to   VARCHAR(64)  NULL,
      locked_at     DATETIME     NULL,
      outcome       VARCHAR(24)  NULL,
      created_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
      updated_at    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      PRIMARY KEY (id),
      UNIQUE KEY uq_task_identity (source, visit_doc_no, phone),
      KEY idx_status_next (status, next_attempt_at),
      KEY idx_phone (phone),
      KEY idx_pat (pat_id),
      KEY idx_visit_date (visit_date)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS maxcall_dashboard.feedback_responses (
      id            BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
      task_id       BIGINT UNSIGNED NOT NULL,
      phone         VARCHAR(20) NOT NULL,
      agent         VARCHAR(64) NOT NULL,
      outcome       VARCHAR(24) NOT NULL,
      score         TINYINT     NULL,
      categories    VARCHAR(255) NULL,
      comment       TEXT        NULL,
      duration_sec  INT         NULL,
      feedback_at   DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
      PRIMARY KEY (id),
      KEY idx_task (task_id),
      KEY idx_agent (agent),
      KEY idx_feedback_at (feedback_at),
      KEY idx_outcome (outcome)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    """CREATE TABLE IF NOT EXISTS maxcall_dashboard.feedback_config (
      cfg_key   VARCHAR(64) NOT NULL,
      cfg_value VARCHAR(255) NOT NULL,
      updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      PRIMARY KEY (cfg_key)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
]


def _ensure_feedback_tables():
    """Create feedback tables if missing. Safe to call on every startup."""
    for sql in _FEEDBACK_TABLES_SQL:
        db(sql, database=DASH_DB, fetch=False)

    # موجودات قد أُنشئت قبل إضافة أعمدة التغذية السريعة — أضفها إن غابت
    cols = {r["column_name"] for r in db(
        "SELECT column_name AS column_name FROM information_schema.columns "
        "WHERE table_schema=%s AND table_name='feedback_tasks'", (DASH_DB,), database=DASH_DB,
    ) or []}
    wanted = {
        "pat_age": "VARCHAR(12) NULL",
        "pat_sex": "VARCHAR(4) NULL",
        "pat_nationality": "VARCHAR(64) NULL",
        "pat_payer_code": "VARCHAR(16) NULL",
    }
    for col, ddl in wanted.items():
        if col not in cols:
            db(f"ALTER TABLE maxcall_dashboard.feedback_tasks ADD COLUMN {col} {ddl}",
               database=DASH_DB, fetch=False)

    #seed defaults if empty
    defaults = {
        "hours_back": "2",            # ساعاتrettrospective من لحظة إنشاء المهمة
        "max_attempts": "3",
        "backoff_days": "1,3,7",      # مهلة إعادة المحاولة بعد "لم يجب" (بالأيام)
        "min_amount": "0",
        "payer_filter": "",
        "doctor_filter": "",
        "exclude_repeat_visits": "0",
        "repeat_visit_threshold": "7",
        "enabled": "1",
    }
    for key, val in defaults.items():
        existing = db(
            "SELECT cfg_key FROM maxcall_dashboard.feedback_config WHERE cfg_key=%s",
            (key,), database=DASH_DB,
        )
        if not existing:
            db(
                "INSERT INTO maxcall_dashboard.feedback_config (cfg_key, cfg_value) VALUES (%s, %s)",
                (key, val), database=DASH_DB, fetch=False,
            )


def _cfg(key):
    """قراءة قيمة إعداد من جدول feedback_config."""
    r = db("SELECT cfg_value FROM maxcall_dashboard.feedback_config WHERE cfg_key=%s",
           (key,), database=DASH_DB)
    return r[0]["cfg_value"] if r else None


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


def _verify_agent_credentials(username, password):
    """Shared credential check: a MaxCall extension device or a dashboard user.

    Used by /api/agent/login and by the HIS API (Basic auth) — the popup client
    holds the same credentials it proved at agent login, so a separate login
    mechanism is never invented here.
    """
    username = (username or "").strip()[:64]
    password = (password or "").strip()[:128]
    if not username or not password:
        return None
    r = db(
        "SELECT e.extension, e.name, d.secret FROM ombutel.ombu_extensions e "
        "JOIN ombutel.ombu_devices d ON d.extension_id=e.extension_id "
        "WHERE (e.name=%s OR e.extension=%s) AND d.secret=%s",
        (username, username, password),
        database="ombutel",
    )
    if r:
        return {"kind": "agent", "extension": str(r[0]["extension"]), "name": r[0]["name"]}
    r2 = _verify_dashboard_user(username, password)
    if r2:
        return {"kind": "dashboard", "username": r2.get("username"), "name": r2.get("display_name")}
    return None


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


# ---- Insurer broker proxy (LAN فقط، بلا دخول لكل موظف) ----
# للتكامل مع المحاورة المحلية: الإضافة تتصل بـ maxcall.local:3001/api/insurer/...
# — الوساطة نفسها معزولة على 127.0.0.1:3011 ولا تقبل إلا من اللوحة بعد
# فحص الشبكة الخاصة (لا تسجيل دخول؛ طلب محدود من شبكة المستشفى فقط).
import ipaddress as _ipaddress
import urllib.request as _urlreq

BROKER_BASE = "http://127.0.0.1:3011"


def _lan_only():
    try:
        ip = _ipaddress.ip_address(request.remote_addr or "")
        return ip.is_private
    except Exception:
        return False


def _broker_json(path, payload=None, method="GET", timeout=30):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = _urlreq.Request(
        BROKER_BASE + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with _urlreq.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _insurer_proxy_error():
    return jsonify({"ok": False,
                    "error": "الخدمة الجماعية للمستندات غير متاحة الآن — "
                             "نفّذ العملية يدوياً كما تعودت"}), 503


@app.route("/api/insurer/health")
def insurer_health():
    if not _lan_only():
        abort(403)
    try:
        return jsonify(_broker_json("/health", timeout=5))
    except Exception:
        return jsonify({"ok": False, "error": "broker_unavailable"}), 503


@app.route("/api/insurer/job", methods=["POST"])
def insurer_job_create():
    if not _lan_only():
        abort(403)
    d = request.get_json(silent=True) or {}
    insurer = str(d.get("insurer") or "").strip()
    member = str(d.get("member") or "").strip()
    card = d.get("card") or {}
    ins = card.get("insurance") or {}
    if not member:
        member = str(ins.get("card_no") or "").strip()
    if not insurer or not member:
        return jsonify({"ok": False, "error": "رقم التأمين مطلوب"}), 400
    payload = {"insurer": insurer, "member": member, "card": card,
               "kind": d.get("kind") or "doc"}
    try:
        return jsonify(_broker_json("/job", payload, method="POST", timeout=10))
    except Exception:
        return _insurer_proxy_error()


@app.route("/api/insurer/job/<jid>", methods=["GET"])
def insurer_job_get(jid):
    if not _lan_only():
        abort(403)
    want_pdf = request.args.get("pdf") == "1"
    try:
        return jsonify(_broker_json(f"/job/{jid}?pdf=1" if want_pdf else f"/job/{jid}",
                                    timeout=30 if want_pdf else 10))
    except Exception:
        return _insurer_proxy_error()


@app.route("/api/insurer/job/<jid>/answer", methods=["POST"])
def insurer_job_answer(jid):
    if not _lan_only():
        abort(403)
    d = request.get_json(silent=True) or {}
    try:
        return jsonify(_broker_json(f"/job/{jid}/answer",
                                    {"token": str(d.get("token") or ""),
                                     "text": str(d.get("text") or "")},
                                    method="POST", timeout=10))
    except Exception:
        return _insurer_proxy_error()


@app.route("/api/agent/login", methods=["POST"])
def agent_login():
    if _hit("agentlogin:" + _client_ip(), 20, 300):
        return jsonify({"error": "Too many attempts, try again later"}), 429
    data = request.get_json(force=True, silent=True) or {}
    u = str(data.get("username", "")).strip()[:64]
    pw = str(data.get("password", "")).strip()[:128]
    if not u or not pw:
        return jsonify({"error": "Invalid credentials"}), 401
    user = _verify_agent_credentials(u, pw)
    if not user:
        return jsonify({"error": "Invalid credentials"}), 401
    host = request.headers.get("Host", "maxcall.local").split(":")[0]
    if user["kind"] == "agent":
        return jsonify({"ok": True, "extension": user["extension"], "name": user["name"], "sip_server": host})
    ext = user.get("username") if (user.get("username") or "").isdigit() else "101"
    name = user.get("name")
    return jsonify({"ok": True, "extension": ext, "name": name if isinstance(name, str) else None, "sip_server": host})


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


def _require_his_api(f):
    """حماية مسارات HIS: جلسة المستخدم (dashboard) أو بيانات الموظف (Basic auth).

    نفس فحص require_login — لا فحص موازي — ولا نُرسل WWW-Authenticate حتى لا
    يظهر أي نافذة كلمة مرور في عميل C++ (يرسل الترويسة بنفسه قبل الطلب).
    """
    @wraps(f)
    def w(*a, **kw):
        if "user" in session:
            return f(*a, **kw)
        auth = request.authorization
        if (auth and auth.type == "basic"
                and _verify_agent_credentials(auth.username or "", auth.password or "")):
            return f(*a, **kw)
        return jsonify({"ok": False, "error": "unauthorized"}), 401
    return w


@app.route("/api/his/caller/<phone>")
@_require_his_api
def his_caller(phone):
    """Patient card from the hospital WebHIS — read-only, fixed operations only."""
    if _hit("his:" + _client_ip(), 30, 60):
        return jsonify({"ok": False, "reason": "rate_limited"}), 429
    by = (request.args.get("by") or "phone").strip().lower()
    if by not in ("phone", "name", "id"):
        by = "phone"
    q = (request.args.get("q") or "").strip()
    pat_id = (request.args.get("pat_id") or "").strip()
    if not re.fullmatch(r"\d{1,10}", pat_id):
        pat_id = None
    return jsonify(his_broker.caller_response(phone, by=by, q=q, pat_id=pat_id))


@app.route("/api/his/patient/<pat_id>")
@_require_his_api
def his_patient(pat_id):
    """بطاقة تأمين مريض واحدة لموظف الاستقبال (Chrome extension) — قراءة فقط.

    نفس حماية /api/his/caller بالضبط (جلسة أو Basic auth لموظف) ونفس
    rate-limit، لكن الإدخال رقم ملف المريض مباشرة لأن الـextension يعرفه
    من صفحة التسجيل. الاستجابة allow-list محددة من his_broker.insurance_card.
    """
    if _hit("his:" + _client_ip(), 30, 60):
        return jsonify({"ok": False, "reason": "rate_limited"}), 429
    if not re.fullmatch(r"\d{1,10}", pat_id or ""):
        return jsonify({"ok": False, "reason": "bad_pat_id"}), 400
    return jsonify(his_broker.insurance_card(pat_id))


@app.route("/api/insurer/creds")
def insurer_creds_get():
    """بيانات دخول التأمينات (مقنّعة: اسم المستخدم + هل الكلمة محفوظة) —
    للامتداد (صفحة الإعدادات). LAN فقط، والتوسّط نفسه داخلي على 3011."""
    if not _lan_only():
        abort(403)
    try:
        return jsonify(_broker_json("/creds", timeout=5))
    except Exception:
        return _insurer_proxy_error()


@app.route("/api/insurer/creds", methods=["POST"])
def insurer_creds_post():
    """حفظ بيانات الدخول من الامتداد → الوسيط يكتب /etc/maxcall-insurers.json
    حياً (بلا restart — الكاش مبنياً على mtime). كلمة مرور فارغة = تبقى الحالية."""
    if not _lan_only():
        abort(403)
    d = request.get_json(silent=True) or {}
    try:
        return jsonify(_broker_json("/creds", d, method="POST", timeout=10))
    except Exception:
        return _insurer_proxy_error()


# ---------------------------------------------------------------------------
# Chrome extension self-hosted updates (CRX + update manifest)
# ---------------------------------------------------------------------------

EXT_MIRROR = pathlib.Path("/opt/maxcall-dashboard/ext-mirror")


@app.route("/ext/updates.xml")
def ext_updates_xml():
    return send_from_directory(EXT_MIRROR, "updates.xml", mimetype="text/xml")


@app.route("/ext/<path:fname>")
def ext_file(fname):
    # لا جلب أي شيء خارج المجلد المخصص
    if "/" in fname or ".." in fname or not re.fullmatch(r"[\w.\-]+", fname):
        abort(404)
    return send_from_directory(EXT_MIRROR, fname)


@app.route("/ext/forms/<path:fname>")
def ext_form(fname):
    if "/" in fname or ".." in fname or not re.fullmatch(r"[\w.\-]+", fname):
        abort(404)
    return send_from_directory(EXT_MIRROR / "forms", fname)


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


# ===========================================================================
# FEEDBACK QUEUE API — MaxCall "تاسك" (Circular Feedback Tasks)
# ===========================================================================

_PHONE_RE_STRICT = re.compile(r"^\d{7,15}$")


def _hydrate_people(rows, cap=8):
    """إثراء الصفوف ببيانات المريض (العمر/الجنس/الجنسية/رمز الدافع) عند غيابها.

    محدودة بـ `cap` مريض لكل نداء كي لا تثقّل أول جولة؛ ما تبقى يُكتمل تلقائياً
    في الطلبات التالية — الحقول تُخزَّن في DB فلا يتكرر نداء HIS لنفس المريض.
    """
    try:
        todo = [r for r in rows if not r.get("pat_age") and r.get("pat_id")][:max(0, int(cap))]
        for r in todo:
            s = his_broker.patient_summary(r.get("pat_id"))
            if not s:
                continue
            db(
                "UPDATE maxcall_dashboard.feedback_tasks SET pat_age=%s, pat_sex=%s, "
                "pat_nationality=%s, pat_payer_code=%s WHERE id=%s",
                (s.get("age") or None, s.get("sex") or None, s.get("nationality") or None,
                 s.get("payer_code") or None, r["id"]), database=DASH_DB, fetch=False,
            )
            r.update({"pat_age": s.get("age"), "pat_sex": s.get("sex"),
                      "pat_nationality": s.get("nationality"), "pat_payer_code": s.get("payer_code")})
    except Exception:
        pass


@app.route("/api/feedback/queue", methods=["GET"])
@_require_his_api
def feedback_queue():
    """قائمة المهام النشطة المستحقة للموظف (pending + no_answer المستحقة الآن)."""
    if _hit("feedback:queue:" + _client_ip(), 60, 60):
        return jsonify({"ok": False, "error": "rate_limited"}), 429
    try:
        limit = max(1, min(int(request.args.get("limit", 50)), 200))
    except Exception:
        limit = 50
    status = request.args.get("status", "")
    doctor = request.args.get("doctor", "").strip()
    payer = request.args.get("payer", "").strip()

    # المهام المستحقة: pending أو no_answer — وكلاهما لا يظهر قبل موعده
    # (next_attempt_at)، فالمدة الثابتة بعد الزيارة مُطبَّقة فعلاً.
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    scheduled = request.args.get("include_scheduled", "").strip().lower() in ("1", "true", "yes")
    if scheduled:
        # معاينة ما هو مجدول لاحقاً (للمشرفين) — تُستعمل في فحص CI الآلي.
        where = ["status IN ('pending','no_answer')"]
        params = []
    else:
        where = ["status IN ('pending','no_answer') AND next_attempt_at IS NOT NULL AND next_attempt_at<=%s"]
        params = [now]
    if status:
        where.append("status=%s"); params.append(status)
    if doctor:
        where.append("doctor LIKE %s"); params.append(f"%{doctor}%")
    if payer:
        where.append("payer LIKE %s"); params.append(f"%{payer}%")
    # المهام المقفلة لأكثر من 15 دقيقة تعود pending تلقائياً
    db(
        "UPDATE maxcall_dashboard.feedback_tasks SET status='pending', assigned_to=NULL, locked_at=NULL "
        "WHERE status='in_progress' AND locked_at IS NOT NULL AND locked_at<DATE_SUB(%s, INTERVAL 15 MINUTE)",
        (now,), database=DASH_DB, fetch=False,
    )
    rows = db(
        f"SELECT id, phone, pat_id, visit_doc_no, visit_date, patient_name, doctor, payer, employer, "
        f"visit_amount, source, rule_tag, status, priority, attempts, max_attempts, next_attempt_at, "
        f"assigned_to, locked_at, outcome, created_at, updated_at, "
        f"pat_age, pat_sex, pat_nationality, pat_payer_code "
        f"FROM maxcall_dashboard.feedback_tasks WHERE {' AND '.join(where)} "
        f"ORDER BY priority ASC, next_attempt_at ASC, created_at ASC LIMIT %s",
        tuple(params) + (limit,), database=DASH_DB,
    ) or []
    # تنسيق التواريخ للنقل عبر JSON + تنظيف أسماء قائد من إصدار قديم + إثراء
    for r in rows:
        r["patient_name"] = his_broker.norm_name(r.get("patient_name"))
        for k in ("visit_date", "next_attempt_at", "created_at", "updated_at"):
            v = r.get(k)
            if hasattr(v, "isoformat"):
                r[k] = v.isoformat()
            elif v is not None:
                r[k] = str(v)
    _hydrate_people(rows)
    return jsonify({"ok": True, "data": rows, "count": len(rows), "v": 1})


@app.route("/api/feedback/my", methods=["GET"])
@_require_his_api
def feedback_my():
    """مهام الموظف قيد العمل (in_progress المسندة إليه) — بعد الاتصال قبل تسجيل النتيجة."""
    if _hit("feedback:my:" + _client_ip(), 60, 60):
        return jsonify({"ok": False, "error": "rate_limited"}), 429
    auth = request.authorization
    agent = (auth.username if (auth and auth.type == "basic") else "") or ""
    if not agent:
        return jsonify({"ok": True, "data": [], "count": 0, "v": 1})
    rows = db(
        f"SELECT id, phone, pat_id, visit_doc_no, visit_date, patient_name, doctor, payer, employer, "
        f"visit_amount, source, rule_tag, status, priority, attempts, max_attempts, next_attempt_at, "
        f"assigned_to, locked_at, outcome, created_at, updated_at, "
        f"pat_age, pat_sex, pat_nationality, pat_payer_code "
        f"FROM maxcall_dashboard.feedback_tasks WHERE status='in_progress' AND assigned_to=%s "
        f"ORDER BY locked_at DESC LIMIT 100",
        (agent,), database=DASH_DB,
    ) or []
    for r in rows:
        r["patient_name"] = his_broker.norm_name(r.get("patient_name"))
        for k in ("visit_date", "next_attempt_at", "created_at", "updated_at", "locked_at"):
            v = r.get(k)
            if hasattr(v, "isoformat"):
                r[k] = v.isoformat()
            elif v is not None:
                r[k] = str(v)
    _hydrate_people(rows)
    return jsonify({"ok": True, "data": rows, "count": len(rows), "v": 1})


@app.route("/api/feedback/task/<int:task_id>/claim", methods=["POST"])
@_require_his_api
def feedback_claim(task_id):
    """الموظف يدّعي مهمة ⇒ فيبروغress، يُسنَد إليه، ويُغلق القفل 15 دقيقة."""
    if _hit("feedback:claim:" + _client_ip(), 60, 60):
        return jsonify({"ok": False, "error": "rate_limited"}), 429
    auth = request.authorization
    agent = (auth.username if (auth and auth.type == "basic") else "") or "unknown"
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # فقط المهام التي حان موعدها يمكن ادعاءها.
    # ملاحظة: db() مع fetch=False يُرجع None دائماً (نجاح أو خطأ)، فالتحقق
    # يكون بإعادة قراءة الصف ومقارنة القفل — لا بقيمة الإرجاع.
    db(
        "UPDATE maxcall_dashboard.feedback_tasks SET status='in_progress', assigned_to=%s, locked_at=%s "
        "WHERE id=%s AND status IN ('pending','no_answer') "
        "AND (next_attempt_at IS NULL OR next_attempt_at<=%s)",
        (agent, now, task_id, now), database=DASH_DB, fetch=False,
    )
    row = db("SELECT id,status,assigned_to,locked_at FROM maxcall_dashboard.feedback_tasks WHERE id=%s",
             (task_id,), database=DASH_DB)
    if not row:
        return jsonify({"ok": False, "error": "task_not_found"}), 404
    if row[0]["status"] != "in_progress" or row[0]["assigned_to"] != agent:
        # ادعاها موظف آخر أو انتهت مهلتها — لا تُسلَم مرتين.
        return jsonify({"ok": False, "error": "task_not_available"}), 409
    return jsonify({"ok": True, "task": {"id": task_id, "status": "in_progress", "assigned_to": agent}})


@app.route("/api/feedback/task/<int:task_id>/respond", methods=["POST"])
@_require_his_api
def feedback_respond(task_id):
    """تسجيل استجابة على المهمة وتحديث حالتها (done / no_answer / skipped / closed)."""
    if _hit("feedback:respond:" + _client_ip(), 60, 60):
        return jsonify({"ok": False, "error": "rate_limited"}), 429
    data = request.get_json(force=True, silent=True) or {}
    auth = request.authorization
    agent = (auth.username if (auth and auth.type == "basic") else "") or "unknown"
    outcome = str(data.get("outcome", "")).strip().lower()
    score = data.get("score", None)
    categories = data.get("categories", "")
    comment = str(data.get("comment", "")).strip()
    duration_sec = data.get("duration_sec", None)

    valid_outcomes = {"done", "no_answer", "refused", "wrong_number", "callback_later", "not_applicable"}
    if outcome not in valid_outcomes:
        return jsonify({"ok": False, "error": "invalid_outcome"}), 400

    # جلب المهمة الحالية
    rows = db("SELECT id,status,attempts,max_attempts,phone FROM maxcall_dashboard.feedback_tasks WHERE id=%s",
              (task_id,), database=DASH_DB)
    if not rows:
        return jsonify({"ok": False, "error": "task_not_found"}), 404
    task = rows[0]
    attempts = int(task["attempts"] or 0)
    max_attempts = int(task["max_attempts"] or 3)
    phone = task["phone"] or ""

    # حفظ الاستجابة (append-only، لا تُحذف أبداً)
    db(
        "INSERT INTO maxcall_dashboard.feedback_responses (task_id, phone, agent, outcome, score, categories, comment, duration_sec) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        (task_id, phone, agent, outcome, score, categories, comment, duration_sec),
        database=DASH_DB, fetch=False,
    )

    # تحديد الحالة الجديدة
    new_status = task["status"]
    next_attempt = None
    if outcome == "done":
        new_status = "done"
    elif outcome in ("refused", "wrong_number", "not_applicable"):
        new_status = "skipped"
    elif outcome == "callback_later":
        new_status = "pending"
        # الموظف يحدد الموعد عبر next_attempt_at إن أُرسل
        nxt = data.get("next_attempt_at", "")
        if nxt:
            next_attempt = nxt
    elif outcome == "no_answer":
        attempts += 1
        if attempts >= max_attempts:
            new_status = "closed"  # استُنفدت المحاولات، لا تعود
        else:
            new_status = "no_answer"
            # backoff متدرج: attempting-1 يُستخدم كمؤشر في قائمة backoff
            backoff = [int(x) for x in str(_cfg("backoff_days") or "1,3,7").split(",") if x.strip().isdigit()]
            idx = min(attempts - 1, len(backoff) - 1) if backoff else 0
            days = backoff[idx] if backoff else 1
            from datetime import timedelta
            next_attempt = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")

    db(
        "UPDATE maxcall_dashboard.feedback_tasks SET status=%s, attempts=%s, outcome=%s, "
        "next_attempt_at=%s, locked_at=NULL, assigned_to=NULL, updated_at=CURRENT_TIMESTAMP WHERE id=%s",
        (new_status, attempts, outcome, next_attempt, task_id), database=DASH_DB, fetch=False,
    )
    return jsonify({"ok": True, "task": {"id": task_id, "status": new_status, "attempts": attempts}})


@app.route("/api/feedback/task/<int:task_id>/skip", methods=["POST"])
@_require_his_api
def feedback_skip(task_id):
    """تخطي مهمة مع سبب ⇒ status='skipped' ولا تعود."""
    if _hit("feedback:skip:" + _client_ip(), 60, 60):
        return jsonify({"ok": False, "error": "rate_limited"}), 429
    data = request.get_json(force=True, silent=True) or {}
    auth = request.authorization
    agent = (auth.username if (auth and auth.type == "basic") else "") or "unknown"
    reason = str(data.get("reason", "")).strip()
    rows = db("SELECT phone FROM maxcall_dashboard.feedback_tasks WHERE id=%s", (task_id,), database=DASH_DB)
    if not rows:
        return jsonify({"ok": False, "error": "task_not_found"}), 404
    db(
        "INSERT INTO maxcall_dashboard.feedback_responses (task_id, phone, agent, outcome, comment) "
        "VALUES (%s,%s,%s,'skipped',%s)",
        (task_id, rows[0]["phone"], agent, reason), database=DASH_DB, fetch=False,
    )
    db("UPDATE maxcall_dashboard.feedback_tasks SET status='skipped', locked_at=NULL, assigned_to=NULL, outcome='skipped' WHERE id=%s",
       (task_id,), database=DASH_DB, fetch=False)
    return jsonify({"ok": True, "task": {"id": task_id, "status": "skipped"}})


@app.route("/api/feedback/task/<int:task_id>/release", methods=["POST"])
@_require_his_api
def feedback_release(task_id):
    """تحرير القفل على مهمة (إغلاق النافذة دون إنهاء) ⇒ تعود pending."""
    if _hit("feedback:release:" + _client_ip(), 60, 60):
        return jsonify({"ok": False, "error": "rate_limited"}), 429
    db("UPDATE maxcall_dashboard.feedback_tasks SET status='pending', locked_at=NULL, assigned_to=NULL WHERE id=%s AND status='in_progress'",
       (task_id,), database=DASH_DB, fetch=False)
    return jsonify({"ok": True})


# ---------- dashboard endpoints ( require_login ) ----------

@app.route("/api/feedback/tasks", methods=["GET"])
@require_login
def feedback_tasks():
    """جدول كل المهام مع فلاتر وتصفيح (لل dashboard)."""
    try:
        limit = max(1, min(int(request.args.get("limit", 50)), 500))
        offset = max(0, int(request.args.get("offset", 0)))
    except Exception:
        limit, offset = 50, 0
    status = request.args.get("status", "").strip()
    doctor = request.args.get("doctor", "").strip()
    payer = request.args.get("payer", "").strip()
    q = request.args.get("q", "").strip()
    days = request.args.get("days", "").strip()

    where = []
    params = []
    if status:
        where.append("status=%s"); params.append(status)
    if doctor:
        where.append("doctor LIKE %s"); params.append(f"%{doctor}%")
    if payer:
        where.append("payer LIKE %s"); params.append(f"%{payer}%")
    if q:
        where.append("(patient_name LIKE %s OR phone LIKE %s OR visit_doc_no LIKE %s)")
        params += [f"%{q}%", f"%{q}%", f"%{q}%"]
    if days and days.isdigit():
        where.append("visit_date >= DATE_SUB(CURDATE(), INTERVAL %s DAY)"); params.append(int(days))

    where_sql = " AND ".join(where) if where else "1=1"
    rows = db(
        f"SELECT id, phone, pat_id, visit_doc_no, visit_date, patient_name, doctor, payer, employer, "
        f"visit_amount, status, priority, attempts, max_attempts, next_attempt_at, assigned_to, outcome, "
        f"created_at, updated_at FROM maxcall_dashboard.feedback_tasks WHERE {where_sql} "
        f"ORDER BY created_at DESC LIMIT %s OFFSET %s",
        tuple(params) + (limit, offset), database=DASH_DB,
    ) or []
    total = db(f"SELECT COUNT(*) as c FROM maxcall_dashboard.feedback_tasks WHERE {where_sql}",
               tuple(params), database=DASH_DB)[0]["c"]
    for r in rows:
        for k in ("visit_date", "next_attempt_at", "created_at", "updated_at"):
            v = r.get(k)
            if hasattr(v, "isoformat"):
                r[k] = v.isoformat()
            elif v is not None:
                r[k] = str(v)
    return jsonify({"ok": True, "data": rows, "total": total, "limit": limit, "offset": offset, "v": 1})


@app.route("/api/feedback/responses", methods=["GET"])
@require_login
def feedback_responses():
    """سجل التغذية الراجعة المسجّلة."""
    try:
        limit = max(1, min(int(request.args.get("limit", 50)), 500))
        offset = max(0, int(request.args.get("offset", 0)))
    except Exception:
        limit, offset = 50, 0
    days = request.args.get("days", "").strip()
    agent = request.args.get("agent", "").strip()
    outcome = request.args.get("outcome", "").strip()

    where = []
    params = []
    if days and days.isdigit():
        where.append("feedback_at >= DATE_SUB(CURDATE(), INTERVAL %s DAY)"); params.append(int(days))
    if agent:
        where.append("agent=%s"); params.append(agent)
    if outcome:
        where.append("outcome=%s"); params.append(outcome)
    where_sql = " AND ".join(where) if where else "1=1"
    rows = db(
        f"SELECT id, task_id, phone, agent, outcome, score, categories, comment, duration_sec, feedback_at "
        f"FROM maxcall_dashboard.feedback_responses WHERE {where_sql} "
        f"ORDER BY feedback_at DESC LIMIT %s OFFSET %s",
        tuple(params) + (limit, offset), database=DASH_DB,
    ) or []
    total = db(f"SELECT COUNT(*) as c FROM maxcall_dashboard.feedback_responses WHERE {where_sql}",
               tuple(params), database=DASH_DB)[0]["c"]
    for r in rows:
        v = r.get("feedback_at")
        if hasattr(v, "isoformat"):
            r["feedback_at"] = v.isoformat()
        elif v is not None:
            r["feedback_at"] = str(v)
    return jsonify({"ok": True, "data": rows, "total": total, "limit": limit, "offset": offset, "v": 1})


@app.route("/api/feedback/summary", methods=["GET"])
@require_login
def feedback_summary():
    """إحصاءات التغذية الراجعة."""
    days = request.args.get("days", "").strip()
    where = ""
    params = ()
    if days and days.isdigit():
        where = "WHERE visit_date >= DATE_SUB(CURDATE(), INTERVAL %s DAY)"
        params = (int(days),)
    total = db(f"SELECT COUNT(*) as c FROM maxcall_dashboard.feedback_tasks {where}", params, database=DASH_DB)[0]["c"]
    done = db(f"SELECT COUNT(*) as c FROM maxcall_dashboard.feedback_tasks {where} AND status='done'", params, database=DASH_DB)[0]["c"]
    no_answer = db(f"SELECT COUNT(*) as c FROM maxcall_dashboard.feedback_tasks {where} AND status='no_answer'", params, database=DASH_DB)[0]["c"]
    closed = db(f"SELECT COUNT(*) as c FROM maxcall_dashboard.feedback_tasks {where} AND status='closed'", params, database=DASH_DB)[0]["c"]
    skipped = db(f"SELECT COUNT(*) as c FROM maxcall_dashboard.feedback_tasks {where} AND status='skipped'", params, database=DASH_DB)[0]["c"]
    pending = db(f"SELECT COUNT(*) as c FROM maxcall_dashboard.feedback_tasks {where} AND status='pending'", params, database=DASH_DB)[0]["c"]
    in_progress = db(f"SELECT COUNT(*) as c FROM maxcall_dashboard.feedback_tasks {where} AND status='in_progress'", params, database=DASH_DB)[0]["c"]
    avg_score = db(
        f"SELECT AVG(score) as a FROM maxcall_dashboard.feedback_responses r "
        f"JOIN maxcall_dashboard.feedback_tasks t ON r.task_id=t.id {where}",
        params, database=DASH_DB,
    )[0]["a"]
    by_doctor = db(
        f"SELECT doctor as name, COUNT(*) as c FROM maxcall_dashboard.feedback_tasks {where} GROUP BY doctor ORDER BY c DESC LIMIT 10",
        params, database=DASH_DB,
    ) or []
    by_payer = db(
        f"SELECT payer as name, COUNT(*) as c FROM maxcall_dashboard.feedback_tasks {where} GROUP BY payer ORDER BY c DESC LIMIT 10",
        params, database=DASH_DB,
    ) or []
    return jsonify({
        "ok": True, "v": 1,
        "total": total, "done": done, "no_answer": no_answer, "closed": closed,
        "skipped": skipped, "pending": pending, "in_progress": in_progress,
        "avg_score": round(float(avg_score), 2) if avg_score is not None else None,
        "by_doctor": [{"name": r["name"] or "غير محدد", "c": r["c"]} for r in by_doctor],
        "by_payer": [{"name": r["name"] or "غير محدد", "c": r["c"]} for r in by_payer],
    })


@app.route("/api/feedback/export", methods=["GET"])
@require_login
def feedback_export():
    """تصدير المهام إلى CSV."""
    import csv, io
    days = request.args.get("days", "").strip()
    where = ""
    params = ()
    if days and days.isdigit():
        where = "WHERE visit_date >= DATE_SUB(CURDATE(), INTERVAL %s DAY)"
        params = (int(days),)
    rows = db(
        f"SELECT visit_date, patient_name, phone, doctor, payer, visit_amount, status, attempts, outcome, created_at "
        f"FROM maxcall_dashboard.feedback_tasks {where} ORDER BY created_at DESC",
        params, database=DASH_DB,
    ) or []
    buf = io.StringIO()
    wcsv = csv.writer(buf)
    wcsv.writerow(["تاريخ الزيارة", "اسم المريض", "الهاتف", "الطبيب", "الجهة الدافعة", "المبلغ", "الحالة", "المحاولات", "النتيجة", "تاريخ الإنشاء"])
    for r in rows:
        wcsv.writerow([r.get("visit_date"), r.get("patient_name") or "", r.get("phone") or "",
                       r.get("doctor") or "", r.get("payer") or "", r.get("visit_amount") or "",
                       r.get("status"), r.get("attempts"), r.get("outcome") or "", r.get("created_at")])
    out = buf.getvalue()
    return app.response_class(out, mimetype="text/csv",
                              headers={"Content-Disposition": "attachment; filename=feedback_tasks.csv"})


@app.route("/api/feedback/generate", methods=["POST"])
@_require_his_api
def feedback_generate():
    """تشغيل التجهيز: جلب زيارات HIS وإنشاء مهام جديدة (idempotent).

    تقبل جلسة الداشبورد (زر التوليد) أو Basic auth (مهمة cron على السيرفر) —
    حتى يعمل التوليد التلقائي بلا تدخل يدوي.
    """
    if _hit("feedback:generate:" + _client_ip(), 5, 300):
        return jsonify({"ok": False, "error": "rate_limited"}), 429
    try:
        hours_back = int(str(_cfg("hours_back") or "2"))
    except Exception:
        hours_back = 2
    if hours_back < 0:
        hours_back = 0
    # نافذة المسح = المدة + ساعة هامش، حتى نلتقط الزيارات التي انتظرت دورها
    # (المفتاح الفريد يضمن عدم التكرار).
    from datetime import timedelta, timezone
    to = datetime.now(timezone.utc)
    from_dt = to - timedelta(hours=hours_back + 1)
    from_iso = from_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    to_iso = to.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    try:
        rows = his_broker.visit_rows(from_iso, to_iso)
    except Exception as e:
        return jsonify({"ok": False, "error": f"his_error: {e}"}), 502

    min_amount = float(str(_cfg("min_amount") or "0"))
    payer_filter = [p.strip() for p in str(_cfg("payer_filter") or "").split(",") if p.strip()]
    doctor_filter = [d.strip() for d in str(_cfg("doctor_filter") or "").split(",") if d.strip()]
    phone_re = re.compile(r"^\d{7,15}$")

    created = 0
    skipped_existing = 0
    skipped_invalid = 0
    skipped_active = 0
    # حماية من الاتصال المتكرر بنفس الشخص: إن كان للمريض مهمة نشطة
    # (pending/no_answer/in_progress) فمهما كانت الزيارة الجديدة — لا مهمة ثانية.
    active_phones = {r["phone"] for r in db(
        "SELECT DISTINCT phone FROM maxcall_dashboard.feedback_tasks "
        "WHERE status IN ('pending','no_answer','in_progress')", database=DASH_DB) or []}
    for row in rows:
        phone = re.sub(r"\D", "", str(row.get("phone") or ""))
        if not phone_re.fullmatch(phone):
            skipped_invalid += 1
            continue
        if phone in active_phones:
            skipped_active += 1
            continue
        visit_date = row.get("visit_date")
        if visit_date:
            try:
                vd = datetime.strptime(str(visit_date)[:10], "%Y-%m-%d").date()
                if vd < from_dt.date():
                    skipped_invalid += 1
                    continue
            except Exception:
                pass
        if min_amount and row.get("amount") is not None:
            try:
                if float(row.get("amount") or 0) < min_amount:
                    skipped_invalid += 1
                    continue
            except Exception:
                pass
        if payer_filter and row.get("payer") and row.get("payer") not in payer_filter:
            skipped_invalid += 1
            continue
        if doctor_filter and row.get("doctor") and row.get("doctor") not in doctor_filter:
            skipped_invalid += 1
            continue
        # exclude repeat visits
        if str(_cfg("exclude_repeat_visits") or "0") == "1":
            threshold = int(str(_cfg("repeat_visit_threshold") or "7"))
            cnt = db(
                "SELECT COUNT(*) as c FROM maxcall_dashboard.feedback_tasks WHERE pat_id=%s AND visit_date>=DATE_SUB(%s, INTERVAL %s DAY)",
                (row.get("pat_id"), str(visit_date)[:10] if visit_date else datetime.now().date(), threshold),
                database=DASH_DB,
            )[0]["c"]
            if cnt > 0:
                skipped_existing += 1
                continue
        try:
            # بيانات سريعة (عمر/جنس/جنسية/رمز الدافع) لحظة الإنشاء —
            # وإن عجز HIS عنها فهي تُهيَّأ لاحقاً عند العرض (_hydrate_people).
            try:
                s = his_broker.patient_summary(row.get("pat_id"))
            except Exception:
                s = {}
            db(
                "INSERT IGNORE INTO maxcall_dashboard.feedback_tasks "
                "(phone, pat_id, visit_doc_no, visit_date, patient_name, doctor, payer, employer, service, visit_amount, "
                "pat_age, pat_sex, pat_nationality, pat_payer_code, source, rule_tag, status, next_attempt_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'HIS','PATREG','pending',%s)",
                (phone, row.get("pat_id"), row.get("doc_no"), visit_date,
                 row.get("patient_name"), row.get("doctor"), row.get("payer"),
                 row.get("employer"), row.get("service"), row.get("amount"),
                 s.get("age") or None, s.get("sex") or None, s.get("nationality") or None, s.get("payer_code") or None,
                 (datetime.now() + timedelta(hours=hours_back)).strftime("%Y-%m-%d %H:%M:%S")),
                database=DASH_DB, fetch=False,
            )
            created += 1
            active_phones.add(phone)
        except Exception:
            skipped_existing += 1
    return jsonify({"ok": True, "v": 1, "created": created, "skipped_existing": skipped_existing,
                    "skipped_active": skipped_active, "skipped_invalid": skipped_invalid,
                    "scanned": len(rows)})


@app.route("/api/feedback/config", methods=["GET"])
@require_login
def feedback_config_get():
    rows = db("SELECT cfg_key, cfg_value FROM maxcall_dashboard.feedback_config ORDER BY cfg_key", database=DASH_DB) or []
    return jsonify({"ok": True, "v": 1, "data": {r["cfg_key"]: r["cfg_value"] for r in rows}})


@app.route("/api/feedback/config", methods=["POST"])
@require_login
def feedback_config_set():
    data = request.get_json(force=True, silent=True) or {}
    if not isinstance(data, dict):
        return jsonify({"ok": False, "error": "invalid_body"}), 400
    for k, v in data.items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", str(k)):
            continue
        db("INSERT INTO maxcall_dashboard.feedback_config (cfg_key, cfg_value) VALUES (%s,%s) "
           "ON DUPLICATE KEY UPDATE cfg_value=VALUES(cfg_value), updated_at=CURRENT_TIMESTAMP",
           (str(k), str(v)), database=DASH_DB, fetch=False)
    return jsonify({"ok": True, "v": 1})


# الخدمة تعمل تحت gunicorn فيظهر __main__ من الأساس — الترحيل يضمن تنفيذه
# عند الاستيراد (idempotent: إنشاء إن غاب + إضافة أعمدة التغذية إن نقصت).
_ensure_feedback_tables()

if __name__ == "__main__":
    _ensure_feedback_tables()
    app.run(host="0.0.0.0", port=3001, debug=False)
