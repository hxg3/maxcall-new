/** @file options.js — insurance logins (user + password per insurer).
 * Save sends the change to the MaxCare server (the broker applies it live).
 * An empty password keeps the one already stored on the server.
 */
import { getSettings, saveSettings } from "../lib/settings.js";

const $ = (id) => document.getElementById(id);
const INSURERS = ["nextcare", "mednet", "liva"];
const FALLBACK_BASES = ["http://maxcall.local:3001", "http://192.168.1.32:3001"];

async function credsRequest(path, { method = "GET", body, timeoutMs = 12000 } = {}) {
  let lastErr = null;
  for (const base of FALLBACK_BASES) {
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
      if (res.status !== 503) break; // the address answered — the error is from the service
    } catch (e) {
      lastErr = e;
      if (e.name === "AbortError") break;
    } finally {
      clearTimeout(timer);
    }
  }
  throw lastErr || new Error("server unreachable");
}

function setOut(text, cls) {
  const out = $("out");
  out.textContent = text;
  out.className = cls || "";
}

async function load() {
  try {
    const res = await credsRequest("/api/insurer/creds");
    for (const ins of INSURERS) {
      const c = (res.insurers || {})[ins] || {};
      $(`${ins}User`).value = c.user || "";
      $(`${ins}Pass`).value = "";
    }
  } catch (e) {
    setOut(`Load failed: ${e.message || e}`, "err");
  }
}

$("save").addEventListener("click", async () => {
  const btn = $("save");
  btn.disabled = true;
  setOut("Saving…");
  try {
    const localPatch = { insurers: {} };
    const changed = [];
    for (const ins of INSURERS) {
      const user = $(`${ins}User`).value.trim();
      const pass = $(`${ins}Pass`).value;
      if (!user && !pass) continue; // nothing to change for this insurer
      const body = { insurer: ins, user };
      if (pass) body.pass = pass;
      await credsRequest("/api/insurer/creds", { method: "POST", body, timeoutMs: 15000 });
      changed.push(ins);
      // silent local mirror — the server stays the source of truth
      localPatch.insurers[ins] = { enabled: true, user, ...(pass ? { pass } : {}) };
    }
    if (changed.length) await saveSettings(localPatch);
    setOut(changed.length ? "Saved" : "No changes", changed.length ? "ok" : "");
    for (const ins of INSURERS) $(`${ins}Pass`).value = "";
    await load();
  } catch (e) {
    setOut(`Error: ${e.message || e}`, "err");
  } finally {
    btn.disabled = false;
  }
});

load();
