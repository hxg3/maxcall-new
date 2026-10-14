#!/usr/bin/env python3
"""
Read-only client for Solver WebHIS (MAXCARE HOSPITAL, Salalah).

Endpoint: http://<server>/API/Api/<Controller>/<Method>
Auth:     POST UserInfo/ValidateUser -> AuthorizationToken (JWT, ~100 days)
          Every call: POST {loggedInInfo, Payload} + header Authorization: Bearer <token>
          loggedInInfo MUST contain ApiKey (otherwise every call throws
          "Object reference not set to an instance of an object").

Payload conventions (verified empirically):
  * object payload  -> {"loggedInInfo": {...}, "Payload": {...}}
  * some endpoints expect a RAW value, not an object
      - PatientSearch/GetPatientVisitHistory  -> Payload = Pat_ID (int or str)
      - patient/GetPatientByID                -> Payload = {"Pat_ID": "<id>"}
      - PatientSearch/GetPatientBillHistory   -> Payload = {"Visit_No": n, "Trans_Type": "BILL"}
"""
import json
import os
import urllib.error
import urllib.request

HOST = os.environ.get("HIS_HOST", "192.168.1.77")
PORT = int(os.environ.get("HIS_PORT", "81"))
BASE = f"http://{HOST}:{PORT}/API/Api/"
STATE_DIR = os.environ.get("HIS_STATE_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), ".state"))


def _path(name):
    os.makedirs(STATE_DIR, exist_ok=True)
    return os.path.join(STATE_DIR, name)


def login(username, password, apikey=""):
    """Authenticate and persist token + loggedInInfo to the state dir."""
    body = json.dumps({
        "Usr_Id": username,
        "Usr_Pwd": password,
        "ApiKey": apikey,
        "isPasswordEncrypted": False,
        "Origin": "",
    }).encode()
    req = urllib.request.Request(
        BASE + "UserInfo/ValidateUser", data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"})
    data = json.loads(urllib.request.urlopen(req, timeout=30).read().decode())
    res = data["result"]
    if not res.get("Success"):
        raise RuntimeError(f"login failed: {res}")

    ui = res["UserInfo"]
    user = ui["UserInfo"]
    loc = (ui.get("UserLocations") or [{}])[0]

    logged_in_info = {
        "Usr_Name": user["Usr_Name"],
        "Usr_Pwd": "",
        "Usr_Type": user["Usr_Type"],
        "Usr_Default_Dept": user.get("Usr_Default_Dept"),
        "Allow_Per_Multi_WH": user.get("Allow_Per_Multi_WH", False),
        "Lang_CD": user.get("Usr_Language", "en"),
        "DashBoardId": ui.get("DashBoardId", "FD"),
        "IsCashier": True,
        "CanCollectBill": False,
        "CashierShiftNo": 0,
        "Usr_Email": user.get("Usr_Email", ""),
        "Co_Cd": loc.get("Co_Cd"),
        "Co_Name": loc.get("Co_Name"),
        "Div_Cd": "##",
        "Loc_Id": loc.get("Loc_Id", user.get("Usr_DefaultLoc_ID", 1)),
        "Wh_Cd": "##",
        "Dept_Cd": "##",
        "Vr_Year": ui.get("Vr_Year"),
        "Usr_id": user["Usr_Id"],
        "Usr_Id": user["Usr_Id"],
        "Module_ID": "1",
        "Usr_Language": user.get("Usr_Language", "en"),
        "Default_DashboardID": ui.get("DashBoardId", "FD"),
        # REQUIRED - without it the API throws a null-reference error.
        "ApiKey": ui.get("ApiKey") or apikey,
    }

    with open(_path("token.txt"), "w") as f:
        f.write(res["AuthorizationToken"])
    with open(_path("logged_in_info.json"), "w") as f:
        json.dump(logged_in_info, f, ensure_ascii=False)
    return res["AuthorizationToken"]


def _load():
    with open(_path("token.txt")) as f:
        token = f.read().strip()
    with open(_path("logged_in_info.json")) as f:
        return token, json.load(f)


def call(endpoint, payload=""):
    """POST to <Controller>/<Method> with the standard envelope."""
    token, li = _load()
    body = json.dumps({"loggedInInfo": li, "Payload": payload}).encode()
    req = urllib.request.Request(BASE + endpoint, data=body, headers={
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": "Bearer " + token,
    })
    try:
        return json.loads(urllib.request.urlopen(req, timeout=45).read().decode())
    except urllib.error.HTTPError as e:
        try:
            return json.loads(e.read().decode())
        except Exception:
            return {"err": str(e)}
    except Exception as e:
        return {"err": str(e)}


# --- convenience wrappers -------------------------------------------------

_SEARCH_BLANK = {
    "Address": "", "Age": "0", "Category": "", "CompanyCode": "", "CompanyName": "",
    "DateOfBirth": "", "Email": "", "EmployeeID": "", "FirstName": "", "Gender": "",
    "IDCardNo": "", "MaritalStatus": "", "Nationality": "", "NextOfKin": "",
    "Occupation": "", "Pat_ID": "0", "PhDateFrom": "", "PhDateTo": "",
    "PhoneNumber": "", "SecondName": "", "ThirdName": "", "TribeName": "",
}


def search_patient(phone="", **overrides):
    """Fuzzy patient lookup. Phone is matched as a substring."""
    payload = dict(_SEARCH_BLANK)
    payload["PhoneNumber"] = phone
    payload.update(overrides)
    return call("PatientSearch/GetSearchResult", payload).get("result", {}).get("Payload", [])


def get_patient(pat_id):
    return call("patient/GetPatientByID", {"Pat_ID": str(pat_id)}).get("result", {}).get("Payload", [])


def get_visits(pat_id):
    return call("PatientSearch/GetPatientVisitHistory", pat_id).get("result", {}).get("Payload", [])


def get_bill(visit_no, trans_type="BILL"):
    return call("PatientSearch/GetPatientBillHistory",
                {"Visit_No": visit_no, "Trans_Type": trans_type}).get("result", {}).get("Payload", [])


def get_admission(pat_id):
    """Current inpatient admission, if any."""
    return call("IPAdmission/GetAdmissionDetailByPatID", {"Pat_ID": pat_id}).get("result", {}).get("Payload", [])


def caller_card(phone):
    """
    Everything maxcall needs for an inbound call, in one call.
    Returns dict: {matches, best, visits, admission, has_debt}
    """
    matches = search_patient(phone)
    if not matches:
        return {"matches": [], "best": None, "visits": [], "admission": [], "has_debt": False}

    # Prefer the newest MRN (highest Pat_ID is a safe proxy for recency here).
    best = max(matches, key=lambda r: r.get("Pat_ID") or 0)
    pat_id = best["Pat_ID"]
    visits = get_visits(pat_id)
    admission = get_admission(pat_id) or []

    return {
        "matches": matches,
        "best": best,
        "pat_id": pat_id,
        "detail": (get_patient(pat_id) or [{}])[0],
        "visits": visits,
        "admission": admission,
        "is_inpatient": bool(admission),
    }


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        print(json.dumps(caller_card(sys.argv[1]), ensure_ascii=False, indent=1))
    else:
        print("usage: his_client.py <phone>")