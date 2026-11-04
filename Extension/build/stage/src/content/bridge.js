(() => {
  if (window.__Bridge) return;

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  // هل هذا المحتوى ملف PDF فعلاً؟ (بعض السيرفرات تُرجع صفحة خطأ/تسجيل دخول بترميز 200)
  async function looksLikePdf(blob) {
    try {
      const head = new Uint8Array(await blob.slice(0, 1024).arrayBuffer());
      for (let i = 0; i < head.length - 4; i++) {
        if (head[i] === 0x25 && head[i + 1] === 0x50 && head[i + 2] === 0x44 && head[i + 3] === 0x46) {
          return true; // %PDF
        }
      }
      return false;
    } catch (e) {
      return false;
    }
  }

  function isVisible(el) {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    const st = getComputedStyle(el);
    return st.visibility !== "hidden" && st.display !== "none";
  }

  // رابط PDF داخل صفحة HTML — بعض المواقع تفتح صفحة فيها زر/رابط للملف
  async function findPdfLink(blob, baseUrl) {
    let html = "";
    try {
      html = await blob.text();
    } catch (e) {
      return null;
    }
    const re = /(?:href|src|url)\s*[=:]\s*["']([^"']+)["']/gi;
    let m;
    while ((m = re.exec(html))) {
      const raw = m[1].trim();
      if (!raw || raw.startsWith("#") || raw.startsWith("javascript:")) continue;
      if (!/pdf|print|report|claim|document|download|generate/i.test(raw)) continue;
      try {
        const abs = new URL(raw, baseUrl).href;
        const res = await fetch(abs, { credentials: "include", cache: "no-store" });
        if (!res.ok) continue;
        const b = await res.blob();
        if (await looksLikePdf(b)) return abs;
      } catch (e) { /* try the next candidate */ }
    }
    return null;
  }

  // ADF/WebForms wrappers listen to input/change — set via the native
  // prototype setter so Angular/jQuery validation notices the change too.
  function setNativeValue(el, value) {
    const proto = el instanceof HTMLSelectElement ? HTMLSelectElement.prototype
      : el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype
      : HTMLInputElement.prototype;
    const desc = Object.getOwnPropertyDescriptor(proto, "value");
    if (desc && desc.set) desc.set.call(el, value);
    else el.value = value;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }

  async function waitFor(fn, { timeout = 15000, poll = 250 } = {}) {
    const start = Date.now();
    while (Date.now() - start < timeout) {
      try {
        const v = fn();
        if (v) return v;
      } catch (e) { /* keep polling */ }
      await sleep(poll);
    }
    return null;
  }

  function progress(step, status, detail) {
    try {
      chrome.runtime.sendMessage({ type: "adapter:progress", step, status, detail });
    } catch (e) { /* extension context gone */ }
  }

  // سجل تشخيصي مباشر من المحوّل إلى سجل الإضافة (popup → Copy log).
  // يُستخدم عند غياب الإشارات: نسجّل ما في الصفحة فعلاً بدل التخمين.
  function diag(area, msg, extra) {
    try {
      chrome.runtime.sendMessage({ type: "diag:log", area: area || "adapter", msg, extra });
    } catch (e) { /* extension context gone */ }
  }

  // ------------------------------------------------------------------
  // التقاط مستند الطباعة
  // mednet-capture.js (عالم الصفحة) يعترض window.open / target=_blank ويكتب
  // الرابط على <html data-mcr-report-url>. هنا نلتقط الرابط من أي إطار،
  // نرسله للخلفية لتجلبه بكوكيز الجلسة وتطبعه في صفحة الموظف — بلا نوافذ جديدة.
  // ------------------------------------------------------------------
  const ARMED_ATTR = "data-mcr-arm";
  const URL_ATTR = "data-mcr-report-url";

  function isArmed() {
    return document.documentElement.getAttribute(ARMED_ATTR) === "1";
  }

  window.addEventListener("message", (e) => {
    const d = e && e.data;
    if (!d) return;
    if (typeof d.__mcrDiag === "string" && d.__mcrDiag) {
      // تشخيص من عالم الصفحة (الأحداث/الاستجابات أثناء التسليح) → السجل
      try {
        chrome.runtime.sendMessage(
          { type: "diag:log", area: "page", msg: d.__mcrDiag },
          () => {
            void chrome.runtime.lastError;
          }
        );
      } catch (e2) { /* ignore */ }
      return;
    }
    const u = d.__mcrReport;
    if (typeof u !== "string" || !u) return;
    if (!isArmed()) return;
    document.documentElement.setAttribute(URL_ATTR, u);
  });

  // ------------------------------------------------------------------
  // سؤال الموظف — من صفحة التأمين إلى صفحة المشفى
  // (المستخدم يحلّ الكابتشا أو يختار اسماً من قائمة داخل HIS)
  // ------------------------------------------------------------------
  const pendingAsks = new Map();

  function askUser(request, { timeout = 180000 } = {}) {
    const requestId =
      "mcr-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 8);
    return new Promise((resolve) => {
      let done = false;
      const finish = (value) => {
        if (done) return;
        done = true;
        pendingAsks.delete(requestId);
        resolve(value);
      };
      pendingAsks.set(requestId, finish);
      try {
        chrome.runtime.sendMessage({ type: "insurer:ask", requestId, request }, (res) => {
          void chrome.runtime.lastError;
          // ok =reach question reached the HIS page — we now wait for the answer
          if (!res?.ok) finish({ error: res?.reason || "unavailable" });
        });
      } catch (e) {
        finish({ error: "unavailable" });
      }
      setTimeout(() => {
        if (done) return;
        // المستخدم لم يُجب — أغلق اللوحة في صفحة المشفى (بلا لوحات معلّقة)
        try {
          chrome.runtime.sendMessage({ type: "insurer:askCancel", requestId }, () => {
            void chrome.runtime.lastError;
          });
        } catch (e) { /* ignore */ }
        finish({ error: "timeout" });
      }, timeout);
    });
  }

  // التقاط تنقل يفتحه الموقع بـwindow.open عند نقرة (بطاقات بوابة Liva).
  // المتصفح يمنع window.open الآلي (نقرة غير حقيقية = بلا تفاعل مستخدم)،
  // فنلتقط الرابط ونُبحر بأنفسنا في نفس التبويب: لا نافذة محظورة ولا تبويب
  // ضائع. يُستخدم أيضاً لـ<a target=_blank> (يُعترض ويُلتقط).
  async function captureNav(opts = {}) {
    const root = document.documentElement;
    root.setAttribute(ARMED_ATTR, "1");
    root.removeAttribute(URL_ATTR);
    try {
      if (typeof opts.click === "function") opts.click();
      const url = await waitFor(
        () => {
          const u = root.getAttribute(URL_ATTR);
          return u && /^https?:/i.test(u) ? u : null;
        },
        { timeout: opts.timeout || 15000, poll: 120 }
      );
      return { ok: !!url, url: url || null };
    } finally {
      root.removeAttribute(ARMED_ATTR);
      root.removeAttribute(URL_ATTR);
    }
  }

  async function printReport(opts = {}) {
    // 1) سلّح الخلفية (webRequest) قبل النقر — تلتقط المسارات التي لا تمر بـwindow.open
    //    (يُتخطّى في وضع discard: نريد اعتراض الرابط وإهماله، لا جلبه وطباعته)
    if (!opts.discard) {
      await new Promise((resolve) => {
        try {
          chrome.runtime.sendMessage({ type: "insurer:armPrint", urlRe: opts.urlRe }, () => {
            void chrome.runtime.lastError;
            resolve();
          });
        } catch (e) { resolve(); }
      });
    }

    // 2) سلّح اعتراض الصفحة
    const root = document.documentElement;
    root.setAttribute(ARMED_ATTR, "1");
    root.removeAttribute(URL_ATTR);

    // 3) انقر زر الطباعة في الموقع
    const el = typeof opts.click === "function" ? opts.click() : null;
    if (!el) {
      root.removeAttribute(ARMED_ATTR);
      return { ok: false, error: "print_control_not_found" };
    }
    try { el.click(); } catch (e) { /* ignore */ }

    // 4) انتظر الرابط (حدثي — يُكتب لحظة اعتراضه)
    const url = await waitFor(() => root.getAttribute(URL_ATTR), {
      timeout: opts.timeout || 20000,
      poll: 150,
    });
    // 5) أنهِ التسليح حتى لا يتأثر أي استخدام يدوي لاحق للموقع
    root.removeAttribute(ARMED_ATTR);

    // وضع الإهمال: الرابط التقطناه فقط لتفادي فتح نافذة/تبويب — لا جلب ولا طباعة
    if (opts.discard) {
      root.removeAttribute(URL_ATTR);
      return { ok: !!url, url };
    }

    // 6) سلّمه للخلفية: الجلب + الطباعة في صفحة الموظف
    try {
      chrome.runtime.sendMessage({ type: "insurer:printReport", url: url || null }, () => {
        void chrome.runtime.lastError;
      });
    } catch (e) { /* ignore */ }

    if (url) {
      progress("print", "run");
      return { ok: true, url };
    }
    // لا رابط بعد — الخلفية تكمل عبر webRequest (form/target=_blank/تنزيل)
    progress("print", "run");
    return { ok: true, url: null };
  }

  // ------------------------------------------------------------------
  // overlay — floating status panel shown on the insurer page
  // ------------------------------------------------------------------
  const STYLE = `
    .mcr-ov{position:fixed;bottom:16px;inset-inline-start:16px;z-index:2147483647;
      direction:rtl;font-family:"Segoe UI",Tahoma,sans-serif;font-size:13px;color:#e2e8f0;
      background:#0f172aee;border:1px solid #334155;border-radius:14px;min-width:260px;max-width:340px;
      box-shadow:0 12px 32px #0006;backdrop-filter:blur(6px);overflow:hidden}
    .mcr-ov-head{display:flex;align-items:center;justify-content:space-between;
      padding:10px 14px;border-bottom:1px solid #1e293b;font-weight:600;color:#f8fafc}
    .mcr-ov-x{cursor:pointer;border:none;background:none;color:#64748b;font-size:15px;line-height:1;padding:2px}
    .mcr-ov-x:hover{color:#94a3b8}
    .mcr-ov-body{padding:10px 14px 14px;display:flex;flex-direction:column;gap:8px}
    .mcr-ov-row{display:flex;align-items:center;gap:8px;line-height:1.4}
    .mcr-ov-ic{width:16px;text-align:center;flex:none}
    .mcr-ov-spin{display:inline-block;width:11px;height:11px;border:2px solid #38bdf8;
      border-top-color:transparent;border-radius:50%;animation:mcr-spin .7s linear infinite}
    @keyframes mcr-spin{to{transform:rotate(360deg)}}
    .mcr-ov-ok{color:#34d399}.mcr-ov-err{color:#f87171}
    .mcr-ov-note{color:#94a3b8;font-size:12px}
    .mcr-ov-btn{cursor:pointer;border:none;border-radius:9px;padding:8px 14px;font-family:inherit;
      font-size:13px;font-weight:600}
    .mcr-ov-btn.primary{background:#0ea5e9;color:#fff}
    .mcr-ov-btn.primary:hover{background:#0284c7}
    .mcr-ov-btn.ghost{background:#1e293b;color:#cbd5e1}
    .mcr-ov-btn:disabled{opacity:.5;cursor:default}
    .mcr-ov-probe{max-height:200px;overflow:auto;font-size:11px;color:#cbd5e1;direction:ltr;text-align:left}
    .mcr-ov-probe div{padding:2px 0;border-bottom:1px solid #1e293b;white-space:nowrap}
    .mcr-ov-probe b{color:#7dd3fc;font-weight:600}
  `;

  let ov = null;
  function ensureOverlay() {
    if (ov && ov.root.isConnected) return ov;
    if (!document.getElementById("mcr-ov-style")) {
      const st = document.createElement("style");
      st.id = "mcr-ov-style";
      st.textContent = STYLE;
      document.documentElement.appendChild(st);
    }
    const root = document.createElement("div");
    root.className = "mcr-ov";
    root.innerHTML = `
      <div class="mcr-ov-head"><span class="mcr-ov-title"></span>
        <button class="mcr-ov-x" title="Close">✕</button></div>
      <div class="mcr-ov-body"></div>`;
    document.documentElement.appendChild(root);
    root.querySelector(".mcr-ov-x").addEventListener("click", () => root.remove());
    ov = { root, title: root.querySelector(".mcr-ov-title"), body: root.querySelector(".mcr-ov-body") };
    return ov;
  }

  function stepRow(text, kind) {
    const row = document.createElement("div");
    row.className = "mcr-ov-row";
    const ic = document.createElement("span");
    ic.className = "mcr-ov-ic";
    if (kind === "run") ic.innerHTML = '<span class="mcr-ov-spin"></span>';
    else if (kind === "ok") { ic.textContent = "✓"; ic.classList.add("mcr-ov-ok"); }
    else if (kind === "error") { ic.textContent = "✕"; ic.classList.add("mcr-ov-err"); }
    else ic.textContent = "•";
    const label = document.createElement("span");
    label.textContent = text;
    row.append(ic, label);
    return row;
  }

  function markDone(stepEl) {
    const ic = stepEl.querySelector(".mcr-ov-ic");
    if (ic) { ic.textContent = "✓"; ic.classList.add("mcr-ov-ok"); }
  }

  const overlay = {
    show(title) {
      const o = ensureOverlay();
      o.title.textContent = title || "Reception Assistant";
      o.body.textContent = "";
      return o;
    },
    step(text, kind = "run") {
      const o = ensureOverlay();
      const prev = o.body.querySelector('[data-run="1"]');
      if (prev) { prev.removeAttribute("data-run"); markDone(prev); }
      const row = stepRow(text, kind);
      if (kind === "run") row.setAttribute("data-run", "1");
      o.body.appendChild(row);
      return row;
    },
    note(text) {
      const o = ensureOverlay();
      const n = document.createElement("div");
      n.className = "mcr-ov-note";
      n.textContent = text;
      o.body.appendChild(n);
      return n;
    },
    error(msg) {
      const o = ensureOverlay();
      const prev = o.body.querySelector('[data-run="1"]');
      if (prev) { prev.removeAttribute("data-run"); markDone(prev); }
      o.body.appendChild(stepRow(msg, "error"));
    },
    result({ title, autoPrint, onPrint }) {
      const o = ensureOverlay();
      o.title.textContent = title || "المستند جاهز";
      o.body.textContent = "";
      o.body.appendChild(stepRow("Reached the insurance document", "ok"));
      const btn = document.createElement("button");
      btn.className = "mcr-ov-btn primary";
      btn.textContent = "Print";
      btn.addEventListener("click", () => { btn.disabled = true; window.print(); btn.disabled = false; });
      o.body.appendChild(btn);
      if (autoPrint) {
        const noteEl = o.note("Auto-print in 3s…");
        let left = 3;
        const t = setInterval(() => {
          left -= 1;
          if (left <= 0) { clearInterval(t); noteEl.remove(); window.print(); }
          else noteEl.textContent = `Auto-print in ${left}s… (close this panel to cancel)`;
        }, 1000);
      }
      if (onPrint) onPrint(btn);
    },
    probe(rows, hint) {
      const o = ensureOverlay();
      o.title.textContent = "Probe mode";
      o.body.textContent = "";
      if (hint) {
        const n = document.createElement("div");
        n.className = "mcr-ov-note";
        n.textContent = hint;
        o.body.appendChild(n);
      }
      const box = document.createElement("div");
      box.className = "mcr-ov-probe";
      for (const r of rows) {
        const d = document.createElement("div");
        d.innerHTML = r;
        box.appendChild(d);
      }
      o.body.appendChild(box);
    },
    close() { if (ov) ov.root.remove(); },
    body() { return ensureOverlay().body; },
  };

  // ------------------------------------------------------------------
  // probe — dump candidate fields/controls for building adapters
  // ------------------------------------------------------------------
  function labelFor(el) {
    const holder = el.closest("td,th,div,li,p") || el.parentElement;
    const txt = holder ? holder.textContent.trim().replace(/\s+/g, " ") : "";
    return txt.slice(0, 60);
  }

  function runProbe() {
    const rows = [];
    for (const el of document.querySelectorAll("input,select,textarea")) {
      if (!isVisible(el)) continue;
      const t = el.tagName.toLowerCase() + (el.type ? `[${el.type}]` : "");
      const id = el.id ? `id=<b>${el.id}</b>` : "";
      const nm = el.name ? `name=${el.name}` : "";
      rows.push(`${t} ${id} ${nm} ${el.placeholder ? `ph="${el.placeholder}"` : ""} «${labelFor(el)}»`);
    }
    for (const el of document.querySelectorAll("a,input[type=button],input[type=submit],button")) {
      if (!isVisible(el)) continue;
      const txt = (el.value || el.textContent || "").trim().replace(/\s+/g, " ").slice(0, 40);
      if (!txt && !el.id) continue;
      rows.push(`${el.tagName.toLowerCase()} ${el.id ? `id=<b>${el.id}</b>` : ""} "${txt}"`);
    }
    overlay.probe(rows.slice(0, 80), `${rows.length} candidate controls — copy this list to the developer`);
    console.table(rows);
    progress("probe", "ok", `${rows.length} items`);
  }

  // ------------------------------------------------------------------
  // action recorder — يسجل نقرات وتعبئات الموظف اليدوية (لرسم خريطة التدفق)
  // ------------------------------------------------------------------
  function describeEl(el) {
    if (!el || !el.tagName) return "?";
    const id = el.id ? `#${el.id}` : "";
    const nm = el.name ? `[name=${el.name}]` : "";
    const txt = (el.value || el.textContent || "").trim().replace(/\s+/g, " ").slice(0, 40);
    return `${el.tagName.toLowerCase()}${id}${nm}${txt ? ` "${txt}"` : ""}`;
  }

  function startXhrHook() {
    // حقن في عالم الصفحة: التقط bodies النداءات المهمة (Ajax.NET) عبر جسر DOM
    if (document.getElementById("mcr-xhr-hook")) return;
    const hook = document.createElement("script");
    hook.id = "mcr-xhr-hook";
    hook.textContent = `
      (function () {
        if (window.__mcrXhr) return; window.__mcrXhr = true;
        document.documentElement.setAttribute("data-mcr-hook", "1");
        var oOpen = XMLHttpRequest.prototype.open, oSend = XMLHttpRequest.prototype.send;
        XMLHttpRequest.prototype.open = function (m, u) {
          this.__mcr = u; return oOpen.apply(this, arguments);
        };
        XMLHttpRequest.prototype.send = function (b) {
          try {
            var u = String(this.__mcr || "");
            if (/ashx|aspx/i.test(u)) {
              var body = b == null ? "" : String(b);
              document.documentElement.setAttribute("data-mcr-xhr",
                JSON.stringify({ u: u.slice(0, 200), b: body.slice(0, 2000) }));
              document.documentElement.dispatchEvent(new Event("data-mcr-xhr-evt"));
            }
          } catch (e) {}
          return oSend.apply(this, arguments);
        };
      })();`;
    document.documentElement.appendChild(hook);
    hook.remove();
    const hookOk = document.documentElement.getAttribute("data-mcr-hook") === "1";
    console.log("%cMCR XHR hook:", "color:#8b5cf6", hookOk ? "active" : "BLOCKED (CSP)");
    try {
      chrome.runtime.sendMessage({
        type: "diag:log", area: "xhr",
        msg: hookOk ? "xhr hook active" : "xhr hook BLOCKED by page CSP",
      });
    } catch (e) {}
    document.documentElement.addEventListener("data-mcr-xhr-evt", () => {
      try {
        const raw = document.documentElement.getAttribute("data-mcr-xhr");
        if (!raw) return;
        const { u, b } = JSON.parse(raw);
        const msg = `[rec-xhr] POST ${u} body=${b}`;
        console.log("%cMCR XHR", "color:#8b5cf6", msg);
        try { chrome.runtime.sendMessage({ type: "diag:log", area: "xhr", msg }); } catch (e) {}
      } catch (e) {}
    });
  }

  function startRecording(seconds = 180) {
    startXhrHook();
    const until = Date.now() + seconds * 1000;
    const onClick = (e) => {
      if (Date.now() > until) {
        document.removeEventListener("click", onClick, true);
        document.removeEventListener("change", onChange, true);
        return;
      }
      const el = e.target.closest("a,button,input,li,[onclick],select") || e.target;
      const msg = `[rec] click ${describeEl(el)}`;
      console.log("%cMCR REC", "color:#0ea5e9", msg);
      try { chrome.runtime.sendMessage({ type: "diag:log", area: "rec", msg }); } catch (err) {}
    };
    const onChange = (e) => {
      if (Date.now() > until) {
        document.removeEventListener("click", onClick, true);
        document.removeEventListener("change", onChange, true);
        return;
      }
      const el = e.target;
      if (!el || !el.tagName || ["INPUT", "SELECT", "TEXTAREA"].indexOf(el.tagName) < 0) return;
      const val = el.type === "password" ? "***" : (el.value || "").slice(0, 50);
      const msg = `[rec] set ${describeEl(el)} = "${val}"`;
      console.log("%cMCR REC", "color:#0ea5e9", msg);
      try { chrome.runtime.sendMessage({ type: "diag:log", area: "rec", msg }); } catch (err) {}
    };
    document.addEventListener("click", onClick, true);
    document.addEventListener("change", onChange, true);
    progress("probe", "run", `recording ${seconds}s`);
  }

  chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    if (msg?.type === "insurer:run") {
      const adapter = window.__InsurerAdapter;
      if (!adapter) {
        sendResponse({ ok: false, reason: "no_adapter" });
        return;
      }
      // الرد فوراً — تعليق الاستجابة على اكتمال المحوّل كان يجمّد مهام الخلفية
      sendResponse({ ok: true, accepted: true });
      Promise.resolve()
        .then(() => adapter.run(msg, window.__Bridge))
        .catch((e) => {
          const detail = String((e && e.message) || e);
          progress("adapter", "error", detail);
          overlay.error(`Error: ${detail}`);
        });
      return;
    }
    if (msg?.type === "insurer:login") {
      const a = window.__InsurerAdapter;
      if (!a?.login) { sendResponse({ ok: false, reason: "no login" }); return; }
      Promise.resolve()
        .then(() => a.login(msg, window.__Bridge))
        .then(() => sendResponse({ ok: true }))
        .catch((e) => sendResponse({ ok: false, error: String((e && e.message) || e) }));
      return true;
    }
    if (msg?.type === "insurer:showDocument") {
      const a = window.__InsurerAdapter;
      if (a?.showDocument) {
        Promise.resolve()
          .then(() => a.showDocument(msg, window.__Bridge))
          .catch((e) => {
            progress("adapter", "error", String((e && e.message) || e));
          });
      }
      sendResponse({ ok: true });
      return;
    }
    if (msg?.type === "insurer:askAnswer") {
      // إجابة الموظف (كابتشا / اسم) تصل من صفحة المشفى عبر الخلفية
      const finish = pendingAsks.get(msg.requestId);
      if (finish) finish({ value: String(msg.value ?? "") });
      sendResponse({ ok: true });
      return;
    }
    if (msg?.type === "insurer:fetchReport") {
      // الخلفية تطلب الـPDF — الجلب من هنا يمر بكوكيز الجلسة تماماً كطلب صفحة.
      // التقرير يُولَّد على السيرفر بعد الطباعة بلحظات، لذلك نعيد المحاولة
      // (لا مهلة ثابتة واحدة) ونتحقق أن المحتوى PDF فعلاً لا صفحة خطأ.
      // Liva: بعض المسارات ترفض GET (http_405) — نعيد إرسال POST بجسمه
      // الأصلي المحفوظ في الخلفية.
      (async () => {
        let postBytes = null;
        let postKind = "json";
        try {
          if (msg.post?.body64) {
            postBytes = Uint8Array.from(atob(msg.post.body64), (c) => c.charCodeAt(0));
            postKind = msg.post.kind || "json";
          }
        } catch (e) { postBytes = null; }

        const tryRes = async (opts) => {
          const res = await fetch(msg.url, {
            credentials: "include",
            cache: "no-store",
            ...opts,
          });
          if (!res.ok) throw new Error("http_" + res.status);
          return res;
        };

        let lastErr = "unknown";
        for (let i = 0; i < 10; i++) {
          try {
            let res;
            try {
              res = await tryRes({});
            } catch (getErr) {
              if (!postBytes) throw getErr;
              // GET رُفض → نعيد إرسال POST الأصلي (نفس جسم الطلب الأصلي)
              res = await tryRes({
                method: "POST",
                headers: {
                  "Content-Type":
                    postKind === "json"
                      ? "application/json"
                      : "application/x-www-form-urlencoded",
                },
                body: postBytes,
              });
            }
            const blob = await res.blob();
            let pdf = blob;
            if (!(await looksLikePdf(blob))) {
              // ربما صفحة HTML فيها رابط PDF — نتبعه (بعض المواقع تفتح صفحة
              // تشرح المستند بدل الملف نفسه)
              const link = await findPdfLink(blob, msg.url);
              if (!link) {
                throw new Error("not_pdf:" + (res.headers.get("content-type") || "?"));
              }
              const res2 = await fetch(link, { credentials: "include", cache: "no-store" });
              if (!res2.ok) throw new Error("http_" + res2.status);
              pdf = await res2.blob();
              if (!(await looksLikePdf(pdf))) throw new Error("not_pdf:link");
            }
            const reader = new FileReader();
            const dataUrl = await new Promise((resolve, reject) => {
              reader.onload = () => resolve(reader.result);
              reader.onerror = reject;
              reader.readAsDataURL(pdf);
            });
            sendResponse({ ok: true, dataUrl });
            return;
          } catch (e) {
            lastErr = String((e && e.message) || e);
            await sleep(1200);
          }
        }
        sendResponse({ ok: false, error: lastErr });
      })();
      return true;
    }
    if (msg?.type === "insurer:print") {
      // الخلفية استعادت النافذة — استخدم دالة الطباعة الخاصة بالمحوّل إن وُجدت (PDF)، وإلا window.print
      setTimeout(() => {
        try {
          (window.__MCR_PRINT ? window.__MCR_PRINT() : window.print());
        } catch (e) { /* ignore */ }
      }, 100);
      sendResponse({ ok: true });
      return;
    }
    if (msg?.type === "insurer:probe") {
      runProbe();
      startRecording(180);
      sendResponse({ ok: true });
    }
  });

  // أوامر لعالم الصفحة (report-capture.js MAIN) — القناع/مكتبات الموقع
  function pageCmd(op, args = [], timeoutMs = 2500) {
    return new Promise((resolve) => {
      const id = "cmd" + Math.random().toString(36).slice(2, 9);
      const on = (ev) => {
        if (ev.source !== window || !ev.data || ev.data.__mcrRes !== true || ev.data.id !== id) return;
        window.removeEventListener("message", on);
        clearTimeout(t);
        resolve(ev.data.r || { ok: false, err: "no_result" });
      };
      const t = setTimeout(() => {
        window.removeEventListener("message", on);
        resolve({ ok: false, err: "timeout" });
      }, timeoutMs);
      window.addEventListener("message", on);
      window.postMessage({ __mcrCmd: true, id, op, args }, "*");
    });
  }

  window.__Bridge = {
    sleep, isVisible, setNativeValue, waitFor, progress,
    diag,
    pageCmd,
    captureNav,
    printReport,
    ask: askUser,
    overlay,
    probe: runProbe,
    fail(msg) { const e = new Error(msg); e.handled = true; throw e; },
  };

  try {
    chrome.runtime.sendMessage({
      type: "diag:log", area: "content",
      msg: "bridge ready", extra: location.href.slice(0, 120),
    });
  } catch (e) { /* context not ready */ }
})();
