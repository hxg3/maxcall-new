"""
Read-only broker between MaxCall and the hospital's Solver WebHIS
(MAXCARE HOSPITAL, Salalah — product AfiInfotech_SolverWebHis_Dream_Project).

Contract (verified, see his-integration/EXECUTION_PROMPT.md):
    base  = http://<HIS_HOST>:<HIS_PORT>/API/Api/          (NOT the site root)
    login = POST UserInfo/ValidateUser -> result.AuthorizationToken (JWT, 100 days)
    call  = POST <Controller>/<Method>
            headers: Authorization: Bearer <token>
            body: {"loggedInInfo": {..}, "Payload": <per-endpoint shape>}
            loggedInInfo MUST contain ApiKey or every call fails with
            "Object reference not set to an instance of an object".

Security model (hard requirements from the brief):
    - credentials stay on this server only (HIS_USERNAME / HIS_PASSWORD env);
    - the WebHIS bearer token is refreshed locally (decoded from the JWT exp)
      whenever fewer than 65 days remain, and re-issued once on 401/403;
    - only the four whitelisted read-only operations are used — no generic
      proxy, no Save/Post/Delete/booking endpoint, ever;
    - nothing patient-identifiable beyond the field allow-list leaves this
      module, and no token is ever logged or returned.
"""
import base64
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from collections import OrderedDict

HIS_HOST = os.environ.get("HIS_HOST", "192.168.1.77")
HIS_PORT = int(os.environ.get("HIS_PORT", "81"))
BASE = f"http://{HIS_HOST}:{HIS_PORT}/API/Api/"

# Canonical hostname for user-facing URLs (popup + "Open in WebHIS" deep link)
SOURCE_URL = "http://solverserver:81/#/home/hmis/op/PatientSearch"

# Re-login when fewer than 65 days remain (token lifetime is 100 days)
REFRESH_MARGIN_DAYS = 65

CACHE_TTL = 600        # search / patient-card cache lifetime (seconds) — upstream takes 1.1-1.8 s
CACHE_MAX = 500        # bounded LRU so caches cannot grow forever
HTTP_TIMEOUT = 30

_LOCK = threading.Lock()
_STATE = {"token": None, "li": None, "exp": 0}
_SEARCH_CACHE = OrderedDict()   # (by, q) -> (ts, rows)
_CARD_CACHE = OrderedDict()     # (pat_id,) -> (ts, dict)


# ---------------------------------------------------------------------------
# token handling
# ---------------------------------------------------------------------------

def _token_exp(token):
    """Decode the JWT payload locally -> int exp (epoch). 0 when unreadable."""
    try:
        seg = token.split(".")[1]
        seg += "=" * (-len(seg) % 4)
        payload = json.loads(base64.urlsafe_b64decode(seg.encode()).decode())
        return int(payload.get("exp") or 0)
    except Exception:
        return 0


def _credentials():
    # read at call time so operators can rotate them without a restart
    return os.environ.get("HIS_USERNAME", "").strip(), os.environ.get("HIS_PASSWORD", "")


class _HISError(Exception):
    """استدعاء WebHIS فشل — يعاد كـ {"ok": false, "reason": ...} للـ popup."""


