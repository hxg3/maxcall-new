# قاعدة عامل الاستقبال — جلسة Playwright واحدة دائمة لكل تأمين.
#
# الفلسفة المنقولة من محوّلات الإضافة (نفس المبادئ المُثبتة):
# • لا أعلام "تم النقر" — الحالة تُقرأ من الـDOM عند كل خطوة (idempotent)
#   فإعادة المحاولة بعد أي تنقل تُكمل من حيث توقفت.
# • لا انتظار ثابت — كل خطوة تنتظر إشارة الواقع (عنصر/استجابة/رسالة).
# • تعبئة الحقول بجسم setNativeValue نفسه المُثبت على الموقع (مُعدّل القيمة
#   الأصلي + حدثا input/change) — لا مخاطرة بمسار جديد غير مختبر.
#
# الجديد في عالم العامل:
# • الأحداث أصلية (نقرة/كتابة حقيقية) — بلا محاكاة كائنات مزوّرة.
# • التقدم يُرفع إلى المهمة الحالة عبر progress() حتى شاشة الموظف.
# • أعطال الصفحة تُعالَج بإعادة إنشاء السياق عند الجولة التالية
#   (بدل حلقة إعادة الإرسال في الإضافة — هنا العامل دائم فالجولة واحدة).

import json
import threading
import time
from pathlib import Path

CREDS_FILE = Path("/etc/maxcall-insurers.json")
STATE_DIR = Path("/var/lib/maxcall-broker")

_creds_lock = threading.Lock()
_creds_cache = {"mtime": None, "at": 0.0, "data": {}}


def load_creds():
    """بيانات الدخول المتغيرة — قراءة حية عند كل محاولة (اللوحة تُحدّث
    الملف دون إعادة تشغيل الخدمة). كاش 5 ثوانٍ كحد أقصى."""
    with _creds_lock:
        try:
            mtime = CREDS_FILE.stat().st_mtime
        except OSError:
            mtime = None
        fresh = (mtime != _creds_cache["mtime"]) or (time.time() - _creds_cache["at"] > 5)
        if fresh:
            try:
                _creds_cache["data"] = json.loads(CREDS_FILE.read_text(encoding="utf-8"))
            except Exception:
                _creds_cache["data"] = {}
            _creds_cache["mtime"] = mtime
            _creds_cache["at"] = time.time()
        return _creds_cache["data"]


def creds_for(insurer):
    d = load_creds().get(insurer) or {}
    return {"user": d.get("user", ""), "pass": d.get("pass", ""), "url": d.get("url", "")}


def last_good_member(insurer):
    p = STATE_DIR / "heartbeat.json"
    try:
        return (json.loads(p.read_text(encoding="utf-8"))).get(insurer, "")
    except Exception:
        return ""


def save_last_good_member(insurer, member):
    p = STATE_DIR / "heartbeat.json"
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        if member:
            data[insurer] = member
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)
    except Exception:
        pass


class NeedsCaptcha(Exception):
    """الدخول يحتاج تحققًا بشريًا — الصورة تُرفع لشاشة الموظف عبر المهمة."""

    def __init__(self, image_b64):
        super().__init__("captcha_required")
        self.image_b64 = image_b64


class AuthLocked(Exception):
    """قفل كامل لتأمين واحد: رفض صريح ببيانات الدخول (كلمة المرور)
    أو رسالة القفل "Too many bad login attempts".
    لا تُجرى أي محاولة جديدة حتى تتغير بيانات الاعتماد في
    /etc/maxcall-insurers.json (تغيير كلمة المرور يفتح القفل تلقائياً)."""

    def __init__(self, reason):
        super().__init__(str(reason))
        self.reason = str(reason)


# ---------- سجل الأحداث الجنائي للجلسات + القفل ----------
SESSION_EVENTS_FILE = STATE_DIR / "session-events.jsonl"
AUTHLOCK_FILE = STATE_DIR / "authlock.json"
_LOCK_EVENTS = ("lock", "unlock")

