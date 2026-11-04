// التقاط رابط تقرير الطباعة من عالم الصفحة (MAIN world).
//
// مواقع التأمين تفتح التقرير في نافذة/تبويب جديد غالباً عبر window.open
// (وأحياناً form بـtarget=_blank). سكربت المحتوى يعمل في عالم معزول ولا يمكنه
// اعتراض دوال الصفحة — لذلك هذا الملف يُحقن في عالم الصفحة نفسه
// (manifest: world=MAIN) قبل أي سكربت من الموقع، ويُسلّم الرابط لجزء
// الإضافة عبر سمة على <html>.
//
// لا يتدخل إطلاقاً إلا عندما يكون مساعد الاستقبال "مسلّحاً"
// (data-mcr-arm=1) أي أثناء نقر زر الطباعة من المحوّل — فلا يؤثر على
// الاستخدام اليدوي للموقع.
//
// لكل موقع قاعدة التقاط: نمط الرابط المقبول + هل نمنع فتح النافذة؟
// (Liva: نمط رابط التقرير غير موثّق → نقبل أي رابط يُفتح أثناء الطباعة).

(() => {
  if (window.__mcrReportHook) return;
  window.__mcrReportHook = true;

  const RULES = {
    "mednetapprovals.com": {
      re: /pdfdisplayservlet|ReportsViewer|\.pdf(\?|$)/i,
      suppress: true,
    },
    "nlicgulf.com": {
      re: /./,
      suppress: true,
    },
  };
  const entry = Object.keys(RULES)
    .map((host) => [host, RULES[host]])
    .find(([host]) => location.hostname === host || location.hostname.endsWith("." + host));
  const RULE = entry ? entry[1] : { re: /pdf|report|print|download|\.pdf(\?|$)/i, suppress: true };
  const REPORT_RE = RULE.re;

  // ---- قناة أوامر عالم الصفحة (content ↔ MAIN) ----
  // سكربت المحتوى المعزول لا يرى مكتبات الموقع — عمليات تريد القناع نفسه
  // (رقم الهاتف inputmask مثلاً) تُنفّذ هنا حتى يتوافق ما في الحقل مع
  // ما يقرأه السيرفر عند الإرسال تماماً كما تكتب يدك.
  function execOp(op, a) {
    if (op === "gsmSet") {
      const gsm = String((a && a[0]) || "").replace(/\D/g, "");
      const el = document.querySelector("#txtGsmNo");
      if (!el) return { ok: false, err: "no_field" };
      const $ = window.jQuery || window.$;
      const hasMask = !!($ && $.fn && $.fn.inputmask);
      const full = "968-" + gsm;
      if (hasMask) {
        // عبر القناع نفسه: تعبئة الأرقام الصالحة، وإن رفض القناع
        // (كما مع prefix) نجرب الصيغة الكاملة
        try { $(el).inputmask("setvalue", gsm); } catch (e) { /* ignore */ }
        if (String(el.value || "") !== full) {
          try { $(el).inputmask("setvalue", full); } catch (e) { /* ignore */ }
        }
      } else if ($) {
        try { $(el).val(full); } catch (e) { /* ignore */ }
      }
      return {
        ok: gsm.length === 8 && String(el.value || "") === full,
        val: String(el.value || ""),
        mask: hasMask,
      };
    }
    return { ok: false, err: "unknown_op" };
  }
  window.addEventListener("message", (ev) => {
    if (ev.source !== window) return;
    const d = ev.data;
    if (!d || d.__mcrCmd !== true) return;
    let r;
    try {
      r = execOp(String(d.op || ""), d.args);
    } catch (e) {
      r = { ok: false, err: String((e && e.message) || e) };
    }
    try { window.postMessage({ __mcrRes: true, id: d.id, r }, "*"); } catch (e) { /* ignore */ }
  });

  const armed = () => {
    try {
      return document.documentElement.getAttribute("data-mcr-arm") === "1";
    } catch (e) {
      return false;
    }
  };

  function publish(url) {
    let s = String(url || "").trim();
    if (!s) return;
    // رابط نسبي → مطلق (يسهّل السجل والمطابقة)
    try {
      if (!/^[a-z][a-z0-9+.-]*:/i.test(s)) s = new URL(s, location.href).href;
    } catch (e) {
      return;
    }
    if (!/^https?:/i.test(s)) return;
    // إطار فرعي؟ مرّر الرابط للإطار الأعلى حيث يعمل المحوّل
    try {
      if (window.top && window.top !== window) {
        window.top.postMessage({ __mcrReport: s }, "*");
        return;
      }
    } catch (e) { /* إطار مغلق */ }
    try {
      document.documentElement.setAttribute("data-mcr-report-url", s);
      document.documentElement.dispatchEvent(new CustomEvent("mcr-report", { detail: s }));
    } catch (e) { /* ignore */ }
  }

  // نافذة بديلة تُعاد للموقع بدل النافذة الحقيقية — حتى لا تتعطل سكربتاته.
  // location بـsetter يعترض الطريقة الشائعة: open('') ثم window.location = url
  function stubWindow(url) {
    const state = { href: String(url || "") };
    const obj = {
      closed: false,
      name: "",
      opener: window,
      focus() {}, blur() {}, close() {}, print() {}, postMessage() {},
      moveTo() {}, resizeTo() {}, scrollTo() {},
      document: {
        write() {}, writeln() {}, close() {}, open() { return this; },
        get location() { return obj.location; },
      },
    };
    Object.defineProperty(obj, "location", {
      get() {
        return {
          get href() { return state.href; },
          set href(v) { assign(v); },
          replace(v) { assign(v); },
          assign(v) { assign(v); },
          reload() {},
          toString() { return state.href; },
        };
      },
      set(v) { assign(v); },
    });
    function assign(v) {
      const s = typeof v === "string" ? v : (v && v.href) || "";
      if (!s) return;
      state.href = s;
      if (REPORT_RE.test(s)) publish(s);
    }
    return obj;
  }

  function diag(msg) {
    try {
      window.top.postMessage({ __mcrDiag: String(msg).slice(0, 900) }, "*");
    } catch (e) { /* ignore */ }
  }

  // تنصت الشبكة أثناء التسليح: استجابات POST لمسارات Reports — نلخصها
  // (text/json أول 600 حرف) وننشر أي رابط PDF/URL داخلها. هذا ما يكشف
  // كيف يولّد الموقع مستند الاستمارة فعلياً.
  const NET_RE = /\/Reports\/|report|print|claim/i;
  function sniffNet() {
    if (window.__mcrNetHook) return;
    window.__mcrNetHook = true;

    const share = (status, url, method, ct, snippet) => {
      diag(`net ${method} ${url} → ${status} ${ct || ""} ${(snippet || "").slice(0, 600)}`);
      // رابط المستند داخل الاستجابة — Liva: الحقل FilePath (مسار نسبي
      // مثل ‎/ProviderAPI/Documents/.../PatientInfo_xxx.PDF)
      try {
        const s = snippet || "";
        let hit = null;
        const fp = s.match(/"(?:FilePath|filePath|PdfPath|ReportPath)"\s*:\s*"([^"]+)"/);
        if (fp && fp[1]) hit = fp[1];
        if (!hit) {
          const m = s.match(
            /https?:\/\/[^\s"'\\]+|(?:[\/\w\-.]+)\/[\w\-./]+\.pdf[a-z0-9?=&.]*/i
          );
          if (m) hit = m[0].replace(/\\+$/, "");
        }
        if (hit) publish(hit);
      } catch (e) { /* ignore */ }
    };

    const nativeFetch = window.fetch;
    window.fetch = function (input, init) {
      const url =
        typeof input === "string" ? input : (input && input.url) || "";
      const method = String(
        (init && init.method) || (input && input.method) || "GET"
      );
      const p = nativeFetch.apply(this, arguments);
      if (armed() && /^POST$/i.test(method) && NET_RE.test(String(url))) {
        p.then(async (r) => {
          let ct = "";
          let snippet = "";
          try {
            ct = r.headers.get("content-type") || "";
            if (/json|text/i.test(ct)) snippet = (await r.clone().text()).slice(0, 700);
            else {
              const b = await r.clone().blob();
              snippet = `[${b.size} bytes non-text]`;
            }
          } catch (e) { /* ignore */ }
          share(r.status, url, method.toUpperCase(), ct, snippet);
        }).catch((e) => diag(`net POST ${url} → ERR ${String(e).slice(0, 120)}`));
      }
      return p;
    };

    const oOpen = XMLHttpRequest.prototype.open;
    const oSend = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.open = function (m, u) {
      try { this.__mcrNet = { m: String(m), u: String(u) }; } catch (e) { /* ignore */ }
      return oOpen.apply(this, arguments);
    };
    XMLHttpRequest.prototype.send = function (b) {
      const info = this.__mcrNet;
      if (armed() && info && /^POST$/i.test(info.m) && NET_RE.test(info.u)) {
        this.addEventListener("load", () => {
          let ct = "";
          let snippet = "";
          try {
            ct = this.getResponseHeader("Content-Type") || "";
            const rt = this.responseType;
            if (!rt || rt === "text" || rt === "json") {
              snippet = String(rt === "json" ? JSON.stringify(this.response) : this.responseText || "").slice(0, 700);
            } else if (rt === "blob") snippet = `[blob ${(this.response && this.response.size) || 0}b]`;
            else if (rt === "arraybuffer") snippet = `[arraybuffer ${(this.response && this.response.byteLength) || 0}b]`;
          } catch (e) { /* ignore */ }
          share(this.status, info.u, info.m, ct, snippet);
        });
      }
      return oSend.apply(this, arguments);
    };
  }
  sniffNet();

  const nativeOpen = window.open;
  let sink = null;

  window.open = function (url, ...rest) {
    try {
      const s = url == null ? "" : String(url);
      if (armed()) {
        if (s && REPORT_RE.test(s)) {
          publish(s);
          if (RULE.suppress) return stubWindow(s);
          return nativeOpen.apply(window, [url, ...rest]);
        }
        // نافذة فارغة يكتب فيها الموقع نموذجاً ثم يرسله: أعطه iframe مخفياً
        // حقيقياً داخل الصفحة (نفس الأصل) — لا بوب أب، والنموذج يعمل كما هو.
        if (!s || s === "about:blank") return sinkWindow();
      }
    } catch (e) { /* ignore */ }
    return nativeOpen.apply(window, [url, ...rest]);
  };

  function getSink() {
    if (sink && sink.isConnected) return sink;
    sink = document.createElement("iframe");
    sink.name = "mcr-report-sink";
    sink.style.cssText =
      "position:fixed;right:0;bottom:0;width:2px;height:2px;opacity:0.01;border:0;";
    (document.body || document.documentElement).appendChild(sink);
    return sink;
  }

  function sinkWindow() {
    return getSink().contentWindow;
  }

  // <a target="_blank" href="...">
  document.addEventListener(
    "click",
    (e) => {
      if (!armed()) return;
      const a = e.target && e.target.closest ? e.target.closest("a[href]") : null;
      if (!a) return;
      const href = a.href || "";
      if (!REPORT_RE.test(href)) return;
      if (a.target && a.target !== "_self" && a.target !== "_top") {
        if (RULE.suppress) {
          e.preventDefault();
          e.stopPropagation();
        }
        publish(href);
      }
    },
    true
  );

  // form target=_blank → حوّله إلى iframe داخل الصفحة بدل فتح نافذة
  function sinkName() {
    return getSink().name;
  }
  document.addEventListener(
    "submit",
    (e) => {
      if (!armed()) return;
      const f = e.target;
      if (!f || f.tagName !== "FORM") return;
      const t = String(f.target || "").toLowerCase();
      if (t && t !== "_self" && t !== "_top" && t !== "_parent") {
        try { f.target = sinkName(); } catch (err) { /* ignore */ }
      }
    },
    true
  );
})();
