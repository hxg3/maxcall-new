// تعبئة نموذج xlsx في المتصفح: نفك الضغط (ZIP) في الذاكرة، نعدل خلايا sheet1.xml
// (inline strings + أرقام + تواريخ)، ونعيد ضغط الملف — sans Excel/sans سيرفر.
// التسميات/التنسيق/الشعار كلها محفوظة (تعديل XML مباشر — surgical patch).
export async function fetchTemplateCached(url, cacheKey) {
  const { [cacheKey]: b64 } = await chrome.storage.local.get(cacheKey);
  if (b64) {
    const bin = atob(b64);
    const arr = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
    return arr.buffer;
  }
  const res = await fetch(url);
  if (!res.ok) throw new Error(`template fetch failed: ${res.status}`);
  const buf = await res.arrayBuffer();
  const bytes = new Uint8Array(buf);
  let bin = "";
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
  await chrome.storage.local.set({ [cacheKey]: btoa(bin) });
  return buf;
}

function escapeXml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function dateToExcelSerial(d) {
  // Excel epoch = 1899-12-30
  const epoch = Date.UTC(1899, 11, 30);
  return Math.round((d.getTime() - epoch) / 86400000);
}

// تحويل "10/18/2026" أو "2026-10-18" → serial number
function parseDateToSerial(v) {
  if (v == null || v === "") return null;
  if (typeof v === "number") return v;
  const s = String(v).trim();
  let m = s.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/);
  if (m) return dateToExcelSerial(new Date(+m[3], +m[1] - 1, +m[2]));
  m = s.match(/^(\d{4})-(\d{1,2})-(\d{1,2})$/);
  if (m) return dateToExcelSerial(new Date(+m[1], +m[2] - 1, +m[3]));
  m = s.match(/^(\d{4})\/(\d{1,2})\/(\d{1,2})$/);
  if (m) return dateToExcelSerial(new Date(+m[1], +m[2] - 1, +m[3]));
  return null;
}

// patch: list of {ref, value, type: "text"|"number"|"date"}
export async function patchXlsx(buffer, patches) {
  const zip = await JSZip.loadAsync(buffer);
  const sheetPath = "xl/worksheets/sheet1.xml";
  let xml = await zip.file(sheetPath).async("string");

  for (const p of patches) {
    if (p.value == null || p.value === "") continue;
    // ا捕获 style (s="...") من الخلية الأصلية إن وُجدت
    const re = new RegExp(`<c\\s+r="${p.ref}"(\\s+s="(\\d+)")?[^>]*>.*?</c>`, "s");
    const m = xml.match(re);
    const style = m ? (m[2] || "") : "";
    const sAttr = style ? ` s="${style}"` : "";

    let cellXml;
    if (p.type === "number") {
      cellXml = `<c r="${p.ref}"${sAttr}><v>${Number(p.value)}</v></c>`;
    } else if (p.type === "date") {
      const serial = parseDateToSerial(p.value);
      if (serial == null) continue;
      cellXml = `<c r="${p.ref}"${sAttr}><v>${serial}</v></c>`;
    } else {
      // inline string — لا يمس sharedStrings، وExcel يقرأه بشكل مثالي
      cellXml = `<c r="${p.ref}"${sAttr} t="inlineStr"><is><t xml:space="preserve">${escapeXml(p.value)}</t></is></c>`;
    }

    if (m) {
      xml = xml.replace(re, cellXml);
    } else {
      // خلية غير موجودة — أضفها في صفها (أو أنشئ الصف). افتراض: الصف موجود.
      const rowRe = new RegExp(`<row\\s+r="${p.ref.replace(/[0-9]/g, "")}"[^>]*>`, "s");
      // fallback: لا نُنشئ — تخطّ (النموذج يحتوي جميع الخلايا)
    }
  }

  zip.file(sheetPath, xml);
  const blob = await zip.generateAsync({
    type: "blob",
    mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    compression: "DEFLATE",
  });
  return blob;
}

// تحويل Blob → data URL (للتنزيل من extension page)
export function blobToDataUrl(blob) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(r.result);
    r.onerror = reject;
    r.readAsDataURL(blob);
  });
}

// توليد اسم الملف
export function buildFileName(patientName, dateStr) {
  const safe = String(patientName || "patient")
    .replace(/[\\/:*?"<>|]/g, "_")
    .trim()
    .slice(0, 40);
  return `Vipul-Claim-${safe}-${dateStr}.xlsx`;
}