# كاش في الذاكرة — يقرأ /health منه، ويُهيأ من السجل JSONL عند الإقلاع
_session_cache: dict[str, dict] = {}


def session_event(insurer, event, detail="", **fields):
    """سطر JSONL واحد لكل تبدل حالة — المصدر الوحيد لكل إحصائيات الجلسة."""
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "epoch": round(time.time(), 3),
           "insurer": insurer, "event": event,
           **({"detail": str(detail)[:300]} if detail else {}),
           **fields}
    try:
        import json as _json
        p = SESSION_EVENTS_FILE
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(_json.dumps(rec, ensure_ascii=False) + "\n")
        # تدوير عند تجاوز 2MB — لا نترك الملف يتضخم بلا نهاية
        try:
            if p.stat().st_size > 2 * 1024 * 1024:
                p.replace(p.with_suffix(p.suffix + ".1"))
        except OSError:
            pass
    except Exception:
        pass  # السجل تشخيصي — لا يكسر أي تدفق أبداً
    session_event_cache_apply(rec)
    return rec


def session_event_cache_apply(rec):
    try:
        insurer = rec.get("insurer")
        if not insurer:
            return
        c = _session_cache.setdefault(insurer, {})
        ev = rec.get("event")
        c["lastEvent"] = ev
        c["lastEventAt"] = rec.get("epoch")
        if ev == "ready":
            c["lastOk"] = rec.get("epoch")
        elif ev == "dead":
            c["lastDead"] = rec.get("epoch")
        elif ev in ("restored", "restored_cold"):
            c["lastRestore"] = rec.get("epoch")
            c["restoresTotal"] = int(c.get("restoresTotal", 0)) + 1
            c["lastOk"] = rec.get("epoch")
        elif ev == "lock":
            c["locked"] = True
            c["lockReason"] = rec.get("detail") or ""
            c["lockAt"] = rec.get("epoch")
        elif ev == "unlock":
            c["locked"] = False
            c["lockReason"] = None
    except Exception:
        pass


def session_cache_view(insurer):
    c = dict(_session_cache.get(insurer, {}))
    if c.get("locked") is None:
        c["locked"] = bool(auth_locked(insurer))
    return c


def _creds_stamp():
    """بصمة ملف الاعتمادات — تغيير كلمة المرور يغيرها ⇒ فتح القفل."""
    try:
        import hashlib
        data = CREDS_FILE.read_bytes()
        return f"{CREDS_FILE.stat().st_mtime_ns}:{hashlib.sha1(data).hexdigest()}"
    except OSError:
        return "missing"


