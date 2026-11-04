import { getSettings } from "./settings.js";
import { log } from "./log.js";

const TIMEOUT_MS = 25000;
// بدائل عند فشل حل العنوان المضبوط (مثلاً maxcall.local لا يُحلّ على بعض الأجهزة)
const FALLBACK_BASES = ["http://maxcall.local:3001", "http://192.168.1.32:3001"];

async function fetchWithBase(base, url, auth, signal) {
  return fetch(`${base}${url}`, {
    headers: auth ? { Authorization: auth } : undefined,
    signal,
  });
}

export async function hisPatient(patId) {
  const id = String(patId || "").trim();
  if (!/^\d{1,10}$/.test(id)) {
    return { ok: false, reason: "bad_pat_id" };
  }
  const s = await getSettings();
  const bases = [...new Set(
    [(s.serverUrl || "").replace(/\/+$/, ""), ...FALLBACK_BASES].filter(Boolean)
  )];

  const ctl = new AbortController();
  const timer = setTimeout(() => ctl.abort(), TIMEOUT_MS);
  try {
    for (const base of bases) {
      try {
        const res = await fetchWithBase(
          base, `/api/his/patient/${id}`,
          "Basic " + btoa(`${s.serverUser}:${s.serverPass}`), ctl.signal
        );
        if (res.status === 401) return { ok: false, reason: "unauthorized" };
        if (res.status === 429) return { ok: false, reason: "rate_limited" };
        if (!res.ok) return { ok: false, reason: `http_${res.status}` };
        log("info", "server reachable", base);
        return await res.json();
      } catch (e) {
        // هذا العنوان لا يستجيب — جرب التالي (AbortError يلغي الجميع)
        if (e.name === "AbortError") break;
        log("warn", "server base unreachable", `${base} -> ${String(e).slice(0, 100)}`);
      }
    }
    return { ok: false, reason: "server_unreachable" };
  } finally {
    clearTimeout(timer);
  }
}
