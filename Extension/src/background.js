import { getSettings, insurerCreds } from "./lib/settings.js";
import { hisPatient } from "./lib/server.js";
import { INSURERS, detectInsurer } from "./lib/insurers.js";
import { log, getLogText } from "./lib/log.js";

const HIS_TAB_PATTERNS = ["http://solverserver:81/*", "http://192.168.1.77:81/*"];
const JOB_TTL_MS = 180000;
const jobs = new Map();        // tabId -> job state
const insWindows = new Map();  // insurerId -> dedicated (minimized) window id
const pendingAsks = new Map(); // requestId -> { tabId, at } — أسئلة الموظف
const pendingHandover = new Map(); // newTabId -> openerTabId (تسليم المهمة لتبويب جديد)
let printWatch = null;         // { tabId, urlRe, since } — مراقبة رابط التقرير
const handlingReports = new Set(); // tabIds قيد الجلب الآن (منع التكرار)
const queuedReportRef = new Map(); // tabId -> أحدث رابط بانتظار فراغ المعالجة
const REPORT_URL_RE = /pdfdisplayservlet|ReportsViewer|\.pdf(\?|$)/i;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  (async () => {
    try {
      switch (msg?.type) {
        case "his:patient":
          sendResponse(await hisPatient(msg.patId));
          break;
        case "his:detect":
          sendResponse(await detectActivePatient());
          break;
        case "insurer:run":
          sendResponse(await runInsurer(msg));
          break;
        case "job:status": {
          const job = jobs.get(msg.tabId);
          sendResponse(job ? { ok: true, job: publicJob(job) } : { ok: false, reason: "no_job" });
          break;
        }
        case "insurer:printHtml": {
          const html = msg.html || "";
          const tabs = await chrome.tabs.query({ url: HIS_TAB_PATTERNS }).catch(() => []);
          const userTab = tabs.find((t) => t.active) || tabs[0];
          if (!userTab) { sendResponse({ ok: false, reason: "no_his_tab" }); break; }
          try {
            const res = await chrome.scripting.executeScript({
              target: { tabId: userTab.id },
              world: "MAIN",
              args: [html],
              func: async (htmlStr) => {
                try {
                  const blob = new Blob([htmlStr], { type: "text/html" });
                  const url = URL.createObjectURL(blob);
                  const iframe = document.createElement("iframe");
                  iframe.style.cssText = "position:fixed;right:0;bottom:0;width:2px;height:2px;opacity:0.01;border:0;";
                  iframe.src = url;
                  document.body.appendChild(iframe);
                  await new Promise((r2) => { iframe.onload = r2; setTimeout(r2, 8000); });
                  iframe.contentWindow.print();
                  setTimeout(() => { iframe.remove(); URL.revokeObjectURL(url); }, 1000);
                  return { ok: true };
                } catch (e) {
                  return { ok: false, error: String((e && e.message) || e) };
                }
              },
            });
            sendResponse(res?.[0]?.result?.ok ? { ok: true } : { ok: false, reason: res?.[0]?.result?.error || "print failed" });
          } catch (e) {
            sendResponse({ ok: false, reason: String(e) });
          }
          break;
        }
        case "insurer:ask": {
          // المحوّل يسأل الموظف (كابتشا/اسم) — نعرض السؤال في صفحة المشفى
          const tabId = sender?.tab?.id ?? null;
          const job = tabId != null ? jobs.get(tabId) : null;
          if (tabId != null) {
            pendingAsks.set(msg.requestId, { tabId, at: Date.now() });
            // الموظف قد يحتاج دقائق — نمدّ حياة المهمة
            if (job) job.askUntil = Date.now() + 240000;
          }
          const tabs = await chrome.tabs.query({ url: HIS_TAB_PATTERNS }).catch(() => []);
          const hisTab = tabs.find((t) => t.active) || tabs[0];
          if (!hisTab) {
            await revealInsurerWindow(job);
            sendResponse({ ok: false, reason: "no_his_tab" });
            break;
          }
          const delivered = await new Promise((resolve) => {
            chrome.tabs.sendMessage(
              hisTab.id,
              { type: "ui:ask", requestId: msg.requestId, request: msg.request || {} },
              () => resolve(!chrome.runtime.lastError)
            );
          });
          if (!delivered) {
            await revealInsurerWindow(job);
            sendResponse({ ok: false, reason: "no_his_tab" });
            break;
          }
          sendResponse({ ok: true });
          break;
        }
        case "insurer:askImageBlocked": {
          // صورة الكابتشا لم تظهر في صفحة المشفى → نُظهر نافذة البوابة
          const rec = pendingAsks.get(msg.requestId);
          const job = rec?.tabId != null ? jobs.get(rec.tabId) : null;
          await revealInsurerWindow(job);
          sendResponse({ ok: true });
          break;
        }
        case "insurer:askAnswer": {
          // الجهاز الطلب يحل كابتشا الشعبية: وسيط (لا تبويب)
          const claim = brokerCaptchaClaims.get(msg.requestId);
          if (claim) {
            brokerCaptchaClaims.delete(msg.requestId);
            const ok = await submitCaptchaAnswer(msg.requestId, claim, msg.value ?? "");
            if (!ok) await log("warn", "broker captcha answer rejected", msg.requestId);
            sendResponse({ ok });
            break;
          }
          // إجابة الموظف من صفحة المشفى → تصل إلى تبويب التأمين
          const rec = pendingAsks.get(msg.requestId);
          pendingAsks.delete(msg.requestId);
          if (rec?.tabId) {
            chrome.tabs
              .sendMessage(rec.tabId, {
                type: "insurer:askAnswer",
                requestId: msg.requestId,
                value: msg.value ?? "",
              })
              .catch(() => {});
          }
          const tabs = await chrome.tabs.query({ url: HIS_TAB_PATTERNS }).catch(() => []);
          for (const t of tabs) {
            chrome.tabs.sendMessage(t.id, { type: "ui:askClear", requestId: msg.requestId }).catch(() => {});
          }
          sendResponse({ ok: true });
          break;
        }
        case "insurer:askCancel": {
          // انتهى انتظار الإجابة — أزل اللوحة من صفحة المشفى والسجّل الداخلي
          pendingAsks.delete(msg.requestId);
          const tabs = await chrome.tabs.query({ url: HIS_TAB_PATTERNS }).catch(() => []);
          for (const t of tabs) {
            chrome.tabs.sendMessage(t.id, { type: "ui:askClear", requestId: msg.requestId }).catch(() => {});
          }
          sendResponse({ ok: true });
          break;
        }
        case "insurer:armPrint": {
          // المحوّل سينقر الطباعة الآن — جهّز اعتراض الرابط عبر webRequest
          const tabId = sender?.tab?.id ?? null;
          printWatch = {
            tabId,
            urlRe: new RegExp(msg.urlRe || "pdfdisplayservlet|\\.pdf", "i"),
            since: Date.now(),
          };
          await log("info", "print watch armed", `tab=${tabId}`);
          // حارس: إن لم يصل أي رابط خلال دقيقة، أبلغ المستخدم بدل الصمت
          const armedAt = printWatch.since;
          setTimeout(() => {
            if (!printWatch || printWatch.since !== armedAt) return;
            const job = jobs.get(tabId);
            if (job && !job.reportHandled) {
              report(tabId, {
                step: "print", status: "error", detail: "no report link captured",
              });
            }
          }, 60000);
          sendResponse({ ok: true });
          break;
        }
        case "insurer:printReport": {
          // رابط التقرير وصل من عالم الصفحة (window.open / target=_blank)
          const tabId = sender?.tab?.id;
          sendResponse({ ok: true });
          if (msg.url) {
            handleReportUrl(tabId, msg.url).catch((e) =>
              log("error", "report handling failed", String(e).slice(0, 160))
            );
          }
          break;
        }
        case "diag:get":
          sendResponse({ ok: true, text: await getLogText() });
          break;
        case "diag:log":
          log(`content:${msg.area || "generic"}`, msg.msg, msg.extra);
          sendResponse({ ok: true });
          break;
        default:
          sendResponse({ ok: false, reason: "unknown_message" });
      }
    } catch (e) {
      await log("error", "background handler failed", String(e));
      sendResponse({ ok: false, reason: "background_error", detail: String(e) });
    }
  })();
  return true; // async response
});