def _read_locks():
    try:
        return json.loads(AUTHLOCK_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_locks(d):
    try:
        AUTHLOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = AUTHLOCK_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        tmp.replace(AUTHLOCK_FILE)
    except Exception:
        pass


def auth_locked(insurer):
    """هل التأمين مقفل؟ ويفتح تلقائياً إذا تغيرت بيانات الاعتماد."""
    locks = _read_locks()
    d = locks.get(insurer)
    if not d:
        return False
    if d.get("stamp") != _creds_stamp():
        # كلمة المرور تغيرت — افتح القفل واستأنف
        try:
            del locks[insurer]
            _write_locks(locks)
        except Exception:
            pass
        session_event(insurer, "unlock", "creds changed — lock auto-cleared")
        return False
    return True


def auth_lock_reason(insurer):
    d = _read_locks().get(insurer) or {}
    return str(d.get("reason") or "") if auth_locked(insurer) else ""


def set_auth_lock(insurer, reason):
    stamp = _creds_stamp()
    locks = _read_locks()
    locks[insurer] = {"reason": str(reason)[:300], "stamp": stamp,
                      "locked_at": round(time.time(), 3)}
    _write_locks(locks)
    c = _session_cache.setdefault(insurer, {})
    c["locked"] = True
    c["lockReason"] = str(reason)[:300]
    c["lockAt"] = locks[insurer]["locked_at"]
    session_event(insurer, "lock", str(reason)[:300])


def _load_event_tail():
    """هيئ كاش /health من آخر 400 سطر من السجل بعد إقلاع الخدمة."""
    try:
        p = SESSION_EVENTS_FILE
        if not p.exists():
            return
        lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()[-400:]
        for line in lines:
            try:
                session_event_cache_apply(json.loads(line))
            except Exception:
                continue
    except Exception:
        pass


_load_event_tail()


class NeedsChoice(Exception):
    """يحتاج الموظف أن يختار من قائمة (مثلاً: اسم طبيب غير مطابق في HIS)."""

    def __init__(self, options, title, hint=""):
        super().__init__("choice_required")
        self.options = [str(o) for o in (options or [])]
        self.title = str(title or "اختر من القائمة")
        self.hint = str(hint or "")


class BackendError(Exception):
    pass


class Backend:
    """واجهة خلف التأمين: دورة عمل واحدة، الحالة من الـDOM دائماً."""

    id = ""

    def run(self, w, kind="doc", member=None, card=None):
        """ملء/تحقق/طباعة — يعيد قاموس النتيجة أو يرفع BackendError/NeedsCaptcha."""
        raise NotImplementedError

    @staticmethod
    def report_matches(url):
        return False


class Worker:
    """بيئة واحدة دائمة (سياق + تبويب) تخدم كل مهام تأمين واحد — بلا
    نافذة/تبويب إضافي لكل طلب. البوب-أب عند الطباعة يُلتَق دائماً إلى
    مستمع الاستجابات، ويُغلق بعد الالتقاط أو يُهمَل في وضع discard."""

    JOB_DEADLINE_S = 150.0
    KEEPALIVE_DEADLINE_S = 90.0

    def __init__(self, insurer, backend, playwright, log):
        self.insurer = insurer
        self.backend = backend
        self.pw = playwright
        self.log = log
        self.ctx = None
        self.page = None
        self.needs_context = False
        self.current_job = None
        self._report_hit = None    # أول استجابة مطابقة للنمط أثناء انتظارها
        self.waiting_report = False
        self.suppress_popups = False
        self._started_at = 0.0
        # حالات dialogs أصلية WebForms (alert/confirm) — تُقرأ أثناء المهمة
        self._already_printed = False
        self._wrong_captcha = False
        self._provider_submit_at = 0.0   # لحظة إرسال نموذج الكابتشا (فترة سماح)
        self._captcha_verdict = None     # من استجابة ValidateUserWithCaptcha: ok/fail/None

    # ---------------- إدارة السياق ----------------
    def ensure_context(self):
        """سياق دائم يُبنى مرة واحدة; إن كُسر (أعطال/مغادرة) يُعاد."""
        if self.ctx and not self.needs_context:
            return
        if self.ctx:
            try:
                self.ctx.close()
            except Exception:
                pass
            self.ctx = None
        profile = STATE_DIR / f"profiles/{self.insurer}"
        profile.mkdir(parents=True, exist_ok=True)
        self.ctx = self.pw.chromium.launch_persistent_context(
            str(profile),
            headless=True,
            accept_downloads=True,
            args=[
                "--disable-gpu",
                "--disable-dev-shm-usage",
                "--no-first-run",
                "--disable-first-run-ui",
                "--disable-features=Translate,BackForwardCache",
            ],
        )
        # اعترض window.open على كل الإطارات قبل أي سكربت موقعي —
        # ADF تطلق الطباعة عبر window.open وتنقذ الوسيطة؛ نعترضها
        # بدل الاعتماد على سلوك النافذة (لا تظهر فعلاً في headless)
        self.ctx.add_init_script("""
        (() => {
          if (window.__mcrOpenHook) return;
          window.__mcrOpenHook = true;
          window.open = function (u) {
            try {
              if (u) {
                window.__MCR_OPEN_URL = String(u);
                window.__MCR_OPEN_AT = Date.now();   // مطبوعة جديدة — للسنة!
              }
            } catch (e) {}
            return { focus() {}, close() {}, self: window,
                     location: { href: String(u || '') } };
          };
        })();
        """)
        self.page = self.ctx.pages[0] if self.ctx.pages else self.ctx.new_page()
        self.page.set_default_timeout(30000)
        self._hook(self.page)
        self.ctx.on("page", self._on_popup)
        self.needs_context = False
        self.log.info(f"[{self.insurer}] context ready profile={profile}")

    # ---------------- dialogs أصلية (WebForms alert/confirm) ----------------
    # WebForms يصل رسائل مثل "Action Failed / Enter wrong captcha!" عبر
    # window.alert — لا تصل إلى DOM. بدون هذا المستمع كانت المهمة ترى
    # "لا شيء حدث" وتفشل بصمت.
    def _on_dialog(self, dialog):
        try:
            msg = (dialog.message or "").strip()
            self.log.info(f"[{self.insurer}] dialog: {msg[:180]}")
            low = msg.lower()
            if "already printed" in low or "re-print" in low:
                self._already_printed = True
            elif "wrong captcha" in low or "invalid captcha" in low:
                self._wrong_captcha = True
            dialog.accept()
        except Exception:
            try:
                dialog.dismiss()
            except Exception:
                pass

    def _on_popup(self, page):
        try:
            if self.suppress_popups:
                page.close()
                return
            page.set_default_timeout(30000)
            self._hook(page)
            self.log.info(f"[{self.insurer}] popup attached {page.url[:80]}")
        except Exception:
            pass

    def _hook(self, page):
        page.on("response", self._on_response)
        page.on("download", self._on_download)
        page.on("dialog", self._on_dialog)
        page.on("console", self._on_console)
        page.on("requestfailed", self._on_requestfailed)

    def _on_download(self, dl):
        try:
            if not self.waiting_report or self._report_hit is not None:
                return
            url = dl.url or ""
            if self.backend.report_matches(url):
                self._report_hit = {"kind": "download", "url": url, "dl": dl}
                self.log.info(f"[{self.insurer}] report download {url[:120]}")
            else:
                self.log.info(f"[{self.insurer}] download (ignored) {url[:110]}")
        except Exception:
            pass

    # ---------------- أدوات تشخيص (تبقى في السيرفر) ----------------
    def _on_console(self, msg):
        try:
            t = msg.type
            if t in ("error", "warning"):
                text = (msg.text or "").replace("\n", " ")[:160]
                self.log.info(f"[{self.insurer}] console.{t}: {text}")
        except Exception:
            pass

    def _on_requestfailed(self, req):
        try:
            self.log.info(f"[{self.insurer}] requestfailed: {req.method} {req.url[:110]}")
        except Exception:
            pass

    def _on_response(self, resp):
        try:
            url = resp.url or ""
            host_ok = ("nlicgulf.com" in url) or ("health.livainsurance.om" in url)
            interesting = ("pdf" in url.lower() or "application/pdf" in
                           (resp.headers.get("content-type") or "").lower()
                           or "viewer" in url.lower() or "servlet" in url.lower()
                           or "print" in url.lower())
            if host_ok:
                # سجّل فقط الطلبات الموجهة لواجهة Liva — URL + status (بلا أجسام)
                ct = (resp.headers.get("content-type") or "").split(";")[0]
                self.log.info(f"[{self.insurer}] net {resp.status} {url[:120]}")
                if "ValidateUserWithCaptcha" in url:
                    try:
                        body = (resp.body() or b"")[:180].decode("utf-8", "ignore")
                        self.log.info(f"[{self.insurer}] captcha-check body={body}")
                        low = body.lower()
                        if any(k in low for k in ('"result":true', '"success":true', '"issuccess":true')):
                            self._captcha_verdict = "ok"
                        elif any(k in low for k in ('"result":false', '"success":false',
                                                   '"issuccess":false',
                                                   'wrong captcha', 'invalid')):
                            self._captcha_verdict = "fail"
                    except Exception:
                        pass
            elif interesting:
                self.log.info(
                    f"[{self.insurer}] report? {(resp.headers.get('content-type') or 'n/a').split(';')[0]} {url[:120]}")
            if self.backend.report_matches(url) or "application/pdf" in (resp.headers.get("content-type") or "").lower():
                if self.waiting_report and self._report_hit is None:
                    self.log.info(f"[{self.insurer}] report response captured")
                    self._report_hit = {"kind": "response", "url": url, "resp": resp}
        except Exception:
            pass

    # ---------------- مساعدات قياس (منقولة حرفياً من الإضافة) ----------------
    @staticmethod
    def _report_bytes(hit):
        """أكمل التقاط التقرير لتصبير: نوع response (body مباشرة) أو
        نوع download (المسار المؤقت بعد الاكتمال)."""
        try:
            if hit.get("kind") == "response":
                return hit["resp"].body()
            dl = hit.get("dl")
            if not dl:
                return b""
            path = dl.path()
            from pathlib import Path as _P
            return _P(path).read_bytes() if path else b""
        except Exception:
            return b""

    def q1(self, sel):
        try:
            return self.page.query_selector(sel)
        except Exception:
            return None

    def visible(self, handle):
        if not handle:
            return False
        try:
            return bool(handle.evaluate(
                "el => !!el && el.getClientRects().length > 0 && !el.disabled && el.readOnly !== true"
            ))
        except Exception:
            return False

    def set_value(self, handle, value):
        """نفس setNativeValue: حالة ملكية value الأصلي من HTMLInputElement
        prototype + حدثا input/change فقاعياً — التوقيع المُثبت على ADF."""
        handle.evaluate(
            """(el, v) => {
                const d = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value');
                if (d && d.set) d.set.call(el, String(v));
                else el.value = String(v);
                el.dispatchEvent(new Event('input', {bubbles: true}));
                el.dispatchEvent(new Event('change', {bubbles: true}));
            }""",
            str(value),
        )

    def click(self, handle):
        try:
            handle.evaluate("el => el.click()")
            return True
        except Exception:
            return False

    def trusted_click(self, handle):
        """نقرة CDP حقيقية بالإحداثيات — تُفعّل النوافذ المنبثقة والمعالجات
        التي ترفض النقر الاصطناعي."""
        try:
            box = handle.bounding_box()
            if not box or box.get("width", 0) <= 0 or box.get("height", 0) <= 0:
                return False
            self.page.mouse.click(box["x"] + box["width"] / 2,
                                  box["y"] + box["height"] / 2)
            return True
        except Exception:
            return False

    def wait_for(self, js_pred, timeout_s=20, poll_s=0.3):
        try:
            self.page.wait_for_function(
                js_pred, timeout=int(timeout_s * 1000), polling=int(poll_s * 1000)
            )
            return True
        except Exception:
            return False

    def sleep(self, s=0.3):
        time.sleep(s)

    def remaining(self, kind="doc"):
        limit = self.KEEPALIVE_DEADLINE_S if kind == "keepalive" else self.JOB_DEADLINE_S
        return max(0.0, limit - (time.monotonic() - self._started_at))

    # ---------------- التقدم والتقاط التقرير ----------------
    def progress(self, step, status, detail=""):
        job = self.current_job
        if job:
            job.add_stage(step, status, detail)
        self.log.info(f"[{self.insurer}] {step}:{status} {detail}".strip())

    def capture_report(self, find_control, timeout_s=25, discard=False):
        """انقر زر الطباعة مرة (مع إعادة إيجاده إن أعاد ADF بناء الصف،
        دون نقرات إضافية أثناء انتظار الاستجابة) ثم التقط أي:
        (أ) window.open — نفترضها ونطلبها بالجلسات أنفسها؛
        (ب) download — تُقرأ من المسار المؤقت؛
        (ج) HTTP response — body مباشرة. discard=True: تُهمَل وبوب-أب يُغلق."""
        import time

        deadline = time.monotonic() + timeout_s
        self._report_hit = None
        self.waiting_report = True
        self.suppress_popups = bool(discard)
        clicked = False
        try:
            # امنع التقاط عنوان "معلّق" من طباعة سابقة — الطباعة الجديدة
            # فقط تستحق أن تُرسل (هذا هو مصدر خطأ مستند المريض السابق)
            for frame in [self.page, *self.page.frames]:
                try:
                    frame.evaluate(
                        "() => { window.__MCR_OPEN_URL = ''; window.__MCR_OPEN_AT = 0; }")
                except Exception:
                    pass
            while time.monotonic() < deadline:
                if not clicked:
                    ctl = find_control()
                    if ctl and self.click(ctl):
                        clicked = True
                if self._report_hit is not None:
                    hit = self._report_hit
                    return self._finish_hit(hit, discard)
                # (أ) interception side: window.open argument from the page
                url = self._open_url()
                if url:
                    self._report_hit = {"kind": "url", "url": url}
                    return self._finish_hit(self._report_hit, discard)
                time.sleep(0.25)
            return {"ok": False,
                    "error": "print_control_missing" if not clicked else "report_not_captured"}
        finally:
            self.waiting_report = False
            self.suppress_popups = False
            self._report_hit = None

    def _open_url(self):
        for frame in [self.page, *self.page.frames]:
            try:
                url, at = frame.evaluate(
                    """() => [String(window.__MCR_OPEN_URL || "").trim(),
                              Number(window.__MCR_OPEN_AT || 0)]""")
            except Exception:
                continue
            if not url or url in ("", "about:blank"):
                continue
            if (time.time() * 1000) - at > 30000:
                continue            # طباعة قديمة — تجاهل
            self.log.info(f"[{self.insurer}] window.open intercepted {url[:120]}")
            return url
        return None

    def _finish_hit(self, hit, discard):
        url = hit.get("url", "")
        if discard:
            return {"ok": True, "url": url, "bytes": None}
        if hit.get("kind") == "url":
            data = self._fetch_pdf(url)
        else:
            data = self._report_bytes(hit)
        if not data:
            return {"ok": False, "url": url, "error": "pdf_fetch_empty"}
        return {"ok": True, "url": url, "bytes": data}

    def _fetch_pdf(self, url):
        """جلب الرابط المعترض بنفس الكوكيز (جلسة واحدة) — لا نافذة، لا popup.
        نسبي ⇒ يُلحق على صفحة المصدر الحالية (ASP.NET يرمي نوافذ هذه التقارير نسبياً)."""
        import urllib.parse
        abs_url = url
        if not abs_url.lower().startswith(("http://", "https://")):
            abs_url = urllib.parse.urljoin(self.page.url or "", url)
        try:
            resp = self.ctx.request.get(abs_url, timeout=60000)
            ct = (resp.headers.get("content-type") or "").lower()
            if resp.status not in (200,):
                self.log.info(f"[{self.insurer}] pdf fetch http_{resp.status} ct={ct}")
                if resp.status in (405, 302, 301):
                    resp = self.ctx.request.get(url, timeout=60000,
                                                headers={"Accept": "application/pdf,*/*"},
                                                max_redirects=5)
            ok = 200 <= int(resp.status or 0) < 300
            size = len(resp.body() or b"")
            self.log.info(
                f"[{self.insurer}] pdf fetch http_{resp.status} ct={(resp.headers.get('content-type') or '').split(';')[0]} size={size}")
            return resp.body() if ok else b""
        except Exception as e:
            self.log.info(f"[{self.insurer}] pdf fetch failed {str(e)[:120]}")
            return b""
