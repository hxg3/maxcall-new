# MedNet — Oracle ADF (WebLogic). نقل حرفي موجّه من
# Extension/src/content/insurers/mednet.js (المحددات وقواعد الانتظار نفسها).
#
# التدفق: دخول → MainOnline → شاشة Member Eligibility → رقم العضوية →
# Eligibility Check → Print → التقاط استجابة PDF → إعادتها للمهمة.
#
# مبادئ (الأصل):
# • لا أعلام "تم النقر" — الحالة تُقرأ من الـDOM عند كل خطوة (idempotent).
# • لا انتظار ثابت — كل خطوة تنتظر إشارة واقعها (عنصر/درع ADF/رسالة).
# • "Member is Invalid." رغم صحة العضوية → نقر Print (مستند فارغ يُهمَل)
#   ثم إعادة التحقق حتى 8 محاولات — المسار اليدوي المُثبت.
# • النجاح القاطع: حقل "Eligibility Number" (it3) بقيمة قبل النقر —
#   النقر المبكر يعطي مستنداً فارغاً.

import time

from .base import Backend, BackendError, save_last_good_member, creds_for


def _REPORT_RE():
    import re
    return re.compile(r"pdfdisplayservlet|ReportsViewer|\.pdf(\?|$)", re.I)


# ---------------- منقول JS: controlText وأدوات الأهلية ----------------
_MJS = """
const okEl = (el) => !!el && el.getClientRects().length > 0 &&
    !el.disabled && el.readOnly !== true;
const controlText = (el) => {
  if (!el) return "";
  const img = el.querySelector && el.querySelector("img[alt]");
  return [
    el.value, el.textContent,
    el.getAttribute && el.getAttribute("aria-label"),
    el.getAttribute && el.getAttribute("title"),
    el.getAttribute && el.getAttribute("alt"),
    img ? img.getAttribute("alt") : "",
  ].filter(Boolean).join(" ").replace(/\\s+/g, " ").trim();
};
const labelText = (el) => {
  const holder = el.closest("td, tr, div, span") || el.parentElement;
  return (holder ? holder.textContent : "").replace(/\\s+/g, " ").slice(0, 80);
};
"""

JS_MEMBER_INPUT = f"""() => {{ {_MJS}
  const direct = document.querySelector(
    'input[id$="it5::content"], input[id$=":it5"]');
  if (direct && okEl(direct)) return direct;
  const inputs = [...document.querySelectorAll(
    'input[type="text"], input:not([type])')].filter(okEl);
  return inputs.find((el) =>
    /member|card|certif|policy|insur/i.test(
      `${{el.id}} ${{el.name || ""}} ${{el.placeholder || ""}} ${{labelText(el)}}`
    )
  ) || null;
}}"""

JS_NAV_LINK = f"""() => {{ {_MJS}
  const links = [...document.querySelectorAll("a")].filter(okEl);
  const byText = links.find((a) =>
    /member\\s*eligib|check\\s*eligib|eligibility\\s*check/i.test(controlText(a)));
  if (byText) return byText;
  const byId = document.querySelector('a[id$="cil1"]');
  if (byId && okEl(byId) &&
      /main|online|eligib|member|home|next|start/i.test(controlText(byId))) return byId;
  return null;
}}"""

JS_CHECK_BTN = f"""() => {{ {_MJS}
  const el = document.querySelector(
    'button[id$="cb1"], input[id$="cb1"], a[id$="cb1"]');
  if (el && okEl(el)) return el;
  return [...document.querySelectorAll(
    'button, input[type="submit"], input[type="button"]')]
    .filter(okEl)
    .find((el) => /check|eligib|search|submit/i.test(controlText(el))) || null;
}}"""

JS_PRINT_CTL = f"""() => {{ {_MJS}
  const el = document.querySelector(
    'button[id$="cb2"], input[id$="cb2"], a[id$="cb2"]');
  if (el && okEl(el)) return el;
  return [...document.querySelectorAll(
    'button, input[type="submit"], input[type="button"]')]
    .filter(okEl)
    .find((el) => /^\\s*print\\b/i.test(controlText(el))) || null;
}}"""

JS_LOGIN_USER = f"""() => {{ {_MJS}
  return [...document.querySelectorAll(
    'input[id$="it1::content"], input[id$=":it1"]')].find(okEl) || null;
}}"""

JS_LOGIN_PASS = f"""() => {{ {_MJS}
  return [...document.querySelectorAll(
    'input[id$="it2::content"], input[id$=":it2"]')].find(okEl) || null;
}}"""

JS_LOGIN_SUBMIT = f"""() => {{ {_MJS}
  const el = document.querySelector(
    'button[id$="cb1"], input[id$="cb1"], a[id$="cb1"]');
  if (el && okEl(el)) return el;
  return [...document.querySelectorAll(
    'button, input[type="submit"], input[type="button"]')]
    .filter(okEl)
    .find((el) => /sign\\s*in|log\\s*in|login|submit/i.test(controlText(el))) || null;
}}"""