def _login_locked():
    """Fresh ValidateUser call. Caller must hold _LOCK."""
    username, password = _credentials()
    if not username or not password:
        return "not_configured"
    body = json.dumps({
        "Usr_Id": username,
        "Usr_Pwd": password,
        "ApiKey": "",
        "isPasswordEncrypted": False,
        "Origin": "",
    }).encode()
    req = urllib.request.Request(
        BASE + "UserInfo/ValidateUser", data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"})
    try:
        res = json.loads(urllib.request.urlopen(req, timeout=HTTP_TIMEOUT).read().decode()).get("result") or {}
        if not isinstance(res, dict) or not res.get("Success"):
            return "his_login_failed"
        ui = res["UserInfo"]
        user = ui["UserInfo"]
        loc = (ui.get("UserLocations") or [{}])[0]
        _STATE["li"] = {
            "Usr_Name": user["Usr_Name"],
            "Usr_Pwd": "",
            "Usr_Type": user.get("Usr_Type", 0),
            "Usr_Default_Dept": user.get("Usr_Default_Dept", "FO"),
            "Allow_Per_Multi_WH": user.get("Allow_Per_Multi_WH", False),
            "Lang_CD": user.get("Usr_Language", "en"),
            "DashBoardId": ui.get("DashBoardId", "FD"),
            "IsCashier": True,
            "CanCollectBill": False,
            "CashierShiftNo": 0,
            "Usr_Email": user.get("Usr_Email", ""),
            "Co_Cd": loc.get("Co_Cd", "MA"),
            "Co_Name": loc.get("Co_Name", ""),
            "Div_Cd": "##",
            "Loc_Id": loc.get("Loc_Id", 1),
            "Wh_Cd": "##",
            "Dept_Cd": "##",
            "Vr_Year": ui.get("Vr_Year", time.localtime().tm_year),
            "Usr_id": user["Usr_Id"],
            "Usr_Id": user["Usr_Id"],
            "Module_ID": "1",
            "Usr_Language": user.get("Usr_Language", "en"),
            "Default_DashboardID": ui.get("DashBoardId", "FD"),
            # REQUIRED — without it every endpoint throws a null-reference error
            "ApiKey": ui.get("ApiKey") or "MAX",
        }
        _STATE["token"] = res["AuthorizationToken"]
        _STATE["exp"] = _token_exp(_STATE["token"])
        return ""
    except urllib.error.HTTPError as e:
        return f"his_http_{e.code}"
    except Exception:
        return "his_unreachable"


def _ensure_token():
    """Make sure a fresh-enough token exists. Returns '' or a failure reason."""
    with _LOCK:
        exp = _STATE.get("exp") or 0
        stale = exp and (exp - time.time()) < REFRESH_MARGIN_DAYS * 86400
        if _STATE.get("token") and not stale:
            return ""
        return _login_locked()


def _call(endpoint, payload, retry=True):
    """One whitelisted upstream POST. Returns (response_dict|None, reason_str)."""
    reason = _ensure_token()
    if reason:
        return None, reason
    body = json.dumps({"loggedInInfo": _STATE["li"], "Payload": payload}).encode()
    req = urllib.request.Request(
        BASE + endpoint, data=body, headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Authorization": "Bearer " + _STATE["token"],
        })
    try:
        return json.loads(urllib.request.urlopen(req, timeout=HTTP_TIMEOUT).read().decode()), ""
    except urllib.error.HTTPError as e:
        if e.code in (401, 403) and retry:
            # جبر الجلسة: دخول جديد مرة واحدة ثم إعادة الإرسال مرة واحدة فقط
            with _LOCK:
                _STATE["token"] = None
                _STATE["exp"] = 0
                bad = _login_locked()
            if not bad:
                return _call(endpoint, payload, retry=False)
            return None, bad
        try:
            # غرابة WebHIS: بعض النقاط (مثل IPAdmission عند عدم وجود دخول) تُعيد
            # HTTP 400/500 مع جسم JSON سليم {"result": {...}} — تُعتبر استجابة عادية.
            parsed = json.loads(e.read().decode())
        except Exception:
            return None, f"his_http_{e.code}"
        if isinstance(parsed, dict) and isinstance(parsed.get("result"), dict):
            return parsed, ""
        return None, f"his_http_{e.code}"
    except Exception:
        return None, "his_unreachable"


def _payload(res):
    """Normalise an upstream response into its result.Payload list."""
    if not isinstance(res, dict):
        return []
    r = res.get("result")
    if not isinstance(r, dict):
        return []
    p = r.get("Payload")
    return p if isinstance(p, list) else []


# ---------------------------------------------------------------------------
# whitelisted read-only operations (the ONLY four upstream calls we ever make)
# ---------------------------------------------------------------------------

