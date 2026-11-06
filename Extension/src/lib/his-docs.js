/** @file lib/his-docs.js
 * جلب مستندات المريض المحفوظة في صفحة المشفى (HIS) عبر واجهة الصفحة نفسها.
 * تدفق العمل:
 *  1) FileUpload/GetUploadedFiles   → قائمة المستندات (PatientFileID، النوع، الفئة)
 *  2) FileUpload/GetUploadedFilesByID → محتوى كل مستند (PatientFileString base64)
 * تُنفّذ في عالم الصفحة (MAIN world) داخل تبويب المشفى حتى تحمل الكوكيز والجلسة الحية تلقائياً.
 * قراءة فقط — لا تُحفظ أي شيء في سجلات المستشفى؛ الإرفاق فقط في الحانة والموظف يكمل الحفظ إذا أراد.
 */
export async function fetchHisDocuments(patId) {
  const loggedInfoRaw = localStorage.getItem("loggedInfo");
  const currentUserRaw = localStorage.getItem("currentUser");
  if (!loggedInfoRaw) throw new Error("loggedInfo missing — open the HIS page and sign in");
  let loggedInfo;
  try { loggedInfo = JSON.parse(loggedInfoRaw); } catch (e) { throw new Error("loggedInfo is not valid JSON"); }
  let token = "";
  try {
    const cu = JSON.parse(currentUserRaw || "{}");
    token = (cu.result && cu.result.AuthorizationToken) || "";
  } catch (e) { /* token optional */ }

  const base = location.origin.replace(/\/$/, "");
  const headers = {
    "Content-Type": "application/json",
    ...(token ? { "Authorization": "Bearer " + token } : {}),
  };
  const post = (path, payload) => fetch(`${base}/API/Api//${path}`, {
    method: "POST", headers, body: JSON.stringify({ loggedInInfo: loggedInfo, Payload: payload }),
    credentials: "include", cache: "no-store",
  });

  // الخطوة 1: قائمة مستندات المريض
  const r1 = await post("FileUpload/GetUploadedFiles", {
    Ph_No: 0,
    Pat_Id: Number(patId) || 0,
    Loc_Id: loggedInfo.Loc_Id || 1,
    FileCategory: 0,
    blloadDoc: false,
  });
  if (!r1.ok) throw new Error(`GetUploadedFiles HTTP ${r1.status}`);
  const j1 = await r1.json().catch(() => null);
  if (!j1 || !j1.result || !j1.result.Success) {
    const msg = j1 && j1.result && j1.result.Message ? JSON.stringify(j1.result.Message) : "";
    throw new Error("GetUploadedFiles failed: " + msg);
  }
  const list = j1.result.Payload || [];

  const documents = [];
  for (const it of list) {
    if (!it || !it.PatientFileID) continue;
    const ft = (it.FileType || "").toLowerCase();
    if (ft.indexOf("image") < 0 && ft.indexOf("jpeg") < 0 && ft.indexOf("png") < 0) continue;

    // الخطوة 2: محتوى كل مستند بالبايتات
    const r2 = await post("FileUpload/GetUploadedFilesByID", {
      PatientFileID: it.PatientFileID,
      Pat_Id: it.Pat_Id || Number(patId) || 0,
      Loc_Id: it.Loc_Id || loggedInfo.Loc_Id || 1,
    });
    if (!r2.ok) continue;
    const j2 = await r2.json().catch(() => null);
    const detail = j2 && j2.result && j2.result.Payload ? (Array.isArray(j2.result.Payload) ? j2.result.Payload[0] : j2.result.Payload) : null;
    if (!detail || !detail.PatientFileString) continue;

    documents.push({
      id: it.PatientFileID,
      patId: it.Pat_Id || Number(patId) || 0,
      fileName: it.FileName || `image_${it.PatientFileID}.${ft.split("/")[1] || "jpeg"}`,
      fileType: it.FileType || "image/jpeg",
      category: it.KB_FileType || "",
      dataUrl: `data:${it.FileType || "image/jpeg"};base64,${detail.PatientFileString}`,
      size: Math.round((detail.PatientFileString.length * 3) / 4),
    });
  }
  return { ok: true, documents, patId };
}
