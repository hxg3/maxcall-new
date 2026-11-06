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

async function fetchLastWaImages(limit = 5) {
  const root =
    document.querySelector('[data-testid="conversation-panel-messages"]') ||
    document.body;
  const imgs = [...root.querySelectorAll("img")];
  const images = [];
  const seen = new Set();

  // من الأحدث للأقدم (ترتيب DOM تصاعدي للرسائل)
  for (let i = imgs.length - 1; i >= 0 && images.length < limit; i--) {
    const el = imgs[i];
    const src = el.currentSrc || el.src || "";
    if (!/^(data:image|blob:)/.test(src)) continue;

    const msg = msgForThumb(el);
    const id = msg && msg.id
      ? (msg.id._serialized || String(msg.id))
      : `src#${src.length}_${src.slice(-48)}`;
    if (seen.has(id)) continue;
    seen.add(id);

    let dataUrl = "";
    try {
      dataUrl = await srcToDataUrl(src);
    } catch (e) {
      continue; // blob محجوز/محذوف — نتجاوز هذه الصورة
    }
    if (!dataUrl) continue;

    images.push({
      id,
      chatName: activeChatName(),
      t: (msg && msg.t) || 0,
      caption: msg ? String(msg.caption || "").slice(0, 60) : "",
      dataUrl,
      mime: mimeOf(src, dataUrl),
      size: Math.round((dataUrl.length - dataUrl.indexOf(",") - 1) * 0.75),
      source: "whatsapp",
    });
  }

  return { ok: true, images, rendered: imgs.length, module: WA_MAIN_MODULES[0] };
}

// يُعرَّض على النافذة العالمية — تستدعيه الخلفية عبر chrome.scripting (world:"MAIN")
if (typeof window !== "undefined") {
  window.__mcr_fetchLastWaImages = fetchLastWaImages;
}