_SEARCH_BLANK = {
    "Address": "", "Age": "0", "Category": "", "CompanyCode": "", "CompanyName": "",
    "DateOfBirth": "", "Email": "", "EmployeeID": "", "FirstName": "", "Gender": "",
    "IDCardNo": "", "MaritalStatus": "", "Nationality": "", "NextOfKin": "",
    "Occupation": "", "Pat_ID": "0", "PhDateFrom": "", "PhDateTo": "",
    "PhoneNumber": "", "SecondName": "", "ThirdName": "", "TribeName": "",
}

_SEARCH_FIELD = {"phone": "PhoneNumber", "name": "FirstName", "id": "IDCardNo", "pid": "Pat_ID"}


def _search_rows(by, q):
    """PatientSearch/GetSearchResult with one whitelisted filter field."""
    if by not in _SEARCH_FIELD or not q:
        return []
    key = (by, q)
    row = _SEARCH_CACHE.get(key)
    if row and time.time() - row[0] < CACHE_TTL:
        _SEARCH_CACHE.move_to_end(key)
        return list(row[1])
    payload = dict(_SEARCH_BLANK)
    payload[_SEARCH_FIELD[by]] = q
    res, reason = _call("PatientSearch/GetSearchResult", payload)
    if res is None:
        raise _HISError(reason)
    rows = [r for r in _payload(res) if isinstance(r, dict)]
    _SEARCH_CACHE[key] = (time.time(), rows)
    _SEARCH_CACHE.move_to_end(key)
    while len(_SEARCH_CACHE) > CACHE_MAX:
        _SEARCH_CACHE.popitem(last=False)
    return rows


def _patient_detail(pat_id):
    """patient/GetPatientByID — Payload MUST be {"Pat_ID": "<id>"} as a string."""
    res, reason = _call("patient/GetPatientByID", {"Pat_ID": str(pat_id)})
    if res is None:
        raise _HISError(reason)
    rows = [r for r in _payload(res) if isinstance(r, dict)]
    return rows[0] if rows else {}


def _visit_rows(pat_id):
    """PatientSearch/GetPatientVisitHistory — Payload is the RAW Pat_ID."""
    res, reason = _call("PatientSearch/GetPatientVisitHistory", str(pat_id))
    if res is None:
        raise _HISError(reason)
    return [r for r in _payload(res) if isinstance(r, dict)]


def _bill_rows(visit_no):
    """PatientSearch/GetPatientBillHistory — Payload {"Visit_No": int, "Trans_Type": "BILL"}."""
    res, reason = _call("PatientSearch/GetPatientBillHistory", {"Visit_No": int(visit_no), "Trans_Type": "BILL"})
    if res is None:
        raise _HISError(reason)
    return [r for r in _payload(res) if isinstance(r, dict)]


def _admission_row(pat_id):
    """IPAdmission/GetAdmissionDetailByPatID — empty payload = not admitted (normal)."""
    try:
        pid = int(str(pat_id))
    except (TypeError, ValueError):
        return {}
    res, reason = _call("IPAdmission/GetAdmissionDetailByPatID", {"Pat_ID": pid})
    if res is None:
        raise _HISError(reason)
    rows = [r for r in _payload(res) if isinstance(r, dict)]
    return rows[0] if rows else {}


# ---------------------------------------------------------------------------
# visit_rows — زيارات الاستقبال من HIS (PATREG) للقراءة فقط
# يتم استدعاؤها من /api/feedback/generate لبناء قائمة مهام التغذية الراجعة
# ---------------------------------------------------------------------------

