# NextCare PULSE (ASP.NET WebForms + chosen.js). نقل حرفي من
# Extension/src/content/insurers/nextcare.js — نفس المحددات ونفس منطق
# window.Login2.Login (المتصفح ينادي دبيشيته نفسها لا محاكاة سطح اليوم).
#
# التدفق: دخول → EligibilityChecking2.aspx → card_no + type →
# btnCheckEligibilityorSearchbyPolicy → انتظار زر الطباعة → Print
# (الملف يُنزّل مباشرة — نلتقطه عبر download في العامل بدل نافذة).

import time
import urllib.parse

from .base import Backend, BackendError, save_last_good_member
from .base import creds_for

HOST = "https://pulse-uae.nextcarehealth.com"
ELIG_URL = HOST + "/EligibilityChecking2.aspx"
LOGIN_URL = HOST + "/Login2.aspx?token=AQ=="

_MJS = """
const okEl = (el) => !!el && el.getClientRects().length > 0 &&
    !el.disabled && !el.readOnly;
"""

JS_MEMBER = f"""() => {{ {_MJS}
  const direct = document.querySelector('[id$="_txtCardNumber"]');
  if (direct && okEl(direct)) return direct;
  return [...document.querySelectorAll("input")].find((el) =>
    okEl(el) && (el.type === "text" || !el.type) &&
    /member|card|certif|insur/i.test(
      `${{el.id}} ${{el.name || ""}} ${{el.placeholder || ""}}`)
  ) || null;
}}"""

JS_TYPE_SELECT = f"""() => {{ {_MJS}
  return document.querySelector('select[id$="_cmbType"]') ||
         document.querySelector('select[id*="cmbType"]') || null;
}}"""

JS_NEXT_BTN = f"""() => {{ {_MJS}
  const direct = document.querySelector(
    '#btnCheckEligibilityorSearchbyPolicy, [id$="_btnCheckEligibilityorSearchbyPolicy"]');
  if (direct && okEl(direct)) return direct;
  return [...document.querySelectorAll(
    "a,button,input[type=button],input[type=submit],span[onclick]")]
    .find((el) =>
      okEl(el) &&
      /check\\s*eligib|next|continue|proceed|search|submit|التالي|متابعة|تحقق/i.test(
        `${{el.id}} ${{el.name || ""}} ${{el.value || ""}} ${{(el.textContent||"").trim()}} ${{el.getAttribute("onclick") || ""}}`)
    ) || null;
}}"""

JS_PRINT_BTN = f"""() => {{ {_MJS}
  const el = document.querySelector(
    '#btnPrintVerification, [id$="_btnPrintVerification"]');
  return (el && okEl(el)) ? el : null;
}}"""

JS_IS_LOGIN = """() => !!(document.querySelector("#frmLogin") &&
  document.querySelector("#txtUserName"))"""

JS_LOGIN_ERR = """() => {
  const el = document.querySelector("#lblerrLogin");
  return (el && el.offsetParent !== null && (el.textContent || "").trim())
    ? el.textContent.trim() : null;
}"""