JS_ELIG_NUMBER = """() => {
  const el = document.querySelector(
    'input[id$="it3::content"], input[id$=":it3"]');
  if (!el) return null;
  const v = String(el.value || "").trim();
  return (v && v !== "0") ? v : null;
}"""

JS_INVALID_NOTICE = """() => {
  const nodes = document.querySelectorAll(
    '[id*=":ot"], [id*=":msg"], [class*="AFMessage"]');
  for (const el of nodes) {
    if (!el.getClientRects().length) continue;
    const t = String(el.textContent || "").replace(/\\s+/g, " ").trim();
    if (/^member\\s+is\\s+invalid\\.?$/i.test(t)) return el.id || "__msg";
  }
  return null;
}"""

JS_IS_LOGIN = """() => !!document.querySelector('input[type="password"]') &&
  document.querySelector('input[type="password"]').getClientRects().length > 0"""

JS_MASKING = """() => !!document.querySelector(".AFMaskingPane")"""

JS_STATE = f"""() => {{ {_MJS}
  const member = document.querySelector(
    'input[id$="it5::content"], input[id$=":it5"]');
  return [
    "url=" + (location.pathname + location.search).slice(0, 80),
    "member=" + (member ? JSON.stringify(String(member.value || "").trim()) : "-"),
    "elig=" + (() => {{
      const e = document.querySelector(
        'input[id$="it3::content"], input[id$=":it3"]');
      return e ? JSON.stringify(String(e.value || "").trim()) : "0";
    }})(),
    "invalid=" + (document.querySelector(
      '[id*=":ot"], [class*="AFMessage"]') ? 1 : 0),
    "print=" + (document.querySelector(
      'button[id$="cb2"], input[id$="cb2"]') ? 1 : 0),
  ].join(" ");
}}"""


