#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Unit tests for the MaxCall Feedback Queue feature.

Run from the repo root:
    cd server && python3 test_feedback.py

These tests use the Flask test client + the real maxcall_dashboard database
(with tables created/dropped per test). his_broker.visit_rows is mocked so no
real HIS call is made. Exits non-zero on any failure.
"""
import os
import sys
import json
import re
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import app as app_module          # noqa: E402
import his_broker                 # noqa: E402

_RESULTS = []


def check(name, cond, detail=""):
    _RESULTS.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  [{detail}]"))


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

DASH_DB = "maxcall_dashboard"


def _clean_tables():
    """حذف جميع صفوف جداول التغذية الراجعة قبل/بعد كل اختبار."""
    for table in ("feedback_responses", "feedback_tasks", "feedback_config"):
        app_module.db(f"DELETE FROM {DASH_DB}.{table}", database=DASH_DB, fetch=False)


def _mock_visit_rows(rows):
    """استبدال his_broker.visit_rows بدالة تعيد صفوف اختبار."""

    def fake(from_iso, to_iso):
        return rows

    his_broker.visit_rows = fake


def _client():
    """ Flask test client مع جلسة مستخدم (لحماية require_login). """
    app_module.app.testing = True
    c = app_module.app.test_client()
    with c.session_transaction() as sess:
        sess["user"] = {"id": 1, "username": "admin", "role": "admin", "display_name": "Admin"}
    return c


def _basic_auth(ext="101", pwd="101"):
    """توليد ترويسة Basic auth لنقاط _require_his_api."""
    import base64
    token = base64.b64encode(f"{ext}:{pwd}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


# ---------------------------------------------------------------------------
# section: schema
# ---------------------------------------------------------------------------

def section_schema():
    print("\n--- Schema ---")
    _clean_tables()
    app_module._ensure_feedback_tables()
    # التحقق من وجود الجداول
    for table in ("feedback_tasks", "feedback_responses", "feedback_config"):
        r = app_module.db(f"SHOW TABLES LIKE '{table}'", database=DASH_DB)
        check(f"table {table} exists", bool(r), f"got {r}")
    # مفتاح فريد يمنع التكرار
    r = app_module.db("SHOW INDEX FROM maxcall_dashboard.feedback_tasks WHERE Key_name='uq_task_identity'", database=DASH_DB)
    check("unique key uq_task_identity exists", bool(r), f"got {r}")
    # الإعدادات الافتراضية تُحقن
    r = app_module.db("SELECT cfg_key, cfg_value FROM maxcall_dashboard.feedback_config ORDER BY cfg_key", database=DASH_DB)
    keys = {x["cfg_key"]: x["cfg_value"] for x in r}
    check("default hours_back=2", keys.get("hours_back") == "2", str(keys))
    check("default max_attempts=3", keys.get("max_attempts") == "3", str(keys))
    _clean_tables()


# ---------------------------------------------------------------------------
# section: generate — idempotency + phone filtering
# ---------------------------------------------------------------------------

def section_generate():
    print("\n--- Generate ---")
    _clean_tables()
    app_module._ensure_feedback_tables()
    c = _client()

    # صفوف اختبار من HIS (مُ.Mock)
    test_rows = [
        {"docNo": 1001, "docDate": "2026-10-22T00:00:00", "patID": "6655756",
         "patName": "AHMED ALI", "patGsmNo": "98168873", "consultant": "DR. A",
         "acDesc": "INSURANCE X", "Usr_ID": "user1", "docAmt": 50.0},
        {"docNo": 1002, "docDate": "2026-10-22T00:00:00", "patID": "6655757",
         "patName": "FATMA SALEM", "patGsmNo": "", "consultant": "DR. B",
         "acDesc": "INSURANCE Y", "Usr_ID": "user2", "docAmt": 30.0},  # هاتف فارغ → مستبعد
        {"docNo": 1003, "docDate": "2026-10-22T00:00:00", "patID": "6655758",
         "patName": "OMAR KHALID", "patGsmNo": "123", "consultant": "DR. C",
         "acDesc": "INSURANCE Z", "Usr_ID": "user3", "docAmt": 20.0},  # هاتف قصير → مستبعد
        {"docNo": 1004, "docDate": "2026-10-22T00:00:00", "patID": "6655759",
         "patName": "SARA AHMED", "patGsmNo": "999888777", "consultant": "DR. D",
         "acDesc": "INSURANCE W", "Usr_ID": "user4", "docAmt": 10.0},
    ]
    _mock_visit_rows(test_rows)

    # generate المرة الأولى
    resp = c.post("/api/feedback/generate", content_type="application/json")
    body = resp.get_json()
    check("generate 1st status 200", resp.status_code == 200, str(resp.status_code))
    check("generate 1st ok", body.get("ok") is True, str(body))
    check("generate 1st created=2", body.get("created") == 2, f"created={body.get('created')}")
    check("generate 1st skipped_invalid=2", body.get("skipped_invalid") == 2,
          f"skipped_invalid={body.get('skipped_invalid')}")
    check("generate 1st scanned=4", body.get("scanned") == 4, f"scanned={body.get('scanned')}")

    # generate مرة ثانية → idempotent، لا تكرار
    resp2 = c.post("/api/feedback/generate", content_type="application/json")
    body2 = resp2.get_json()
    check("generate 2nd created=0 (idempotent)", body2.get("created") == 0, f"created={body2.get('created')}")
    check("generate 2nd skipped_existing=2", body2.get("skipped_existing") == 2,
          f"skipped_existing={body2.get('skipped_existing')}")

    # التحقق من المهام المُنشأة في القاعدة
    tasks = app_module.db("SELECT id, phone, status FROM maxcall_dashboard.feedback_tasks ORDER BY id", database=DASH_DB)
    check("2 tasks in db", len(tasks) == 2, f"got {len(tasks)}")
    phones = {t["phone"] for t in tasks}
    check("phones valid", phones == {"98168873", "999888777"}, str(phones))
    check("all status pending", all(t["status"] == "pending" for t in tasks), str([t["status"] for t in tasks]))
    _clean_tables()


# ---------------------------------------------------------------------------
# section: state machine (claim, respond, skip, release)
# ---------------------------------------------------------------------------

def section_state_machine():
    print("\n--- State Machine ---")
    _clean_tables()
    app_module._ensure_feedback_tables()
    c = _client()
    auth = _basic_auth()

    #seed task
    now = datetime.now()
    app_module.db(
        "INSERT INTO maxcall_dashboard.feedback_tasks (phone, pat_id, visit_doc_no, visit_date, patient_name, "
        "doctor, payer, status, attempts, max_attempts, next_attempt_at) VALUES (%s,%s,%s,%s,%s,%s,%s,'pending',0,3,%s)",
        ("98168873", "6655756", "1001", now.strftime("%Y-%m-%d"), "AHMED ALI", "DR. A", "INS X",
         now.strftime("%Y-%m-%d %H:%M:%S")), database=DASH_DB, fetch=False,
    )
    task = app_module.db("SELECT id FROM maxcall_dashboard.feedback_tasks WHERE phone='98168873'", database=DASH_DB)[0]
    tid = task["id"]

    # claim
    r = c.post(f"/api/feedback/task/{tid}/claim", headers=auth, content_type="application/json")
    check("claim ok", r.get_json().get("ok") is True, str(r.get_json()))
    row = app_module.db("SELECT status, assigned_to FROM maxcall_dashboard.feedback_tasks WHERE id=%s", (tid,), database=DASH_DB)[0]
    check("claim → in_progress", row["status"] == "in_progress", row["status"])
    check("claim assigned_to set", row["assigned_to"] == "101", str(row["assigned_to"]))

    # claim مجدداً يجب أن يفشل (مُClaimed)
    r2 = c.post(f"/api/feedback/task/{tid}/claim", headers=auth, content_type="application/json")
    check("double claim rejected", r2.status_code == 409, str(r2.status_code))

    # respond done → تختفي من الطابور
    r3 = c.post(f"/api/feedback/task/{tid}/respond",
                headers=auth, content_type="application/json",
                data=json.dumps({"outcome": "done", "score": 4, "categories": "doctor,cleanliness",
                                 "comment": "ممتاز", "duration_sec": 180}))
    check("respond done ok", r3.get_json().get("ok") is True, str(r3.get_json()))
    row = app_module.db("SELECT status FROM maxcall_dashboard.feedback_tasks WHERE id=%s", (tid,), database=DASH_DB)[0]
    check("done → status done", row["status"] == "done", row["status"])
    # الاستجابة مسجلة
    resp = app_module.db("SELECT COUNT(*) as c FROM maxcall_dashboard.feedback_responses WHERE task_id=%s", (tid,), database=DASH_DB)[0]
    check("response recorded", resp["c"] == 1, str(resp["c"]))

    #创建 task جديدة لاختبار no_answer و skip
    app_module.db(
        "INSERT INTO maxcall_dashboard.feedback_tasks (phone, pat_id, visit_doc_no, visit_date, patient_name, "
        "doctor, payer, status, attempts, max_attempts, next_attempt_at) VALUES (%s,%s,%s,%s,%s,%s,%s,'pending',0,3,NULL)",
        ("999888777", "6655757", "1002", now.strftime("%Y-%m-%d"), "FATMA", "DR. B", "INS Y", None),
        database=DASH_DB, fetch=False,
    )
    tid2 = app_module.db("SELECT id FROM maxcall_dashboard.feedback_tasks WHERE phone='999888777'", database=DASH_DB)[0]["id"]

    # no_answer المرة الأولى → attempts=1، تبقى في الطابور مع next_attempt_at
    c.post(f"/api/feedback/task/{tid2}/claim", headers=auth, content_type="application/json")
    r4 = c.post(f"/api/feedback/task/{tid2}/respond", headers=auth, content_type="application/json",
                data=json.dumps({"outcome": "no_answer"}))
    check("no_answer ok", r4.get_json().get("ok") is True, str(r4.get_json()))
    row = app_module.db("SELECT status, attempts FROM maxcall_dashboard.feedback_tasks WHERE id=%s", (tid2,), database=DASH_DB)[0]
    check("no_answer → attempts=1", row["attempts"] == 1, str(row["attempts"]))
    check("no_answer → status no_answer", row["status"] == "no_answer", row["status"])
    check("no_answer → next_attempt_at set", row["next_attempt_at"] is not None, str(row["next_attempt_at"]))

    # no_answer الثانية → attempts=2
    c.post(f"/api/feedback/task/{tid2}/claim", headers=auth, content_type="application/json")
    c.post(f"/api/feedback/task/{tid2}/respond", headers=auth, content_type="application/json",
           data=json.dumps({"outcome": "no_answer"}))
    row = app_module.db("SELECT status, attempts FROM maxcall_dashboard.feedback_tasks WHERE id=%s", (tid2,), database=DASH_DB)[0]
    check("2nd no_answer → attempts=2", row["attempts"] == 2, str(row["attempts"]))

    # no_answer الثالثة → attempts=3 → closed ولا تعود
    c.post(f"/api/feedback/task/{tid2}/claim", headers=auth, content_type="application/json")
    c.post(f"/api/feedback/task/{tid2}/respond", headers=auth, content_type="application/json",
           data=json.dumps({"outcome": "no_answer"}))
    row = app_module.db("SELECT status, attempts FROM maxcall_dashboard.feedback_tasks WHERE id=%s", (tid2,), database=DASH_DB)[0]
    check("3rd no_answer → status closed", row["status"] == "closed", row["status"])
    check("3rd no_answer → attempts=3", row["attempts"] == 3, str(row["attempts"]))

    # skipped (refused)
    app_module.db(
        "INSERT INTO maxcall_dashboard.feedback_tasks (phone, pat_id, visit_doc_no, visit_date, patient_name, "
        "doctor, payer, status, attempts, max_attempts) VALUES (%s,%s,%s,%s,%s,%s,%s,'pending',0,3)",
        ("555000111", "6655758", "1003", now.strftime("%Y-%m-%d"), "OMAR", "DR. C", "INS Z"),
        database=DASH_DB, fetch=False,
    )
    tid3 = app_module.db("SELECT id FROM maxcall_dashboard.feedback_tasks WHERE phone='555000111'", database=DASH_DB)[0]["id"]
    r5 = c.post(f"/api/feedback/task/{tid3}/respond", headers=auth, content_type="application/json",
                data=json.dumps({"outcome": "refused"}))
    check("refused ok", r5.get_json().get("ok") is True, str(r5.get_json()))
    row = app_module.db("SELECT status FROM maxcall_dashboard.feedback_tasks WHERE id=%s", (tid3,), database=DASH_DB)[0]
    check("refused → status skipped", row["status"] == "skipped", row["status"])

    # release (تحرير قفل مهام in_progress)
    app_module.db(
        "INSERT INTO maxcall_dashboard.feedback_tasks (phone, pat_id, visit_doc_no, visit_date, patient_name, "
        "doctor, payer, status, attempts, max_attempts) VALUES (%s,%s,%s,%s,%s,%s,%s,'in_progress',0,3)",
        ("555000222", "6655759", "1004", now.strftime("%Y-%m-%d"), "SARA", "DR. D", "INS W"),
        database=DASH_DB, fetch=False,
    )
    tid4 = app_module.db("SELECT id FROM maxcall_dashboard.feedback_tasks WHERE phone='555000222'", database=DASH_DB)[0]["id"]
    r6 = c.post(f"/api/feedback/task/{tid4}/release", headers=auth, content_type="application/json")
    check("release ok", r6.get_json().get("ok") is True, str(r6.get_json()))
    row = app_module.db("SELECT status FROM maxcall_dashboard.feedback_tasks WHERE id=%s", (tid4,), database=DASH_DB)[0]
    check("release → status pending", row["status"] == "pending", row["status"])
    _clean_tables()


# ---------------------------------------------------------------------------
# section: callers.notes isolation
# ---------------------------------------------------------------------------

def section_notes_isolation():
    print("\n--- callers.notes isolation ---")
    _clean_tables()
    app_module._ensure_feedback_tables()
    c = _client()
    auth = _basic_auth()

    #seed a caller row with notes (للتحقق من عدم لمسه)
    app_module.db(
        "INSERT INTO maxcall_dashboard.callers (phone, name, notes, total_calls) VALUES (%s,%s,%s,%s)",
        ("98168873", "AHMED", "patient asked for recheck", 5), database=DASH_DB, fetch=False,
    )
    notes_before = app_module.db("SELECT notes FROM maxcall_dashboard.callers WHERE phone='98168873'", database=DASH_DB)[0]["notes"]

    app_module.db(
        "INSERT INTO maxcall_dashboard.feedback_tasks (phone, pat_id, visit_doc_no, visit_date, patient_name, doctor, payer, status) VALUES (%s,%s,%s,%s,%s,%s,%s,'pending')",
        ("98168873", "6655756", "1001", "2026-10-22", "AHMED ALI", "DR. A", "INS X"), database=DASH_DB, fetch=False,
    )
    tid = app_module.db("SELECT id FROM maxcall_dashboard.feedback_tasks WHERE phone='98168873'", database=DASH_DB)[0]["id"]

    c.post(f"/api/feedback/task/{tid}/respond", headers=auth, content_type="application/json",
           data=json.dumps({"outcome": "done", "comment": "feedback comment here"}))
    notes_after = app_module.db("SELECT notes FROM maxcall_dashboard.callers WHERE phone='98168873'", database=DASH_DB)[0]["notes"]
    check("callers.notes unchanged", notes_before == notes_after, f"before={notes_before} after={notes_after}")
    # feedback_responses.comment محفوظة بشكل منفصل
    resp = app_module.db("SELECT comment FROM maxcall_dashboard.feedback_responses WHERE task_id=%s", (tid,), database=DASH_DB)[0]
    check("feedback comment saved", resp["comment"] == "feedback comment here", str(resp["comment"]))
    _clean_tables()


# ---------------------------------------------------------------------------
# section: queue endpoint
# ---------------------------------------------------------------------------

def section_queue():
    print("\n--- Queue endpoint ---")
    _clean_tables()
    app_module._ensure_feedback_tables()
    c = _client()
    auth = _basic_auth()

    now = datetime.now()
    past = now - timedelta(minutes=5)
    future = now + timedelta(days=1)

    app_module.db(
        "INSERT INTO maxcall_dashboard.feedback_tasks (phone, patient_name, doctor, payer, status, attempts, max_attempts, next_attempt_at) VALUES (%s,%s,%s,%s,'pending',0,3,%s)",
        ("98168873", "A", "DR. A", "INS", now.strftime("%Y-%m-%d %H:%M:%S")), database=DASH_DB, fetch=False,
    )
    app_module.db(
        "INSERT INTO maxcall_dashboard.feedback_tasks (phone, patient_name, doctor, payer, status, attempts, max_attempts, next_attempt_at) VALUES (%s,%s,%s,%s,'no_answer',0,3,%s)",
        ("999888777", "B", "DR. B", "INS", past.strftime("%Y-%m-%d %H:%M:%S")), database=DASH_DB, fetch=False,
    )
    app_module.db(
        "INSERT INTO maxcall_dashboard.feedback_tasks (phone, patient_name, doctor, payer, status, attempts, max_attempts, next_attempt_at) VALUES (%s,%s,%s,%s,'no_answer',0,3,%s)",
        ("555000111", "C", "DR. C", "INS", future.strftime("%Y-%m-%d %H:%M:%S")), database=DASH_DB, fetch=False,
    )
    app_module.db(
        "INSERT INTO maxcall_dashboard.feedback_tasks (phone, patient_name, doctor, payer, status) VALUES (%s,%s,%s,%s,'done')",
        ("555000222", "D", "DR. D", "INS"), database=DASH_DB, fetch=False,
    )

    r = c.get("/api/feedback/queue", headers=auth)
    check("queue 200", r.status_code == 200, str(r.status_code))
    body = r.get_json()
    check("queue ok", body.get("ok") is True, str(body))
    data = body.get("data", [])
    # pending + no_answer المستحقة (past) فقط → 2 مهام؛ future و done مستبعدة
    check("queue returns 2 tasks", len(data) == 2, f"got {len(data)}")
    phones = {t["phone"] for t in data}
    check("queue phones correct", phones == {"98168873", "999888777"}, str(phones))
    # العداد
    check("queue count=2", body.get("count") == 2, str(body.get("count")))
    _clean_tables()


# ---------------------------------------------------------------------------
# section: summary
# ---------------------------------------------------------------------------

def section_summary():
    print("\n--- Summary ---")
    _clean_tables()
    app_module._ensure_feedback_tables()
    c = _client()

    app_module.db(
        "INSERT INTO maxcall_dashboard.feedback_tasks (phone, patient_name, doctor, payer, status, visit_amount) VALUES (%s,%s,%s,%s,'done',50.0)",
        ("98168873", "A", "DR. A", "INS"), database=DASH_DB, fetch=False,
    )
    app_module.db(
        "INSERT INTO maxcall_dashboard.feedback_tasks (phone, patient_name, doctor, payer, status) VALUES (%s,%s,%s,%s,'closed')",
        ("999888777", "B", "DR. B", "INS"), database=DASH_DB, fetch=False,
    )
    app_module.db(
        "INSERT INTO maxcall_dashboard.feedback_tasks (phone, patient_name, doctor, payer, status) VALUES (%s,%s,%s,%s,'pending')",
        ("555000111", "C", "DR. C", "INS"), database=DASH_DB, fetch=False,
    )

    r = c.get("/api/feedback/summary")
    check("summary 200", r.status_code == 200, str(r.status_code))
    body = r.get_json()
    check("summary ok", body.get("ok") is True, str(body))
    check("summary total=3", body.get("total") == 3, str(body.get("total")))
    check("summary done=1", body.get("done") == 1, str(body.get("done")))
    check("summary closed=1", body.get("closed") == 1, str(body.get("closed")))
    check("summary pending=1", body.get("pending") == 1, str(body.get("pending")))
    _clean_tables()


# ---------------------------------------------------------------------------
# section: config
# ---------------------------------------------------------------------------

def section_config():
    print("\n--- Config ---")
    _clean_tables()
    app_module._ensure_feedback_tables()
    c = _client()

    r = c.get("/api/feedback/config")
    check("config get ok", r.get_json().get("ok") is True, str(r.get_json()))
    check("config has hours_back", "hours_back" in r.get_json().get("data", {}), str(r.get_json()))

    r2 = c.post("/api/feedback/config", content_type="application/json",
                data=json.dumps({"hours_back": "4", "max_attempts": "5"}))
    check("config set ok", r2.get_json().get("ok") is True, str(r2.get_json()))
    r3 = c.get("/api/feedback/config")
    data = r3.get_json().get("data", {})
    check("config hours_back updated", data.get("hours_back") == "4", str(data))
    check("config max_attempts updated", data.get("max_attempts") == "5", str(data))
    _clean_tables()


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------

def main():
    section_schema()
    section_generate()
    section_state_machine()
    section_notes_isolation()
    section_queue()
    section_summary()
    section_config()

    failures = [r for r in _RESULTS if not r[1]]
    print(f"\n=== {len(_RESULTS)} checks, {len(failures)} failed ===")
    for name, cond, detail in failures:
        print(f"  FAIL: {name} [{detail}]")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
