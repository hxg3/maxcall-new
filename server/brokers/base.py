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

    def _on_response(self, resp):
        try:
            if not self.waiting_report or self._report_hit is not None:
                return
            url = resp.url or ""
            ct = (resp.headers.get("content-type") or "").lower()
            interesting = ("pdf" in url.lower() or "application/pdf" in ct
                           or "viewer" in url.lower() or "servlet" in url.lower()
                           or "print" in url.lower())
            if interesting:
                self.log.info(
                    f"[{self.insurer}] report? {(ct or 'n/a').split(';')[0]} {url[:120]}")
            if self.backend.report_matches(url) or "application/pdf" in ct:
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
            try:
                self.page.evaluate(
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
        try:
            url, at = self.page.evaluate(
                """() => [String(window.__MCR_OPEN_URL || "").trim(),
                          Number(window.__MCR_OPEN_AT || 0)]""")
            if not url or url in ("", "about:blank"):
                return None
            if (time.time() * 1000) - at > 30000:
                return None            # طباعة قديمة — تجاهل
            self.log.info(f"[{self.insurer}] window.open intercepted {url[:120]}")
            return url
        except Exception:
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
