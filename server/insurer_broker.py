#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""وساطة الاستقبال — استخراج مستندات التأمين على السيرفر (Playwright).

النموذج (تمت الموافقة عليه):
• جهاز الموظف عميل رفيع: يقرأ المتغيرات من HIS، يطلب، يتقبل PDF، يعرضه للطباعة.
• عامل دائم لكل تأمين (سياق Playwright واحد يُبنى مرة، يُعاد عند العطل فقط).
• طابور تسلسلي لكل تأمين — تضارب الكابتشا يذوب بالتصميم: دخول واحد في
  نفس اللحظة لكل تأمين، فلا كابتشا مزدوجة أبدًا.
• مهلة كابتشا 15 ث لكل جهاز (lease) ثم التنقل للجهاز الآخر الذي يعرض
  المؤشر؛ الإجابة الأولى تُقبل والبقية تُرفض برسالة موحّدة.
• النبضة فقط عند عدم جري طلب حقيقي خلال 4 دقائق: تحقق حقيقي بآخر رقم
  عضوية ناجح لكل تأمين (نجاحًا المُثبت من تجربة 15 دقيقة ×3 مواقع).
• بيانات الدخول حية من /etc/maxcall-insurers.json (اللوحة تعيد كتابتها).

الواجهة HTTP داخلية 127.0.0.1:3011 — app.py الوازِع هو من يعرضها
للموظفين (الوصول محدود LAN بدل تسجيل دخول لكل موظف).
"""

import base64
import logging
import sys
import threading
import time
import uuid
from collections import deque
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from flask import Flask, jsonify, request

from brokers.base import (
    BackendError, NeedsCaptcha, NeedsChoice, last_good_member, STATE_DIR,
)
from brokers.mednet import MedNet
from brokers.nextcare import NextCare
from brokers.liva import Liva

try:
    from playwright.sync_api import sync_playwright
    HAS_PW = True
except Exception:  # pragma: no cover
    HAS_PW = False


BROKER_PORT = 3011
CAPTCHA_LEASE_S = 30.0      # مهلة عادلة — المستخدم يقرأ الكابتشا بسرعة
CHOICE_LEASE_S = 75.0        # مهلة اختيار عنصر من قائمة (طبيب) — أطول للقراءة
CAPTCHA_TOTAL_S = 300.0      # الحد الكلي — خمس دقائق مهلة عادلة
HEARTBEAT_WINDOW_S = 240.0   # نافذة نبض 4 دقائق
KEEPALIVE_MAX_S = 90.0

log = logging.getLogger("broker")
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)-5s %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")

app = Flask(__name__)

_jobs = {}
_jobs_lock = threading.Lock()

INSURERS = {"mednet": MedNet, "nextcare": NextCare, "liva": Liva}


def _uuid():
    return uuid.uuid4().hex[:12]


class Job(dict):
    """سجل مهمة واحد للطلاء الأول والاحتمالات التقدمية للمستخدم."""

    def add_stage(self, step, status, detail=""):
        self.setdefault("stages", []).append({
            "step": step, "status": status, "detail": str(detail)[:200],
            "ts": round(time.time()),
        })


class InsurerWorker(threading.Thread):
    """عامل دائم لكل تأمين: طابور تسلسلي + Playwright داخل هذا الخيط."""

    def __init__(self, insurer_id, backend_c):
        super().__init__(name=f"worker-{insurer_id}", daemon=True)
        self.insurer = insurer_id
        self.backend_c = backend_c
        self.backend = backend_c()
        self.q = deque()
        self.cv = threading.Condition()
        self.worker = None          # brokers.base.Worker
        self.last_activity = 0.0
        self.running_job_id = None
        self.cap = None             # {img, token, until, answer, event}
        self.cap_lock = threading.Lock()
        self.stop = threading.Event()

    # ---------------- الطابور ----------------
    def push(self, job):
        with self.cv:
            self.q.append(job["id"])
            self.cv.notify()
            return len(self.q)

    def next_job(self):
        with self.cv:
            while not self.q and not self.stop.is_set():
                self.cv.wait(5)
            return None if self.stop.is_set() else self.q.popleft()

    # ---------------- الحياة ----------------
    def run(self):
        if not HAS_PW:
            log.error(f"[{self.insurer}] playwright missing — worker idle")
            return
        self.pw = sync_playwright().start()
        from brokers.base import Worker
        self.worker = Worker(self.insurer, self.backend, self.pw, log)
        log.info(f"[{self.insurer}] worker started")
        while not self.stop.is_set():
            jid = self.next_job()
            if not jid:
                continue
            with _jobs_lock:
                job = _jobs.get(jid)
            if job:
                self.execute(job)

    def execute(self, job):
        self.running_job_id = job["id"]
        kind = job.get("kind") or "doc"
        job["state"] = "running"
        self.log_job(job, "info", f"start kind={kind}")
        try:
            self.worker.ensure_context()
            self.worker.current_job = job
            out = self.backend.run(
                self.worker, kind=kind,
                member=job.get("member"), card=job.get("card"),
            )
            self.finish(job, out)
        except NeedsCaptcha as e:
            self.human_checkpoint(
                job, kind,
                {"type": "captcha", "img_b64": e.image_b64,
                 "lease": CAPTCHA_LEASE_S})
        except NeedsChoice as e:
            self.human_checkpoint(
                job, kind,
                {"type": "choice", "options": e.options,
                 "title": e.title, "hint": e.hint,
                 "lease": CHOICE_LEASE_S})
        except BackendError as e:
            self.fail(job, str(e) or "backend_error")
        except Exception as e:
            self.worker.needs_context = True
            import traceback
            self.fail(job, "unexpected_error",
                      backend_detail=f"{type(e).__name__}: {e}\n{traceback.format_exc()[-600:]}")
        finally:
            self.worker.current_job = None
            self.running_job_id = None
            self.last_activity = time.time()

    def finish(self, job, out):
        out = out or {}
        pdf_bytes = out.pop("pdf_bytes", None)     # لا تُلبث raw bytes في result
        if pdf_bytes:
            job["pdf_b64"] = base64.b64encode(pdf_bytes).decode("ascii")
        job["result"] = out
        job["state"] = "done"
        self.log_job(job, "info", "done")

    def fail(self, job, msg, backend_detail=""):
        job["state"] = "error"
        job["error"] = msg
        if backend_detail:
            job["backendDetail"] = backend_detail
        self.log_job(job, "error", msg)

    # ---------------- تحقق بشري: كابتشا/اختيار — عقد إيجار + تنقّل بين الأجهزة ----------------
    def human_checkpoint(self, job, kind, prompt):
        """تحقق بشري: الطلب يُعرض على الجهاز الذي يرصد المهمة (عقد إيجار
        لكل جهاز؛ عند انتهائه ينتقل للجهاز التالي). عند قبول الإدخال الأول
        يُستأنف العمل من الحالة الحية نفسها (النموذج لا يبدأ من الصفر).
        prompt: {type: "captcha"|"choice", img_b64?|options?, title?, hint?, lease}"""
        prompt = dict(prompt or {})
        with self.cap_lock:
            self.cap = {"prompt": prompt,
                        "img": prompt.get("img_b64", ""),
                        "lease": prompt.get("lease", CAPTCHA_LEASE_S),
                        "token": None, "until": 0.0,
                        "answer": None, "event": threading.Event()}
        job["state"] = "captcha"
        job["captcha_type"] = prompt.get("type", "captcha")
        job.add_stage("captcha", "run", "بانتظار التحقق البشري")
        deadline = time.monotonic() + CAPTCHA_TOTAL_S
        fresh = getattr(self.backend, "fresh_captcha_image", None)
        last_refresh = 0.0
        answer = None
        while time.monotonic() < deadline:
            if self.cap is None:
                break  # مسح خارجي (أمان)
            # حدّث صورة الكابتشا من DOM كل ~2 ثانية — البوابة تجددها
            # كل دقيقة؛ الاحتفاظ بصورة قديمة = إجابة تُرفض دائماً
            now = time.monotonic()
            if fresh and now - last_refresh > 2.0:
                last_refresh = now
                try:
                    img = fresh(self.worker)
                    if img:
                        self.cap["img"] = img
                except Exception:
                    pass
            answer = self.cap["answer"] if self.cap["event"].wait(2) else None
            if answer is not None:
                break
        with self.cap_lock:
            self.cap = None
        if answer is None:
            self.fail(job, "لم يكتمل التحقق البشري",
                      backend_detail="captcha_timeout")
            return
        if str(answer).strip() == "":
            self.fail(job, "أُلغيت العملية من الموظف",
                      backend_detail="human_cancelled")
            return
        job.add_stage("captcha", "ok", "تم قبول الإدخال — استئناف العمل")
        resume = getattr(self.backend, "resume_after_captcha", None)
        # مرّر نوع الطلب للخلفية — تقرر معه كيف تستأنف
        self.worker._pending_prompt = prompt
        try:
            self.worker.current_job = job
            out = (
                resume(self.worker, answer, kind=kind,
                       member=job.get("member"), card=job.get("card"))
                if resume
                else self.backend.run(self.worker, kind=kind,
                                      member=job.get("member"),
                                      card=job.get("card"))
            )
            self.finish(job, out)
        except NeedsCaptcha as e:
            self.human_checkpoint(
                job, kind,
                {"type": "captcha", "img_b64": e.image_b64,
                 "lease": CAPTCHA_LEASE_S})
        except NeedsChoice as e:
            self.human_checkpoint(
                job, kind,
                {"type": "choice", "options": e.options,
                 "title": e.title, "hint": e.hint,
                 "lease": CHOICE_LEASE_S})  # صورة جديدة — كرّر
        except BackendError as e:
            self.fail(job, str(e) or "backend_error")
        except Exception as e:
            self.worker.needs_context = True
            import traceback
            self.fail(job, "unexpected_error",
                      backend_detail=f"{type(e).__name__}: {e}\n{traceback.format_exc()[-600:]}")

    def on_captcha_answer(self, token, text):
        with self.cap_lock:
            cap = self.cap
            if not cap or cap["token"] != token or time.time() >= cap["until"]:
                return False, "انتهت مهلة التدقيق — تحقق انتقل لجهاز آخر"
            cap["answer"] = text
            cap["event"].set()
            return True, ""

    def claim_captcha(self):
        """أي جهاز يعرض المؤشر يستطيع استطلاع الرخصة: أول جهاز يحجز;
        إذا انتهت عادت الرخصة فينتقل لها الجهاز الآخر تلقائياً.
        الإدخال يعمل فقط برمز عقد صالح — لا تداخل بين جهازين."""
        with self.cap_lock:
            cap = self.cap
            if not cap:
                return None
            if cap["until"] > time.time():
                return {"busy": True}
            prompt = cap.get("prompt") or {}
            lease = cap.get("lease", CAPTCHA_LEASE_S)
            cap["token"] = _uuid()
            cap["until"] = time.time() + lease
            out = {"token": cap["token"],
                   "type": prompt.get("type", "captcha"),
                   "img_b64": cap.get("img", ""),      # الصورة الطازجة من DOM
                   "lease": lease}
            if prompt.get("type") == "choice":
                out["options"] = prompt.get("options") or []
                out["title"] = prompt.get("title") or "اختر من القائمة"
                out["hint"] = prompt.get("hint") or ""
            return out

    def log_job(self, job, lvl, detail):
        log.log(logging.INFO if lvl == "info" else logging.WARNING,
                f"[{self.insurer}] job={job['id'][:6]} {job.get('kind', 'doc')} {detail}")

    # ---------------- النبضة: آخر نجاح بلا طلب حقيقي 4 دقائق ----------------
    def keepalive_loop(self):
        while not self.stop.is_set():
            time.sleep(30)
            if not HAS_PW:
                continue
            member = last_good_member(self.insurer)
            if not member and not getattr(self.backend,
                                          "keepalive_without_member", False):
                continue
            if self.last_activity and time.time() - self.last_activity < HEARTBEAT_WINDOW_S:
                continue
            if self.q or self.running_job_id:
                continue  # نشاط حقيقي الآن — لا حاجة للنبضة
            jid = _uuid()
            job = Job(id=jid, insurer=self.insurer, kind="keepalive",
                      member=member, state="queued",
                      created=round(time.time()), stages=[], result={})
            with _jobs_lock:
                _jobs[jid] = job
            self.push(job)
            log.info(f"[{self.insurer}] heartbeat queued job={jid[:6]} member={member}")


workers = {}
for _id, _cls in INSURERS.items():
    _w = InsurerWorker(_id, _cls)
    _w.start()
    threading.Thread(target=_w.keepalive_loop, name=f"hb-{_id}", daemon=True).start()
    workers[_id] = _w


# ---------------- تنظيف ملفات التصفح (استقرار السيرفر) ----------------
# المحتلة: ماتبقى ذاكرة التخزين المؤقت, وليس ملف الجلسة الكوكيز أبداً.
CACHE_DIRS = ["Cache", "Code Cache", "GPUCache", "DawnGraphiteCache",
              "DawnWebGPUCache", "Service Worker/CacheStorage", "ShaderCache"]


def cleanup_loop():
    while True:
        time.sleep(6 * 3600)
        try:
            if any(w.q or w.running_job_id for w in workers.values()):
                continue  # مشغول — خطوة بطيئة (لا نتكية إلتقاط التابِع)
            freed = 0
            for w in workers.values():
                prof = STATE_DIR / f"profiles/{w.insurer}"
                for c in CACHE_DIRS:
                    d = prof / c
                    if not d.is_dir():
                        continue
                    for p in d.rglob("*"):
                        try:
                            freed += p.stat().st_size
                            p.unlink()
                        except (OSError, FileNotFoundError):
                            pass
            log.info(f"cleanup done freed={freed // 1024}KB")
        except Exception as e:
            log.warning(f"cleanup failed {e}")


threading.Thread(target=cleanup_loop, name="cleanup", daemon=True).start()


# ---------------- الواجهة (app.py يعرضها للموظفين) ----------------
def _job_view(j, include_pdf=False):
    worker = workers.get(j["insurer"])
    out = {
        "id": j["id"], "insurer": j["insurer"], "kind": j.get("kind", "doc"),
        "state": j["state"], "error": j.get("error", ""),
        "backendDetail": j.get("backendDetail", ""),
        "stages": j.get("stages", [])[-12:],
        "queuePos": 0, "result": j.get("result") or {},
    }
    if worker:
        with worker.cv:
            try:
                out["queuePos"] = list(worker.q).index(j["id"]) + 1
            except ValueError:
                pass
    if include_pdf and j.get("pdf_b64"):
        out["pdf_b64"] = j["pdf_b64"]
    if j["state"] == "captcha" and worker:
        claim = worker.claim_captcha()
        if claim and claim.get("token"):
            out["captcha"] = claim
        elif claim:
            out["captcha"] = {"busy": True}
    return out


@app.post("/job")
def create_job():
    d = request.get_json(silent=True) or {}
    insurer = str(d.get("insurer", "")).strip()
    if insurer not in workers:
        return jsonify({"ok": False,
                        "error": "التأمين غير مدعوم على السيرفر بعد"}), 404
    jid = _uuid()
    job = Job(id=jid, insurer=insurer, kind=d.get("kind") or "doc",
              member=str(d.get("member") or "").strip(),
              card=d.get("card") or {}, state="queued",
              created=round(time.time()), stages=[], result={})
    with _jobs_lock:
        _jobs[jid] = job
    queue_pos = workers[insurer].push(job)
    log.info(f"[{insurer}] job queued id={jid[:6]} member={job['member'] or '-'} "
             f"queue={queue_pos}")
    return jsonify({"ok": True, "jobId": jid, "queuePos": queue_pos})


@app.get("/job/<jid>")
def get_job(jid):
    with _jobs_lock:
        j = _jobs.get(jid)
    if not j:
        return jsonify({"ok": False, "error": "مهمة غير معروفة"}), 404
    want_pdf = request.args.get("pdf") == "1"
    return jsonify({"ok": True, **_job_view(j, include_pdf=want_pdf)})


@app.post("/job/<jid>/answer")
def answer_captcha(jid):
    with _jobs_lock:
        j = _jobs.get(jid)
        worker = workers.get((j or {}).get("insurer"))
    if not j or not worker:
        return jsonify({"ok": False, "error": "مهمة غير معروفة"}), 404
    d = request.get_json(silent=True) or {}
    ok, err = worker.on_captcha_answer(str(d.get("token", "")),
                                       str(d.get("text", "")))
    if ok:
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": err}), 409


@app.get("/health")
def health():
    out = {}
    for iid, w in workers.items():
        out[iid] = {
            "alive": w.is_alive(),
            "busy": bool(w.running_job_id),
            "queue": len(w.q),
            "lastActivity": round(w.last_activity) or None,
            "sessionWarm": bool(w.last_activity),
            "captchaPending": bool(w.cap),
        }
    return jsonify({"ok": True, **out})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=BROKER_PORT, threaded=True, debug=False)