// ---- adapter progress reporting (from content scripts) ----
chrome.runtime.onMessage.addListener((msg, sender) => {
  if (msg?.type === "adapter:progress" && sender.tab) {
    const tabId = sender.tab.id;
    const job = jobs.get(tabId);
    if (job) {
      job.lastProgress = msg;
      const ns = stageAfter(msg);
      // (لا نستخدم هنا لحظة login:ok كحدّ واقٍ — دخول VPN يتعارض مع
      // صفحة دخول المزوّد فيؤخر كابتشا المزوّد 20 ثانية زائدة؛ توقيت
      // الإرسال الحقيقي يُرصد في مستمع webRequest تحت job.provSubmitAt)
      // وضع الوكيل يبقى وكيلاً بعد الدخول (لا نرتد للتدفق الكامل)
      if (!(job.stage === "agent" && ns === "after_login")) {
        job.stage = ns ?? job.stage;
      }
      if (msg.status === "error") job.error = msg.detail || msg.step;
    }
    log("info", `adapter ${msg.step}:${msg.status}`, `${msg.detail || ""} url=${sender.tab.url?.slice(0, 90)}`);
    chrome.runtime.sendMessage({ ...msg, tabId }).catch(() => {});
    forwardProgressToHIS(msg);
    if (msg.step === "print" && msg.status === "run" && job) {
      // مرحلة الطباعة: أي تبويب/رابط جديد من تبويب المهمة هو التقرير
      job.awaitingPrint = true;
      job.awaitingPrintUntil = Date.now() + 120000;
    }
    if (msg.step === "document" && msg.status === "ok" && job) {
      job.awaitingPrint = true;
      setTimeout(() => { if (jobs.get(tabId) === job) job.awaitingPrint = false; }, 90000);
    }
    if (msg.step === "submitted" && msg.status === "ok" && job && job.insurerId === "nextcare") {
      startResultPolling(tabId, job);
    }
  }
});

// استقصاء النتيجة من الخلفية: مؤقتات التبويب المخفي يخنقها/يجمدها كروم،
// لذلك نستقصي بأنفسنا عبر scripting كل 800ms حتى يظهر زر الطباعة أو رسالة خطأ.
function startResultPolling(tabId, job) {
  const start = Date.now();
  const tick = async () => {
    if (!jobs.get(tabId) || Date.now() - start > 45000) return;
    try {
      const res = await chrome.scripting.executeScript({
        target: { tabId },
        func: () => {
          const btn = document.querySelector(
            '#btnPrintVerification, [id$="_btnPrintVerification"]'
          );
          if (btn && btn.offsetParent !== null) return { ready: true };
          const err = document.querySelector(
            ".swal2-html-container, .swal2-title, .field-validation-error, .validation-summary-errors"
          );
          if (err && err.offsetParent !== null && err.textContent.trim()) {
            return { error: err.textContent.trim().slice(0, 150) };
          }
          return { ready: false };
        },
      });
      const r = res?.[0]?.result;
      if (r?.ready) {
        log("info", "result detected (background polling)", `tab=${tabId}`);
        chrome.tabs.sendMessage(tabId, {
          type: "insurer:showDocument",
          autoPrint: job.autoPrint,
        }).catch(() => {});
        return;
      }
      if (r?.error) {
        log("warn", "site error during check", r.error);
        report(tabId, { step: "eligibility", status: "error", detail: r.error });
        return;
      }
    } catch (e) {
      // التبويب في منتصف تنقل — نكمل الاستقصاء
    }
    setTimeout(tick, 800);
  };
  setTimeout(tick, 800);
}

// نافذة المستخدم: نافذة المشفى إن وُجدت، وإلا آخر نافذة غير نافذة التأمين
async function userWindowId(job) {
  const insWin = insWindows.get(job.insurerId);
  const win = await chrome.windows.getLastFocused().catch(() => null);
  if (win && win.type === "normal" && win.id !== insWin) return win.id;
  const tabs = await chrome.tabs.query({ url: HIS_TAB_PATTERNS });
  if (tabs.length) return tabs[0].windowId;
  const w = await chrome.windows.create({ focused: true });
  return w.id;
}

// ---- مستند التأمين: رابط → جلب بجلسة الصفحة → طباعة في صفحة الموظف ----
// مصدران للرابط، كلاهما حدثي بلا مؤقتات:
//  1) عالم الصفحة (mednet-capture.js) يعترض window.open / target=_blank
//     ويسلّمه عبر bridge.printReport.
//  2) webRequest يلتقط المسارات الأخرى (form POST، إطار، تنزيل).
const pendingReportTabs = new Map(); // reportTabId -> openerTabId