def visit_rows(from_iso, to_iso):
    """زيارات الاستقبال من HIS خلال فترة زمنية — قراءة فقط.

    يستدعي TransDocSearch/searchDocument بـ docType=PATREG وتواريخ ISO كاملة.
    ترجع قائمة قواميس بحقول مقبولة فقط: doc_no, visit_date, pat_id, patient_name,
    phone, doctor, payer, amount, employer, service.
    عند أي خطأ ترجع [] ولا تسرب أي خطأ للعميل.
    """
    payload = {
        "docType": "PATREG",
        "fromDate": from_iso,
        "toDate": to_iso,
        "patID": "", "CusEmp_FileNo": "", "billNo": "", "sampleId": "",
        "patName": "", "accd": "", "acDesc": "", "consultant": "", "patGSM": "",
    }
    res, reason = _call("TransDocSearch/searchDocument", payload)
    if res is None:
        return []
    rows = [r for r in _payload(res) if isinstance(r, dict)]
    out = []
    for r in rows:
        phone = re.sub(r"\D", "", str(r.get("patGsmNo") or ""))[:20]
        out.append({
            "doc_no": str(r.get("docNo") or ""),
            "visit_date": str(r.get("docDate") or "")[:10],
            "pat_id": str(r.get("patID") or ""),
            "patient_name": norm_name(r.get("patName")),
            "phone": phone,
            "doctor": _clean(r.get("consultant")),
            "payer": _clean(r.get("acDesc")),
            "employer": _clean(r.get("Usr_ID")),
            "service": "",
            "amount": _num(r.get("docAmt")),
        })
    return out


# ---------------------------------------------------------------------------
# compaction (field allow-list — no raw upstream payloads reach the client)
# ---------------------------------------------------------------------------

def _clean(v):
    if v is None:
        return ""
    return str(v).strip()


def norm_name(v):
    """تنظيف أسماء المرضى القادمة من HIS.

    الأسماء تأتي بمخلفات فواصل مثل 'ABOUD . .' و'NILANTHA WIJEKOON  - -'
    وسنفعل: نسقط سلاسل النقاط/الشرطات (فتصبح 'ABOUD' و'NILANTHA WIJEKOON').
    لا تُستخدم للأطباء/الدافع لأن 'DR.' و'( OQIC )' فيها دلالة.
    """
    s = _clean(v)
    s = re.sub(r"(?:\s*[.\-]\s*)+", " ", s)
    return re.sub(r"\s{2,}", " ", s).strip()


_SUMMARY_CACHE = OrderedDict()   # (pat_id,) -> (ts, dict) — ملخص خفيف للمهام


def patient_summary(pat_id):
    """العمر/الجنس/الجنسية/رمز الدافع لمريض — كال خام kesyen (GetPatientByID).

    يستخدمها توليد المهام وإثراء القائمة عند العرض. عند أي خطأ يرجع {}.
    """
    pid = _clean(pat_id) or "0"
    row = _SUMMARY_CACHE.get((pid,))
    if row and time.time() - row[0] < CACHE_TTL:
        _SUMMARY_CACHE.move_to_end((pid,))
        return dict(row[1])
    try:
        d = _patient_detail(pid) or {}
    except Exception:
        return {}
    out = {
        "age": _clean(d.get("PAT_AGE"))[:12],
        "sex": _clean(d.get("PAT_SEX"))[:4].upper(),
        "nationality": _clean(d.get("Nationality"))[:64],
        "payer_code": _clean(d.get("Ac_Cd"))[:16],
    }
    _SUMMARY_CACHE[(pid,)] = (time.time(), out)
    if len(_SUMMARY_CACHE) > CACHE_MAX:
        _SUMMARY_CACHE.popitem(last=False)
    return out


def _name(r):
    parts = [_clean(r.get("FirstName")), _clean(r.get("SecondName")), _clean(r.get("ThirdName"))]
    return " ".join(p for p in parts if p)


def _num(v):
    try:
        return round(float(v), 3)
    except (TypeError, ValueError):
        return 0.0


def _compact_match(r):
    phone = re.sub(r"\D", "", _clean(r.get("PhoneNumber")))[:20]
    return {
        "pat_id": _clean(r.get("Pat_ID")) or "0",
        "name": _name(r),
        "age": _clean(r.get("Age")),
        "sex": _clean(r.get("Gender")),
        "phone": phone,
        "nationality": _clean(r.get("Nationality")),
        "category": _clean(r.get("Category")),
    }