class NextCare(Backend):
    id = "nextcare"
    report_capture = "download"          # الملف ينزل مباشرة — لا نافذة
    REPORT_EXT_RE = r"\.pdf(\?|$)|download|="

    # ---------- أدوات ----------
    def report_matches(self, url):
        return bool(url)

    def _find(self, w, js):
        try:
            h = w.page.evaluate_handle(js)
            return h.as_element()
        except Exception:
            return None

    def _safe_eval(self, w, js):
        try:
            return w.page.evaluate(js)
        except Exception:
            return None

    # ---------- الدخول ----------
    def do_login(self, w, creds):
        """المسار المُثبت عملياً: تعبئة الحقول ثم نقر زر الواجهة (#btnLogin) —
        هذه الصفحة لا تُبقي إضافة login-خطوة في نفس الصفحة؛ الدخول ينشئ
        جلسة ASPXAUTH ثم يحوّل للمقاصد الواردة (Inquiry2.aspx أولاً)."""
        w.progress("login", "run", "doLogin")
        user = self._find(w, "() => document.querySelector('#txtUserName')")
        passt = self._find(w, "() => document.querySelector('#txtPassword')")
        submit = self._find(w, "() => document.querySelector('#btnLogin')")
        if not user or not passt or not submit:
            raise BackendError("login_fields_not_found")
        w.set_value(user, creds["user"])
        w.set_value(passt, creds["pass"])
        try:
            v3 = self._find(w, "() => document.querySelector('#btnSlider1')")
            if v3 and self._safe_eval(w, "() => !!document.querySelector('#btnSlider1') && document.querySelector('#btnSlider1').offsetParent !== null"):
                w.click(v3)
                time.sleep(0.5)
        except Exception:
            pass
        try:
            with w.page.expect_navigation(wait_until="domcontentloaded",
                                          timeout=30000):
                w.click(submit)
        except Exception:
            pass
        door = time.monotonic() + 30
        logged = False
        while time.monotonic() < door:
            err = self._safe_eval(w, JS_LOGIN_ERR)
            if err:
                w.progress("login", "error", str(err)[:120])
                raise BackendError(f"login_failed: {err}")
            url = (w.page.url or "").lower()
            if "/login2.aspx" not in url:
                logged = True
                break
            time.sleep(0.5)
        if not logged:
            w.progress("login", "error", "login_timeout")
            raise BackendError("login_timeout")
        w.progress("login", "ok", "signed in")
        return

    # ---------- الجلسة ----------
    def _open_eligibility(self, w):
        """دائما: اعادة تحميل صفحة الاستعلام كاملة عند طلب جديد — لا تبقَ على
        حالة النتيجة السابقة (خانة العضوية لا تظهر بعد أول نتيجة/طباعة)."""
        w.progress("navigate", "run", "goto EligibilityChecking2")
        w.page.goto(ELIG_URL, wait_until="domcontentloaded", timeout=45000)
        # انتظر ظهور حقل العضوية — الصفحة البيضاء/حالة النتيجة ليست صالحة
        door = time.monotonic() + 25
        while time.monotonic() < door and not self._find(w, JS_MEMBER):
            time.sleep(0.4)
        return

    def _wait_print_btn(self, w, timeout_s):
        door = time.monotonic() + timeout_s
        while time.monotonic() < door:
            btn = self._find(w, JS_PRINT_BTN)
            if btn:
                return btn
            time.sleep(0.5)
        return None

    # ---------- التدفق ----------
    def run(self, w, kind="doc", member=None, card=None):
        w._started_at = time.monotonic()
        creds = creds_for(self.id)
        member = (member or ((card or {}).get("insurance", {}).get("card_no")
                             or "")).strip()
        service_type = ((card or {}).get("insurance", {}).get("service_type")
                        or (card or {}).get("service_type")
                        or (card or {}).get("plan") or "Out-Patient").strip()
        if not member:
            raise BackendError("member_empty")

        if "pulse-uae.nextcarehealth.com" not in (w.page.url or ""):
            w.progress("navigate", "run", f"goto {creds['url'][:60]}")
            w.page.goto(creds["url"], wait_until="domcontentloaded",
                        timeout=45000)

        if self._safe_eval(w, JS_IS_LOGIN):
            self.do_login(w, creds)
        w.progress("navigate", "run", f"pre_open url={(w.page.url or '')[-80:]}")

        self._open_eligibility(w)

        # رقم العضوية
        inp = self._find(w, JS_MEMBER)
        if not inp:
            door = time.monotonic() + min(20, max(3, w.remaining(kind)))
            while time.monotonic() < door and not inp:
                inp = self._find(w, JS_MEMBER)
                time.sleep(0.5)
        if not inp:
            raise BackendError("member_input_not_found")

        try:
            current = str(inp.evaluate("el => el.value") or "").strip()
        except Exception:
            current = ""
        if current != member:
            w.progress("eligibility", "run", f"enter member={member}")
            inp.evaluate("el => el.focus()")
            w.set_value(inp, member)

        # النوع (chosen.js — نحدّث القيمة الأصلية؛ chosen تلتقط التغيير من DOM)
        if service_type:
            for _ in range(10):
                set_ok = w.page.evaluate(
                    """(want) => {
                        const sel = document.querySelector('select[id$="_cmbType"]') ||
                                    document.querySelector('select[id*="cmbType"]');
                        if (!sel) return null;
                        const w = String(want).trim().toLowerCase();
                        const opt = [...sel.options].find(o =>
                            o.text.trim().toLowerCase() === w ||
                            o.value.trim().toLowerCase() === w);
                        if (!opt) return false;
                        const d = Object.getOwnPropertyDescriptor(
                            window.HTMLSelectElement.prototype, 'value');
                        if (d && d.set) d.set.call(sel, opt.value);
                        else sel.value = opt.value;
                        sel.dispatchEvent(new Event('input', {bubbles: true}));
                        sel.dispatchEvent(new Event('change', {bubbles: true}));
                        return sel.value === opt.value;
                    }""",
                    service_type)
                if set_ok is not None:
                    break
                time.sleep(0.5)
            else:
                set_ok = None
            w.progress("eligibility", "run",
                       f"typeSet={set_ok} type={service_type}")
            if set_ok is False:
                raise BackendError(f"service_type_not_in_list: {service_type}")

        # المتابعة
        nxt = self._find(w, JS_NEXT_BTN)
        if not nxt:
            raise BackendError("next_button_not_found")
        w.progress("submitted", "ok")
        w.click(nxt)

        # النتيجة: ظهور زر الطباعة
        print_btn = self._wait_print_btn(
            w, min(60, max(5, w.remaining(kind))))
        if not print_btn:
            raise BackendError("print_button_not_found")

        save_last_good_member(self.id, member)
        w.progress("eligibility", "ok")
        if kind == "keepalive":
            return {"member": member, "result": "ok"}

        # الطباعة — التنزيل يُلتقط عبر download في العامل
        w.progress("print", "run")
        r = w.capture_report(lambda: self._find(w, JS_PRINT_BTN),
                             timeout_s=min(40, max(5, w.remaining(kind))),
                             discard=False)
        if not r.get("ok"):
            raise BackendError(f"print_failed | {r.get('error')}")
        w.progress("print", "ok", f"url={str(r.get('url', ''))[:110]}")
        return {"member": member, "pdf_bytes": r.get("bytes"),
                "pdf_url": r.get("url", "")}
