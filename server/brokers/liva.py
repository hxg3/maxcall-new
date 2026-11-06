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
import urllib.parse
from collections import deque
from pathlib import Path

from .base import Backend, BackendError, NeedsCaptcha, NeedsChoice
from .base import creds_for, save_last_good_member, last_good_member
from .base import auth_locked, set_auth_lock, session_event, AuthLocked

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

JS_DIAG_DEEP = """() => {
  const vis = el => el.getClientRects().length > 0;
  const out = {};
  out.allInputs = [...document.querySelectorAll('input, select, textarea')]
      .map(el => ({tag: el.tagName, id: el.id || '', name: el.name || '',
                   type: el.type || '', value: String(el.value || '').slice(0, 60),
                   cls: String(el.className || '').slice(0, 40),
                   placeholder: el.placeholder || '', maxLength: el.maxLength,
                   readonly: el.readOnly, disabled: el.disabled,
                   outer: el.outerHTML.slice(0, 160)}));
  const chain = (el, n) => { const out = []; let cur = el;
      for (let i = 0; i < n && cur; i++, cur = cur.parentElement)
          out.push({tag: cur.tagName, cls: String(cur.className||'').slice(0,40),
                    id: cur.id || ''}); return out; };
  const btn = document.querySelector('button.Printmember');
  out.btnParents = btn ? chain(btn, 5) : null;
  const spec = document.querySelector('#txtSpeciality');
  out.specOuter = spec ? spec.outerHTML.slice(0, 220) : null;
  out.specParents = spec ? chain(spec, 4) : null;
  out.dropdownItems = [...document.querySelectorAll('ul.ui-autocomplete li.ui-menu-item')]
      .filter(vis).map(li => (li.textContent || '').trim().slice(0, 60));
  out.printBtn = btn ? {
    outer: btn.outerHTML.slice(0, 300),
    attrs: Object.fromEntries([...btn.attributes].map(a => [a.name, a.value])),
    disabled: btn.disabled, type: btn.type, isConnected: btn.isConnected,
  } : null;
  out.printLike = [...document.querySelectorAll('button, a, input[type=button], input[type=submit]')]
      .filter(el => /print/i.test(el.textContent || el.value || ''))
      .map(el => ({tag: el.tagName, cls: el.className, id: el.id || '',
                   disabled: !!el.disabled,
                   outer: el.outerHTML.slice(0, 160)}));
  out.forms = [...document.forms].map(f => ({id: f.id, action: f.action, method: f.method}));
  const el = document.querySelector('#txtGsmNo');
  out.phone = el ? {outer: el.outerHTML.slice(0, 300), value: el.value,
                    attrs: Object.fromEntries([...el.attributes].map(a => [a.name, a.value]))} : null;
  out.specialityVal = (document.querySelector('#txtSpeciality') || {}).value || '';
  out.ko = typeof window.ko === 'undefined' ? 'none' : 'present';
  try {
    if (window.ko && btn) {
      const data = (window.ko.dataFor ? window.ko.dataFor(btn) : null)
                 || (window.ko.contextFor ? window.ko.contextFor(btn).$data : null);
      if (data) {
        out.koKeys = Object.keys(data);
        const sample = {};
        for (const k of Object.keys(data)) {
          try {
            const v = (window.ko.isObservable && window.ko.isObservable(data[k])) ? data[k]() : data[k];
            sample[k] = (v && typeof v === 'object')
              ? '(' + ((v.constructor || {}).name || 'object') + ')' : v;
          } catch (e) { sample[k] = '<err>'; }
        }
        out.koModel = sample;
      }
    }
  } catch (e) { out.koErr = String(e); }
  // عناصر تحمل onclick مربوط بـprint/claim
  out.onclicks = [...document.querySelectorAll('[onclick]')]
      .map(el => ({tag: el.tagName, id: el.id || '', onclick: (el.getAttribute('onclick') || '').slice(0, 120)}))
      .filter(x => /print|claim/i.test(x.onclick));
  return out;
}"""

JS_MEMBER = f"""() => {{ {_MJS}
  const el = document.querySelector('#txtMemberId');
  return okEl(el) ? el : null;
}}"""

# محددات الكابتشا تُعامل عبر locator مباشرة (JS_STATE يكفي للكشف)

JS_SEARCH_BTN = f"""() => {{ {_MJS}
  return [...document.querySelectorAll('#btnSearch, input[type=button], button')]
      .find(el => okEl(el) && (el.id === 'btnSearch' || /^\\s*search\\s*$/i.test(el.textContent || el.value || ''))) || null;
}}"""

JS_DOCTOR_ITEMS = """() => {
  // القائمة الخاصة بـ #txtDoctor فقط — jQuery UI يبقي كل قوائم الصفحة
  // (طبيب + speciality + غيرها) في الـDOM بعد إخفائها، والقراءة العامة
  // كانت تُرجع عناصر بحث قديم متبقّي (فتقترح "ELSAYED MUSTAFA" مثلاً).
  // الطريقة الرسمية: $(...).autocomplete('widget') تُرجع عنصر قائمة هذا
  // الحقل وحده. والنتيجة تُقرأ فقط و القائمة ظاهرة (رد المقبس وصل).
  try {
    const $el = jQuery('#txtDoctor');
    const menu = $el.autocomplete('widget');
    if (!menu || !menu.length) return [];
    if (!menu.is(':visible')) return [];
    return menu.find('li.ui-menu-item').toArray()
        .map(li => ((li.querySelector('.ui-menu-item-wrapper') || li).textContent || '').trim())
        .filter(Boolean);
  } catch (e) {
    return [];
  }
}"""


JS_DOCTOR_MENU_READY = """() => {
  try {
    const $el = jQuery('#txtDoctor');
    const menu = $el.autocomplete('widget');
    return !!menu && !!menu.length && menu.is(':visible') &&
           menu.find('li.ui-menu-item').length > 0;
  } catch (e) {
    return false;
  }
}"""



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