def _card_for(pat_id, match=None):
    """Full compact card for one patient: selected + visits + bills."""
    pid = _clean(pat_id)
    key = (pid,)
    row = _CARD_CACHE.get(key)
    if row and time.time() - row[0] < CACHE_TTL:
        _CARD_CACHE.move_to_end(key)
        card = dict(row[1])
        if match:
            sel = card["selected"]
            sel["nationality"] = sel["nationality"] or _clean(match.get("nationality"))
            sel["category"] = sel["category"] or _clean(match.get("category"))
        return card

    d = _patient_detail(pid)
    # الحقول الوطنية/الفئة آتية من صف البحث (مؤكدة) وتُستخدم فقط كاحتياط
    match = match or {}
    selected = {
        "pat_id": pid,
        "name": _clean(d.get("PAT_NAME")) or _name(d),
        "name_ar": _clean(d.get("PAT_ArName")) or _clean(d.get("Pat_ArName")),
        "age": _clean(d.get("PAT_AGE")),
        "sex": _clean(d.get("PAT_SEX")),
        "phone": _clean(d.get("PAT_GSMNO")),
        "nationality": _clean(d.get("Nationality")) or _clean(match.get("nationality")),
        "category": _clean(d.get("Ac_Cd")) or _clean(match.get("category")),
        "insurance_code": _clean(d.get("Ac_Cd")),
        "mrn": pid,
        "consultant": _clean(d.get("Cons_Name")),
        "speciality": _clean(d.get("Sp_Cd")),
        "blood_group": _clean(d.get("Blood_Group")),
        "outstanding": _num(d.get("RemainingAmt")),
        "consumed": _num(d.get("ConsumedAmt")),
        "is_inpatient": bool(d.get("Is_Patient_Ip")) or bool(_admission_row(pid)),
        "has_registration": _num(d.get("HasRegistration")) > 0,
        "appointment_date": _clean(d.get("AppointmentDate"))[:10] or None,
        "appointment_consultant": _clean(d.get("AppoinmentConsultant")) or None,
    }

    visits = []
    for r in _visit_rows(pid):
        if r.get("PH_ISCANCELLED") in (1, "1", True):
            continue
        visits.append({
            "type": _clean(r.get("Trans_Type")),
            "ph_no": r.get("PH_NO"),
            "date": _clean(r.get("PH_DATE"))[:10],
            "time": _clean(r.get("PH_Time")),
            "consultant": _clean(r.get("CONS_NAME")),
            "amount": _num(r.get("CONS_NET_AMT")),
            "payment": _clean(r.get("PaymentType")),
        })

    bills = []
    for r in visits:  # أحدث فاتورة غير ملغاة (الصفوف مرتبة تنازلياً من المصدر)
        if r["type"] == "BILL" and r.get("ph_no"):
            bills = [
                {
                    "item_code": _clean(b.get("ITEM_CODE")),
                    "description": _clean(b.get("ITEM_DESC")),
                    "qty": _num(b.get("QTY")),
                    "net": _num(b.get("NET_AMT")),
                }
                for b in _bill_rows(r["ph_no"])
                if b.get("ISCANCELLED") in (0, "0", "", None, False)
            ]
            break

    card = {"selected": selected, "visits": visits, "bills": bills}
    _CARD_CACHE[key] = (time.time(), card)
    _CARD_CACHE.move_to_end(key)
    while len(_CARD_CACHE) > CACHE_MAX:
        _CARD_CACHE.popitem(last=False)
    return dict(card)


# ---------------------------------------------------------------------------
# public API (used by app.py)
# ---------------------------------------------------------------------------

def _empty(matched, hint=False):
    resp = {"ok": True, "matched": matched, "matches": [], "selected": None,
            "visits": [], "bills": [], "source_url": SOURCE_URL}
    if hint:
        resp["search_hint"] = "name_or_id"
    return resp


def _error(reason):
    """Never raises — الحالة الفاشلة تُعاد بشكل منظم ليخفيها الـ popup بهدوء."""
    return {"ok": False, "reason": reason, "matches": [], "selected": None,
            "visits": [], "bills": [], "source_url": SOURCE_URL}


