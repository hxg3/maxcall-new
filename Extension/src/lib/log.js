// سجل تشخيصي دائري في chrome.storage.session — يبقى طوال فتح المتصفح،
// ثم يُنسخ من الـpopup ويرسل للمطور. لا يخزن أي كلمات مرور أبداً (sanitize).
const MAX_ENTRIES = 300;
const KEY = "diaglog";

function sanitize(v) {
  if (v == null) return v;
  const s = typeof v === "string" ? v : JSON.stringify(v);
  return s
    .replace(/("?\s*(pass|password|secret|token|authorization)"?\s*[:=]\s*)("[^"]*"|[^,}&\s]+)/gi, "$1***")
    .slice(0, 500);
}

export async function log(level, msg, extra) {
  const entry = { t: new Date().toISOString(), level, msg, extra: sanitize(extra) ?? undefined };
  try {
    const { [KEY]: arr } = await chrome.storage.session.get(KEY);
    const list = Array.isArray(arr) ? arr : [];
    list.push(entry);
    while (list.length > MAX_ENTRIES) list.shift();
    await chrome.storage.session.set({ [KEY]: list });
  } catch (e) {
    // storage.session غير متاح — لا شيء حرج
  }
  if (level === "error") console.error("[MCR]", msg, extra ?? "");
  else console.log("[MCR]", msg, extra ?? "");
}

export async function getLogText() {
  const { [KEY]: arr } = await chrome.storage.session.get(KEY);
  const m = chrome.runtime.getManifest();
  const lines = [
    `MaxCare Reception Assistant v${m.version}`,
    `time: ${new Date().toISOString()}`,
    `ua: ${navigator.userAgent}`,
    "----------------------------------------",
  ];
  for (const e of arr || []) {
    lines.push(`${e.t} [${e.level}] ${e.msg}${e.extra ? " | " + e.extra : ""}`);
  }
  if (!(arr || []).length) lines.push("(لا سجلات بعد)");
  return lines.join("\n");
}

export async function clearLog() {
  await chrome.storage.session.remove(KEY).catch(() => {});
}
