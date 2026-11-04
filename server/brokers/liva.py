# Liva / NLI Gulf — بوابة FortiGate SSL-VPN ثم بوابة مزوّد Angular (proxy نفس الأصل).
#
# التدفق (كله داخل سياق Playwright دائم — لا نافذة ولا تبويب على جهاز الموظف):
#   1) https://online.nlicgulf.com:12443/remote/login?lang=en  (VPN: user/pass — بلا كابتشا)
#   2) لوحة البوابة: بطاقة "Provider" → تفتح بوابة المزوّد (نتبعها بنفس التبويب)
#   3) بوابة المزوّد: نفس البيانات + كابتشا صورة (#CaptchaInputText)
#      → الصورة تُعرض على صفحة المشفى (his-assist) — الموظف يكتب الرمز خلال 90 ث
#   4) "Member Eligiblity & Online Claim Form" → MemberDetails
#   5) #txtMemberId + #btnSearch → الطبيب (jQuery UI — تطابق مع HIS،
#      وإن لم يوجد تطابق تُرفع قائمة اختيار للموظف عبر نفس قناة الكابتشا)
#   → #txtGsmNo (قناع 968-) → انتظار امتلاء #txtSpeciality → Printmember → PDF.
#
# ملاحظات تصميمية:
# • النبضة (keepalive): تحقق eligibility برقم التأمين داخل MemberDetails نفسها
#   (نفس فكرة MedNet — لا طباعة/إيصال/طلب، الصفحة لا تُغادر ولا تُعاد تحميلها)؛
#   تجديد VPN بحد أقصى مرة/ساعة عند غياب الجلسة؛ كابتشا البوابة لا تُطلب أبداً.
# • بعد أي نجاح، الصفحة تبقى كما هي — عند الطلب التالي تُعاد قراءة الحالة
#   من DOM (نفس مبدأ الإضافة: لا أعلام "تم النقر").
# • إلغاء الموظف (إجابة فارغة) يُجهض المهمة برسالة واضحة.

import json
import os
import re
import time
from pathlib import Path

from .base import Backend, BackendError, NeedsCaptcha, NeedsChoice
from .base import creds_for, save_last_good_member

HOST = "https://online.nlicgulf.com:12443"
_REPORT_PAT = re.compile(r"\.pdf|pdfdisplayservlet|ReportsViewer", re.I)

_MJS = """const okEl = (el) => !!el && el.getClientRects().length > 0 &&
    !el.disabled && !el.readOnly;"""

# حالة الصفحة (تُقرأ من DOM في كل خطوة — لا ذاكرة "خطوات")
JS_STATE = f"""() => {{ {_MJS}
  if (document.querySelector('#CaptchaInputText')) return 'provider-login';
  if (document.querySelector('#txtMemberId')) return 'member';
  const links = [...document.querySelectorAll('a')];
  const elig = links.find(a => okEl(a) && (/member\\s*eligib/i.test(a.textContent || '') ||
                                          /MemberDetails/i.test(a.getAttribute('href') || '')));
  if (elig) return 'portal-home';
  // بطاقة المزوّد: FortiGate قد يعرضها inline على /remote/login أو في
  // صفحة بوابة مستقلة — نكشفها قبل فحص المسار
  const prov = [...document.querySelectorAll('.condensed-card, .card-title, .card-header, .card h3, .card h4, a, button, [role="button"]')]
      .find(el => okEl(el) && /^provider$/i.test((el.textContent || '').trim()));
  if (prov) return 'provider-card';
  if (/\\/remote\\/login/i.test(location.pathname + location.search)) return 'vpn-login';
  return 'unknown';
}}"""

JS_MEMBER = f"""() => {{ {_MJS}
  const el = document.querySelector('#txtMemberId');
  return okEl(el) ? el : null;
}}"""

# محددات الكابتشا تُعامل عبر locator مباشرة (JS_STATE يكفي للكشف)

JS_SEARCH_BTN = f"""() => {{ {_MJS}
  return [...document.querySelectorAll('#btnSearch, input[type=button], button')]
      .find(el => okEl(el) && (el.id === 'btnSearch' || /^\\s*search\\s*$/i.test(el.textContent || el.value || ''))) || null;
}}"""

JS_DOCTOR_ITEMS = """() => [...document.querySelectorAll('ul.ui-autocomplete li.ui-menu-item')]
    .map(li => ((li.querySelector('.ui-menu-item-wrapper') || li).textContent || '').trim())
    .filter(Boolean)"""



JS_PRINT_BTN = f"""() => {{ {_MJS}
  return document.querySelector('button.Printmember') ||
         [...document.querySelectorAll('button, a.btn, input[type=submit]')]
             .find(el => okEl(el) && /print\\s+(online\\s+)?claim/i.test((el.textContent || el.value || ''))) || null;
}}"""

JS_ALREADY_PRINTED = """() => {
  const ok = el => el.getClientRects().length > 0;
  return [...document.querySelectorAll('body *')]
      .filter(el => ok(el) && /already printed/i.test(el.textContent || '') && el.children.length < 4)
      .length;
}"""

JS_DISMISS_ALERT = """() => {
  const ok = el => el.getClientRects().length > 0 && !el.disabled;
  const alerts = [...document.querySelectorAll('body *')]
      .filter(el => ok(el) && /already printed/i.test(el.textContent || ''));
  if (!alerts.length) return 'none';
  // ابحث عن زر موافق/إغلاق داخل أي من آباء التنبيه (أحدثها أولاً)
  for (const a of alerts) {
    let node = a;
    for (let depth = 0; node && depth < 6; depth++, node = node.parentElement) {
      const btn = [...node.querySelectorAll('button, a, input[type=button], input[type=submit]')]
          .find(el => ok(el) && /^\\s*(ok|yes|close|dismiss|حسناً|موافق)\\s*$/i.test(el.textContent || el.value || ''));
      if (btn) { btn.click(); return 'clicked'; }
    }
  }
  return 'no-button';
}"""

