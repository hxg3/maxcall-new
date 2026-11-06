/** @file options.js — إعدادات كلمات مرور صفحات التأمين.
 * الحفظ يُرسل إلى خادم MaxCare (الوسيط يحدث /etc/maxcall-insurers.json حياً —
 * الوسيط يقرأه عند كل محاولة دخول بلا restart).
 * كلمة المرور الفارغة = تبقى الحالية على الخادم كما هي.
 */
import { getSettings, saveSettings } from "../lib/settings.js";

const $ = (id) => document.getElementById(id);
const INSURERS = ["nextcare", "mednet", "liva"];
const FALLBACK_BASES = ["http://maxcall.local:3001", "http://192.168.1.32:3001"];

function bases() {
  return FALLBACK_BASES; // host_permissions تغطي العنوانين فقط
}

async function credsRequest(path, { method = "GET", body, timeoutMs = 12000 } = {}) {
  let lastErr = null;
  for (const base of bases()) {
    const ctl = new AbortController();
    const timer = setTimeout(() => ctl.abort(), timeoutMs);
    try {
      const res = await fetch(`${base}${path}`, {
        method,
        signal: ctl.signal,
        headers: body ? { "Content-Type": "application/json" } : undefined,
        body: body ? JSON.stringify(body) : undefined,
      });
      const json = await res.json().catch(() => null);
      if (res.ok && json) return json;
      lastErr = new Error(json?.error || `http_${res.status}`);
      if (res.status !== 503) break; // العنوان يرد — الخطأ من الخدمة نفسها لا من العنوان
    } catch (e) {
      lastErr = e;
      if (e.name === "AbortError") break; // abort يلغي الجميع
    } finally {
      clearTimeout(timer);
    }
  }
  throw lastErr || new Error("server_unreachable");
}

function setOut(text, cls) {
  const out = $("out");
  out.textContent = text;
  out.className = cls || "";
}

function markSet(insurer, hasPass) {
  $(`${insurer}Set`).style.display = hasPass ? "" : "none";
}

// ---------- التحميل: اقرأ ما هو محفوظ على الخادم الآن ----------
async function load() {
  try {
    const res = await credsRequest("/api/insurer/creds");
    for (const ins of INSURERS) {
      const c = (res.insurers || {})[ins] || {};
      $(`${ins}User`).value = c.user || "";
      $(`${ins}Pass`).value = "";
      markSet(ins, !!c.has_pass);
    }
  } catch (e) {
    setOut(`تعذر قراءة البيانات من الخادم: ${e.message || e}`, "err");
  }
}

// ---------- الحفظ: يُرسل التغيير إلى الخادم ----------
$("save").addEventListener("click", async () => {
  const btn = $("save");
  btn.disabled = true;
  setOut("جاري الحفظ على الخادم…");
  try {
    const localPatch = { insurers: {} };
    const changed = [];
    for (const ins of INSURERS) {
      const user = $(`${ins}User`).value.trim();
      const pass = $(`${ins}Pass`).value;
      if (!user && !pass) continue; // لا تغيير لهذه الشركة
      const body = { insurer: ins, user };
      if (pass) body.pass = pass;
      await credsRequest("/api/insurer/creds", { method: "POST", body, timeoutMs: 15000 });
      changed.push(ins);
      // مرآة محلية صامتة للمطابقة (الخادم هو المصدر المعتمد)
      localPatch.insurers[ins] = { enabled: true, user, ...(pass ? { pass } : {}) };
    }
    if (changed.length) await saveSettings(localPatch);
    if (changed.length === 1) {
      setOut(`تم حفظ بيانات ${changed[0]} على الخادم ✓`, "ok");
    } else if (changed.length > 1) {
      setOut(`تم حفظ بيانات ${changed.length} شركات على الخادم ✓`, "ok");
    } else {
      setOut("لا توجد تغييرات للحفظ");
    }
    // منع إرسال نفس كلمة المرور مجدداً عند حفظ لاحق
    for (const ins of INSURERS) $(`${ins}Pass`).value = "";
    await load();
  } catch (e) {
    setOut(`فشل الحفظ: ${e.message || e} — راجع اتصال الخادم`, "err");
  } finally {
    btn.disabled = false;
  }
});

load();