async function fetchReportViaTab(tabId, url, post = null) {
  const resp = await Promise.race([
    new Promise((resolve) => {
      chrome.tabs.sendMessage(tabId, { type: "insurer:fetchReport", url, post }, (r) => {
        void chrome.runtime.lastError;
        resolve(r || { ok: false, error: chrome.runtime.lastError?.message || "no_response" });
      });
    }),
    sleep(30000).then(() => ({ ok: false, error: "timeout" })),
  ]);
  if (resp?.ok && resp.dataUrl) return resp;

  // احتياط: الجلب من عالم الصفحة (MAIN) — أصل الصفحة + كوكيزها (لو كان
  // التقرير على أصل مختلف، يمنعها CORS في سكربت المحتوى).
  try {
    const res = await chrome.scripting.executeScript({
      target: { tabId },
      world: "MAIN",
      args: [url],
      func: async (u) => {
        try {
          const r = await fetch(u, { credentials: "include", cache: "no-store" });
          if (!r.ok) return { ok: false, error: "http_" + r.status };
          const b = await r.blob();
          const head = new Uint8Array(await b.slice(0, 4).arrayBuffer());
          const isPdf =
            head.length >= 4 &&
            head[0] === 0x25 && head[1] === 0x50 && head[2] === 0x44 && head[3] === 0x46;
          if (!isPdf) return { ok: false, error: "not_pdf" };
          const dataUrl = await new Promise((resolve, reject) => {
            const fr = new FileReader();
            fr.onload = () => resolve(fr.result);
            fr.onerror = reject;
            fr.readAsDataURL(b);
          });
          return { ok: true, dataUrl };
        } catch (e) {
          return { ok: false, error: String((e && e.message) || e) };
        }
      },
    });
    const r = res?.[0]?.result;
    if (r?.ok && r.dataUrl) return r;
  } catch (e) {
    await log("warn", "main-world fetch failed", String(e).slice(0, 120));
  }
  return resp || { ok: false, error: "no_response" };
}

// جلسة Liva تعمل عبر بروكسي SSL-VPN (nlicgulf) — الرابط الملتقط قد يكون
// على مضيف الموقع الحقيقي (health.livainsurance.om) بلا كوكيز جلستنا
// (يظهر http_405). الصيغة الصحيحة: نفس الملف عبر مسار البروكسي.
async function proxyRewriteUrl(tabId, url) {
  try {
    const t = await new Promise((r) => chrome.tabs.get(tabId, (t) => r(t || null)));
    const m = String(t?.url || "").match(/^https?:\/\/[^/]+\/proxy\/([a-z0-9]+)\/https\/([^/]+)/i);
    if (!m) return url;
    const u = new URL(url);
    if (u.host.toLowerCase() !== m[2].toLowerCase()) return url;
    const base = new URL(t.url);
    return `${base.origin}/proxy/${m[1]}/https/${u.host}${u.pathname}${u.search}`;
  } catch (e) { return url; }
}

async function handleReportUrl(tabId, ref) {
  // ref: رابط نصي، أو كائن { url, post } (post = جسم POST أعيد إرساله إن
  // رفض السيرفر GET — Liva: Reports/PatientInfo يعيد 405 على GET).
  let url = typeof ref === "object" && ref ? ref.url : ref;
  const post = typeof ref === "object" && ref ? ref.post || null : null;
  if (!tabId || !url) return;
  const job = jobs.get(tabId);
  if (job?.reportHandled) return;
  // نعالجة رابط أصلاً؟ الأحدث يستبدل المتصوّر في طابور الفراغ — إسقاط
  // صامت هنا كان يبتلع FilePath الحقيقي (وصل أثناء دورة GET المستعصية)
  if (handlingReports.has(tabId)) {
    queuedReportRef.set(tabId, ref);
    await log("info", "report queued (handler busy)", `${String(url).slice(0, 140)}`);
    return;
  }
  handlingReports.add(tabId);
  try {
    const insurerId = job?.insurerId || "mednet";
    const viaProxy = await proxyRewriteUrl(tabId, url);
    await log(
      "info",
      "report url captured",
      `${insurerId} ${String(viaProxy).slice(0, 150)}${post ? " (POST replay)" : ""}${viaProxy !== url ? " (proxy)" : ""}`
    );
    url = viaProxy;
    report(tabId, { step: "print", status: "run" });

    const resp = await fetchReportViaTab(tabId, url, post);
    if (!resp?.ok || !resp.dataUrl) {
      const why = String(resp?.error || "unknown");
      await log("warn", "report fetch failed", why.slice(0, 160));
      // رابط غير صحيح (صفحة HTML مثلاً) — قد يتبعه رابط التقرير الحقيقي،
      // فلا نُفشل المهمة هنا؛ حارس "armPrint" يبلّغ إن لم يصل أي رابط.
      if (!/not_pdf/i.test(why)) {
        report(tabId, {
          step: "print",
          status: "error",
          detail: `could not load the report (${why.slice(0, 60)})`,
        });
      }
      return;
    }
    const dataUrl = resp.dataUrl;
    await log("info", "report fetched", `${Math.round(dataUrl.length / 1024)} KB`);

    // اطبع في صفحة الموظف نفسها (نفس سلوك NextCare) — لا نافذة ولا تبويب جديد
    await printDataUrlInUserTab(dataUrl, insurerId);
    report(tabId, { step: "print", status: "ok" });
    if (job) job.reportHandled = true;
    await finishJob(tabId, insurerId);
  } finally {
    handlingReports.delete(tabId);
    // الفراغ الآن — عوّل أولوية لأحدث رابط وصل أثناء الانشغال
    // (غالباً FilePath الحقيقي من تنصت FormDetail)
    const next = queuedReportRef.get(tabId);
    queuedReportRef.delete(tabId);
    if (next && !jobs.get(tabId)?.reportHandled) {
      handleReportUrl(tabId, next).catch(() => {});
    }
  }
}

async function finishJob(tabId, insurerId) {
  if (printWatch && printWatch.tabId === tabId) printWatch = null;
  jobs.delete(tabId);
  // نافذة العمل الخلفية انتهت — أغلقها (الكوكيز تبقى للجلسة القادمة)
  const winId = insWindows.get(insurerId);
  if (winId) {
    insWindows.delete(insurerId);
    await chrome.windows.remove(winId).catch(() => {});
  }
}

