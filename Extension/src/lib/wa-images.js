/** @file lib/wa-images.js
 * سحب آخر الصور المستلمة من واتساب ويب (WhatsApp Web) دون مغادرة صفحة المشفى.
 * تُنفّذ في عالم صفحة واتساب (MAIN world) حيث تتوفر window.require و WAWebCollections.
 * المنطق: أحدث N محادثة حسب وقت النشاط (t) → آخر رسالة صورة من كل محادثة → تحميل الوسيط
 * عبر downloadMedia({}) → قراءة mediaObject.mediaBlob → تحويلها لـ data URL.
 * لا يُرسل أي رسالة، لا يُ’ouv أي محادثة، قراءة فقط.
 */
const WA_REQUIRE_NAMES = ["WAWebCollections", "WAWebMsgStore"];

async function waRequire(name) {
  const m = window.require(name);
  if (!m) throw new Error(`whatsapp module ${name} not loaded`);
  return m;
}

async function blobToDataUrl(blob) {
  return new Promise((resolve, reject) => {
    const fr = new FileReader();
    fr.onload = () => resolve(String(fr.result || ""));
    fr.onerror = () => reject(fr.error || new Error("readAsDataURL failed"));
    fr.readAsDataURL(blob);
  });
}

async function loadImageMedia(msg) {
  // 1) forsøg via mediaObject.mediaBlob بعد downloadMedia
  const mediaObj = msg.mediaObject || {};
  const mediaData = msg.__x_mediaData || {};

  if (!mediaObj.mediaBlob) {
    try {
      if (typeof msg.downloadMedia === "function") {
        await msg.downloadMedia({});
      }
    } catch (e) {
      // فشل التحميل لهذه الرسالة — نكمل ونحاول البديل أدناه
    }
    // انتظر ليعالج واتساب تحميل الوسيط داخلياً (فحص كل ثانية، حتى 3 ثوانٍ)
    let waited = 0;
    while (waited < 3000) {
      await new Promise((r) => setTimeout(r, 1000));
      waited += 1000;
      const b2 = mediaObj.mediaBlob || (mediaData && mediaData.__x_mediaBlob) || null;
      if (b2 instanceof Blob) break;
    }
  }

  const blob = mediaObj.mediaBlob || (mediaData && mediaData.__x_mediaBlob) || null;
  if (blob instanceof Blob) {
    const dataUrl = await blobToDataUrl(blob);
    return { ok: true, dataUrl, mime: blob.type || "image/jpeg", size: blob.size };
  }

  // 2) بديل DOM: أي صورة مرسومة في الصفحة يمكن أن تكون هذه الرسالة (بالمقاس/النوع)
  const imgs = [...document.querySelectorAll('img[src^="blob:"]')];
  const w = mediaData.__x_fullWidth || msg.__x_width || 0;
  const h = mediaData.__x_fullHeight || 0;
  const mime = mediaData.__x_mimetype || msg.mediaObject?.type || "image/jpeg";
  const size = mediaData.__x_size || msg.mediaObject?.size || 0;
  const target = imgs.find((i) => {
    const matchW = w ? i.naturalWidth === w : true;
    const matchH = h ? i.naturalHeight === h : true;
    return matchW && matchH;
  });
  if (target && (target.currentSrc || target.src)) {
    try {
      const resp = await fetch(target.currentSrc || target.src);
      const b = await resp.blob();
      return { ok: true, dataUrl: await blobToDataUrl(b), mime: b.type || mime, size: b.size || size };
    } catch (e) {
      // السقوط أدناه
    }
  }

  return { ok: false, reason: "media blob unavailable", mime, size };
}

async function fetchLastWaImages(limit = 5) {
  const Chat = (await waRequire("WAWebCollections")).Chat;
  const allChats = Chat.getModelsArray ? Chat.getModelsArray() : [];
  if (!Array.isArray(allChats)) throw new Error("WhatsApp chats not available");

  // أحدث المحادثات أولاً حسب وقت آخر نشاط
  const sorted = [...allChats].sort((a, b) => (b.t || 0) - (a.t || 0));
  const images = [];
  const seenChatIds = new Set();

  for (const chat of sorted) {
    if (images.length >= limit) break;
    const chatId = chat.id && (chat.id._serialized || chat.id);
    if (!chatId || seenChatIds.has(chatId)) continue;
    seenChatIds.add(chatId);

    const chatName = chat.name || chat.formattedTitle || chatId || "Unknown";
    let msgs = null;
    try {
      msgs = chat.msgs && chat.msgs.getModelsArray ? chat.msgs.getModelsArray() : null;
    } catch (e) { /* skip this chat */ }
    if (!Array.isArray(msgs)) continue;

    const imageMsgs = [...msgs].filter((m) => m.type === "image").sort((a, b) => (b.t || 0) - (a.t || 0));
    if (!imageMsgs.length) continue;

    const imgMsg = imageMsgs[0];
    const loaded = await loadImageMedia(imgMsg);
    if (loaded.ok) {
      images.push({
        id: imgMsg.id && (imgMsg.id._serialized || imgMsg.id),
        chatName,
        chatId,
        t: imgMsg.t || 0,
        caption: (imgMsg.caption || "").slice(0, 60),
        dataUrl: loaded.dataUrl,
        mime: loaded.mime,
        size: loaded.size,
        source: "whatsapp",
      });
    }
  }
  return { ok: true, images, totalChats: sorted.length };
}

// تُعرَّض على النافذة العالمية ليتم استدعاؤها عبرchrome.scripting.executeScript من الخلفية
if (typeof window !== "undefined") {
  window.__mcr_fetchLastWaImages = fetchLastWaImages;
}
