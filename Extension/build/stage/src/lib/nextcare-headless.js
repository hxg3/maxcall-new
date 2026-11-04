// NextCare headless — التدفق كاملاً بنداءات مباشرة من الخلفية، صفر نوافذ/تبويبات.
// الصيغ مُستخرجة من proxy الموقع نفسه ومُختبرة بمتصفح حقيقي headless:
//   Login:   POST /ajax/Nextcare.Login2,Nextcare.ashx?_method=Login&_session=rw
//            body: username=..\r\npassword=..\r\ntoken=..\r\nenableUserLocalLanguage=1
//   Check:   POST /ajax/Nextcare.EligibilityChecking2,Nextcare.ashx?_method=CheckEligibility&_session=rw
//            body: حقول key=value (الاستجابة تحمل BENEFICIARYID)
//   Report:  ReportsViewer.aspx?reportEligibilityId=<BENEFICIARYID>-<typeID>&sProviderId=<id>&name=rptUCAF.rpt
import { log } from "./log.js";

const BASE = "https://pulse-uae.nextcarehealth.com";

const TYPE_IDS = {
  "In-Patient": "1",
  "Out-Patient": "2",
  "Dental": "7",
  "Life": "13",
  "Cobra": "14",
  "Optical": "15",
  "Travel Insurance": "16",
  "Collision Damage Waiver Excess": "17",
  "Chronic Out": "2.1",
  "Emergency Room Services": "1.1",
  "Maternity": "2.2",
};

// قالب التقرير معروف مؤكداً لـOut-Patient فقط — البقية ترجع للتدفق الاحتياطي بالنوافذ
const REPORT_NAMES = { "Out-Patient": "rptUCAF.rpt" };

const enc = encodeURIComponent;
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function ajaxPro(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "text/plain; charset=UTF-8" },
    body,
  });
  return res.text();
}

function errText(resp) {
  const m = resp.match(/ajax_error\('([^']*)','([^']*)'/);
  return m ? `${m[1]}: ${m[2]}` : resp.slice(0, 150);
}

function blobToDataUrl(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = reject;
    reader.readAsDataURL(blob);
  });
}

function claimDate() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, "0");
  return `${p(d.getDate())}-${p(d.getMonth() + 1)}-${d.getFullYear()} ` +
         `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

export async function headlessPrint(settings, card, serviceType) {
  const typeText = serviceType || "Out-Patient";
  const typeId = TYPE_IDS[typeText];
  const reportName = REPORT_NAMES[typeText];
  if (!typeId || !reportName) {
    return { ok: false, reason: `no headless mapping for "${typeText}" — window fallback` };
  }
  const cardNo = card?.insurance?.card_no || "";
  if (!cardNo) return { ok: false, reason: "no card number" };

  const creds = (settings.insurers || {}).nextcare || {};
  if (!creds.user || !creds.pass) {
    return { ok: false, reason: "no credentials — window fallback" };
  }

  // 1) جلسة حية؟
  let page = await fetch(`${BASE}/EligibilityChecking2.aspx`);
  if (page.url.includes("Login2.aspx")) {
    await log("info", "headless: session dead — logging in");
    const html = await (await fetch(`${BASE}/Login2.aspx?token=AQ==`)).text();
    const token = html.match(/id="token" value="([^"]*)"/)?.[1] || "";
    const loginBody =
      `username=${enc(creds.user)}\r\n` +
      `password=${enc(creds.pass)}\r\n` +
      `token=${enc(token)}\r\n` +
      `enableUserLocalLanguage=1\r\n`;
    const resp = await ajaxPro(
      `${BASE}/ajax/Nextcare.Login2,Nextcare.ashx?_method=Login&_session=rw`, loginBody
    );
    if (!resp.includes("LOGIN_SUCCESS")) {
      return { ok: false, reason: `login failed: ${errText(resp)}` };
    }
    await log("info", "headless: LOGIN_SUCCESS");
    page = await fetch(`${BASE}/EligibilityChecking2.aspx`);
    if (page.url.includes("Login2.aspx")) {
      return { ok: false, reason: "login did not stick" };
    }
  } else {
    await log("info", "headless: session alive");
  }

  // 2) توكن صفحة الاستعلام + sProviderId
  const html = await page.text();
  const pageToken = html.match(/id="token" value="([^"]*)"/)?.[1] || "";
  const providerId = html.match(/sProviderId=(\d+)/)?.[1] || "3";

  // 3) CheckEligibility
  const cardFormatted = cardNo.replace(/(.{4})(?=.)/g, "$1-");
  const checkBody =
    `sCaseNumber=\r\n` +
    `sFirstName=\r\n` +
    `sFamilyName=\r\n` +
    `lsCardno=${enc(cardFormatted)}\r\n` +
    `sSearchFieldValue=\r\n` +
    `iPayerID=0\r\n` +
    `type=${enc(typeText)}\r\n` +
    `claimdate=${enc(claimDate())}\r\n` +
    `typeID=${typeId}\r\n` +
    `iFieldToSearch=0\r\n` +
    `sPolicyNbr=\r\n` +
    `YOB=0\r\n` +
    `hasPolicyOntheFly=0\r\n` +
    `token=${enc(pageToken)}\r\n`;
  const resp = await ajaxPro(
    `${BASE}/ajax/Nextcare.EligibilityChecking2,Nextcare.ashx?_method=CheckEligibility&_session=rw`,
    checkBody
  );
  if (resp.includes("ajax_error")) {
    return { ok: false, reason: `check error: ${errText(resp)}` };
  }
  const benId = resp.match(/BENEFICIARYID'?:\s*(\d+)/)?.[1];
  const status = resp.match(/STATUS'?:\s*'([^']*)'/)?.[1] || "";
  if (!benId) {
    // ليست JSON أهلية — غالباً رسالة عدم أهلية/خطأ من الموقع
    const msg = resp.match(/OUTMESSAGE'?:\s*'([^']*)'/)?.[1] || resp.slice(0, 120);
    return { ok: false, reason: `no beneficiary id — ${msg}` };
  }
  await log("info", "headless: eligible", `BENEFICIARYID=${benId} status=${status}`);

  // 4) جلب الـPDF مباشرة
  const reportUrl =
    `${BASE}/ReportsViewer.aspx?reportEligibilityId=${benId}-${typeId}` +
    `&sProviderId=${providerId}&name=${reportName}`;
  await sleep(300);
  const pdfRes = await fetch(reportUrl);
  const mime = pdfRes.headers.get("content-type") || "";
  if (!mime.includes("pdf")) {
    return { ok: false, reason: `report returned ${mime || "no content-type"}` };
  }
  const dataUrl = await blobToDataUrl(await pdfRes.blob());
  await log("info", "headless: PDF fetched", `${Math.round(dataUrl.length / 1024)} KB`);
  return { ok: true, dataUrl };
}