// يُحقن في عالم الصفحة (MAIN) داخل تبويب المشفى — يفتح PDF من data URL
// في iframe مخفي ثم يفتح حوار الطباعة فوراً.
function MCR_PrintDataUrl(dataUrl) {
  return (async () => {
    try {
      const blob = await (await fetch(dataUrl)).blob();
      const url = URL.createObjectURL(blob);
      const iframe = document.createElement("iframe");
      iframe.style.cssText =
        "position:fixed;right:0;bottom:0;width:2px;height:2px;opacity:0.01;border:0;";
      iframe.src = url;
      document.body.appendChild(iframe);
      await new Promise((resolve) => { iframe.onload = resolve; setTimeout(resolve, 8000); });
      iframe.contentWindow.print();
      setTimeout(() => { iframe.remove(); URL.revokeObjectURL(url); }, 5000);
      return { ok: true };
    } catch (e) {
      return { ok: false, error: String((e && e.message) || e) };
    }
  })();
}

async function printDataUrlInUserTab(dataUrl, insurerId = "nextcare") {
  const tabs = await chrome.tabs.query({ url: HIS_TAB_PATTERNS }).catch(() => []);
  const hisTab = tabs.find((t) => t.active) || tabs[0];
  if (hisTab) {
    try {
      const res = await chrome.scripting.executeScript({
        target: { tabId: hisTab.id },
        world: "MAIN",
        args: [dataUrl],
        func: MCR_PrintDataUrl,
      });
      if (res?.[0]?.result?.ok) return true;
      await log("warn", "in-tab print failed", String(res?.[0]?.result?.error || ""));
    } catch (e) {
      await log("warn", "in-tab print injection failed", String(e).slice(0, 120));
    }
  }
  // احتياط: تبويب طباعة في نافذة الموظف (نفس المنطق السابق)
  await chrome.storage.session.set({ mcrPrintPdf: dataUrl }).catch(() => {});
  const userWinId = await userWindowId({ insurerId });
  await chrome.tabs.create({
    windowId: userWinId, url: chrome.runtime.getURL("src/print/print.html"), active: true,
  });
  return false;
}

function watchingJob(tabId) {
  return printWatch && printWatch.tabId === tabId && Date.now() - printWatch.since < 120000;
}

chrome.tabs.onCreated.addListener((tab) => {
  if (!tab.openerTabId) return;
  const job = jobs.get(tab.openerTabId);
  if (!job || job.reportHandled) return;

  // Liva: بطاقة "Provider" تفتح تبويب البوابة الداخلية — ننتظر عنوانه
  // ثم نسلّم المهمة إليه (التدفق يكمل هناك).
  if (job.insurerId === "liva" && !jobAwaitingPrint(job)) {
    pendingHandover.set(tab.id, tab.openerTabId);
    log("info", "child tab opened during liva job", `tab=${tab.id} url=${String(tab.pendingUrl || tab.url || "").slice(0, 120)}`);
    setTimeout(() => pendingHandover.delete(tab.id), 60000);
    return;
  }

  if (!job.awaitingPrint && !watchingJob(tab.openerTabId)) return;
  const url = tab.pendingUrl || tab.url || "";
  if (!/^https?:/i.test(url)) {
    // about:blank الآن — راقب تنقله
    pendingReportTabs.set(tab.id, tab.openerTabId);
    setTimeout(() => pendingReportTabs.delete(tab.id), 60000);
    return;
  }
  if (!isReportUrl(job, url)) return;
  // التقرير وصل كتوبي جديد — أغلق فوراً واعرض المستند في صفحة الموظف
  log("info", "report tab captured", String(url).slice(0, 150));
  chrome.tabs.remove(tab.id).catch(() => {});
  handleReportUrl(tab.openerTabId, url).catch(() => {});
});

chrome.tabs.onUpdated.addListener((tabId, info, tab) => {
  // ---- تسليم المهمة إلى تبويب جديد (Liva: بطاقة Provider) ----
  const opener = pendingHandover.get(tabId);
  if (opener) {
    const u = tab.url || "";
    // أي تبويب فرعي لصفحة البوابة أثناء مهمة Liva = بوابة المزوّد
    // (نمط المسار قد يختلف، فلا نشترط proxy/Provider).
    if (u && /^https?:/i.test(u) && !/\/sslvpn\/portal\//i.test(u)) {
      pendingHandover.delete(tabId);
      handoverJob(tabId, opener);
      return;
    }
    if (info.status === "complete") {
      pendingHandover.delete(tabId);
      log("warn", "handover skipped — url not usable", String(u).slice(0, 120));
    }
  }

  // ---- تبويب تقرير انفتح من تبويب المهمة (blank ثم تنقل) ----
  const reportOpener = pendingReportTabs.get(tabId);
  if (!reportOpener) return;
  if (info.status === "complete" && tab.url && /^https?:/i.test(tab.url)) {
    pendingReportTabs.delete(tabId);
    const job = jobs.get(reportOpener);
    if (!isReportUrl(job, tab.url)) return;
    chrome.tabs.remove(tabId).catch(() => {});
    handleReportUrl(reportOpener, tab.url).catch(() => {});
  }
});

// ReportsViewer يعيد الملف بترويسة تنزيل — لا تبويب ولا عنوان في الصفحة،
// الرابط يظهر فقط هنا (downloads.onCreated). نلغيه ونطبعه في صفحة الموظف.
chrome.downloads.onCreated.addListener((item) => {
  const url = item?.url || "";
  if (!/^https?:/i.test(url)) return;
  for (const [tabId, job] of jobs) {
    if (job.reportHandled) continue;
    if (!jobAwaitingPrint(job) && !watchingJob(tabId)) continue;
    const insurer = INSURERS.find((i) => i.id === job.insurerId);
    if (!insurer || !insurer.urlPatterns.some((p) => url.startsWith(p.replace(/\*$/, "")))) continue;
    if (!isReportUrl(job, url)) continue;
    log("info", "report download captured", url.slice(0, 150));
    job.awaitingPrint = false;
    for (const [rt, op] of pendingReportTabs) {
      if (op === tabId) {
        pendingReportTabs.delete(rt);
        chrome.tabs.remove(rt).catch(() => {});
      }
    }
    try { chrome.downloads.cancel(item.id).catch(() => {}); } catch (e) {}
    handleReportUrl(tabId, url).catch(() => {});
    break;
  }
});

