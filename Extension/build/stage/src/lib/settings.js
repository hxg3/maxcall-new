const DEFAULTS = {
  serverUrl: "http://maxcall.local:3001",
  serverUser: "",
  serverPass: "",
  autoPrint: true,
  insurers: {
    nextcare: { enabled: true, user: "", pass: "" },
    mednet: { enabled: true, user: "", pass: "" },
    liva: { enabled: true, user: "", pass: "" },
  },
};

function deepMerge(base, patch) {
  for (const k of Object.keys(patch || {})) {
    const v = patch[k];
    if (v && typeof v === "object" && !Array.isArray(v) && base[k] && typeof base[k] === "object") {
      deepMerge(base[k], v);
    } else if (v !== undefined && v !== null) {
      base[k] = v;
    }
  }
  return base;
}

export async function getSettings() {
  const { settings } = await chrome.storage.local.get("settings");
  return deepMerge(structuredClone(DEFAULTS), settings || {});
}

export async function saveSettings(patch) {
  const current = await getSettings();
  const merged = deepMerge(current, patch || {});
  await chrome.storage.local.set({ settings: merged });
  return merged;
}

export function insurerCreds(settings, insurerId) {
  const c = (settings.insurers || {})[insurerId] || {};
  return { enabled: c.enabled !== false, user: c.user || "", pass: c.pass || "" };
}