JS_REPRINT_BTN = """() => {
  const ok = el => el.getClientRects().length > 0 && !el.disabled;
  return [...document.querySelectorAll('button, a, input[type=button], input[type=submit]')]
      .find(el => ok(el) && /reprint\\s+(online\\s+)?claim/i.test((el.textContent || el.value || ''))) || null;
}"""

JS_REPRINT_ROWS = """() => {
  const ok = el => el.getClientRects().length > 0;
  const claimRe = /\\d{4}-\\d{4}-\\d{4,}/;
  const out = [];
  for (const tr of document.querySelectorAll('tr')) {
    if (!ok(tr)) continue;
    const cells = [...tr.cells].map(td => (td.textContent || '').trim().replace(/\\s+/g, ' '));
    const joined = cells.join(' | ');
    const m = joined.match(claimRe);
    if (m) out.push({ claim: m[0], cells });
  }
  return out;
}"""

JS_MEMBER_LINK = """() => {
  const links = [...document.querySelectorAll('a')];
  return links.find(a => /member\\s*eligib/i.test(a.textContent || '') ||
                         /MemberDetails/i.test(a.getAttribute('href') || '')) || null;
}"""

JS_PROVIDER_DEEPEST = """() => {
  const isProv = el => /^provider$/i.test((el.textContent || '').trim());
  const vis = el => el.getClientRects().length > 0;
  const title = [...document.querySelectorAll(
      '.card-title, .card h3, .card h4, [class*="card-title"], a, button, [role="button"]')]
      .find(el => isProv(el) && vis(el));
  if (!title) return null;
  const a = title.tagName === 'A' ? title : title.querySelector('a[href]');
  if (a) return { mode: 'anchor', href: a.href };
  const deep = [...title.querySelectorAll('*')].filter(el => isProv(el) && vis(el));
  return { mode: 'click', text: (deep.length ? deep[deep.length - 1] : title).textContent.trim() };
}"""

JS_SPECIALITY_OK = """() => {
  const el = document.querySelector('#txtSpeciality');
  if (!el) return 'absent';
  return (el.value || '').trim() ? 'ok' : 'pending';
}"""

JS_GSM_VALUE = "() => (document.querySelector('#txtGsmNo') || {}).value || ''"