function stageAfter(msg) {
  if (msg.status === "error") return "error";
  if (msg.step === "login" && msg.status === "ok") return "after_login";
  if (msg.step === "eligibility" && msg.status === "ok") return "awaiting_result";
  if (msg.step === "submitted" && msg.status === "ok") return "awaiting_result";
  if (msg.step === "document") return "done";
  return null;
}

function publicJob(job) {
  return {
    insurerId: job.insurerId,
    stage: job.stage,
    lastProgress: job.lastProgress,
    error: job.error,
    startedAt: job.startedAt,
  };
}

// ---- patient detection on the HIS page ----
async function detectActivePatient() {
  const tabs = await chrome.tabs.query({ url: HIS_TAB_PATTERNS });
  if (!tabs.length) {
    await log("warn", "detect: no HIS tab open");
    return { ok: false, reason: "no_his_tab" };
  }
  const ordered = [...tabs.filter((t) => t.active), ...tabs.filter((t) => !t.active)];
  for (const tab of ordered) {
    try {
      const res = await chrome.tabs.sendMessage(tab.id, { type: "his:readPatient" });
      if (res && (res.patId || res.cardNo || res.payerText)) {
        await log("info", "detect: patient data found", JSON.stringify(res));
        return { ok: true, ...res };
      }
      if (res?.diag) {
        await log("warn", "detect: reader found nothing", JSON.stringify(res.diag));
      }
    } catch (e) {
      // content script not injected yet on this tab — try the next one
    }
  }
  return { ok: false, reason: "patient_not_detected" };
}

// ---- NextCare agent: يعمل في عالم الصفحة (MAIN) — دوال الموقع نفسها ----
// يُحقن عبر chrome.scripting (world:"MAIN") — لا يتأثر بـCSP الموقع إطلاقاً.
function MCR_NextcareAgent(card, type) {
  const jq = window.jQuery;
  const inp = document.querySelector('[id$="_txtCardNumber"]');
  const sel = document.querySelector('select[id$="_cmbType"]');
  if (!inp || !sel) return Promise.resolve({ ok: false, error: "fields not found" });
  inp.focus();
  inp.value = card;
  inp.dispatchEvent(new Event("input", { bubbles: true }));
  const opt = [...sel.options].find((o) => o.text.trim() === type);
  if (!opt) return Promise.resolve({ ok: false, error: "type not in list: " + type });
  if (jq) jq(sel).val(opt.value).trigger("change");
  else { sel.value = opt.value; sel.dispatchEvent(new Event("change", { bubbles: true })); }
  let reportUrl = null;
  const origOpen = window.open;
  window.open = function (u) { reportUrl = u; return null; };
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  return (async () => {
    try {
      window.CheckEligibilityorSearchbyPolicy();
      const t0 = Date.now();
      while (Date.now() - t0 < 35000) {
        const btn = document.querySelector("#btnPrintVerification");
        if (btn && btn.offsetParent !== null) break;
        await wait(300);
      }
      const btn = document.querySelector("#btnPrintVerification");
      if (!btn || btn.offsetParent === null) return { ok: false, error: "timeout waiting for result" };
      await wait(700);
      window.ShowReport();
      const t1 = Date.now();
      while (!reportUrl && Date.now() - t1 < 12000) await wait(200);
      if (!reportUrl) return { ok: false, error: "report URL not captured" };
      const blob = await fetch(reportUrl, { credentials: "include" }).then((r) => r.blob());
      const dataUrl = await new Promise((resolve, reject) => {
        const fr = new FileReader();
        fr.onload = () => resolve(fr.result);
        fr.onerror = reject;
        fr.readAsDataURL(blob);
      });
      return { ok: true, dataUrl };
    } finally {
      window.open = origOpen;
    }
  })();
}

async function agentFlow(insurer, card, serviceType, settings) {
  const tab = await ensureInsurerTab(insurer);
  const creds = insurerCreds(settings, insurer.id);
  for (let i = 0; i < 6; i++) {
    await waitForTabComplete(tab.id).catch(() => {});
    const st = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: () => ({
        isLogin: !!document.querySelector("#txtUserName") && !!document.querySelector("#frmLogin"),
        hasFields:
          !!document.querySelector('[id$="_txtCardNumber"]') &&
          !!document.querySelector('select[id$="_cmbType"]'),
      }),
    }).catch(() => null);
    const page = st?.[0]?.result;
    if (!page) { await sleep(1200); continue; }
    if (page.isLogin) {
      if (!creds.user || !creds.pass) return { ok: false, error: "session dead and no credentials" };
      report(null, { step: "login", status: "run" });
      await chrome.tabs.sendMessage(tab.id, { type: "insurer:login", creds }).catch(() => {});
      await sleep(3000);
      continue;
    }
    if (!page.hasFields) {
      await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        func: () => { location.href = "/EligibilityChecking2.aspx"; },
      }).catch(() => {});
      await sleep(1500);
      continue;
    }
    report(null, { step: "eligibility", status: "run", detail: "agent" });
    const res = await chrome.scripting.executeScript({
      target: { tabId: tab.id }, world: "MAIN",
      args: [card.insurance.card_no, serviceType || "Out-Patient"],
      func: MCR_NextcareAgent,
    }).catch((e) => {
      log("error", "agent injection failed", String(e).slice(0, 120));
      return null;
    });
    const r = res?.[0]?.result;
    if (r?.ok) {
      await log("info", "agent: document ready", `${Math.round(r.dataUrl.length / 1024)} KB`);
      return r;
    }
    return { ok: false, error: r?.error || "agent returned nothing" };
  }
  return { ok: false, error: "agent could not reach the eligibility page" };
}

// ---- insurer run orchestration (fully in background) ----
// ---- جسر مستندات التأمين (الوساطة على السيرفر) ----
// المواقع تُشغَّل على السيرفر (خدمات لا نافذة على جهاز الموظف)؛
// الإضافة تكتفي بطلب + تقدم + زر طباعة في صفحة المشفى.
const INSURER_API = "http://maxcall.local:3001";
const brokerCaptchaClaims = new Map();          // jobId → lease token (15ث)

