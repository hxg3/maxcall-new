(() => {
  // نافذة سؤال الموظف داخل صفحة المشفى (HIS).
  //
  // تستخدمها شركة Liva: صورة الكابتشا (يحلّها الموظف) أو قائمة أسماء
  // للاختيار (اسم الطبيب). النافذة تظهر داخل صفحة المشفى نفسها — الموظف
  // لا يترك شغله ولا يفتح نافذة الموقع.
  //
  // الرسائل: "ui:ask" (اعرض) و"ui:askClear" (أغلق).

  const STYLE = `
    .mcr-ask{position:fixed;inset-inline-end:18px;bottom:18px;z-index:2147483647;direction:ltr;
      font-family:"Segoe UI",Tahoma,sans-serif;font-size:13px;color:#e2e8f0;background:#0f172af5;
      border:1px solid #334155;border-radius:14px;box-shadow:0 14px 36px #0007;overflow:hidden;
      width:320px;max-width:calc(100vw - 32px)}
    .mcr-ask-h{display:flex;align-items:center;justify-content:space-between;gap:8px;
      padding:10px 14px;border-bottom:1px solid #1e293b;font-weight:600;color:#f8fafc}
    .mcr-ask-x{cursor:pointer;border:none;background:none;color:#64748b;font-size:15px;line-height:1;padding:2px}
    .mcr-ask-x:hover{color:#94a3b8}
    .mcr-ask-x:focus-visible{outline:2px solid #38bdf8;outline-offset:2px;border-radius:4px}
    .mcr-ask-b{padding:12px 14px 14px;display:flex;flex-direction:column;gap:10px}
    .mcr-ask-img{background:#fff;border-radius:10px;padding:8px;display:flex;justify-content:center;min-height:40px}
    .mcr-ask-img img{max-width:100%;border-radius:6px}
    .mcr-ask-in{width:100%;border:1px solid #334155;border-radius:9px;padding:9px 11px;font:inherit;
      background:#0b1220;color:#e2e8f0;direction:ltr;text-align:center;letter-spacing:2px}
    .mcr-ask-in:focus{outline:2px solid #38bdf8;border-color:#38bdf8}
    .mcr-ask-list{max-height:220px;overflow:auto;display:flex;flex-direction:column;gap:2px;
      border:1px solid #1e293b;border-radius:10px;padding:6px}
    .mcr-ask-opt{text-align:start;background:none;border:none;color:#cbd5e1;font:inherit;cursor:pointer;
      padding:7px 9px;border-radius:7px}
    .mcr-ask-opt:hover,.mcr-ask-opt:focus-visible{background:#1e293b;color:#f8fafc;outline:none}
    .mcr-ask-note{font-size:11.5px;color:#94a3b8;line-height:1.6}
    .mcr-ask-count{min-width:34px;height:24px;padding:0 8px;display:inline-flex;
      align-items:center;justify-content:center;border-radius:7px;font-size:12px;
      font-weight:700;letter-spacing:1px;color:#fbbf24;background:#1e293b;
      border:1px solid #334155;font-variant-numeric:tabular-nums}
    .mcr-ask-count.low{color:#fca5a5;border-color:#7f1d1d;background:#2a1214}
    .mcr-ask-btn{cursor:pointer;border:none;border-radius:9px;padding:9px 14px;font:inherit;font-weight:600;
      background:#0ea5e9;color:#fff}
    .mcr-ask-btn:hover{background:#0284c7}
    .mcr-ask-btn:focus-visible{outline:2px solid #7dd3fc;outline-offset:2px}
  `;

  let panel = null;

  function ensureStyle() {
    if (document.getElementById("mcr-ask-style")) return;
    const st = document.createElement("style");
    st.id = "mcr-ask-style";
    st.textContent = STYLE;
    document.documentElement.appendChild(st);
  }

  function hide() {
    if (panel) {
      panel.remove();
      panel = null;
    }
  }

  function answer(requestId, value) {
    try {
      chrome.runtime.sendMessage({ type: "insurer:askAnswer", requestId, value });
    } catch (e) { /* extension context gone */ }
  }

  function build({ requestId, kind, title, image, options, placeholder, hint, countdownSeconds }) {
    hide();
    ensureStyle();

    const root = document.createElement("div");
    root.className = "mcr-ask";
    root.setAttribute("role", "dialog");
    root.setAttribute("aria-modal", "false");
    root.setAttribute("aria-label", title || "MaxCare");

    const head = document.createElement("div");
    head.className = "mcr-ask-h";
    const hTitle = document.createElement("span");
    hTitle.textContent = title || "MaxCare";
    const x = document.createElement("button");
    x.className = "mcr-ask-x";
    x.type = "button";
    x.title = "Close";
    x.setAttribute("aria-label", "Close");
    x.textContent = "✕";
    x.addEventListener("click", () => {
      answer(requestId, "");
      hide();
    });

    // سباق تنازلي للكابتشا — انتهى يختفي تلقائياً (الجهاز الأول) والشريك التالي يلتقطه
    let countEl = null, countTimer = null;
    const total = Number(countdownSeconds || 0);
    if (total > 0) {
      countEl = document.createElement("span");
      countEl.className = "mcr-ask-count";
      countEl.textContent = String(total);
      let left = total;
      countTimer = setInterval(() => {
        left -= 1;
        if (!root.isConnected) { clearInterval(countTimer); return; }
        if (left <= 0) {
          countEl.textContent = "0";
          countEl.classList.add("low");
          clearInterval(countTimer);
          // لا تُرسل شيئاً — الرخصة تنتهي على السيرفر وتنتقل للجهاز التالي؛
          // الإرسال بإجابة فارغة كان سيُجهض المهمة للجميع
          setTimeout(hide, 900);
          return;
        }
        countEl.textContent = String(left);
        if (left <= 5) countEl.classList.add("low");
      }, 1000);
    }
    if (countEl) { head.append(hTitle, countEl, x); } else { head.append(hTitle, x); }

    const body = document.createElement("div");
    body.className = "mcr-ask-b";

    if (hint) {
      const note = document.createElement("div");
      note.className = "mcr-ask-note";
      note.textContent = hint;
      body.appendChild(note);
    }

    let input = null;
    if (kind === "captcha") {
      const box = document.createElement("div");
      box.className = "mcr-ask-img";
      if (image) {
        const img = document.createElement("img");
        img.src = image;
        img.alt = "Captcha";
        img.addEventListener("error", () => {
          // CSP في صفحة المشفى قد يمنع=data: — نطلب إظهار نافذة البوابة
          const note = document.createElement("div");
          note.className = "mcr-ask-note";
          note.textContent = "Image blocked here — the insurer window was opened; type the code there.";
          body.insertBefore(note, body.firstChild);
          try {
            chrome.runtime.sendMessage({ type: "insurer:askImageBlocked", requestId });
          } catch (e) { /* ignore */ }
        });
        box.appendChild(img);
      } else {
        const span = document.createElement("span");
        span.style.cssText = "color:#64748b;font-size:12px";
        span.textContent = "Captcha image unavailable — the insurer window was opened; type the code there.";
        box.appendChild(span);
        try {
          chrome.runtime.sendMessage({ type: "insurer:askImageBlocked", requestId });
        } catch (e) { /* ignore */ }
      }
      body.appendChild(box);

      input = document.createElement("input");
      input.className = "mcr-ask-in";
      input.type = "text";
      input.autocomplete = "off";
      input.placeholder = placeholder || "Captcha code";
      input.setAttribute("aria-label", placeholder || "Captcha code");
      input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          answer(requestId, input.value.trim());
          hide();
        }
      });
      body.appendChild(input);
    } else {
      const list = document.createElement("div");
      list.className = "mcr-ask-list";
      list.setAttribute("role", "group");
      list.setAttribute("aria-label", title || "Options");
      for (const opt of options || []) {
        const b = document.createElement("button");
        b.className = "mcr-ask-opt";
        b.type = "button";
        b.textContent = opt;
        b.addEventListener("click", () => {
          answer(requestId, opt);
          hide();
        });
        list.appendChild(b);
      }
      if (!(options || []).length) {
        const empty = document.createElement("div");
        empty.className = "mcr-ask-note";
        empty.textContent = "No options available.";
        list.appendChild(empty);
      }
      body.appendChild(list);
    }

    const actions = document.createElement("div");
    actions.style.cssText = "display:flex;gap:8px;justify-content:flex-end";
    const cancel = document.createElement("button");
    cancel.className = "mcr-ask-btn";
    cancel.type = "button";
    cancel.style.cssText = "background:#1e293b;color:#cbd5e1";
    cancel.textContent = "Cancel";
    cancel.addEventListener("click", () => {
      answer(requestId, "");
      hide();
    });
    const ok = document.createElement("button");
    ok.className = "mcr-ask-btn";
    ok.type = "button";
    ok.textContent = "Continue";
    ok.addEventListener("click", () => {
      answer(requestId, input ? input.value.trim() : "");
      hide();
    });
    actions.append(cancel, ok);
    body.appendChild(actions);

    root.append(head, body);
    root.dataset.requestId = requestId || "";
    document.documentElement.appendChild(root);
    panel = root;
    if (input) input.focus();
    else {
      const first = body.querySelector(".mcr-ask-opt");
      if (first) first.focus();
    }
  }

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg?.type === "ui:ask") {
      // الخلفية تُغلّف السؤال في msg.request، ومعرّف الطلب في msg.requestId
      build({ requestId: msg.requestId, ...(msg.request || {}) });
      sendResponse({ ok: true });
    } else if (msg?.type === "ui:askClear") {
      hide();
      sendResponse({ ok: true });
    }
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && panel) {
      answer(panel.dataset.requestId || "", "");
      hide();
    }
  });
})();
