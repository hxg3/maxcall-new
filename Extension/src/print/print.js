const $ = (id) => document.getElementById(id);
const msg = (t) => { $("msg").textContent = t; };

async function loadPdfObjectUrl() {
  const { mcrPrintPdf } = await chrome.storage.session.get("mcrPrintPdf");
  if (!mcrPrintPdf) return null;
  // data URL → blob URL (الأضمن لعارض PDF المدمج)
  const blob = await (await fetch(mcrPrintPdf)).blob();
  return URL.createObjectURL(blob);
}

(async () => {
  let objUrl = null;
  try {
    objUrl = await loadPdfObjectUrl();
  } catch (e) {
    msg("Could not load the document.");
  }
  if (!objUrl) {
    $("sp").style.display = "none";
    msg("Document not found — retry from the assistant.");
    return;
  }
  msg("Opening the print dialog…");
  const iframe = document.createElement("iframe");
  iframe.src = objUrl;
  document.body.appendChild(iframe);
  await new Promise((resolve) => {
    iframe.onload = resolve;
    setTimeout(resolve, 8000);
  });
  setTimeout(() => {
    try {
      iframe.contentWindow.print();
      msg("Print dialog opened — you can close this tab.");
      // أغلق التبويب بعد مغادرة حوار الطباعة
      setTimeout(() => window.close(), 1500);
    } catch (e) {
      // fallback: أزرار يدوية
      $("sp").style.display = "none";
      msg("Press Print to print the document.");
      $("btn").style.display = "inline-block";
      $("btn").addEventListener("click", () => {
        try { iframe.contentWindow.print(); } catch (err) { window.open(objUrl, "_blank"); }
      });
    }
  }, 400);
})();