async function insurerRequest(path, { method = "GET", body, timeoutMs = 20000 } = {}) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(`${INSURER_API}${path}`, {
      method,
      signal: ctrl.signal,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
    const text = await res.text();
    let json = null;
    try { json = JSON.parse(text); } catch (e) { /* response was html/truncated */ }
    if (!res.ok || (json && json.ok === false)) {
      throw new Error(json?.error || `http_${res.status}`);
    }
    return json || {};
  } finally {
    clearTimeout(timer);
  }
}

async function submitCaptchaAnswer(jobId, token, text) {
  try {
    await insurerRequest(`/api/insurer/job/${jobId}/answer`,
      { method: "POST", body: { token, text }, timeoutMs: 12000 });
    return true;
  } catch (e) {
    return false;
  }
}

const STEP_LINK = {
  navigate: "navigate", login: "login",
  eligibility: "eligibility", submitted: "submitted",
  print: "print", captcha: "captcha",
};

async function runMednetBroker(insurer, card, serviceType) {
  const member = strMember(card) || "-";
  await log("info", "run: mednet via insurer-broker", `member=${member}`);
  report(null, { step: "navigate", status: "run", detail: `running for ${member}` });

  let job;
  try {
    job = await insurerRequest("/api/insurer/job", {
      method: "POST",
      body: { insurer: "mednet", card, serviceType: serviceType || null },
      timeoutMs: 15000,
    });
  } catch (e) {
    await brokerFailed(String(e));
    return { ok: false, reason: String(e).slice(0, 80) };
  }
  const jobId = job?.jobId;
  if (!jobId) {
    await brokerFailed("no_job_id");
    return { ok: false, reason: "broker_rejected" };
  }

  let captchaShown = false;
  const deadline = Date.now() + 240000;
  while (Date.now() < deadline) {
    await sleep(1400);
    let s;
    try {
      s = await insurerRequest(`/api/insurer/job/${jobId}`, { timeoutMs: 12000 });
    } catch (e) {
      continue; // اضطراب عابر — استمر بالاستطلاع
    }
    const last = (s.stages || []).slice(-1)[0];
    if (last) {
      report(null, {
        step: STEP_LINK[last.step] || last.step,
        status: last.status === "error" ? "error" : "run",
        detail: last.detail || "",
      });
    }
    if (s.state === "captcha" && s.captcha?.img_b64 && !captchaShown) {
      captchaShown = true;
      brokerCaptchaClaims.set(jobId, s.captcha.token);
      await openBrokerCaptchaAsk(jobId, s.captcha);
    }
    if (s.state === "done") {
      return await finishBrokerPrint(jobId, "mednet");
    }
    if (s.state === "error") {
      await brokerFailed(s.error || "broker_failed");
      return { ok: false, reason: s.error || "broker_failed" };
    }
  }
  await brokerFailed("انتهت مهلة العملية — نفّذ يدوياً كما تعودت");
  return { ok: false, reason: "broker_timeout" };
}

async function brokerFailed(err) {
  await log("warn", "mednet broker failed", String(err).slice(0, 200));
  report(null, {
    step: "print", status: "error",
    detail: "تعذر استخراج المستند — نفّذ العملية يدوياً كما تعودت",
  });
}

function strMember(card) {
  return String(card?.insurance?.card_no || card?.member || "").trim();
}

async function finishBrokerPrint(jobId, insurerId) {
  let pdf;
  try {
    pdf = await insurerRequest(`/api/insurer/job/${jobId}?pdf=1`, { timeoutMs: 60000 });
  } catch (e) {
    await brokerFailed(String(e));
    return { ok: false, reason: "pdf_fetch_failed" };
  }
  if (!pdf?.pdf_b64) {
    await brokerFailed("pdf_missing");
    return { ok: false, reason: "pdf_missing" };
  }
  report(null, { step: "document", status: "ok" });
  const dataUrl = `data:application/pdf;base64,${pdf.pdf_b64}`;
  const ok = await printDataUrlInUserTab(dataUrl, insurerId);
  report(null, { step: "print", status: "ok" });
  return ok ? { ok: true } : { ok: true, printError: true };
}

// نافذة التحقق البشري على صفحة المشفى — يتطلب 10 ث للقراءة و15 ث للإجابة؛
// عند الانقضاء يتلاشى تلقائياً (الخلفية تلتقط الطلب التالي المتزامن).
async function openBrokerCaptchaAsk(jobId, captcha) {
  const tabs = await chrome.tabs.query({ url: HIS_TAB_PATTERNS }).catch(() => []);
  const hisTab = tabs.find((t) => t.active) || tabs[0];
  if (!hisTab) {
    await revealInsurerWindow({ insurerId: "mednet" });
    await log("warn", "no_his_tab for captcha ask", `job=${jobId.slice(0, 6)}`);
    return;
  }
  const requestId = jobId; // يُستهلك في insurer:askAnswer → submitCaptchaAnswer
  chrome.tabs.sendMessage(hisTab.id, {
    type: "ui:ask",
    requestId,
    request: {
      kind: "captcha",
      title: "MedNet — التحقق البشري",
      image: `data:image/jpeg;base64,${captcha.img_b64}`,
      countdownSeconds: 15,
      hint: "أدخل رمز التحقق — سينتهي وقد يتحرك التحقق إلى جهاز آخر",
      placeholder: "الرمز الظاهر في الصورة",
    },
  }).catch(() => {});
}

// ------------------------------------------------------------------
// نهاية جسر الوساطة (المواقع تعمل على السيرفر — بلا نوافذ)
// ------------------------------------------------------------------