class MedNet(Backend):
    id = "mednet"

    def report_matches(self, url):
        return bool(_REPORT_RE().search(url or ""))

    # ---------- أدوات ----------
    def _find(self, w, js):
        try:
            h = w.page.evaluate_handle(js)
            return h.as_element()
        except Exception:
            return None

    def _adf_idle(self, w, timeout_s=15):
        """نفس adfIdle: انزوال درع ADF (نهاية جولة الطلب) ثم 300ms نجاعة."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if not w.page.evaluate(JS_MASKING):
                time.sleep(0.3)
                return True
            time.sleep(0.2)
        return not w.page.evaluate(JS_MASKING)

    def _member_value(self, w):
        inp = self._find(w, JS_MEMBER_INPUT)
        if not inp:
            return None, None
        try:
            return inp, str(inp.evaluate("el => el.value") or "").strip()
        except Exception:
            return inp, ""

    def do_login(self, w, creds):
        w.progress("login", "run", "doLogin")
        self._adf_idle(w, 15)
        user = self._find(w, JS_LOGIN_USER)
        passt = self._find(w, JS_LOGIN_PASS)
        if not user or not passt:
            w.progress("login", "error", "login_fields_not_found")
            raise BackendError("login_fields_not_found")
        w.set_value(user, creds["user"])
        w.set_value(passt, creds["pass"])
        submit = self._find(w, JS_LOGIN_SUBMIT)
        if not submit:
            w.progress("login", "error", "submit_not_found")
            raise BackendError("submit_not_found")
        w.click(submit)
        self._adf_idle(w, 25)
        if w.page.evaluate(JS_IS_LOGIN):
            w.progress("login", "error", "login_rejected")
            raise BackendError("login_failed")
        w.progress("login", "ok")

    def await_verdict(self, w, timeout_s=45, prev_notice=None):
        """نقل حرفي من awaitVerdict: نجاح (it3) أو فشل (رسالة جديدة —
        بالمقارنة عبر معرف العنصر) أو timeout. ظهور الدرع ثم اختفاؤه
        يعني انتهاء جولة كاملة."""
        door = time.monotonic() + timeout_s
        round_seen = False
        while time.monotonic() < door:
            if w.page.evaluate(JS_ELIG_NUMBER):
                return "ok"
            notice = w.page.evaluate(JS_INVALID_NOTICE)
            if notice and notice != prev_notice:
                return "invalid"
            if w.page.evaluate(JS_MASKING):
                round_seen = True
            elif round_seen:
                time.sleep(0.3)
                return "ok" if w.page.evaluate(JS_ELIG_NUMBER) else "invalid"
            time.sleep(0.25)
        return "ok" if w.page.evaluate(JS_ELIG_NUMBER) else "timeout"

    def state(self, w):
        try:
            return w.page.evaluate(JS_STATE)
        except Exception:
            return "state=unavailable"

    def _wait_find(self, w, js, timeout_s):
        door = time.monotonic() + max(0.5, timeout_s)
        while time.monotonic() < door:
            el = self._find(w, js)
            if el:
                return el
            time.sleep(0.3)
        return None

    # ---------- التدفق ----------
    def run(self, w, kind="doc", member=None, card=None):
        w._started_at = time.monotonic()
        creds = creds_for(self.id)
        if not creds.get("url"):
            raise BackendError("no_site_url")

        member = (member or ((card or {}).get("insurance", {}).get("card_no")
                             or "")).strip()
        if not member:
            raise BackendError("member_empty")

        # الوصول: الجلسة تبدأ صفحة الدخول إن كنا الإخلاص أول مرة
        if "mednetapprovals.com" not in (w.page.url or ""):
            w.progress("navigate", "run", f"goto {creds['url'][:60]}")
            w.page.goto(creds["url"], wait_until="domcontentloaded", timeout=45)

        # 1) تسجيل الدخول
        if w.page.evaluate(JS_IS_LOGIN):
            self.do_login(w, creds)

        # 2) شاشة البحث: موجودة فعلاً، أو انتقل إليها (نفس ترتيب الأصل)
        inp, val = self._member_value(w)
        if not inp:
            nav = self._find(w, JS_NAV_LINK)
            if nav:
                w.progress("navigate", "run", "click Member Eligibility")
                w.click(nav)
            inp = self._wait_find(
                w, JS_MEMBER_INPUT, min(25, max(3, w.remaining(kind))))
        if not inp:
            raise BackendError(f"member_screen_not_found | {self.state(w)}")

        # 3) رقم العضوية — لا نعيد الكتابة إن كانت قيمتها هي
        if val != member:
            w.progress("eligibility", "run", f"enter member={member}")
            inp.evaluate("el => el.focus()")
            w.set_value(inp, member)
            self._adf_idle(w, 15)

        # 4) زر الفحص
        btn = self._find(w, JS_CHECK_BTN)
        if not btn:
            raise BackendError(f"check_button_not_found | {self.state(w)}")

        # 5) دورة التحقق + إعادة المزامنة (طباعة مهملة) — MAX_ATTEMPTS=8
        MAX_ATTEMPTS = 8
        notice = w.page.evaluate(JS_INVALID_NOTICE)
        elig_no = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            field, fval = self._member_value(w)
            if field and fval != member:
                field.evaluate("el => el.focus()")
                w.set_value(field, member)
                self._adf_idle(w, 15)
            cur = self._find(w, JS_CHECK_BTN)
            if not cur:
                break
            w.progress("eligibility", "ok" if attempt == 1 else "run",
                       f"check attempt={attempt}/{MAX_ATTEMPTS}"
                       + ("" if attempt == 1 else f" | {self.state(w)}"))
            w.click(cur)
            verdict = self.await_verdict(
                w,
                timeout_s=min(45, max(5, w.remaining(kind))),
                prev_notice=notice,
            )
            notice = w.page.evaluate(JS_INVALID_NOTICE)
            w.progress("eligibility",
                       "ok" if verdict == "ok" else "run",
                       f"verdict={verdict} attempt={attempt}")
            if verdict == "ok":
                elig_no = w.page.evaluate(JS_ELIG_NUMBER)
                break
            if verdict == "timeout":
                break
            # رفض → مزامنة الجلسة بطباعة مهملة ثم إعادة التحقق
            w.progress("eligibility", "run", "reset=print_discard")
            w.capture_report(lambda: self._find(w, JS_PRINT_CTL),
                             timeout_s=12, discard=True)
            notice = w.page.evaluate(JS_INVALID_NOTICE)

        if not elig_no:
            raise BackendError(f"eligibility_result_missing | {self.state(w)}")

        save_last_good_member(self.id, member)
        w.progress("eligibility", "ok", f"eligibility_no={elig_no}")

        if kind == "keepalive":
            return {"member": member, "eligibility_no": elig_no}

        # 6) زر الطباعة (ظاهر الآن — النتيجة جاهزة)
        print_ctl = self._wait_find(
            w, JS_PRINT_CTL, min(15, max(3, w.remaining(kind))))
        if not print_ctl:
            raise BackendError(f"result_not_ready | {self.state(w)}")

        r = w.capture_report(lambda: self._find(w, JS_PRINT_CTL),
                             timeout_s=min(25, max(3, w.remaining(kind))),
                             discard=False)
        if not r.get("ok"):
            raise BackendError(f"print_failed | {r.get('error')}")
        if r.get("bytes") and len(r["bytes"]) < 512:
            w.progress("print", "run", f"suspicious_small_pdf={len(r['bytes'])}")
        w.progress("print", "ok", f"url={str(r.get('url', ''))[:110]}")
        return {"member": member, "eligibility_no": elig_no,
                "pdf_bytes": r.get("bytes"), "pdf_url": r.get("url", "")}
