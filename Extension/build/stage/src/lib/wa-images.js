/** @file lib/wa-images.js
 * سحب آخر الصور من واتساب ويب (قراءة فقط — لا إرسال، لا فتح محادثات).
 *
 * يعمل داخل world:MAIN (window.require لا يوجد إلا في عالم الصفحة).
 *
 * المسار المعتمد: صور لوحة المحادثة المفتوحة التي يحمل عنصرها مصدراً جاهزاً
 * (data: أو blob:) — نأخذها من الأحدث للأقدم حتى limit.
 * لماذا لا downloadMedia؟ في إصدار واتساب الحالي ينتهي التحميل (RESOLVED)
 * وبقيّة mediaBlob تبقى null، والطرق الانتظارية بـsetTimeout تتجمد في التبويب
 * الخلفي (تخفيف المؤقتات) فتبدو الإضافة معلّقة.
 */
const WA_MAIN_MODULES = ["WAWebCollections"];

function waModule(name) {
  if (window[name]) return window[name];
  if (typeof window.require !== "function") return null;
  try { return window.require(name) || null; } catch (e) { return null; }
}

function blobToDataUrl(blob) {
  return new Promise((resolve, reject) => {
    const fr = new FileReader();
    fr.onload = () => resolve(String(fr.result || ""));
    fr.onerror = () => reject(fr.error || new Error("readAsDataURL failed"));
    fr.readAsDataURL(blob);
  });
}

function reactProps(el) {
  const k = Object.keys(el).find((x) => x.startsWith("__reactProps$"));
  try { return k ? el[k] : null; } catch (e) { return null; }
}

function activeChatName() {
  try {
    const C = waModule("WAWebCollections");
    const chats = C && C.Chat && C.Chat.getModelsArray ? C.Chat.getModelsArray() : [];
    const act = chats.find((c) => c.active) || null;
    return act ? (act.name || act.formattedTitle || "") : "";
  } catch (e) { return ""; }
}

// رسالة الصورة خلف عنصر المعاينة (React props: mediaData + msgKey)
function msgForThumb(el) {
  try {
    const p = reactProps(el);
    const child = p && p.children && p.children[0];
    const key = child && child.props && child.props.msgKey;
    const C = waModule("WAWebCollections");
    if (key && C && typeof C.Msg !== "undefined" && typeof C.Msg.get === "function") {
      return C.Msg.get(key) || null;
    }
  } catch (e) { /* لا بأس — نكمل بلا بيانات الرسالة */ }
  return null;
}

async function srcToDataUrl(src) {
  if (src.startsWith("data:")) return src;
  const resp = await fetch(src);
  const blob = await resp.blob();
  if (!blob || !blob.size) throw new Error("empty blob");
  return blobToDataUrl(blob);
}

function mimeOf(src, dataUrl) {
  const m = /^data:([^;,]+)/.exec(dataUrl || src || "");
  return (m && m[1]) || "image/jpeg";
}

// رسالة واحدة = صورة واحدة: نُجمّع الصور حسب مفتاح الرسالة (msgKey عبر
// React props، وإلا data-id، وإلا المصدر) ونُبقي الأعلى جودة فقط — مهما
// تعددت نسخ المعاينة (صورة مصغّرة + نسختان بجودات مختلفة لكل رسالة).
async function fetchLastWaImages(limit = 5) {
  const root =
    document.querySelector('[data-testid="conversation-panel-messages"]') ||
    document.body;
  const imgs = [...root.querySelectorAll("img")].filter((el) =>
    /^(data:image|blob:)/.test(el.currentSrc || el.src || ""));

  function keyFor(el, idx) {
    try {
      const p = reactProps(el);
      const child = p && p.children && p.children[0];
      const mk = child && child.props && child.props.msgKey;
      if (mk) return "mk:" + JSON.stringify(mk);
    } catch (e) { /* نكمل بالمفاتيح البديلة */ }
    let node = el;
    for (let d = 0; node && d < 14; d++, node = node.parentElement) {
      const did = node.getAttribute && node.getAttribute("data-id");
      if (did) return "id:" + did;
    }
    return "src:" + (el.currentSrc || el.src || idx);
  }

  // التجميع: مفتاح الرسالة → أنشط الصور فيها (بترتيب الظهور في DOM)
  const groups = new Map();
  imgs.forEach((el, idx) => {
    const key = keyFor(el, idx);
    const arr = groups.get(key) || [];
    arr.push({
      el,
      idx,
      px: (el.naturalWidth || 0) * (el.naturalHeight || 0),
      src: el.currentSrc || el.src || "",
    });
    groups.set(key, arr);
  });

  // أحدث رسائل الصور فقط (آخر ظهور في DOM = الأحدث) — لا نجرّب بقية المحادثة
  const newest = [...groups.values()]
    .map((arr) => ({ arr, pos: Math.max(...arr.map((x) => x.idx)) }))
    .sort((a, b) => b.pos - a.pos)
    .slice(0, limit);

  // لكل رسالة: مرشحو الأعلى أبعاد (بحد أقصى مصدرين متنوّعين) — نقرأهم
  // ونختار الأعلى جودة = الأكبر حجماً عند تساوي الأبعاد
  const winners = await Promise.all(newest.map(async ({ arr, pos }) => {
    const maxPx = Math.max(...arr.map((x) => x.px));
    const tops = [];
    const seenSrc = new Set();
    for (const x of arr) {
      if (x.px !== maxPx || seenSrc.has(x.src)) continue;
      seenSrc.add(x.src);
      tops.push(x);
      if (tops.length >= 2) break;
    }
    let best = null;
    for (const x of tops) {
      let dataUrl = "";
      try {
        dataUrl = await srcToDataUrl(x.src);
      } catch (e) {
        continue; // blob محجوز/محذوف — نتجاوز
      }
      if (dataUrl && (!best || dataUrl.length > best.dataUrl.length)) {
        best = { x, dataUrl, pos };
      }
    }
    return best;
  }));

  const images = [];
  for (const w of winners) {
    if (!w) continue;
    const msg = msgForThumb(w.x.el);
    const id = msg && msg.id
      ? (msg.id._serialized || String(msg.id))
      : `grp#${String(w.x.src).slice(-48)}`;
    images.push({
      id,
      chatName: activeChatName(),
      t: (msg && msg.t) || 0,
      caption: msg ? String(msg.caption || "").slice(0, 60) : "",
      dataUrl: w.dataUrl,
      mime: mimeOf(w.x.src, w.dataUrl),
      size: Math.round((w.dataUrl.length - w.dataUrl.indexOf(",") - 1) * 0.75),
      source: "whatsapp",
    });
  }

  return {
    ok: true,
    images,
    rendered: imgs.length,
    groups: groups.size,
    module: WA_MAIN_MODULES[0],
  };
}

// يُعرَّض على النافذة العالمية — تستدعيه الخلفية عبر chrome.scripting (world:"MAIN")
if (typeof window !== "undefined") {
  window.__mcr_fetchLastWaImages = fetchLastWaImages;
}