async function runInsurer({ insurerId, card, serviceType }) {
  const insurer = INSURERS.find((i) => i.id === insurerId);
  if (!insurer) return { ok: false, reason: "unknown_insurer" };
  const settings = await getSettings();
  const creds = insurerCreds(settings, insurerId);
  if (!creds.user || !creds.pass) {
    await log("warn", `run: incomplete credentials for ${insurerId} — relying on live session`);
  }

  // وضع الوسيط (الوساطة على السيرفر — بواعلام التأمين تشبه NextCare)
  if (insurerId === "mednet") {
    return await runMednetBroker(insurer, card, serviceType);
  }

    // v0.4.3: وضع الوكيل — تنسيق كامل من الخلفية عبر MAIN-world injection
  // (لا يتأثر بـCSP الموقع). تبويب مصغّر واحد، نداءان لدوال الموقع، صفر تعبئة/نقر.
  if (insurerId === "nextcare") {
    report(null, { step: "navigate", status: "run", detail: "agent mode" });
    const r = await agentFlow(insurer, card, serviceType, settings).catch(
      (e) => ({ ok: false, error: String(e).slice(0, 150) })
    );
    if (r?.ok && r.dataUrl) {
      // الطباعة داخل تبويب الموظف نفسه (iframe مخفي + print) — لا تبويب جديد
      await printDataUrlInUserTab(r.dataUrl, "nextcare");
      report(null, { step: "print", status: "ok" });
      const winId = insWindows.get("nextcare");
      if (winId) {
        insWindows.delete("nextcare");
        await chrome.windows.remove(winId).catch(() => {});
      }
      await log("info", "agent flow complete — print dialog opened in user window");
      return { ok: true, agent: true };
    }
    await log("warn", "agent failed — full window flow fallback", r?.error || "");
    report(null, { step: "navigate", status: "run", detail: "window fallback" });
  }

  const tab = await ensureInsurerTab(insurer);
  jobs.set(tab.id, {
    insurerId,
    card,
    serviceType: serviceType || null,
    creds,
    autoPrint: settings.autoPrint !== false,
    stage: null,
    lastProgress: null,
    error: null,
    startedAt: Date.now(),
    dispatching: false,
  });
  await log("info", `run: ${insurerId} on tab ${tab.id} (background window)`, `serviceType=${serviceType || "-"}`);
  report(tab.id, { step: "navigate", status: "run" });
  dispatchToTab(tab.id);
  return { ok: true, tabId: tab.id };
}

// نافذة مخصصة مصغّرة لكل شركة — الموقع يشتغل بعيداً عن عين الموظف،
// وعند الوصول للمستند تُستعاد النافذة ويظهر مربع الطباعة مباشرة.
async function ensureInsurerTab(insurer) {
  const winId = insWindows.get(insurer.id);
  if (winId) {
    const win = await chrome.windows.get(winId, { populate: true }).catch(() => null);
    if (win) {
      let tab = win.tabs.find(
        (t) => insurer.urlPatterns.some((p) => (t.url || "").startsWith(p.replace(/\*$/, "")))
      );
      if (!tab) {
        tab = await chrome.tabs.create({ windowId: winId, url: insurer.loginUrl, active: true });
      }
      return tab;
    }
  }
  // تبويب قائم للموقع في أي نافذة → انقله لنافذة مخصصة مصغّرة
  const existing = await chrome.tabs.query({ url: insurer.urlPatterns });
  if (existing.length) {
    const w = await chrome.windows.create({ tabId: existing[0].id, focused: false });
    insWindows.set(insurer.id, w.id);
    await chrome.windows.update(w.id, { state: "minimized" }).catch(() => {});
    return existing[0];
  }
  const prev = await chrome.windows.getLastFocused().catch(() => null);
  const w = await chrome.windows.create({
    url: insurer.loginUrl, focused: false, width: 1280, height: 900,
  });
  insWindows.set(insurer.id, w.id);
  // التصغير بعد الإنشاء — بعض إصدارات كروم ترفض state:"minimized" داخل create
  await chrome.windows.update(w.id, { state: "minimized" }).catch(() => {});
  // أعِد التركيز لنافذة الموظف فوراً حتى يبقى الـpopup مفتوحاً والتحميل ظاهراً
  if (prev && prev.id && prev.id !== w.id) {
    await chrome.windows.update(prev.id, { focused: true }).catch(() => {});
  }
  return w.tabs[0];
}

// استعادة النافذة + استدعاء الطباعة مباشرة
async function revealAndPrint(tabId, job) {
  try {
    const winId = insWindows.get(job.insurerId);
    if (winId) {
      await chrome.tabs.update(tabId, { active: true }).catch(() => {});
      await chrome.windows.update(winId, { state: "normal", focused: true }).catch(() => {});
      await sleep(700);
    }
    if (job.autoPrint) {
      await chrome.tabs.sendMessage(tabId, { type: "insurer:print" }).catch(() => {});
    }
  } catch (e) {
    await log("error", "revealAndPrint failed", String(e));
  }
}

// إظهار نافذة شركة التأمين (احتياطي: الموظف يحلّ الكابتشا يدوياً)
async function revealInsurerWindow(job) {
  const winId = job && insWindows.get(job.insurerId);
  if (!winId) return;
  await chrome.windows.update(winId, { state: "normal", focused: true }).catch(() => {});
}

// هل انتهت صلاحية المهمة؟ (انتظار إجابة الموظف prolongs المهمة)
function jobExpired(job) {
  if (job.askUntil && Date.now() < job.askUntil) return false;
  return Date.now() - job.startedAt > JOB_TTL_MS;
}

// رابط تقرير؟ (لـLiva نمط الرابط غير موثّق → أي رابط أثناء مرحلة الطباعة)
function isReportUrl(job, url) {
  if (!url) return false;
  if (REPORT_URL_RE.test(url)) return true;
  return !!(job?.insurerId === "liva" && jobAwaitingPrint(job));
}

function jobAwaitingPrint(job) {
  if (!job) return false;
  if (job.awaitingPrintUntil && Date.now() < job.awaitingPrintUntil) return true;
  return !!job.awaitingPrint;
}

// تسليم المهمة من تبويب إلى تبويب (Liva: بطاقة "Provider" تفتح تبويباً جديداً)
function handoverJob(newTabId, openerTabId) {
  const job = jobs.get(openerTabId);
  if (!job) return;
  jobs.delete(openerTabId);
  job.dispatching = false;
  job.navCount = 0;
  job.handedOver = true;
  jobs.set(newTabId, job);
  log("info", "job handed over to the new tab", `from=${openerTabId} to=${newTabId}`);
  // الـpopup يتبع التبويب الذي يعمل فيه — نبلّغه بالتبويب الجديد
  chrome.runtime.sendMessage({ type: "job:handover", tabId: newTabId }).catch(() => {});
  dispatchToTab(newTabId);
}