# #FailedMsg — حاوية رسائل البوابة (Action Failed / Claim Form already…)
# تظهر باللون الأحمر عند الرفض؛ نقرأها حرفياً لنميّز "already printed"
# (الشرع الوحيد لمسار Reprint) عن أي رفض آخر يجب أن يتوقف عنده النظام.
JS_FAILED_MSG_VISIBLE = """() => {
  const el = document.querySelector('#FailedMsg');
  if (!el) return false;
  const cs = getComputedStyle(el);
  const shown = cs.display !== 'none' && cs.visibility !== 'hidden' &&
                el.getClientRects().length > 0;
  return shown && (el.textContent || '').trim().length > 0;
}"""

JS_FAILED_MSG_TEXT = """() => {
  const el = document.querySelector('#FailedMsg');
  return el ? (el.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 400) : '';
}"""

JS_DISMISS_ALERT = """() => {
  const ok = el => el.getClientRects().length > 0 && !el.disabled;
  const alerts = [...document.querySelectorAll('body *')]
      .filter(el => ok(el) && /already printed/i.test(el.textContent || ''));
  if (!alerts.length) return 'none';
  // ابحث عن أي زر قابل للنقر داخل نفس التنبيه أو في آبائه
  for (const a of alerts) {
    let node = a;
    for (let depth = 0; node && depth < 8; depth++, node = node.parentElement) {
      const btns = [...node.querySelectorAll('button, a, input[type=button], input[type=submit]')]
          .filter(el => ok(el));
      // أول زر غير مرتبط بإلغاء (تجنب زر "لا" إن وجد)
      const btn = btns.find(el => {
        const t = (el.textContent || el.value || '').trim().toLowerCase();
        return !/^(no|cancel|إلغاء|لا)$/i.test(t);
      }) || btns[0];
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
    best, best_score, best_tok = None, 0.0, None
    runner_up = 0.0
    for it in items:
        n = _norm_name(it)
        if not n:
            continue
        if n == w:
            score = 100.0
            tok_scores = None
        elif n in w or w in n:
            score = 88.0
            tok_scores = None
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
            runner_up, best, best_score, best_tok = best_score, it, score, tok_scores
        elif score > runner_up:
            runner_up = score
    if best is None:
        return None
    # حارس الهوية: الاختيار معتمد على رمز واحد مطابق تماماً بينما باقي
    # الرموز ضعيفة (<0.75) — العلاقة حينها قائمة على رمز شائع (ABDUL,
    # SREEDHAR, SREE…) وليست هوية موثوقة ⇒ مرّر للموظف بدل الاختيار الخطأ
    if best_tok and len(best_tok) >= 2:
        if any(s >= 0.99 for s in best_tok) and any(s < 0.75 for s in best_tok):
            return None
    # قرار: واضح (أو متفوق بفارق مريح) ⇒ تلقائي؛ وإلا مرر للموظف
    if best_score >= 62.0 and (best_score - runner_up) >= 15.0:
        return best
    if best_score >= 80.0:
        return best
    return None


def _is_vpn_denied(url, body_text):
    """رفض صريح من بوابة FortiGate: قفل "Too many bad login attempts"
    أو أي بارامتر err= في رابط تسجيل الدخول (رفض كلمة المرور)."""
    u = (url or "").lower()
    if "err=sslvpn_login_permission_denied" in u:
        return True
    if "/remote/login" in u and "err=" in u:
        return True     # أي رفض صريح من البوابة، ليس انتهاء مهلة شبكي
    t = (body_text or "").lower()
    if "too many bad login attempts" in t:
        return True
    return False


class Liva(Backend):
    id = "liva"
    report_capture = "download"
    REPORT_EXT_RE = r"\.pdf(\?|$)|^="

    def __init__(self):
        self._choice_answer = None
        self._vpn_login_at = 0.0
        self._portal_auths = deque()   # طوابع استعادة البوابة — حد 10/ساعة
        self._cold_rebuilds = deque()  # طوابع البناء البارد — حد 4/ساعة
        self._dead_since = None        # None = الجلسة حية آخر تحقق
        self._last_ok_at = 0.0

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
            # رفض صريح من FortiGate؟ — قفل فوري: لا محاولة أخرى إطلاقاً
            # حتى تتغير كلمة المرور (حماية من القفل الكامل للبوابة)
            url = w.page.url or ""
            try:
                body = w.page.evaluate(
                    "() => document.body ? document.body.innerText : ''") or ""
            except Exception:
                body = ""
            if _is_vpn_denied(url, body):
                reason = ("sslvpn_permission_denied (Too many bad login attempts)"
                          if "permission_denied" in url.lower()
                          else "sslvpn_login_rejected (err=)").strip()
                set_auth_lock(self.id, reason)
                raise AuthLocked(reason)
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

    # ---------- الحل الآلي للكابتشا (ddddocr) قبل إزعاج الموظف ----------
    _OCR = None

    @staticmethod
    def _get_ocr():
        """مكتبة OCR أوفلاين متخصصة بالكابتشا — تُهيأ مرة واحدة (تكلفة ~0.5 ث)."""
        if Liva._OCR is None:
            try:
                import ddddocr
                Liva._OCR = ddddocr.DdddOcr(show_ad=False)
            except Exception:
                Liva._OCR = False
        return Liva._OCR or None

    def _auto_solve_captcha(self, w, attempts=6):
        """حاول حل كابتشا بوابة المزوّد آلياً (ddddocr) حتى `attempts` مرات.

        كل محاولة فاشلة تُصدر كابتشا جديدة من الموقع ⇒ النص يتغير فعلياً.
        الحل يجب أن ينتظر جاهزية صفحة الدخول (Angular يربط معالج النموذج
        مبكراً قبل اكتمال البناء) — إرسال مبكر ⇒ النقرة تُفقد ⇒ فشل وهمي.
        التحقق من النجاح: مغادرة صفحة الدخول نحو اللوحة، لا مجرد اختفاء
        حقل الكابتشا (يختفي أثناء إعادة البناء حتى بعد الرفض!)."""
        ocr = self._get_ocr()
        if not ocr:
            return False
        # انتظار جاهزية صفحة الدخول بالكامل قبل أي تفاعل (النقرة تُفقد
        # إن أُرسلت قبل أن يتشبث Angular بمعالج النموذج)
        ready = w.wait_for("() => !!document.querySelector('#CaptchaInputText')",
                           timeout_s=25, poll_s=0.5)
        if not ready:
            return False
        time.sleep(2.5)
        for i in range(attempts):
            shot = self._captcha_jpeg_bytes(w)
            if not shot:
                return False
            try:
                ans = ocr.classification(shot) or ""
            except Exception:
                return False
            ans = re.sub(r"[^A-Za-z]", "", str(ans or "")).upper()
            if not ans:
                continue
            w.progress("captcha", "run", f"auto solve attempt={i+1} text={ans}")
            try:
                self._submit_provider_login(w, ans)
            except BackendError as e:
                w.progress("captcha", "run", f"auto submit error: {e}")
                return False
            ok = w.wait_for("() => /Dashboard/i.test(location.pathname)",
                            timeout_s=25, poll_s=0.5)
            if ok:
                w.progress("captcha", "ok", "auto solve succeeded")
                return True
            w.progress("captcha", "run", "auto solve rejected — retry fresh captcha")
            time.sleep(1.5)
        return False

    # ---------- كابتشا بوابة المزوّد ----------
    def _freeze_captcha(self, w):
        """أوقف دوران الكابتشا التلقائي (كل 60 ثانية).

        السبب: البوابة تستدعي DefaultCaptcha/Refresh كل دقيقة فتدوّر قيمة
        الكابتشا في الخادم — فتصبح الصورة التي قرأها الموظف قديمة إن تأخّر
        ⇒ رفض "Enter wrong captcha!". بتجميدها تبقى الصورة والقيمة ثابتتين
        طوال مهلة التحقق ⇒ الموظف يأخذ راحته ولا يُرفض له شيء.
        يُسجَّل مرة واحدة لكل سياق."""
        try:
            if getattr(w, "_captcha_frozen", False):
                return
            w.ctx.route("**/DefaultCaptcha/Refresh", lambda route: route.abort())
            w._captcha_frozen = True
            w.progress("captcha", "run", "captcha refresh frozen (stable code)")
        except Exception:
            pass

    @staticmethod
    def _captcha_jpeg_bytes(w):
        el = w.q1("#CaptchaImage")
        if el:
            try:
                return w.page.locator("#CaptchaImage").screenshot(type="jpeg", timeout=3000)
            except Exception:
                return None
        # احتياط: التقط أي صورة ظاهرة
        try:
            return w.page.screenshot(type="jpeg", quality=70)
        except Exception:
            return None

    def _captcha_needs_human(self, w):
        if not w.q1("#CaptchaInputText"):
            return None
        import base64
        shot = self._captcha_jpeg_bytes(w)
        if shot:
            w.progress("captcha", "run", "captcha shown to operator")
            return NeedsCaptcha(base64.b64encode(shot).decode("ascii"))
        raise BackendError("captcha_image_failed")

    def fresh_captcha_image(self, w):
        """صورة الكابتشا الحالية من DOM — تُستدعى دورياً من وسيط الانتظار
        للإبقاء على الصورة طازجة دائماً (البوابة تجدد الكابتشا كل دقيقة)."""
        import base64
        try:
            if not w.q1("#CaptchaInputText"):
                return None
            shot = self._captcha_jpeg_bytes(w)
            return base64.b64encode(shot).decode("ascii") if shot else None
        except Exception:
            return None

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
        """اكتب في حقل الطبيب فقط حتى تظهر قائمة autocomplete الخاصة به.
        إغلاق القائمة أولاً حتى لا تُقرأ عناصر بحث سابق متبقية.
        ننتظر القائمة **الخاصة بـ#txtDoctor** (jQuery widget) — لا مجرد
        وجود أي li.ui-menu-item على الصفحة."""
        NOT_DOCTOR = {"txtMemberId", "txtGsmNo", "CaptchaInputText", "txtSpeciality",
                      "txtPatientId", "txtDateofAdmission", "txtSecurityToken"}
        loc = w.page.locator('#txtDoctor')
        if loc.count() == 0:
            return None
        el = loc.first
        try:
            try:
                w.page.evaluate("""() => {
                  try { jQuery('#txtDoctor').autocomplete('close'); } catch (e) {}
                }""")
            except Exception:
                pass
            el.fill("")
            el.press_sequentially(text, delay=18)
        except Exception:
            return None
        if w.wait_for(JS_DOCTOR_MENU_READY, timeout_s=2.5, poll_s=0.2):
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
        # انقر العنصر المطابق في قائمة الطبيب فقط (لا قوائم أخرى على الصفحة)
        clicked = w.page.evaluate(
            """(want) => {
                const norm = s => String(s || '').toUpperCase().replace(/[^A-Z0-9 ]+/g, ' ').replace(/\\s+/g, ' ').trim();
                const t = norm(want);
                try {
                  const menu = jQuery('#txtDoctor').autocomplete('widget');
                  const li = menu.find('li.ui-menu-item').toArray()
                    .find(el => norm((el.querySelector('.ui-menu-item-wrapper') || el).textContent) === t);
                  if (!li) return false;
                  li.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
                  li.click();
                  return true;
                } catch (e) { return false; }
            }""", target)
        if not clicked:
            raise BackendError(f"doctor_pick_failed | target={target}")
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
        """البوابة تملأها تلقائياً بعد اختيار الطبيب (SP_DESCRIPTION + Data-spCode).
        يجب أن تحمل الحقل القيمة والكود — شرط معالج الطباعة الصامت."""
        state = w.page.evaluate(JS_SPECIALITY_OK)
        if state == "absent":
            w.progress("eligibility", "run", "speciality field absent — skipped")
            return
        ok = w.wait_for(
            """() => { const el = document.querySelector('#txtSpeciality');
                      return el && (el.value || '').trim() &&
                             (el.getAttribute('Data-spCode') || '').trim(); }""",
            timeout_s=20, poll_s=0.4)
        info = w.page.evaluate(
            """() => { const el = document.querySelector('#txtSpeciality');
                      return { val: (el && el.value) || '',
                               spCode: (el && el.getAttribute('Data-spCode')) || '' }; }""") or {}
        w.progress("eligibility", "run",
                   f"speciality=\"{info.get('val')}\" spCode={info.get('spCode') or '-'}")
        if not ok:
            raise BackendError(
                f"speciality_autofill_missing | val={str(info.get('val', ''))[:40]}")

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
            return True
        # احتياط: Escape
        try:
            w.page.keyboard.press("Escape")
            time.sleep(0.5)
            return not self._already_printed_visible(w)
        except Exception:
            return False

    def _failed_msg_visible(self, w):
        try:
            return bool(w.page.evaluate(JS_FAILED_MSG_VISIBLE))
        except Exception:
            return False

    def _failed_msg_text(self, w):
        try:
            return w.page.evaluate(JS_FAILED_MSG_TEXT) or ""
        except Exception:
            return ""

    def _find_reprint_button(self, w):
        """الزر له كلاس ListOfMem ونص "Reprint Online Claim Form" — ليس RePrintmember."""
        try:
            handle = w.page.evaluate_handle(JS_REPRINT_BTN).as_element()
            if handle:
                return handle
        except Exception:
            pass
        try:
            return w.page.evaluate_handle("""() => {
              const ok = el => el.getClientRects().length > 0 && !el.disabled;
              return [...document.querySelectorAll('button, a, input[type=button], input[type=submit]')]
                  .find(el => ok(el) && /reprint\\s+(online\\s+)?claim/i.test((el.textContent || el.value || ''))) || null;
            }""").as_element()
        except Exception:
            pass
        # fallback by class ListOfMem
        return w.q1("button.ListOfMem, .ListOfMem, button.Listofmem")

    def _wait_reprint_rows(self, w, timeout_s=30):
        door = time.monotonic() + timeout_s
        while time.monotonic() < door:
            try:
                rows = w.page.evaluate(JS_REPRINT_ROWS) or []
            except Exception:
                rows = []
            if rows:
                return rows
            time.sleep(0.5)
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
            # المعالج مركّز على td:first-child — انقر الخلية الأولى حصراً
            cell = w.page.locator(f"#PreAprovals tbody tr:has-text('{claim_no}') td").first
            if cell.count() == 0:
                cell = w.page.locator(f"tr:has-text('{claim_no}') td").first
            if cell.count() == 0:
                return False
            handle = cell.element_handle()
            if handle and w.click(handle):
                return True
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
        # حارس الهوية النهائي: لا طباعة/مطالبة إن لم تكن الصفحة ما زالت
        # على نفس العضو المطلوب (إعادة توجيه أو تحديث نصف أثناء العمل)
        still = w.page.evaluate(
            "() => (document.querySelector('#txtMemberId') || {}).value || ''") or ""
        if still.strip() != str(member or "").strip():
            raise BackendError(
                f"member_mismatch_before_print | page={still.strip() or '?'} want={member}")
        # تصحيح اسم سمة boostrap: البوابة تقرأ data-DocCode (كبيرة) بينما HTML
        # يحمل data-doccode (صغيرة) ⇒ DocNo = undefined ⇒ يتم تجاهلdoAjax بصمت
        try:
            w.page.evaluate("""() => {
              const doc = document.querySelector('#txtDoctor');
              if (doc) {
                const code = doc.getAttribute('data-doccode');
                if (code && !doc.getAttribute('data-DocCode'))
                  doc.setAttribute('data-DocCode', code);
              }
            }""")
        except Exception:
            pass

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
        w.progress("print", "run", "print button clicked — waiting for result (S → PDF · F → reprint only)")
        # الطريقة الافتراضية: نقر زر "Print Online Claim Form" الحقيقي
        # (نفس معالج البوابة .Printmember: validation → Loading → Reports/PatientInfo).
        # window.open(FilePath) يُلتقط بنفس آلية Reprint (FormDetail) — لا فرق
        # بين الطريقتين في كيفية استخراج المستند.
        if not w.click(btn):
            if not w.trusted_click(btn):
                raise BackendError("print_click_failed")
        r = self._report_hit_poll(w, timeout_s=25, stop_pred=self._failed_msg_visible)
        if r.get("ok"):
            w.progress("print", "ok", f"url={(r.get('url') or '')[:110]}")
            return {"member": member, "pdf_bytes": r.get("bytes"),
                    "pdf_url": r.get("url", "")}
        # لم يصل أي رابط PDF — اقرأ سبب الرفض الحقيقي من #FailedMsg
        fmsg = self._failed_msg_text(w) or ""
        # الحالة الشرعية الوحيدة التي تنتقل لمسار Reprint: "Action Failed …
        # Claim Form already printed for this member & for the selected Doctor"
        if not re.search(r"already\s*printed", fmsg, re.IGNORECASE):
            reason = fmsg or r.get("error") or "unknown"
            w.progress("print", "error", f"print_failed | {reason[:180]}")
            raise BackendError(f"print_failed | {reason[:250]}")
        w.progress("print", "run", "already printed — auto fallback to Reprint")
        if not self._dismiss_already_printed(w):
            w.progress("print", "run", "could not dismiss already-printed dialog")

        btn = self._find_reprint_button(w)
        if not btn:
            # تشخيص شامل قبل الفشل
            try:
                all_btns = w.page.evaluate("""() => {
                  return [...document.querySelectorAll('button, a, input[type=button], input[type=submit]')]
                    .filter(el => el.getClientRects().length > 0)
                    .map(el => ({tag: el.tagName, text: (el.textContent||el.value||'').trim().slice(0,60), cls: el.className||''}))
                }""")
                w.progress("print", "error", f"reprint_button_not_found | all_buttons={json.dumps(all_btns, ensure_ascii=False)[:500]}")
            except Exception:
                pass
            raise BackendError("reprint_button_not_found")
        # نقرة JS المباشرة — الزر type="submit" وقد يكون مغطّى بصيرورة
        # التحميل .Loading (فتفقد نقرة CDP إياه). المعالج الموجود
        # delegated عبر jQuery على document فيستقبل أي click.
        if not w.click(btn):
            if not w.trusted_click(btn):
                raise BackendError("reprint_click_failed")
        w.progress("print", "run", "reprint button clicked — waiting for table")
        rows = self._wait_reprint_rows(w, timeout_s=20)
        if not rows:
            # تشخيص شامل لما يظهر على الصفحة بعد النقر على Reprint
            try:
                page_state = w.page.evaluate("""() => {
                  return {
                    visibleTables: [...document.querySelectorAll('table')].filter(t => t.getClientRects().length > 0).map(t => t.outerHTML.slice(0, 200)),
                    modals: [...document.querySelectorAll('.modal, .ui-dialog, .swal2-container, [class*="modal"]')].filter(m => m.getClientRects().length > 0).map(m => m.textContent.trim().slice(0, 200)),
                    failedMsg: (document.querySelector('#FailedMsg')?.textContent || '').trim().slice(0, 200),
                    allVisible: [...document.querySelectorAll('button, a, input[type=button], input[type=submit]')]
                        .filter(el => el.getClientRects().length > 0)
                        .map(el => ({tag: el.tagName, text: (el.textContent||el.value||'').trim().slice(0,60), cls: el.className||''}))
                  };
                }""")
                w.progress("print", "error", f"reprint_table_not_found | page_state={json.dumps(page_state, ensure_ascii=False)[:600]}")
            except Exception:
                pass
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

        # حقل الدخول يجب أن يكون ظاهراً ومفعّلاً ومخالفاً قبل الإدخال
        ready = w.wait_for(
            "() => { const el = document.querySelector('#txtMemberId');"
            " return !!el && el.getClientRects().length > 0 && !el.disabled && !el.readOnly; }",
            timeout_s=20, poll_s=0.4)
        cur = ""
        try:
            cur = (inp.evaluate("el => el.value") or "").strip()
        except Exception:
            pass
        # منع إعادة بحث غير ضرورية إن كان الرقم موجوداً وسليماً —
        # لكن أيضاً تأكد أن بيانات البحث السابقة ما زالت كاملة (لو كانت
        # السابقة قد فشلت نصفياً تُمسح الحقول ويبقى الرقم — فنعيد البحث)
        data_alive = w.page.evaluate(
            """() => {
              const g = ((document.querySelector('#txtGsmNo') || {}).value || '').trim();
              const st = ((document.querySelector('#txtStaffCode') || {}).value || '').trim();
              const en = ((document.querySelector('#txtEmpName') || {}).value || '').trim();
              const pn = ((document.querySelector('#txtPolicyNo') || {}).value || '').trim();
              return !!(g || st || en || pn);
            }""")
        if cur == memberNo and data_alive:
            # أبقِ المسار: البيانات للعضو نفسه — لا إعادة بحث
            pass
        elif cur == memberNo and not data_alive:
            w.progress("eligibility", "run", "stale half-cleared page — forcing fresh search")
            cur = ""   # أسقط إلى مسار البحث الجديد
        if cur != memberNo:
            # أفرغ الحقل تماماً قبل كتابة الرقم الصحيح
            w.page.evaluate(
                "() => { const el = document.querySelector('#txtMemberId');"
                " if (el) { el.value = ''; el.dispatchEvent(new Event('input', { bubbles: true }));"
                " el.dispatchEvent(new Event('change', { bubbles: true })); } }")
            w.set_value(inp, memberNo)
            # تحقق من ملء الحقل بالقيمة الصحيحة قبل النقر على Search
            confirmed = w.page.evaluate(
                "() => { const el = document.querySelector('#txtMemberId');"
                " return el ? el.value.trim() : ''; }") or ""
            if confirmed != memberNo:
                raise BackendError(f"member_not_entered | field={confirmed} want={memberNo}")
            w.progress("eligibility", "run", f"entered member={memberNo} ✓")

            btn = self._find(w, JS_SEARCH_BTN)
            # النجاح الحقيقي للبحث = رد S من PreApproval/GetMemberInfo بعد
            # النقر — لا "وجود عناصر autocomplete" (jQuery UI يضيف الصنف
            # ثابتاً على الصفحة فيعود الانتظار فوراً وتُطبع بيانات العضو
            # السابق). ننتظر الرد نفسه ونتحقق من StatusCode قبل أي خطوة
            # لاحقة — عند أي فشل/بطء نتوقف برسالة واضحة بدل الطباعة الخطأ
            search_resp = None
            try:
                with w.page.expect_response(
                        lambda r: "PreApproval/GetMemberInfo" in (r.url or ""),
                        timeout=25000) as resp_info:
                    if btn:
                        if not w.trusted_click(btn):
                            w.click(btn)
                    else:
                        w.page.keyboard.press("Enter")
                search_resp = resp_info.value
            except Exception as e:
                # لا رد — الصفحة ما زالت تعرض بيانات العضو السابق
                raise BackendError(
                    f"member_search_no_response | {str(e)[:120]}")
            try:
                body = search_resp.json()
            except Exception:
                body = None
                try:
                    body = json.loads(search_resp.text() or "")
                except Exception:
                    body = {}
            st = (body or {}).get("st") or {}
            code = str(st.get("StatusCode") or "").upper()
            if code != "S":
                desc = str(st.get("StatusDescription") or "")[:160]
                raise BackendError(f"member_search_failed | status={code or '?'} | {desc}")
            # فحوصات شبكية (fetch) لا تمنع — مجرد إشارة في السجل:
            # #txtGsmNo يُملأ من MemberData.MobileNo في callback النجاح نفسه
            renewed = w.wait_for(
                """() => { const el = document.querySelector('#txtGsmNo');
                         const v = (el && (el.value || '').trim()) || '';
                         return v.length >= 8; }""",
                timeout_s=8, poll_s=0.3)
            if not renewed:
                w.progress("eligibility", "run",
                           "warn: gsm field empty after search (member may have no mobile on file)")
            w.progress("submitted", "ok", f"search ok member={memberNo}")
            save_last_good_member(self.id, memberNo)   # لتغذية النبضة حتى لو فشلت اللاحقة
        else:
            w.progress("eligibility", "run", f"member already on page={memberNo}")

        self._select_doctor(w, card)
        self._fill_gsm(w, card)
        self._wait_speciality(w)

        btn = self._find(w, JS_PRINT_BTN)
        if not btn:
            raise BackendError(f"print_button_not_found | {self._state_label(w)}")
        return {"member": memberNo}

    # ---------- تشخيص عميق: تفكيك آلية الطباعة/Reprint (kind=diag) ----------
    def _diag_deep(self, w, member, card):
        """يعيد خريطة كاملة لآلية الطباعة: بنية الصفحة، نص doAjax الخام،
        سلوك نقر زر Print الحقيقي، وسلوك نقر زر Reprint (جدول/نافذة/شبكة)."""
        out = {}
        pg = w.page

        def ev(js, default=None):
            try:
                return pg.evaluate(js)
            except Exception as e:
                return {"_err": str(e)[:200]} if default is None else default

        out["globals"] = ev("""() => ({
            url: location.href,
            URL: String(window.URL || ''),
            UserObj: window.UserObj ? JSON.parse(JSON.stringify(window.UserObj)) : null,
            doAjax: typeof window.doAjax,
            title: document.title,
        })""")
        out["fields"] = ev("""() => {
          const ids = ['txtMemberId','txtGsmNo','txtSpeciality','txtDoctor','txtPatientId',
                       'txtEmpName','txtCardNo','txtClientNo','txtPolicyNo',
                       'txtCoverFromDate','txtCoverToDate','txtSecurityToken'];
          const o = {};
          for (const id of ids) {
            const el = document.querySelector('#'+id);
            o[id] = el ? {value: el.value, attrs: Object.fromEntries(
                          [...el.attributes].map(a=>[a.name,a.value]))} : null;
          }
          return o;
        }""")
        out["buttons"] = ev("""() => {
          const vis = e => e.getClientRects().length > 0;
          return [...document.querySelectorAll('button,a.btn,input[type=button],input[type=submit],a')]
            .filter(e => vis(e))
            .map(e => ({tag:e.tagName, id:e.id||'', cls:String(e.className||''),
                        text:(e.textContent||e.value||'').trim().slice(0,80),
                        onclick:(e.getAttribute('onclick')||'').slice(0,400),
                        type:e.type||'', disabled:!!e.disabled,
                        outer:e.outerHTML.slice(0,500)}));
        }""")
        out["jquery_events"] = ev("""() => {
          const o = {};
          for (const b of document.querySelectorAll('.ListOfMem, .Printmember')) {
            let evs = [];
            try {
              const d = jQuery._data(b, 'events');
              if (d) for (const t in d) for (const h of d[t]) {
                evs.push({type:t, ns:h.namespace||'', sel:h.selector||'',
                          handler:String(h.handler).slice(0,4000)});
              }
            } catch (e) { evs.push({err:String(e)}); }
            o[b.className] = evs;
          }
          return o;
        }""")
        out["inline_scripts"] = ev("""() => {
          const hits = [];
          for (const s of document.querySelectorAll('script:not([src])')) {
            const t = s.textContent || '';
            if (/Printmember|ListOfMem|Reprint|PatientInfo|PrintOnline/i.test(t))
              hits.push(t.slice(0, 15000));
          }
          return hits;
        }""")
        out["global_funcs"] = ev("""() => {
          const out = [];
          for (const k of Object.getOwnPropertyNames(window)) {
            try { const v = window[k];
              if (typeof v === 'function') {
                const src = String(v);
                if (/Printmember|ListOfMem|Reprint|PatientInfo|PrintOnline|ClaimForm/i.test(src))
                  out.push({name:k, src: src.slice(0, 4000)});
              }
            } catch (e) {}
          }
          return out;
        }""")
        out["script_srcs"] = ev("""() => [...document.querySelectorAll('script[src]')].map(s=>s.src)""")
        # محتوى ملف JS الخاص بـ MemberDetails (يحمل معالجات الأزرار)
        out["memberdetails_js"] = ev("""async () => {
            const srcs = [...document.querySelectorAll('script[src]')].map(s => s.src);
            const hit = srcs.find(u => /ProviderMemberDetails/i.test(u));
            if (!hit) return { found: false };
            try {
                const r = await fetch(hit, { credentials: 'include' });
                const t = await r.text();
                return { found: true, url: hit, bytes: t.length, text: t.slice(0, 120000) };
            } catch (e) { return { found: true, url: hit, err: String(e) }; }
        }""")

        # ---- 1) doAjax PatientInfo مع التقاط الرد الخام ----
        net = []
        def on_resp(r):
            try:
                u = r.url or ""
                if "livainsurance" in u or "nlicgulf" in u:
                    net.append({"method": r.request.method, "url": u, "status": r.status,
                                "ct": (r.headers or {}).get("content-type", ""),
                                "post": (r.request.post_data or "")[:2000]})
            except Exception:
                pass
        pg.on("response", on_resp)
        raw = {}
        try:
            with pg.expect_response(lambda r: "PatientInfo" in (r.url or ""),
                                     timeout=45000) as ri:
                do_res = ev("""() => {
                  const Memberobj = {};
                  Memberobj.Hospno = window.UserObj?.Name || '';
                  Memberobj.UserType = window.UserObj?.UserType || 'PROVIDER';
                  Memberobj.Speciality = $('#txtSpeciality').attr('Data-spCode');
                  Memberobj.DocNo = $('#txtDoctor').attr('data-DocCode');
                  Memberobj.Gm_MemberNo = $('#txtGsmNo').val();
                  Memberobj.PatientId = $('#txtPatientId').val();
                  Memberobj.UserIdVal = window.UserObj?.UserID || '';
                  Memberobj.ClientName = $('#txtEmpName').val();
                  Memberobj.CardNo = $('#txtCardNo').val();
                  Memberobj.ClientNo = $('#txtClientNo').val();
                  Memberobj.PolicyNo = $('#txtPolicyNo').val();
                  Memberobj.mfromdate = $('#txtCoverFromDate').val();
                  Memberobj.mtodate = $('#txtCoverToDate').val();
                  window.__MCR_MEMOBJ = Memberobj;
                  return new Promise((resolve) => {
                    try {
                      doAjax(URL + 'Reports/PatientInfo', 'Post', Memberobj,
                             'application/json', 'json', '',
                             function (err, result) {
                               if (err) resolve({err:String(err)}); else resolve(result); });
                    } catch (e) { resolve({thrown:String(e)}); }
                  });
                }""")
            resp = ri.value
            raw["status"] = resp.status
            raw["ct"] = (resp.headers or {}).get("content-type", "")
            try:
                raw["body"] = resp.text()[:4000]
            except Exception as e:
                raw["body_err"] = str(e)[:200]
        except Exception as e:
            raw["err"] = str(e)[:250]
        raw["result"] = do_res
        out["patientinfo"] = raw
        out["memberobj"] = ev("() => window.__MCR_MEMOBJ || {}")

        # ---- 2) نقر زر Print الحقيقي (CDP) ومراقبة ما يحدث ----
        def watch(seconds=12, label="print"):
            pages0 = [p.url for p in w.ctx.pages]
            pg.evaluate("() => { window.__MCR_OPEN_URL=''; window.__MCR_OPEN_AT=0; }")
            obs = []
            end = time.monotonic() + seconds
            while time.monotonic() < end:
                snap = ev("""() => ({
                    url: location.href,
                    openUrl: String(window.__MCR_OPEN_URL||''),
                    tables: [...document.querySelectorAll('table')]
                        .filter(t=>t.getClientRects().length>0)
                        .map(t=>({cls:String(t.className||''), id:t.id||'',
                                  txt:(t.textContent||'').trim().slice(0,300)})),
                    rows: [...document.querySelectorAll('tr')].filter(r=>r.getClientRects().length>0)
                        .map(r=>(r.textContent||'').trim().replace(/\\s+/g,' ').slice(0,160)).slice(0,40),
                    dialogs: [...document.querySelectorAll('.modal,.ui-dialog,.swal2-container,[role=dialog]')]
                        .filter(m=>m.getClientRects().length>0).map(m=>(m.textContent||'').trim().slice(0,200)),
                    message: (document.querySelector('#Message')||{}).value || '',
                    heading: (document.querySelector('#Heading')||{}).value || '',
                })""", {})
                snap["ctxPages"] = [p.url for p in w.ctx.pages]
                snap["newPages"] = [u for u in snap["ctxPages"] if u not in pages0]
                obs.append(snap)
                time.sleep(1.0)
            return obs

        def click_btn(substr):
            return ev("""(pat) => {
              const vis = e => e.getClientRects().length > 0 && !e.disabled;
              const low = (pat || '').toLowerCase();
              const b = [...document.querySelectorAll('button,a,input[type=button],input[type=submit]')]
                .find(e => vis(e) && (e.textContent||e.value||'').toLowerCase().includes(low));
              if (!b) return null;
              const r = b.getBoundingClientRect();
              return {outer:b.outerHTML.slice(0,500), x:r.x+r.width/2, y:r.y+r.height/2};
            }""", substr)

        # زر Reprint
        rb = click_btn("reprint online claim")
        out["reprint_button"] = rb
        if rb:
            try:
                pg.mouse.click(rb["x"], rb["y"])
            except Exception:
                ev("""() => { const vis=e=>e.getClientRects().length>0&&!e.disabled;
                   const b=[...document.querySelectorAll('button,a,input[type=button],input[type=submit]')]
                   .find(e=>vis(e)&&/reprint\\s+(online\\s+)?claim/i.test(e.textContent||e.value||''));
                   if(b) b.click(); }""")
            out["reprint_observe"] = watch(20, "reprint")
            out["reprint_net"] = list(net)
            out["reprint_pages"] = [{"url": p.url, "html": (p.content()[:8000]
                                  if p.url != pg.url else "")}
                                 for p in w.ctx.pages if p is not pg]
        # ملاحظة: عمداً لا ننقر زر Print الحقيقي في التشخيص — قد يختم
        # Claim Form حقيقي كـ"مطبوع" للعضو. نقتصر على تشخيص القراءة.
        net.clear()
        out["print_button"] = None
        out["print_observe"] = []
        try:
            pg.remove_listener("response", on_resp)
        except Exception:
            pass
        return {"member": member, "diag": out}

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

    # ---------- الاستعادة الكاملة التلقائية — الجاهزية الدائمة ----------
    def _may_portal_auth(self):
        """حد أقصى 10 استعادة بوابة/ساعة + فاصل 4 دقائق بين المحاولات —
        يمنع أي عاصفة كابتشا حتى لو حدث خطأ منطقي."""
        now = time.time()
        while self._portal_auths and now - self._portal_auths[0] > 3600:
            self._portal_auths.popleft()
        if self._portal_auths and now - self._portal_auths[-1] < 240:
            return False
        if len(self._portal_auths) >= 10:
            return False
        self._portal_auths.append(now)
        return True

    def _may_cold_rebuild(self):
        """حد أقصى 4 بناء بارد/ساعة + فاصل 10 دقائق."""
        now = time.time()
        while self._cold_rebuilds and now - self._cold_rebuilds[0] > 3600:
            self._cold_rebuilds.popleft()
        if self._cold_rebuilds and now - self._cold_rebuilds[-1] < 600:
            return False
        if len(self._cold_rebuilds) >= 4:
            return False
        self._cold_rebuilds.append(now)
        return True

    def _note_death(self, w, reason):
        """سجّل موت الجلسة مرة واحدة لكل فترة (لا سطر كل 4.5 دقيقة)."""
        if self._dead_since is not None:
            return
        now = time.time()
        self._dead_since = now
        session_event(self.id, "dead", reason,
                      last_ok_ago_s=round(now - self._last_ok_at, 0)
                      if self._last_ok_at else None,
                      url=(w.page.url or "")[:120])

    def _note_ready(self, w):
        now = time.time()
        first = (self._last_ok_at == 0.0)
        self._last_ok_at = now
        if self._dead_since is not None:
            dead_for = round(now - self._dead_since, 1)
            session_event(self.id, "restored", f"after {dead_for}s dead",
                          dead_for_s=dead_for)
            self._dead_since = None
        elif first:
            # أول تحقق بعد الإقلاع — تثبيت baseline لعمر الجلسة في التحليلات
            session_event(self.id, "ready",
                          "session alive (first verified after boot)")

    def _pump_ready(self, w, creds, memberNo):
        """قاد المتصفح من أي حالة إلى صفحة MemberDetails الجاهزة.
        يرجع True بمجرد التأكد أن الصفحة حية (eligibility re-check نجاح).

        حلقة idempotent — كل خطوة تقرأ الواقع من الـDOM، لا حالة محفوظة.
        بلا إعادة تحميل إجبارية (كانت تقتل البوابة) — التنقل عبر الحالة فقط."""
        # سجّل أن الجلسة ميتة لحظة اكتشافها (مرة واحدة لكل فترة) —
        # هذا ما يعطينا حقلاً زمنياً واضحاً في التحليلات
        state0 = self._state(w)
        if state0 != "member" and "MemberDetails" not in (w.page.url or ""):
            self._note_death(w, f"not_ready state={state0}")
        if "nlicgulf.com" not in (w.page.url or ""):
            w.page.goto(creds.get("url") or (HOST + "/remote/login?lang=en"),
                        wait_until="domcontentloaded", timeout=45000)
        probe_fails = 0
        for _ in range(12):
            url = w.page.url or ""
            state = self._state(w)
            if state == "member" or "MemberDetails" in url:
                ok = self._eligibility_probe(w, memberNo)
                if ok:
                    return True
                # البحث بجلسة ميتة يرحّل البوابة لصفحة الدخول —
                # اقرأ الحالة جديدة بدل الهروب مباشرة للبناء البارد
                probe_fails += 1
                if probe_fails >= 3:
                    return False
                w.sleep(1.5)
                continue
            if state == "portal-home":
                self._open_member_details(w)
                continue
            if state == "provider-card":
                self._open_provider(w)
                continue
            if state == "provider-login":
                if not self._may_portal_auth():
                    w.progress("keepalive", "run",
                               "portal re-auth rate-limited — سيُستأنف تلقائياً")
                    return False
                session_event(self.id, "restore_portal",
                              "dead session — auto captcha re-auth")
                if self._auto_solve_captcha(w):
                    continue
                w.progress("login", "run",
                           "auto re-auth failed — ستُعاد المحاولة في النبضة القادمة")
                return False
            if state == "vpn-login":
                self._do_vpn_login(w, creds)   # رفض صريح ⇒ AuthLocked ⇒ قفل كامل
                self._vpn_login_at = time.time()
                continue
            # مجهول — Angular أو صفحة بين حالات — انتظر ثم أعد الفحص
            w.progress("navigate", "run", "keepalive: unknown — waiting render")
            w.sleep(2.0)
        return False

    def _keepalive(self, w, member):
        memberNo = str(member or "").strip() or last_good_member(self.id)
        if auth_locked(self.id):
            w.progress("keepalive", "run",
                       "auth locked — لا محاولات إطلاقاً حتى تتغير كلمة المرور")
            return {"member": member, "result": "liva_auth_locked"}
        creds = creds_for(self.id)
        try:
            cold = False
            for _ in range(2):
                ok = self._pump_ready(w, creds, memberNo)
                if ok:
                    self._note_ready(w)
                    return {"member": member, "result": "liva_keepalive_ok"}
                if cold:
                    break
                # فشل — جرّب مرة واحدة بناء بارداً (متصفح جديد من الصفر):
                # نفس مسار "صباح جديد" المضمون — يمحو أي حالة عالقة
                if self._may_cold_rebuild():
                    session_event(self.id, "restore_cold",
                                  f"in-place restore failed | url={(w.page.url or '')[-80:]}")
                    w.needs_context = True
                    self._vpn_login_at = 0.0     # دخول واحد فوراً — ليس عاصفة
                    w.ensure_context()
                    cold = True
                    continue
                break
            self._note_death(w, "restore_failed")
            return {"member": member, "result": "liva_keepalive_restore_failed"}
        except AuthLocked as e:
            self._note_death(w, f"auth locked: {str(e)[:80]}")
            return {"member": member, "result": "liva_auth_locked"}
        except Exception as e:
            self._note_death(w, f"error: {str(e)[:80]}")
            w.progress("navigate", "run", f"keepalive skipped: {str(e)[:80]}")
            session_event(self.id, "restore_failed", str(e)[:150])
            return {"member": member, "result": "liva_keepalive_restore_failed"}

    # ---------- التدفق ----------
    def run(self, w, kind="doc", member=None, card=None):
        w._started_at = time.monotonic()
        if kind != "keepalive" and auth_locked(self.id):
            raise AuthLocked("liva auth locked — password stale?")
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
                self._freeze_captcha(w)
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
                # جرّب الحل التلقائي (ddddocr) قبل إزعاج الموظف — كل محاولة
                # فاشلة تُصدر كابتشا جديدة من الموقع فتُقرأ من جديد
                if self._auto_solve_captcha(w):
                    continue
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
                if kind == "diag":
                    # تشخيص عميق بدل الطباعة — القراءة فقط، لا يُنشأ شيء
                    try:
                        dump = w.page.evaluate(JS_DIAG_DEEP) or {}
                    except Exception as e:
                        dump = {"eval_error": str(e)[:200]}
                    dump["url"] = w.page.url
                    return {"member": member, "diag": dump}
                if kind == "diag2":
                    # تشخيص عميق للطباعة/Reprint — يكسر mechanisms بلا أثر جانبي
                    return self._diag_deep(w, member, card)
                out = self._print_or_reprint(w, member, card)
                self._note_ready(w)
                return out
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
        self._freeze_captcha(w)
        self._submit_provider_login(w, answer)
        # نفس منطق التحقق: الدخول الحقيقي = مغادرة صفحة الدخول نحو اللوحة
        ok = w.wait_for(
            "() => /Dashboard/i.test(location.pathname)",
            timeout_s=25, poll_s=0.5)
        if not ok:
            # رمز خاطئ غالباً → صورة جديدة للموظف
            cap = self._captcha_needs_human(w)
            raise cap or BackendError("captcha_rejected")
        w.progress("login", "ok", "provider")
        return self.run(w, kind, member, card)