def _norm_name(s):
    s = re.sub(r"\bDR\b\.?", " ", str(s or "").upper())
    s = re.sub(r"[^A-Z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _clean_doctor(s):
    s = re.sub(r"[.,]", " ", str(s or ""))
    s = re.sub(r"\bdr\b", " ", s, flags=re.I)
    return re.sub(r"\s+", " ", s).strip()


def _fuzzy(a, b):
    """نسبة تشابه difflib (0..1) بين رمزين."""
    import difflib
    return difflib.SequenceMatcher(None, a, b).ratio()


# ---------- ذاكرة مطابقات الأطباء (HIS → NLI) ----------
# تُملأ تلقائياً عند كل اختيار ناجح (تلقائي أو من الموظف)، وتُزرع أولياً
# بالمطابقات الموثقة يدوياً. تُقرأ قبل أي حساب ضبابي — صفر تكلفة، صفر خطأ.
DOCTOR_ALIASES_FILE = "/var/lib/maxcall-broker/doctor_aliases.json"
_DOCTOR_ALIAS_LOCK = None  # ضئيل داخل خيط العامل الواحد؛ لا حاجة لقفل إضافي
_DOCTOR_ALIAS_CACHE: dict[str, str] = {}


def _alias_key(name):
    return _norm_name(name)


def load_doctor_aliases():
    try:
        if _DOCTOR_ALIAS_CACHE:
            return _DOCTOR_ALIAS_CACHE
        raw = Path(DOCTOR_ALIASES_FILE).read_text(encoding="utf-8")
        data = json.loads(raw)
        if isinstance(data, dict):
            _DOCTOR_ALIAS_CACHE.update({_alias_key(k): str(v) for k, v in data.items() if k and v})
    except Exception:
        pass
    return _DOCTOR_ALIAS_CACHE


def save_doctor_alias(his_name, liva_name):
    """زرع/تحديث مطابقة — تُستدعى بعد كل اختيار ناجح."""
    try:
        key = _alias_key(his_name)
        val = str(liva_name or "").strip()
        if not key or not val:
            return
        aliases = load_doctor_aliases()
        if aliases.get(key) == val:
            return
        aliases[key] = val
        p = Path(DOCTOR_ALIASES_FILE)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(aliases, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, p)
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass
    except Exception:
        pass  # الذاكرة تحسين اختياري — لا تكسر التدفق أبداً


def _doctor_alias_lookup(wanted):
    aliases = load_doctor_aliases()
    return aliases.get(_alias_key(wanted)) if aliases else None


def _query_variants(type_text):
    """بدائل الاستعلام لـ jQuery UI: الاسم الكامل ثم رموز مميزة قصيرة.
    يلتقط حالة الاختلاف الإملائي (SELVAGANESAN ↔ SELVAGANESH) لأن البادئة المشتركة تتطابق."""
    out = [type_text]
    toks = [t for t in type_text.split() if len(t) >= 4]
    for t in toks:
        out.append(t[:6])        # SELVAG
        out.append(t[:4])        # SELV
    # الرموز من الأطول للأقصر
    seen, final = set(), []
    for v in out:
        v = v.strip()
        if v and v not in seen:
            seen.add(v)
            final.append(v)
    return final


def _match_doctor(items, wanted):
    w = _norm_name(wanted)
    if not w:
        return None
    # 1) ذاكرة المطابقات المعتمدة
    alias = _doctor_alias_lookup(wanted)
    if alias:
        hit = next((it for it in items if _norm_name(it) == _norm_name(alias)), None)
        if hit:
            return hit
    # 2) تطابق كامل / احتواء
    best, best_score = None, 0.0
    runner_up = 0.0
    for it in items:
        n = _norm_name(it)
        if not n:
            continue
        if n == w:
            score = 100.0
        elif n in w or w in n:
            score = 88.0
        else:
            a, b = w.split(), n.split()
            tok_scores = []
            for x in a:
                tok_scores.append(max(_fuzzy(x, y) for y in b) if b else 0.0)
            # الوزن بطول الرمز — الرمز الطويل يحمل الهوية (SELVAGANESAN ≫ K)
            weights = [len(x) for x in a]
            score = (sum(s * w for s, w in zip(tok_scores, weights))
                     / sum(weights)) * 80.0 if weights else 0.0
            # تطابق تام لرمز واحد على الأقل يرفع الثقة
            if any(s >= 0.99 for s in tok_scores):
                score += 12.0
        if score > best_score:
            runner_up, best, best_score = best_score, it, score
        elif score > runner_up:
            runner_up = score
    if best is None:
        return None
    # قرار: واضح (أو متفوق بفارق مريح) ⇒ تلقائي؛ وإلا مرر للموظف
    if best_score >= 62.0 and (best_score - runner_up) >= 15.0:
        return best
    if best_score >= 80.0:
        return best
    return None


class Liva(Backend):
    id = "liva"
    report_capture = "download"
    REPORT_EXT_RE = r"\.pdf(\?|$)|^="

    def __init__(self):
        self._choice_answer = None
        self._vpn_login_at = 0.0

    # ---------- التقاط ----------
    @staticmethod
    def report_matches(url):
        return bool(_REPORT_PAT.search(url or ""))

    # ---------- أدوات DOM ----------
    def _find(self, w, js):
        try:
            return w.page.evaluate_handle(js).as_element()
        except Exception:
            return None

    def _state(self, w):
        try:
            return w.page.evaluate(JS_STATE) or "unknown"
        except Exception:
            return "unknown"

    def _state_label(self, w):
        try:
            return w.page.evaluate(
                "() => ['url=' + location.pathname, 'title=' + document.title.slice(0, 40)].join(' ')")
        except Exception:
            return "?"

    def _type_native(self, w, sel, value):
        """كتابة بقيمة الموقع نفسها (setNativeValue) إن وُجد الحقل — وإلا False."""
        el = w.q1(sel)
        if not el:
            return False
        w.set_value(el, value)
        return True

    def _username_field(self, w):
        return (w.q1('input[name="username"]') or w.q1('input#username')
                or w.q1('input[name*="username" i]') or w.q1('input[id*="user" i]')
                or self._find(w, """() => {
                    const pass = [...document.querySelectorAll('input[type="password"]')]
                        .find(el => el.getClientRects().length > 0);
                    const form = pass && (pass.form || pass.closest('form'));
                    const scope = form || document;
                    return [...scope.querySelectorAll('input[type="text"], input:not([type])')]
                        .find(el => el.getClientRects().length > 0 &&
                              el.id !== 'CaptchaInputText' && el.id !== 'txtMemberId') || null;
                }"""))

    def _password_field(self, w):
        return w.q1('input[type="password"]')

    def _submit_button(self, w):
        return (w.q1('input[type="submit"]') or w.q1('button[type="submit"]')
                or w.q1('button:not([type])')
                or self._find(w, """() => [...document.querySelectorAll('button, a.btn')]
                        .find(el => el.getClientRects().length > 0 &&
                              /log\\s*in|sign\\s*in|submit|connect|دخول/i.test(el.textContent || el.value || '')) || null"""))

    # ---------- دخول VPN ----------
    def _do_vpn_login(self, w, creds):
        user, pw = creds.get("user"), creds.get("pass")
        if not user or not pw:
            raise BackendError("no_credentials")
        w.progress("login", "run", "vpn login")
        u_el, p_el = self._username_field(w), self._password_field(w)
        if not u_el or not p_el:
            raise BackendError(f"vpn_fields_not_found | {self._state_label(w)}")
        w.set_value(u_el, user)
        w.set_value(p_el, pw)
        btn = self._submit_button(w)
        if not btn:
            raise BackendError(f"vpn_submit_not_found | {self._state_label(w)}")
        w.click(btn)
        # إشارة نجاح: ظهور بطاقة البوابة (FortiGate قد يعرضها inline على
        # نفس الرابط دون تغييره) أو مغادرة مسار الدخول أو ظهور صفحة البوابة
        ok = w.wait_for("""() => !/\\/remote\\/login/i.test(location.pathname) ||
                             !!document.querySelector('.condensed-card, .card-title, #CaptchaInputText, #txtMemberId')""",
                        timeout_s=30, poll_s=0.5)
        if not ok and not w.page.evaluate("() => !!document.querySelector('.condensed-card, .card-title')"):
            raise BackendError(f"vpn_login_timeout | {self._state_label(w)}")
        w.progress("login", "ok", "vpn")

    # ---------- بطاقة Provider → بوابة المزوّد ----------
    def _open_provider(self, w):
        w.progress("navigate", "run", "open Provider portal")
        info = w.page.evaluate(JS_PROVIDER_DEEPEST) or {}
        mode = info.get("mode")
        if mode == "anchor" and info.get("href"):
            w.page.goto(info["href"], wait_until="domcontentloaded", timeout=60000)
            w.progress("navigate", "ok", f"provider anchor {str(info['href'])[:80]}")
            return
        # نقرة حقيقية-ish على أعمق عنصر ⇒ window.open يُلتقط بالمعترِض
        el = self._find(w, """() => {
            const isProv = e => /^provider$/i.test((e.textContent || '').trim());
            const vis = e => e.getClientRects().length > 0;
            const t = [...document.querySelectorAll('.card-title, .card h3, .card h4, [class*="card-title"], a, button, [role="button"], li')]
                .find(e => isProv(e) && vis(e));
            if (!t) return null;
            const deep = [...t.querySelectorAll('*')].filter(isProv);
            return deep.length ? deep[deep.length - 1] : t;
        }""")
        if not el:
            raise BackendError(f"provider_card_not_found | {self._state_label(w)}")
        try:
            w.page.evaluate("() => { window.__MCR_OPEN_URL = ''; window.__MCR_OPEN_AT = 0; }")
        except Exception:
            pass
        if not w.trusted_click(el):
            w.click(el)
        # انتظر الرابط المعترَض ثم انتقل إليه بنفس التبويب
        url = None
        door = time.monotonic() + 6
        while time.monotonic() < door:
            try:
                url = (w.page.evaluate(
                    "() => String(window.__MCR_OPEN_URL || '').trim()") or "").strip()
            except Exception:
                url = ""
            if url:
                break
            time.sleep(0.25)
        if url:
            import urllib.parse
            abs_url = url if url.lower().startswith(("http://", "https://")) \
                else urllib.parse.urljoin(w.page.url or (HOST + "/"), url)
            w.page.goto(abs_url, wait_until="domcontentloaded", timeout=60000)
            w.progress("navigate", "ok", f"provider via window.open {abs_url[:80]}")
            return
        # بلا نافذة: ربما تنقّلت الصفحة نفسها
        if "/remote/login" not in (w.page.url or "") and self._state(w) != "provider-card":
            w.progress("navigate", "ok", f"provider same-tab {(w.page.url or '')[:80]}")
            return
        raise BackendError(f"provider_open_failed | {self._state_label(w)}")

    # ---------- كابتشا بوابة المزوّد ----------
    def _captcha_needs_human(self, w):
        if not w.q1("#CaptchaInputText"):
            return None
        try:
            shot = w.page.locator("#CaptchaImage").screenshot(type="jpeg", timeout=5000)
            import base64
            if shot:
                w.progress("captcha", "run", "captcha shown to operator")
                return NeedsCaptcha(base64.b64encode(shot).decode("ascii"))
        except Exception:
            pass
        # احتياط: التقاط أي صورة ظاهرة داخل النموذج
        try:
            shot = w.page.screenshot(type="jpeg", quality=70)
            import base64
            return NeedsCaptcha(base64.b64encode(shot).decode("ascii"))
        except Exception:
            raise BackendError("captcha_image_failed")

    def _submit_provider_login(self, w, answer):
        ok = self._type_native(w, "#CaptchaInputText", answer)
        if not ok:
            raise BackendError("captcha_field_not_found")
        # إعادة كتابة المستخدم/العلاقة إن كانت الحقول فارغة (Angular قد صفّرها)
        u_el, p_el = self._username_field(w), self._password_field(w)
        creds = creds_for(self.id)
        try:
            if u_el and not (u_el.evaluate("el => el.value") or "").strip():
                w.set_value(u_el, creds.get("user", ""))
            if p_el and not (p_el.evaluate("el => el.value") or ""):
                w.set_value(p_el, creds.get("pass", ""))
        except Exception:
            pass
        btn = self._submit_button(w)
        if not btn:
            raise BackendError("provider_submit_not_found")
        w._wrong_captcha = False           # ألغِ راية قديمة — يبدأ رصد الاستجابة من الآن
        w._provider_submit_at = time.time()
        w.click(btn)

    # ---------- رابط "Member Eligiblity" ----------
    def _open_member_details(self, w):
        w.progress("navigate", "run", "open Member Eligibility")
        link = self._find(w, JS_MEMBER_LINK)
        if not link:
            raise BackendError(f"member_eligibility_link_not_found | {self._state_label(w)}")
        w.click(link)
        ok = w.wait_for("() => !!document.querySelector('#txtMemberId')",
                        timeout_s=30, poll_s=0.5)
        if not ok:
            raise BackendError(f"member_details_timeout | {self._state_label(w)}")
        w.progress("navigate", "ok", "member details")

    # ---------- صفحة العضوية ----------
    def _doctor_items(self, w):
        try:
            return w.page.evaluate(JS_DOCTOR_ITEMS) or []
        except Exception:
            return []

    def _type_into_doctor(self, w, text):
        """اكتب في كل حقل doctor candidate حتى تظهر قائمة jQuery UI —
        يرجع عنصر الحقل المستخدم أو None."""
        loc = w.page.locator(
            'input.ui-autocomplete-input, input[role="combobox"],'
            ' input[id*="doctor" i], input[name*="doctor" i]')
        for i in range(min(loc.count(), 8)):
            el = loc.nth(i)
            try:
                if not el.is_visible():
                    continue
                el.fill("")
                el.press_sequentially(text, delay=40)
            except Exception:
                continue
            if w.wait_for("""() => document.querySelectorAll('ul.ui-autocomplete li.ui-menu-item').length > 0""",
                          timeout_s=2.5, poll_s=0.2):
                return el
        return None

    def _select_doctor(self, w, card):
        wanted = str(((card or {}).get("visit") or {}).get("consultant") or "").strip()
        choice = self._choice_answer
        self._choice_answer = None
        type_text = _clean_doctor(wanted)
        if not type_text and not choice:
            # لا شيء نكتبه يفتح القائمة — نفشل برسالة واضحة بدل التخمين
            raise BackendError("doctor_missing_in_his")

        inp = None
        items: list[str] = []
        variants = _query_variants(type_text) if type_text else []
        if choice and choice not in variants:
            variants.insert(0, choice)
        for variant in variants:
            inp = self._type_into_doctor(w, variant)
            if inp is not None:
                items = self._doctor_items(w)
                break
        if inp is None or not items:
            state = self._state_label(w)
            raise BackendError(f"doctor_list_empty typed='{type_text}' | {state}")

        target = choice if choice else (_match_doctor(items, wanted) if wanted else None)
        if not target:
            # لا تطابق ولم يُطلب اختيار بعد → ارفع قائمة الموظف عبر نفس القناة
            raise NeedsChoice(
                items,
                title="Liva — اختر الطبيب",
                hint=(f"اسم الطبيب في HIS: {wanted}" if wanted else
                      "لا يوجد اسم طبيب في سجل HIS"),
            )
        if wanted:
            save_doctor_alias(wanted, target)   # ذاكرة المطابقات — لا نطلبها ثانية
        # انقر العنصر المطابق في القائمة
        clicked = w.page.evaluate(
            """(want) => {
                const norm = s => String(s || '').toUpperCase().replace(/[^A-Z0-9 ]+/g, ' ').replace(/\\s+/g, ' ').trim();
                const t = norm(want);
                const li = [...document.querySelectorAll('ul.ui-autocomplete li.ui-menu-item')]
                    .find(el => norm((el.querySelector('.ui-menu-item-wrapper') || el).textContent) === t);
                if (!li) return false;
                li.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
                li.click();
                return true;
            }""", target)
        if not clicked:
            # fallback: ثبت النص يدوياً في الحقل
            try:
                inp.fill(target)
            except Exception:
                pass
        w.progress("eligibility", "run", f"doctor={target}")
        return target

    def _fill_gsm(self, w, card):
        raw = str(((card or {}).get("patient") or {}).get("phone") or "")
        digits = re.sub(r"\D", "", raw)
        if digits.startswith("968") and len(digits) > 8:
            digits = digits[3:]
        if len(digits) > 8:
            digits = digits[-8:]
        if not digits:
            w.progress("eligibility", "run", "gsm absent in card — skipped")
            return
        el = w.q1("#txtGsmNo")
        if not el:
            w.progress("eligibility", "run", "gsm field absent — skipped")
            return
        full = f"968-{digits}"
        # شخّص الحقل أولاً: ما هو maxlength؟ هل الأرقام فقط مسموحة؟
        info = w.page.evaluate("""() => {
          const el = document.querySelector('#txtGsmNo');
          if (!el) return null;
          const cs = getComputedStyle(el);
          return {
            maxlength: el.maxLength, pattern: el.getAttribute('pattern') || '',
            placeholder: el.getAttribute('placeholder') || '',
            type: el.getAttribute('type') || '',
            inputmode: el.getAttribute('inputmode') || '',
            kenumeric: el.getAttribute('data-val-regex') || el.getAttribute('data-val-number') || '',
            label: (el.closest('div,td,th,p')||{}).textContent?.trim().slice(0,80) || '',
            staticPrefix: (el.previousElementSibling && el.previousElementSibling.textContent || '').trim().slice(0,20),
          };
        }""") or {}
        w.progress("eligibility", "run", f"gsm field info {json.dumps(info, ensure_ascii=False)}")
        # تجريد الشكل الحاسم: يجرّب 8 أرقام ثم الصيغة الكاملة — القرار من الحقل نفسه
        def try_set(val):
            w.page.evaluate("""(val) => {
              const el = document.querySelector('#txtGsmNo');
              if (!el) return;
              const d = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value');
              if (d && d.set) d.set.call(el, String(val)); else el.value = String(val);
              for (const t of ['keydown', 'keypress', 'keyup', 'input', 'change', 'blur']) {
                el.dispatchEvent(new Event(t, { bubbles: true, cancelable: true }));
              }
            }""", val)
            return w.page.evaluate("""() => {
              const el = document.querySelector('#txtGsmNo');
              if (!el) return { ok: false };
              const v = String(el.value || '');
              let valid = null;
              try { valid = el.checkValidity ? el.checkValidity() : null; } catch (e) { valid = null; }
              return { ok: v === val, val: v, html5Valid: valid };
            }""")

        r = try_set(digits)          # 8 أرقام — الشكل الأكثر احتمالاً
        w.progress("eligibility", "run",
                   f"gsm try=8digits field={json.dumps(r, ensure_ascii=False)}")
        if r and r.get("ok") and r.get("html5Valid") is not False:
            return
        r = try_set(full)            # الصيغة الكاملة 968-xxxxxxxx
        w.progress("eligibility", "run",
                   f"gsm try=prefix field={json.dumps(r, ensure_ascii=False)}")
        if r and r.get("ok") and r.get("html5Valid") is not False:
            return
        # احتياط أخير خام: الصيغة الكاملة (وفي الحقل ذو 8 أرقام فقط سيُقبل أي صيغة لاحقاً)
        w.set_value(el, full)
        val = w.page.evaluate(JS_GSM_VALUE) or ""
        w.progress("eligibility", "run",
                   f"gsm={digits} final-raw field={val}")

    def _wait_speciality(self, w):
        state = w.page.evaluate(JS_SPECIALITY_OK)
        if state == "absent":
            w.progress("eligibility", "run", "speciality field absent — skipped")
            return
        ok = w.wait_for(
            "() => { const el = document.querySelector('#txtSpeciality');"
            " return el && (el.value || '').trim().length > 0; }",
            timeout_s=14, poll_s=0.4)
        if not ok:
            raise BackendError("speciality_autofill_missing")
        w.progress("eligibility", "run", "speciality filled")

    # ---------- Fallback: مطبوع مسبقاً → Reprint ----------
    def _already_printed_visible(self, w):
        try:
            return int(w.page.evaluate(JS_ALREADY_PRINTED) or 0) > 0
        except Exception:
            return False

    def _dismiss_already_printed(self, w):
        try:
            outcome = w.page.evaluate(JS_DISMISS_ALERT)
        except Exception:
            outcome = "error"
        if outcome in ("clicked", "none"):
            return outcome == "clicked"
        # احتياط: Escape
        try:
            w.page.keyboard.press("Escape")
            time.sleep(0.5)
            return not self._already_printed_visible(w)
        except Exception:
            return False

    def _find_reprint_button(self, w):
        try:
            handle = w.page.evaluate_handle(JS_REPRINT_BTN).as_element()
            return handle or w.q1("button.RePrintmember, .RePrintmember")
        except Exception:
            return w.q1("button.RePrintmember, .RePrintmember")

    def _wait_reprint_rows(self, w, timeout_s=15):
        door = time.monotonic() + timeout_s
        while time.monotonic() < door:
            try:
                rows = w.page.evaluate(JS_REPRINT_ROWS) or []
            except Exception:
                rows = []
            if rows:
                return rows
            time.sleep(0.4)
        return []

    @staticmethod
    def _parse_row_date(cells):
        """خلية التاريخ مثل 04-Nov-2026 → date؛ وإلا None."""
        import datetime as _dt
        for c in cells:
            for fmt in ("%d-%b-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
                try:
                    return _dt.datetime.strptime(c.strip(), fmt).date()
                except ValueError:
                    continue
        return None

    def _pick_reprint_row(self, rows, doctor):
        """فضّل الصف الذي يطابق الطبيب (fuzzy) ثم الأحدث تاريخاً."""
        if not rows:
            return None
        want = _norm_name(doctor) if doctor else None

        def doc_score(cells):
            if not want:
                return 1.0
            joined = " ".join(cells)
            scores = [_fuzzy(want, _norm_name(c)) for c in cells if c.strip()] or [0.0]
            return max(scores)

        scored = []
        for r in rows:
            ds = doc_score(r.get("cells") or [])
            scored.append((r, ds, self._parse_row_date(r.get("cells") or [])))
        with_doc = [s for s in scored if s[1] >= 0.55]
        pool = with_doc or scored
        dated = [s for s in pool if s[2] is not None]
        if dated:
            return max(dated, key=lambda s: s[2])[0]
        return pool[0][0]

    def _click_reprint_row(self, w, claim_no):
        """انقر رقم الـclaim داخل الصف بنقرة CDP موثوقة (قد يفتح popup)."""
        try:
            row = w.page.locator("tr", has_text=claim_no).first
            if row.count() == 0:
                row = w.page.locator(f"text={claim_no}").first
            if row.count() == 0:
                return False
            link = row.locator("a, button, input[type=button]").first
            handle = (link if link.count() > 0 else row).element_handle()
            return bool(handle and w.trusted_click(handle))
        except Exception:
            return False

    def _report_hit_poll(self, w, timeout_s, stop_pred=None):
        """انتظر ظهور نتيجة الطباعة (download / response / window.open).
        stop_pred: دالة تُرجى إيقاف البحث مبكراً (مثلاً عند رسالة الخطأ)."""
        w._report_hit = None
        w.waiting_report = True
        for frame in [w.page, *w.page.frames]:
            try:
                frame.evaluate("() => { window.__MCR_OPEN_URL = ''; window.__MCR_OPEN_AT = 0; }")
            except Exception:
                pass
        door = time.monotonic() + timeout_s
        try:
            while time.monotonic() < door:
                hit = w._report_hit
                if hit is not None:
                    return w._finish_hit(hit, False)
                url = w._open_url()
                if url:
                    return w._finish_hit({"kind": "url", "url": url}, False)
                if stop_pred and stop_pred(w):
                    return {"ok": False, "error": "aborted_early"}
                time.sleep(0.25)
            return {"ok": False, "error": "report_not_captured"}
        finally:
            w.waiting_report = False

    # ---------- أدوات تشخيص تفصيلية للطباعة (تبقى في السيرفر) ----------
    def _dump_print_candidates(self, w):
        try:
            return w.page.evaluate("""() => {
                const vis = el => el.getClientRects().length > 0;
                const pick = el => ({tag: el.tagName, id: el.id || '',
                    cls: String(el.className || '').slice(0, 60),
                    disabled: !!el.disabled, visible: vis(el),
                    text: (el.textContent || el.value || '').trim().slice(0, 60)});
                return [...document.querySelectorAll('button, input[type=button], input[type=submit], a.btn, a')]
                    .filter(el => /print/i.test((el.textContent || el.value || el.getAttribute('value') || '')))
                    .map(pick);
            }""") or []
        except Exception:
            return [{"err": "dump_failed"}]

    def _print_or_reprint(self, w, member, card):
        """المسار الطبيعي (Print Online Claim Form) وإلا مسار Reprint تلقائياً
        عند رسالة "Claim Form already printed"."""
        # لا ننتظر زمناً اعتباطياً ثم نفشل — نسجل بالضبط ما هو موجود
        cands = self._dump_print_candidates(w)
        w.progress("print", "run", f"candidates={json.dumps(cands, ensure_ascii=False)[:400]}")
        btn = None
        door = time.monotonic() + 30
        while time.monotonic() < door:
            el = self._find(w, JS_PRINT_BTN)
            if el:
                try:
                    if el.evaluate("b => !!b && b.getClientRects().length > 0 && !b.disabled"):
                        btn = el
                        break
                except Exception:
                    pass
            time.sleep(0.4)
        if not btn:
            btn2 = self._find(w, JS_PRINT_BTN)
            state = self._state_label(w)
            info = ("found" if btn2 else "absent") + f" | {self._dump_print_candidates(w)}"
            raise BackendError(f"print_button_not_ready | {info[:300]} | {state}")
        w._already_printed = False
        w.progress("print", "run", "clicking Print (trusted mouse)…")
        if not w.trusted_click(btn):
            w.click(btn)          # احتياط بالنقرة الاصطناعية
        r = self._report_hit_poll(w, 22, stop_pred=lambda _w: _w._already_printed
                                                     or self._already_printed_visible(_w))
        if r.get("ok"):
            w.progress("print", "ok", f"url={str(r.get('url', ''))[:110]}")
            return {"member": member, "pdf_bytes": r.get("bytes"),
                    "pdf_url": r.get("url", "")}

        if not (w._already_printed or self._already_printed_visible(w)):
            # سجل كامل للحالة عند الفشل — لن نخمن
            try:
                open_u = w.page.evaluate(
                    "() => String(window.__MCR_OPEN_URL||'')")[:60]
            except Exception:
                open_u = "?"
            w.progress("print", "error",
                       f"not captured. url={(w.page.url or '')[-80:]} "
                       f"open_url={open_u} diag={self._dump_print_candidates(w)}")
            raise BackendError(f"print_failed | {r.get('error')}")
        w.progress("print", "run", "already printed — auto fallback to Reprint")
        if not self._dismiss_already_printed(w):
            w.progress("print", "run", "could not dismiss already-printed dialog")

        btn = self._find_reprint_button(w)
        if not btn:
            raise BackendError("reprint_button_not_found")
        if not w.trusted_click(btn):
            w.click(btn)
        w.progress("print", "run", "reprint table opened")
        rows = self._wait_reprint_rows(w, timeout_s=15)
        if not rows:
            raise BackendError("reprint_table_not_found")
        chosen_doctor = str(((card or {}).get("visit") or {}).get("consultant") or "").strip()
        row = self._pick_reprint_row(rows, chosen_doctor)
        if not row:
            raise BackendError("reprint_row_not_found")
        w.progress("print", "run", f"reprint row {row.get('claim')}")
        if not self._click_reprint_row(w, row.get("claim") or ""):
            raise BackendError(f"reprint_row_click_failed | {row.get('claim')}")
        r2 = self._report_hit_poll(w, timeout_s=25)
        if not r2.get("ok"):
            raise BackendError(f"reprint_failed | {r2.get('error')}")
        return {"member": member, "pdf_bytes": r2.get("bytes"),
                "pdf_url": r2.get("url", "")}

    def _do_member_page(self, w, member, card):
        memberNo = str(member or "").strip()
        inp = None
        door = time.monotonic() + 20
        while time.monotonic() < door and not inp:
            inp = self._find(w, JS_MEMBER)
            if not inp:
                time.sleep(0.4)
        if not inp:
            raise BackendError(f"member_input_not_found | {self._state_label(w)}")

        cur = ""
        try:
            cur = (inp.evaluate("el => el.value") or "").strip()
        except Exception:
            pass
        if cur != memberNo:
            w.progress("eligibility", "run", f"enter member={memberNo}")
            w.set_value(inp, memberNo)
            btn = self._find(w, JS_SEARCH_BTN)
            if btn:
                w.click(btn)
            else:
                w.page.keyboard.press("Enter")
            w.progress("submitted", "ok", f"member={memberNo}")
            save_last_good_member(self.id, memberNo)   # لتغذية النبضة حتى لو فشلت اللاحقة
            ok = w.wait_for(
                "() => document.querySelectorAll('ul.ui-autocomplete li.ui-menu-item').length > 0 ||"
                " !!document.querySelector('input.ui-autocomplete-input')",
                timeout_s=12, poll_s=0.3)
            if not ok:
                w.progress("eligibility", "run", "search returned no doctor list yet")
        else:
            w.progress("eligibility", "run", f"member already on page={memberNo}")

        self._select_doctor(w, card)
        self._fill_gsm(w, card)
        self._wait_speciality(w)

        btn = self._find(w, JS_PRINT_BTN)
        if not btn:
            raise BackendError(f"print_button_not_found | {self._state_label(w)}")
        return {"member": memberNo}

    # ---------- keepalive: eligibility re-check في نفس الصفحة ----------
    keepalive_without_member = True   # الصفحة تحمل رقم العضوية — لا حاجة لتخزينه مسبقاً

    def _eligibility_probe(self, w, member):
        """تحقق eligibility برقم التأمين داخل MemberDetails نفسها —
        لا طباعة، لا إيصال، لا طلب — نفس فكرة نبضة MedNet حرفياً.
        بلا مغادرة للصفحة وبلا إعادة تحميل."""
        memberNo = str(member or "").strip()
        inp = None
        door = time.monotonic() + 8
        while time.monotonic() < door and not inp:
            inp = self._find(w, JS_MEMBER)
            if not inp:
                time.sleep(0.5)
        if not inp:
            return False
        cur = ""
        try:
            cur = (inp.evaluate("el => el.value") or "").strip()
        except Exception:
            pass
        if memberNo:
            if cur != memberNo:
                w.set_value(inp, memberNo)
        elif cur:
            memberNo = cur
        btn = self._find(w, JS_SEARCH_BTN)
        if btn:
            w.click(btn)
        else:
            w.page.keyboard.press("Enter")
        w.progress("keepalive", "ok", f"eligibility re-check member={memberNo or '(current)'}")
        # إرسال HTTP يعيد نشاط جلسة البوابة — لا حاجة لقراءة النتيجة
        time.sleep(3.0)
        # كشف صريح لموت جلسة البوابة بدل نجاح صامت
        try:
            if w.q1("#CaptchaInputText") or "/remote/login" in (w.page.url or ""):
                w.progress("keepalive", "ok", "portal session lost (captcha wall) — deferred to real job")
                return False
        except Exception:
            pass
        return True

    def _keepalive(self, w, member):
        creds = creds_for(self.id)
        try:
            if "nlicgulf.com" not in (w.page.url or ""):
                w.page.goto(creds.get("url") or (HOST + "/remote/login?lang=en"),
                            wait_until="domcontentloaded", timeout=30000)

            # حلقة حالات محدودة — نفس مبدأ run() لكن بلا كابتشا أبداً
            for _ in range(8):
                url = w.page.url or ""
                state = self._state(w)
                if "MemberDetails" in url or state == "member":
                    # الحالة المثالية: نفس الصفحة — لا نخرج أبداً
                    ok = self._eligibility_probe(w, member)
                    return {"member": member,
                            "result": "liva_keepalive_ok" if ok else "liva_keepalive_probe_failed"}
                if state == "portal-home":
                    self._open_member_details(w)
                    ok = self._eligibility_probe(w, member)
                    return {"member": member,
                            "result": "liva_keepalive_ok" if ok else "liva_keepalive_probe_failed"}
                if state == "provider-card":
                    self._open_provider(w)
                    continue
                if state == "vpn-login":
                    if time.time() - self._vpn_login_at > 3600:
                        self._do_vpn_login(w, creds)
                        self._vpn_login_at = time.time()
                        w.progress("login", "ok", "vpn re-login (keepalive)")
                    else:
                        return {"member": member, "result": "liva_keepalive_vpn_throttled"}
                    continue
                if state == "provider-login":
                    # كابتشا — تُترك للمهمة الحقيقية
                    w.progress("navigate", "run",
                               "portal re-auth (captcha) deferred to next real job")
                    return {"member": member,
                            "result": "liva_keepalive_portal_auth_required"}
                # مجهول — غالباً Angular بين الصفحات — انتظر ثم أعد الفحص
                w.progress("navigate", "run", "keepalive: unknown — waiting render")
                w.sleep(2.0)
            return {"member": member, "result": "liva_keepalive_state_loop"}
        except Exception as e:
            w.progress("navigate", "run", f"keepalive skipped: {str(e)[:80]}")
            return {"member": member, "result": "liva_keepalive_skipped"}

    # ---------- التدفق ----------
    def run(self, w, kind="doc", member=None, card=None):
        w._started_at = time.monotonic()
        creds = creds_for(self.id)
        member = (member or ((card or {}).get("insurance", {}) or {}).get("card_no")
                  or "").strip()
        if kind == "keepalive":
            return self._keepalive(w, member)
        if not member:
            raise BackendError("member_empty")

        url = w.page.url or ""
        if "nlicgulf.com" not in url:
            w.progress("navigate", "run", "goto VPN login")
            w.page.goto(creds.get("url") or (HOST + "/remote/login?lang=en"),
                        wait_until="domcontentloaded", timeout=45000)

        unknown_tries = 0
        for _ in range(14):
            state = self._state(w)
            if state == "vpn-login":
                self._do_vpn_login(w, creds)
                continue
            if state == "provider-card":
                self._open_provider(w)
                continue
            if state == "provider-login":
                # لا مهلة اعتباطية: ننتظر حدثاً حقيقياً — مغادرة النموذج
                # (نجاح) أو بقاء النموذج (كابتشا خاطئة ستُظهر alert يُسجَّل
                # الآن في السجل). نكمل فقط حين يختفي حقل الكابتشا.
                recent = (w._provider_submit_at and
                          (time.time() - w._provider_submit_at) < 120)
                if recent:
                    door = time.monotonic() + 75
                    while time.monotonic() < door:
                        if not w.q1("#CaptchaInputText"):
                            break
                        if w._captcha_verdict == "fail" or w._wrong_captcha:
                            break
                        time.sleep(0.4)
                    if not w.q1("#CaptchaInputText"):
                        continue          # دخل المزوّد — أكمل التدفق
                    w.progress("login", "run",
                               "captcha rejected — fresh captcha issued")
                raise self._captcha_needs_human(w) or BackendError("captcha_unavailable")
            if state == "portal-home":
                self._open_member_details(w)
                continue
            if state == "member":
                self._do_member_page(w, member, card)
                save_last_good_member(self.id, member)
                w.progress("eligibility", "ok")
                if kind == "keepalive":
                    return {"member": member, "result": "ok"}
                return self._print_or_reprint(w, member, card)
            # مجهول → غالباً Angular ما زال يبني DOM — انتظر ثم أعد الفحص
            # (لا إعادة تحميل — كانت هي ما يقتل البوابة ويُغرق في حلقة)
            unknown_tries += 1
            if unknown_tries > 8:
                raise BackendError(f"unknown_page | {self._state_label(w)}")
            w.progress("navigate", "run", f"unknown page — waiting render ({unknown_tries})")
            w.sleep(2.0)
        raise BackendError("liva_state_loop")

    # ---------- استئناف بعد تحقق بشري (كابتشا أو اختيار) ----------
    def resume_after_captcha(self, w, answer, kind="doc", member=None, card=None):
        answer = str(answer or "").strip()
        pending = getattr(w, "_pending_prompt", None) or {}
        if not answer:
            raise BackendError("أُلغيت العملية من الموظف")
        if pending.get("type") == "choice":
            self._choice_answer = answer
            w.progress("eligibility", "run", f"doctor choice={answer}")
            return self.run(w, kind, member, card)
        # كابتشا بوابة المزوّد
        self._submit_provider_login(w, answer)
        # إشارة نجاح: مغادرة صفحة الدخول (أو ظهور صفحة العضوية/اللوحة)
        ok = w.wait_for(
            "() => !document.querySelector('#CaptchaInputText')",
            timeout_s=20, poll_s=0.5)
        if not ok:
            # رمز خاطئ غالباً → صورة جديدة للموظف
            cap = self._captcha_needs_human(w)
            raise cap or BackendError("captcha_rejected")
        w.progress("login", "ok", "provider")
        return self.run(w, kind, member, card)