async function dispatchToTab(tabId, attempt = 0) {
  const job = jobs.get(tabId);
  if (!job) return;
  if (jobExpired(job)) {
    jobs.delete(tabId);
    report(tabId, { step: "timeout", status: "error", detail: "انتهت مهلة العملية" });
    return;
  }
  job.dispatching = true;
  try {
    await waitForTabComplete(tabId);
    // مهلة أمان: لا نبقي المهمة "مشغولة" أبداً حتى لو تجمد المحتوى
    await Promise.race([
      chrome.tabs.sendMessage(tabId, {
        type: "insurer:run",
        card: job.card,
        creds: job.creds,
        autoPrint: job.autoPrint,
        serviceType: job.serviceType,
        stage: job.stage,
        // توقيت إرسال دخول المزوّد — يُرصد من POST كابتشا نفسه (user…)
        submitAt: job.provSubmitAt || 0,
      }),
      sleep(45000),
    ]);
    job.dispatching = false;
  } catch (e) {
    job.dispatching = false;
    if (attempt < 12) {
      await sleep(800);
      return dispatchToTab(tabId, attempt + 1);
    }
    await log("error", "dispatch: content script unreachable", `tab=${tabId}`);
    report(tabId, { step: "content_script", status: "error", detail: "تعذر حقن السكربت في صفحة الموقع" });
  }
}

async function waitForTabComplete(tabId, timeoutMs = 25000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    const tab = await chrome.tabs.get(tabId).catch(() => null);
    if (!tab) throw new Error("tab_closed");
    if (tab.status === "complete") return;
    await sleep(250);
  }
  throw new Error("tab_load_timeout");
}

// re-dispatch after every navigation of an active job's tab
// (WebForms postbacks and login redirects reload the whole page)
chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  const job = jobs.get(tabId);
  if (!job || changeInfo.status !== "complete" || !tab.url) return;
  // تبويب التأمين نفسه انتقل إلى رابط التقرير؟
  if (isReportUrl(job, tab.url) && !job.reportHandled) {
    handleReportUrl(tabId, tab.url).catch(() => {});
    return;
  }
  const insurer = INSURERS.find((i) => i.id === job.insurerId);
  if (!insurer || !insurer.urlPatterns.some((p) => tab.url.startsWith(p.replace(/\*$/, "")))) return;
  if (!job.dispatching) {
    job.navCount = (job.navCount || 0) + 1;
    if (job.navCount > 10) {
      report(tabId, { step: "timeout", status: "error", detail: "عدد تنقلات كبير — توقفت الحلقة" });
      jobs.delete(tabId);
      return;
    }
    log("info", "redispatch after navigation", `tab=${tabId} nav=${job.navCount} url=${tab.url.slice(0, 90)}`);
    dispatchToTab(tabId);
  }
});

chrome.tabs.onRemoved.addListener((tabId) => jobs.delete(tabId));
chrome.windows.onRemoved.addListener((winId) => {
  for (const [k, v] of insWindows) if (v === winId) insWindows.delete(k);
});

// ---- التقاط طلبات مواقع التأمين (POST) في اللوج ----
// الهدف: رسم خريطة الـendpoints الحقيقية لبناء النسخة السيرفرية لاحقاً
// (المرحلة الثانية: السيرفر يستعلم مباشرة بلا متصفح — docs/ARCHITECTURE.md)
const CAPTURE_PATTERNS = [
  "https://pulse-uae.nextcarehealth.com/*",
  "https://mednetapprovals.com/*",
  "https://online.nlicgulf.com:12443/*",
];
chrome.webRequest.onBeforeRequest.addListener(
  (details) => {
    // رابط تقرير أثناء مرحلة الطباعة؟ التقطه حتى لو فتحه الموقع في تبويب/إطار/تنزيل
    if (printWatch && printWatch.tabId && Date.now() - printWatch.since < 120000) {
      try {
        const noise = ["image", "stylesheet", "script", "font", "media"];
        if (
          details.tabId === printWatch.tabId &&
          !noise.includes(details.type) &&
          printWatch.urlRe.test(details.url)
        ) {
          // POST؟ نُحفظ جسمه لإعادة إرساله من الصفحة إذا رفض السيرفر GET (405)
          handleReportUrl(printWatch.tabId, {
            url: details.url,
            post: postBodies.get(details.url) || null,
          }).catch((e) => log("error", "report handling failed", String(e).slice(0, 160)));
        }
      } catch (e) { /* ignore */ }
    }
    if (details.method !== "POST") return;
    try { stashPostBody(details); } catch (e) { /* ignore */ }
    // لحظة تفريغ دخول المزوّد (نموذج الكابتشا) — حدّ الحارس "لا تعرض
    // كابتشا جديدة" يعتمد على هذا الإرسال نفسه، لا على دخول VPN
    if (/\/User\/(ValidateUserWithCaptcha|Login)/.test(details.url)) {
      const job = jobs.get(details.tabId);
      if (job && /^\/proxy\//i.test(new URL(details.url).pathname)) job.provSubmitAt = Date.now();
    }
    const fd = details.requestBody?.formData;
    const body = fd
      ? JSON.stringify(fd).slice(0, 400)
      : details.requestBody?.raw
        ? "(raw body)"
        : "";
    log("net", `POST ${details.url.slice(0, 120)}`, body);
  },
  { urls: CAPTURE_PATTERNS },
  ["requestBody"]
);

// أجسام POST الأخيرة — لإعادة إرسال طلبات التقرير التي ترفض GET (405)
const postBodies = new Map(); // url -> { body64, kind }
function stashPostBody(details) {
  const rb = details.requestBody;
  if (!rb) return;
  let entry = null;
  if (rb.raw && rb.raw.length && rb.raw[0]?.bytes) {
    const bytes = new Uint8Array(rb.raw[0].bytes);
    let bin = "";
    for (let i = 0; i < bytes.length; i += 8192) {
      bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 8192));
    }
    entry = { body64: btoa(bin), kind: "json" };
  } else if (rb.formData) {
    entry = { body64: btoa(JSON.stringify(rb.formData)), kind: "form" };
  }
  if (entry) {
    postBodies.set(details.url, entry);
    if (postBodies.size > 60) postBodies.delete(postBodies.keys().next().value);
  }
}

function report(tabId, progress) {
  chrome.runtime.sendMessage({ type: "adapter:progress", tabId, ...progress }).catch(() => {});
  forwardProgressToHIS(progress);
}

// شريط التقدم على صفحة المشفى — يبقى مرئياً حتى لو أُغلق الـpopup
async function forwardProgressToHIS(progress) {
  try {
    const tabs = await chrome.tabs.query({ url: HIS_TAB_PATTERNS });
    if (!tabs.length) return;
    const t = tabs.find((x) => x.active) || tabs[0];
    chrome.tabs.sendMessage(t.id, { type: "ui:progress", ...progress }).catch(() => {});
  } catch (e) { /* لا تبويب */ }
}

log("info", `service worker started v${chrome.runtime.getManifest().version}`);
