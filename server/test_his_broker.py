#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Acceptance tests for the MaxCall <-> WebHIS broker (his-integration/EXECUTION_PROMPT.md §6).

Run from the repo root:
    cd server && HIS_USERNAME=abdullah HIS_PASSWORD=Abdullah python3 test_his_broker.py

Requires only stdlib + flask (same as app.py). Real WebHIS calls are made —
read-only/op-learning only. Exits non-zero on any failure.
"""
import base64
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

HIS_USER = os.environ.get("HIS_USERNAME", "")
HIS_PASS = os.environ.get("HIS_PASSWORD", "")
if not HIS_USER or not HIS_PASS:
    print("SKIP: HIS_USERNAME / HIS_PASSWORD not set — real-WebHIS tests disabled")
    sys.exit(2)

import his_broker as hb          # noqa: E402
import app as app_module         # noqa: E402

_RESULTS = []


def check(name, cond, detail=""):
    _RESULTS.append((name, bool(cond), detail))
    print(("PASS " if cond else "FAIL ") + name + ("" if cond else f"  [{detail}]"))


def section(title):
    print(f"\n--- {title} ---")


# ---------------------------------------------------------------------------
section("A1 — 91671473: 2 matches, compact shape, no raw upstream fields")
t0 = time.time()
r = hb.caller_response("91671473")
dt = time.time() - t0
check("A1 ok/matched", r.get("ok") is True and r.get("matched") is True, str(r.get("reason")))
check("A1 two matches", len(r.get("matches") or []) == 2, f"got {len(r.get('matches') or [])}: {r.get('matches')}")
ids = {str(m["pat_id"]) for m in r["matches"]}
check("A1 pat_ids exact", ids == {"5189831", "6664415"}, str(ids))
keys = set(r["matches"][0].keys())
check("A1 match keys allow-listed", keys <= {"pat_id", "name", "age", "sex", "phone", "nationality", "category"}, str(keys))
check("A1 no raw upstream fields", "PAT_NAME" not in str(r["matches"]) and "PhoneNumber" not in keys, str(keys))
check("A1 source_url canonical", r.get("source_url") == "http://solverserver:81/#/home/hmis/op/PatientSearch", str(r.get("source_url")))
check("A1 response under 4s", dt < 4.0, f"{dt:.1f}s")

# ---------------------------------------------------------------------------
section("A2 — select Pat_ID 5189831: EASIN, 2 visits, CLINIC.VISA bill")
r2 = hb.caller_response("91671473", pat_id="5189831")
sel = r2.get("selected") or {}
check("A2 selected name", "EASIN" in (sel.get("name") or ""), str(sel.get("name")))
check("A2 age/sex", sel.get("age") == "35Y" and sel.get("sex") == "M", f"{sel.get('age')}/{sel.get('sex')}")
check("A2 insurance VM", sel.get("insurance_code") == "VM", str(sel.get("insurance_code")))
check("A2 consultant/speciality", sel.get("consultant") == "Dr.Abdulsalam" and sel.get("speciality") == "GP",
      f"{sel.get('consultant')}/{sel.get('speciality')}")
check("A2 mrn echo", str(sel.get("mrn")) == "5189831", str(sel.get("mrn")))
check("A2 outstanding numeric", isinstance(sel.get("outstanding"), (int, float)), str(sel.get("outstanding")))
check("A2 2 visits", len(r2.get("visits") or []) == 2, str(len(r2.get("visits") or [])))
first = (r2.get("visits") or [{}])[0]
check("A2 visit shape", first.get("type") == "REG" and first.get("ph_no") == 5602144
      and first.get("date") == "2025-01-05" and first.get("consultant") == "Dr.Abdulsalam", str(first))
bills = r2.get("bills") or []
visa = [b for b in bills if b.get("item_code") == "CLINIC.VISA"]
check("A2 CLINIC.VISA bill", bool(visa) and visa[0].get("net") == 22.0, str(bills))
check("A2 bill keys allow-listed", set(bills[0].keys()) == {"item_code", "description", "qty", "net"}, str(set(bills[0].keys())))
check("A2 not inpatient", sel.get("is_inpatient") is False, str(sel.get("is_inpatient")))
check("A2 appointment fields present", "appointment_date" in sel and "appointment_consultant" in sel, "")

# the OTHER match (6664415) also renders a card
r2b = hb.caller_response("91671473", pat_id="6664415")
check("A2b other match renders", (r2b.get("selected") or {}).get("pat_id") == "6664415", str(r2b.get("selected")))

# ---------------------------------------------------------------------------
section("A3 — 23292277: 7 matches, no crash")
r3 = hb.caller_response("23292277")
check("A3 ok", r3.get("ok") is True, str(r3.get("reason")))
check("A3 seven matches", len(r3.get("matches") or []) == 7, str(len(r3.get("matches") or [])))

# ---------------------------------------------------------------------------
section("A4 — 00000000 (registered bulk number) + truly unknown 99999000")
# NOTE: the brief assumed 00000000 was unknown, but the hospital actually has
# 11 patients registered with that number — it must render matches, not noise.
r4 = hb.caller_response("00000000")
check("A4 ok true", r4.get("ok") is True, str(r4.get("reason")))
check("A4 matches rendered (11)", len(r4.get("matches") or []) == 11, str(len(r4.get("matches") or [])))
r4b = hb.caller_response("99999000")
check("A4b truly unknown -> matched:false", r4b.get("ok") is True and r4b.get("matched") is False, str(r4b)[:160])
check("A4b search_hint", r4b.get("search_hint") == "name_or_id", str(r4b.get("search_hint")))
check("A4b no leaks", not r4b.get("selected") and not r4b.get("matches"), "")

# ---------------------------------------------------------------------------
section("A5 — fallback by name CHANDANIE + by known reference rows")
r5 = hb.caller_response(None, by="name", q="CHANDANIE")
check("A5 name fallback matched", r5.get("ok") is True and r5.get("matched") is True, str(r5.get("reason")))
check("A5 name in results", any("CHANDANIE" in (m.get("name") or "") for m in r5.get("matches") or []), "")
r6 = hb.caller_response("98467147")
sel6 = (r6.get("selected") or {})
check("A6 single match 'test ss'", (r6.get("matches") or [{}])[0].get("name", "").lower().startswith("test ss"), str(r6.get("matches"))[:120])
check("A6 arabic name present", bool(sel6.get("name_ar")), str(sel6.get("name_ar")))

# ---------------------------------------------------------------------------
section("A7 — cache: identical repeat makes zero upstream calls")
_calls = {"n": 0}
_orig_call = hb._call


def _counting_call(*a, **kw):
    _calls["n"] += 1
    return _orig_call(*a, **kw)


hb._call = _counting_call
hb._SEARCH_CACHE.clear()
hb._CARD_CACHE.clear()
t_a = time.time()
hb.caller_response("99693070")
upstream_first = _calls["n"]
t_b = time.time()
r_c = hb.caller_response("99693070")
upstream_second = _calls["n"] - upstream_first
dt_a, dt_b = t_b - t_a, time.time() - t_b
hb._call = _orig_call
check("A7 first call hits upstream", upstream_first >= 1, str(_calls["n"]))
check("A7 cached call = 0 upstream", upstream_second == 0, str(upstream_second))
check("A7 cached call faster", dt_b < dt_a, f"first {dt_a:.2f}s vs cached {dt_b:.2f}s")
check("A7 cached response intact", r_c.get("ok") is True and (r_c.get("selected") or {}).get("pat_id") == "928008841", str(r_c.get("selected"))[:120])

# ---------------------------------------------------------------------------
section("A8 — token refresh: exp < 65 days -> re-login")
_logins = {"n": 0}
_orig_login = hb._login_locked


def _counting_login():
    _logins["n"] += 1
    return _orig_login()


hb._login_locked = _counting_login
with hb._LOCK:
    hb._STATE["exp"] = time.time() + 30 * 86400   # 30 days remaining (< 65)
r8 = hb.caller_response("00000000")
hb._login_locked = _orig_login
check("A8 re-login triggered", _logins["n"] == 1, f"logins={_logins['n']}")
with hb._LOCK:
    exp = hb._STATE["exp"]
check("A8 new exp > 65 days away", exp - time.time() > 65 * 86400, f"delta={(exp - time.time())/86400:.1f}d")
check("A8 call still fine", r8.get("ok") is True, str(r8.get("reason")))

# ---------------------------------------------------------------------------
section("A9 — upstream failure (bad password) -> clean structured error")
os.environ["HIS_PASSWORD"] = "wrong-password"
with hb._LOCK:
    hb._STATE["token"] = None
    hb._STATE["exp"] = 0
t0 = time.time()
r9 = hb.caller_response("91671473")
dt9 = time.time() - t0
check("A9 ok false", r9.get("ok") is False, str(r9))
check("A9 reason", r9.get("reason") == "his_login_failed", str(r9.get("reason")))
check("A9 no crash/hang", dt9 < 10.0, f"{dt9:.1f}s")

os.environ["HIS_USERNAME"] = ""
with hb._LOCK:
    hb._STATE["token"] = None
    hb._STATE["exp"] = 0
r9b = hb.caller_response("91671473")
check("A9b not_configured", r9b.get("reason") == "not_configured", str(r9b.get("reason")))
os.environ["HIS_USERNAME"] = HIS_USER

# restore good credentials
os.environ["HIS_PASSWORD"] = HIS_PASS
with hb._LOCK:
    hb._STATE["token"] = None
    hb._STATE["exp"] = 0
r9c = hb.caller_response("00000000")
check("A9c recovery after restore", r9c.get("ok") is True, str(r9c.get("reason")))

# ---------------------------------------------------------------------------
section("B — endpoint auth (Flask test client)")
app_module._verify_agent_credentials = lambda u, p: {"kind": "agent"} if (u == "101" and p == "sip-secret") else None
client = app_module.app.test_client()

def basic(u, p):
    return "Authorization", "Basic " + base64.b64encode(f"{u}:{p}".encode()).decode()

resp = client.get("/api/his/caller/91671473")
check("B1 unauthenticated -> 401", resp.status_code == 401, str(resp.status_code))
check("B1 no patient data in body", b"PAT_" not in resp.data and b"EASIN" not in resp.data, resp.data[:120].decode(errors="replace"))
check("B1 no WWW-Authenticate challenge", "WWW-Authenticate" not in resp.headers, str(resp.headers.get("WWW-Authenticate")))

resp = client.get("/api/his/caller/91671473", headers={basic("101", "bad")})
check("B2 wrong creds -> 401", resp.status_code == 401, str(resp.status_code))

resp = client.get("/api/his/caller/91671473", headers={basic("101", "sip-secret")})
check("B3 basic auth -> 200 + card", resp.status_code == 200 and resp.get_json().get("matched") is True,
      f"{resp.status_code} {str(resp.get_data(as_text=True))[:120]}")
check("B3 bearer token never leaks", "AuthorizationToken" not in resp.get_data(as_text=True), "")
check("B3 compact selection", "PAT_NAME" not in resp.get_data(as_text=True), "")

resp = client.get("/api/his/caller/99999000", headers={basic("101", "sip-secret")})
check("B4 unknown phone via endpoint", resp.status_code == 200 and resp.get_json().get("matched") is False
      and resp.get_json().get("search_hint") == "name_or_id", str(resp.get_json())[:120])

with client.session_transaction() as s:
    s["user"] = {"id": 1, "username": "medic", "role": "viewer", "display_name": "Medic"}
resp = client.get("/api/his/caller/99999001")
check("B5 session auth -> 200", resp.status_code == 200, str(resp.status_code))

resp = client.get("/api/his/caller/x", headers={basic("101", "sip-secret")})
check("B6 weird phone no crash", resp.status_code == 200, str(resp.status_code))

resp = client.get("/api/his/caller/00000000?by=poney&q=JavaScript:alert", headers={basic("101", "sip-secret")})
check("B7 unknown by dropped, q never reaches upstream (11 phone matches)",
      resp.status_code == 200 and resp.get_json().get("matched") is True and len(resp.get_json().get("matches") or []) == 11,
      str(resp.get_json())[:120])

resp = client.get("/api/his/caller/00000000?pat_id=<script>", headers={basic("101", "sip-secret")})
check("B8 non-numeric pat_id ignored", resp.status_code == 200, str(resp.status_code))

resp = client.get("/api/his/caller/00000000?by=name&q=CHANDANIE", headers={basic("101", "sip-secret")})
check("B9 endpoint name fallback", resp.status_code == 200 and resp.get_json().get("matched") is True, str(resp.get_json())[:120])

app_module._RL.clear()
app_module._verify_agent_credentials = lambda u, p: {"kind": "agent"}
for _ in range(30):
    client.get("/api/his/caller/00000000", headers={basic("101", "sip-secret")})
resp = client.get("/api/his/caller/00000000", headers={basic("101", "sip-secret")})
check("B10 rate limit -> 429", resp.status_code == 429, str(resp.status_code))

# ---------------------------------------------------------------------------
fails = [(n, d) for n, ok, d in _RESULTS if not ok]
print(f"\n========== {len(_RESULTS) - len(fails)}/{len(_RESULTS)} passed ==========")
if fails:
    print("FAILURES:")
    for n, d in fails:
        print(f" - {n}: {d}")
sys.exit(1 if fails else 0)