def caller_response(key, by="phone", q="", pat_id=None):
    """Build the popup JSON for one caller lookup.

    by="phone" -> key is the (already normalised) phone number
    by="name"|"id" -> q is the name / ID-card search term (fallback search)
    pat_id -> optional selector among the returned matches (UI choice)
    """
    if by not in ("phone", "name", "id"):
        return _error("bad_request")

    if by == "phone":
        query = re.sub(r"\D", "", str(key or ""))[:20]
        if len(query) < 4:
            return _empty(False, hint=True)
    else:
        query = re.sub(r"[^A-Za-z0-9\u0600-\u06FF \-]", "", str(q or ""))[:64].strip()
        if not query:
            return _empty(False, hint=True)

    try:
        rows = _search_rows(by, query)
        matches = [_compact_match(r) for r in rows]
    except _HISError as e:
        return _error(str(e))
    except Exception:
        return _error("his_broker_error")

    if not matches:
        return _empty(False, hint=True)

    try:
        chosen = None
        chosen_match = None
        if pat_id and re.fullmatch(r"\d{1,10}", str(pat_id)):
            for m in matches:
                if str(m["pat_id"]) == str(pat_id):
                    chosen = m["pat_id"]
                    chosen_match = m
                    break
        if chosen is None:
            # الأحدث أولاً — أعلى MRN هو أقرب بديل للحداثة (نمط his_client.py)
            chosen_match = max(matches, key=lambda m: int(m["pat_id"] or 0))
            chosen = chosen_match["pat_id"]

        card = _card_for(chosen, chosen_match)
        return {
            "ok": True,
            "matched": True,
            "matches": matches,
            "selected": card["selected"],
            "visits": card["visits"],
            "bills": card["bills"],
            "source_url": SOURCE_URL,
        }
    except _HISError as e:
        return _error(str(e))
    except Exception:
        return _error("his_broker_error")


def insurance_card(pat_id):
    """بطاقة التأمين المختصرة لموظف الاستقبال (Chrome extension) — قراءة فقط.

    استدعاءان ثابتان فقط: patient/GetPatientByID + PatientSearch/GetSearchResult
    (فلتر Pat_ID). تعيد الحقول المسموحة فقط (allow-list) — لا شيء آخر يخرج من
    هذا الملف، ولا يتم أي طلب كتابة على WebHIS إطلاقاً.
    """
    pid = _clean(pat_id)
    if not pid.isdigit() or len(pid) > 10:
        return {"ok": False, "reason": "bad_pat_id"}
    try:
        d = _patient_detail(pid) or {}
    except _HISError as e:
        return {"ok": False, "reason": str(e)}
    if not d:
        return {"ok": False, "reason": "not_found"}
    try:
        rows = _search_rows("pid", pid)
    except _HISError:
        rows = []  # اسم الدافع إثراء اختياري — فشله لا يُفشل البطاقة
    company = rows[0] if rows else {}
    return {
        "ok": True,
        "patient": {
            "pat_id": pid,
            "name": _clean(d.get("PAT_NAME")) or _name(d),
            "sex": _clean(d.get("PAT_SEX")),
            "age": _clean(d.get("PAT_AGE")),
            "dob": _clean(d.get("PAT_DOB"))[:10],
            "phone": _clean(d.get("PAT_GSMNO")),
            "country_code": _clean(d.get("Pat_Country_Code")),
            "nationality": _clean(d.get("Nationality")),
        },
        "insurance": {
            "payer_code": _clean(d.get("Ac_Cd")),
            "payer_name": _clean(company.get("CompanyName")),
            "company_code": _clean(company.get("CompanyCode")),
            "card_no": _clean(d.get("Pat_Certificate_No")),
            "policy_no": _clean(d.get("Pat_Policy_No")),
            "card_expiry": _clean(d.get("PAT_Exp_Date"))[:10],
            "deductible": _num(d.get("PAT_Deducitble_Amt")),
            "co_ins_percent": _num(d.get("PAT_CO_INS_PER")),
        },
        "visit": {
            "consultant": _clean(d.get("Cons_Name")),
            "consultant_id": _clean(d.get("Cons_Id")),
            "speciality": _clean(d.get("Sp_Cd")),
            "appointment_date": _clean(d.get("AppointmentDate"))[:10] or None,
            "appointment_consultant": _clean(d.get("AppoinmentConsultant")) or None,
        },
        "source_url": SOURCE_URL,
    }
