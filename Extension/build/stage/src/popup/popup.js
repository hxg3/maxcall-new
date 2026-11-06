import { detectInsurer, INSURERS } from "../lib/insurers.js";
import { getSettings } from "../lib/settings.js";
import { log } from "../lib/log.js";

const $ = (id) => document.getElementById(id);
const state = { card: null, insurer: null, tabId: null };

const send = (msg) => chrome.runtime.sendMessage(msg);

const STEP_LABEL = {
  login: "Signing in",
  eligibility: "Eligibility page",
  submitted: "Submitting search",
  navigate: "Navigating",
  document: "Document ready",
  print: "Preparing document",
  dashboard: "Site dashboard",
  captcha: "Solve the captcha",
  probe: "Probe mode",
};

const ERR_TEXT = {
  no_his_tab: "Open the HIS page first",
  patient_not_detected: "Patient not detected — enter the file number manually",
  unauthorized: "Server credentials are wrong (Settings)",
  server_unreachable: "MaxCare server unreachable",
  server_not_configured: "Server URL not set — Settings",
  rate_limited: "Too many requests — retry in a moment",
  timeout: "Server connection timed out",
};

function setStatus(text, isErr = false) {
  const el = $("status");
  el.textContent = text || "";
  el.classList.toggle("err", isErr);
}

function addStep(text, kind) {
  const steps = $("steps");
  const prev = steps.querySelector('[data-run="1"]');
  if (prev) {
    prev.removeAttribute("data-run");
    prev.querySelector(".spin")?.replaceWith(Object.assign(document.createElement("span"), { className: "ic", textContent: "✓" }));
    prev.classList.add("ok");
  }
  const row = document.createElement("div");
  row.className = "step" + (kind === "err" ? " err" : "");
  if (kind === "run") {
    row.setAttribute("data-run", "1");
    const sp = document.createElement("span");
    sp.className = "spin";
    row.appendChild(sp);
  } else {
    const ic = document.createElement("span");
    ic.className = "ic";
    ic.textContent = kind === "err" ? "✕" : "✓";
    row.appendChild(ic);
  }
  const label = document.createElement("span");
  label.textContent = text;
  row.appendChild(label);
  steps.appendChild(row);
}

function detectMissingExcelFields(card) {
  const d = card || {};
  const ins = d.insurance || {};
  const v = d.visit || {};
  const missing = [];

  const rawPolicy = (d.rawPolicyNo || d.insurance?.policy_no || "").trim();
  const isNumeric = /^\d+$/.test(rawPolicy);
  const policyNo = isNumeric ? rawPolicy : "";
  const company = isNumeric ? "" : rawPolicy;

  if (!policyNo) missing.push({ key: "policyNo", label: "Policy Number", value: "", required: true });
  if (!company) missing.push({ key: "company", label: "Company", value: "", required: true });

  const empNo = (d.employeeNo || "").trim();
  if (!empNo) missing.push({ key: "employeeNo", label: "Employee No.", value: "", required: true });

  missing.push({ key: "periodFrom", label: "Period of Treatment — From (YYYY-MM-DD)", value: "", required: true });

  const exp = (d.expDate || "").trim();
  if (!exp) missing.push({ key: "periodTo", label: "Period of Treatment — To (YYYY-MM-DD)", value: "", required: true });

  const doc = (v.consultant || "").trim();
  const isCode = !doc || /^\d+$/.test(doc) || /^[A-Z]{2,5}$/.test(doc) || /^[A-Z]\d{2,4}$/.test(doc);
  if (isCode) missing.push({ key: "doctor", label: "Name of the Doctor", value: "", required: true });

  const insName = (d.insurerName || "").trim();
  if (!insName) missing.push({ key: "insurer", label: "Name of the Insurer", value: "", required: true });

  return { missing, policyNo, company, empNo, exp, doc, insName };
}

function renderExcelForm(card, info) {
  const fields = info.missing;
  const box = $("excelFields");
  const container = $("excelInputs");
  container.textContent = "";
  if (!fields.length) { box.style.display = "none"; return false; }
  for (const f of fields) {
    const wrap = document.createElement("div");
    wrap.style.marginBottom = "8px";
    const lab = document.createElement("label");
    lab.style.cssText = "display:block;font-size:12px;color:#64748b;margin-bottom:3px;";
    lab.textContent = f.label + (f.required ? " *" : "");
    const inp = document.createElement("input");
    inp.type = "text";
    inp.dataset.key = f.key;
    inp.value = f.value;
    inp.style.cssText = "width:100%;border:1px solid #e2e8f0;border-radius:8px;padding:6px 8px;font:inherit;";
    wrap.append(lab, inp);
    container.appendChild(wrap);
  }
  box.style.display = "";
  return true;
}

function collectExcelInputs() {
  const out = {};
  for (const inp of $("excelInputs").querySelectorAll("input")) {
    out[inp.dataset.key] = inp.value.trim();
  }
  return out;
}

async function runHtmlForm() {
  if (!state.insurer || !state.card) return;
  const card = state.card;
  const info = detectMissingExcelFields(card);

  const inputs = collectExcelInputs();
  for (const f of info.missing) {
    if (f.required && !inputs[f.key]) {
      setStatus(`Please fill: ${f.label}`, true);
      return;
    }
  }

  $("printBtn").disabled = true;
  $("printBtn").textContent = "Preparing…";
  setStatus("Preparing the document…");
  $("steps").textContent = "";
  addStep("Fetching template", "run");

  try {
    const { fetchTemplateCached } = await import("../lib/xlsx-fill.js");
    // Reuse the cached fetch helper for the HTML template (just a text fetch)
    const url = state.insurer.templateUrl.replace("Vipul.xlsx", "Vipul-form.html");
    const res = await fetch(url, { cache: "no-store" });
    if (!res.ok) throw new Error(`template fetch failed: ${res.status}`);
    let html = await res.text();
    addStep("Filling the form", "ok");

    const rawPolicy = (card.rawPolicyNo || card.insurance?.policy_no || "").trim();
    const isNumeric = /^\d+$/.test(rawPolicy);
    const policyNo = isNumeric ? rawPolicy : (inputs.policyNo || "");
    const company = isNumeric ? (inputs.company || "") : rawPolicy;

    const fullName = card.patient.fullName || card.patient.name || "";

    const doc = (inputs.doctor || "").trim() || (card.visit?.consultant || "");
    const isId = /^[A-Z]\d{2,4}$/.test(doc);
    const doctorName = isId ? (inputs.doctor || "") : doc;

    const data = {
      cardNo: card.insurance.card_no || "",
      policyNo: policyNo || inputs.policyNo || "",
      name: fullName,
      age: card.patient.age ? parseInt(card.patient.age, 10) : "",
      sex: card.patient.sex || "",
      company: company || inputs.company || "",
      employeeNo: (inputs.employeeNo || card.employeeNo || "").trim(),
      relation: (card.maritalStatus || "").trim() || "SELF",
      insurer: (inputs.insurer || card.insurerName || "").trim(),
      periodFrom: inputs.periodFrom || new Date().toISOString().slice(0, 10),
      periodTo: inputs.periodTo || (card.expDate || ""),
      doctor: doctorName || inputs.doctor || "",
    };

    for (const [key, val] of Object.entries(data)) {
      html = html.replace(new RegExp(`{{${key}}}`, "g"), String(val ?? ""));
    }

    // إرسال الـHTML المعبأ إلى الخلفية → طباعة في تبويب الموظف
    const res2 = await send({ type: "insurer:printHtml", html });
    if (!res2?.ok) throw new Error(res2?.reason || "print failed");

    addStep("Print dialog opened ✓", "ok");
    setStatus("Done ✓ — the print dialog is open in your window");
  } catch (e) {
    log("error", "html form failed", String(e));
    setStatus(`Failed: ${e.message}`, true);
    addStep("Failed", "err");
  } finally {
    $("printBtn").disabled = false;
    $("printBtn").textContent = state.insurer?.kind === "excel" || state.insurer?.kind === "html-form"
      ? "Print insurance document" : "Print insurance document";
  }
}

async function runGigForm() {
  if (!state.insurer || !state.card) return;
  const card = state.card;
  $("printBtn").disabled = true;
  $("printBtn").textContent = "Preparing…";
  setStatus("Preparing the document…");
  addStep("Fetching GIG form", "run");
  try {
    const res = await fetch(state.insurer.templateUrl, { cache: "no-store" });
    if (!res.ok) throw new Error(`template fetch failed: ${res.status}`);
    let html = await res.text();

    // تقسيم رقم HIS: البوليسي = كل الأجزاء ما عدا آخر 3، والعضوية = آخر 3
    const raw = String(card.insurance?.card_no || "").trim();
    const parts = raw.split("/").map((p) => p.trim()).filter(Boolean);
    const [policyNo, membershipNo] = parts.length > 3
      ? [parts.slice(0, -3).join("/"), parts.slice(-3).join("/")]
      : [raw, raw];

    // نفس تنظيف فيبل: تسقط الرموز الميتة، وفاصل واحد بين الكلمات
    const cleanName = String(card.patient?.fullName || card.patient?.name || "")
      .split(/\s+/).map((t) => t.trim())
      .filter((t) => t && !/^([.\-_]+|na|n\/a)$/i.test(t)).join(" ");

    const sex = String(card.patient?.sex || "").trim().toLowerCase();
    const isMale = sex.startsWith("m"), isFemale = sex.startsWith("f");

    const svc = $("typeSel")?.value || card.insurance?.service_type || card.typeHint || card.service_type || "";
    const vi = visitIndex(svc, card.typeHint);
    const checked = Array.from({ length: 6 }, (_, i) => i === vi ? " checked" : "");

    const data = {
      policyNo, membershipNo, name: cleanName,
      date: new Date().toLocaleDateString("en-GB"),
      timestamp: String(Date.now()),
      sexMaleChecked: isMale ? " checked" : "",
      sexFemaleChecked: isFemale ? " checked" : "",
      visit0: checked[0], visit1: checked[1], visit2: checked[2],
      visit3: checked[3], visit4: checked[4], visit5: checked[5],
    };
    for (const [k, v] of Object.entries(data))
      html = html.replace(new RegExp(`{{${k}}}`, "g"), String(v ?? ""));

    const r2 = await send({ type: "insurer:printHtml", html });
    if (!r2?.ok) throw new Error(r2?.reason || "print failed");
    addStep("Print dialog opened ✓", "ok");
    setStatus("Done ✓ — the print dialog is open in your window");
  } catch (e) {
    log("error", "gig form failed", String(e));
    setStatus(`Failed: ${e.message}`, true);
    addStep("Failed", "err");
  } finally {
    $("printBtn").disabled = false;
    $("printBtn").textContent = "Print insurance document";
  }
}

function visitIndex(svc, hint) {
  // مطابقة substring بالترتيب الأدق-أولاً (نفس منطق النسخة الأخرى من المنطق)؛
  // الرمزان القصيران er / ip يُطابقان ككلمة كاملة فقط — "maternity" تحتوي
  // "er" لكنها تُلتقط بمطابقتها لـ"matern" أولاً
  const hay = (`${String(svc || "").toLowerCase()} ${String(hint || "").toLowerCase()} `).toLowerCase();
  const kws = [
    ["dental", 4], ["tooth", 4],
    ["optical", 5], ["optometr", 5], ["eye", 5],
    ["matern", 3], ["pregnan", 3],
    ["in-patient", 1], ["inpatient", 1], ["in patient", 1], ["admitted", 1], ["admission", 1],
    ["emergency", 2], ["casualty", 2], ["a&e", 2],
    ["out-patient", 0], ["outpatient", 0], ["opd", 0],
  ];
  for (const [kw, idx] of kws) {
    if (hay.includes(kw)) return idx;
  }
  const m = hay.match(/\b(er|ip)\b/);
  if (m) return m[1] === "er" ? 2 : 1;
  return 0; // الأغلب Outpatient
}

async function runStaticPdf() {
  if (!state.insurer || !state.card) return;
  $("printBtn").disabled = true;
  $("printBtn").textContent = "Preparing…";
  setStatus("Opening the printout…");
  addStep("Fetching the form", "run");
  try {
    const res = await fetch(state.insurer.templateUrl, { cache: "no-store" });
    if (!res.ok) throw new Error(`fetch failed: ${res.status}`);
    const blob = await res.blob();
    const dataUrl = await new Promise((resolve, reject) => {
      const fr = new FileReader();
      fr.onload = () => resolve(String(fr.result || ""));
      fr.onerror = () => reject(fr.error || new Error("read failed"));
      fr.readAsDataURL(blob);
    });
    const r2 = await send({ type: "insurer:printPdf", dataUrl });
    if (!r2?.ok) throw new Error(r2?.reason || "print failed");
    addStep("Print dialog opened ✓", "ok");
    setStatus("Done ✓ — the print dialog is open in your window");
  } catch (e) {
    log("error", "static pdf failed", String(e));
    setStatus(`Failed: ${e.message}`, true);
    addStep("Failed", "err");
  } finally {
    $("printBtn").disabled = false;
    $("printBtn").textContent = "Print insurance document";
  }
}

function renderCard(card) {
  state.card = card;
  $("patientEmpty").style.display = "none";
  $("patientBody").style.display = "";
  $("pName").textContent = card.patient.name || "—";
  $("pMeta").textContent =
    [card.patient.sex, card.patient.age, card.patient.phone]
      .filter(Boolean).join(" · ") || `File ${card.patient.pat_id}`;

  const insurer = detectInsurer(card.insurance.payer_name);
  state.insurer = insurer;
  $("insCard").style.display = "";
  if (insurer) {
    $("insChip").textContent = insurer.label;
    $("printBtn").disabled = false;
    $("excelFields").style.display = "none";
    if (insurer.kind === "excel" || insurer.kind === "html-form") {
      $("printBtn").textContent = "Print insurance document";
      const info = detectMissingExcelFields(card);
      renderExcelForm(card, info);
      if (!info.missing.length) {
        $("excelFields").style.display = "none";
        setStatus("All fields detected — ready to print");
      } else {
        setStatus(`${info.missing.length} field(s) need your input`);
      }
    } else {
      $("printBtn").textContent = "Print insurance document";
      if (insurer.types) {
        $("typeRow").style.display = "";
        $("typeSel").textContent = "";
        const hint = (card.typeHint || "").trim().toLowerCase();
        const matched = hint
          ? insurer.types.find((t) => t.toLowerCase() === hint || hint.includes(t.toLowerCase()))
          : null;
        for (const t of insurer.types) {
          const opt = document.createElement("option");
          opt.value = t;
          opt.textContent = t;
          if ((matched || insurer.defaultType) === t) opt.selected = true;
          $("typeSel").appendChild(opt);
        }
      }
    }
  } else {
    $("insChip").textContent = card.insurance.payer_name || "No insurance";
    $("insChip").classList.add("warn");
    setStatus("Insurance company not supported yet", true);
  }
  $("iCard").textContent = card.insurance.card_no || "—";
}

function buildCardFromPage(det) {
  return {
    ok: true,
    patient: {
      pat_id: det.patId || "—",
      name: det.fullName || det.firstName || "",
      firstName: det.firstName || "",
      middleName: det.middleName || "",
      lastName: det.lastName || "",
      sex: det.sex || "",
      age: det.age || "",
      phone: det.phone || "",
    },
    insurance: {
      card_no: det.cardNo || "",
      payer_name: det.payerText || det.insurerName || "",
      policy_no: det.policyNo || "",
    },
    visit: { consultant: det.consultant || "" },
    typeHint: det.typeHint || "",
    // Excel-specific fields (only relevant for excel-form insurers)
    employeeNo: det.employeeNo || "",
    maritalStatus: det.maritalStatus || "",
    expDate: det.expDate || "",
    insurerName: det.insurerName || "",
    rawPolicyNo: det.policyNo || "",
  };
}

function mergeCard(server, det) {
  const p = det || {};
  server.typeHint = server.typeHint || p.typeHint || "";
  server.patient.name = server.patient.name || p.fullName || p.firstName || "";
  server.patient.firstName = server.patient.firstName || p.firstName || "";
  server.patient.middleName = server.patient.middleName || p.middleName || "";
  server.patient.lastName = server.patient.lastName || p.lastName || "";
  server.patient.sex = server.patient.sex || p.sex || "";
  server.patient.age = server.patient.age || p.age || "";
  server.patient.phone = server.patient.phone || p.phone || "";
  server.insurance.card_no = server.insurance.card_no || p.cardNo || "";
  server.insurance.payer_name = server.insurance.payer_name || p.payerText || p.insurerName || "";
  server.insurance.policy_no = server.insurance.policy_no || p.policyNo || "";
  server.visit.consultant = server.visit.consultant || p.consultant || "";
  server.employeeNo = server.employeeNo || p.employeeNo || "";
  server.maritalStatus = server.maritalStatus || p.maritalStatus || "";
  server.expDate = server.expDate || p.expDate || "";
  server.insurerName = server.insurerName || p.insurerName || "";
  server.rawPolicyNo = server.rawPolicyNo || p.policyNo || "";
  return server;
}

async function loadPatient(det) {
  log("info", "popup: loadPatient", JSON.stringify(det || {}).slice(0, 300));
  let card = null;
  if (det?.patId) {
    setStatus("Fetching insurance data…");
    const res = await send({ type: "his:patient", patId: det.patId });
    log("info", "popup: his:patient", JSON.stringify(res).slice(0, 300));
    if (res.ok) card = mergeCard(res, det);
    else if (!det.cardNo && !det.payerText) {
      setStatus(ERR_TEXT[res.reason] || `Error: ${res.reason}`, true);
      if (["patient_not_detected", "no_his_tab"].includes(res.reason)) showManual();
      return;
    }
  }
  if (!card) card = buildCardFromPage(det);
  if (!card.insurance.card_no && !card.insurance.payer_name) {
    setStatus(ERR_TEXT.patient_not_detected, true);
    showManual();
    return;
  }
  setStatus("");
  renderCard(card);
}

function showManual() {
  $("patientEmpty").style.display = "none";
  $("manual").style.display = "flex";
  $("manualId").focus();
}

chrome.runtime.onMessage.addListener((msg) => {
  // Liva: بطاقة "Provider" تفتح تبويباً جديداً — نتبع التبويب الجديد
  if (msg?.type === "job:handover") {
    state.tabId = msg.tabId;
    return;
  }
  if (msg?.type !== "adapter:progress") return;
  // headless: لا tabId — اعرض دائماً؛ window flow: طابق التبويب
  if (state.tabId && msg.tabId && msg.tabId !== state.tabId) return;
  const label = STEP_LABEL[msg.step] || msg.step;
  if (msg.status === "error") {
    addStep(msg.detail ? `${label}: ${msg.detail}` : label, "err");
    setStatus(msg.detail ? `${label}: ${msg.detail}` : label, true);
    printBtnReset();
  } else if (msg.step === "print" && msg.status === "ok") {
    addStep("Print dialog opened", "ok");
    setStatus("Done ✓ — the print dialog is open in your window");
    runBusy = false;
    printBtnReset("Print insurance document");
  } else if (msg.step === "document") {
    addStep(label, "ok");
    setStatus("Document reached — preparing print…");
  } else if (msg.step === "print") {
    printBtnBusy("Preparing document…");
    setStatus("Preparing the document — the print dialog will open here…");
  } else {
    addStep(label, "run");
    setStatus(label + "…");
  }
});

function printBtnBusy(text) {
  $("printBtn").innerHTML =
    '<span class="spin-white"></span>' + text;
}

function printBtnReset(text = "Print insurance document") {
  $("printBtn").textContent = text;
  $("printBtn").disabled = false;
  runBusy = false;
}

let runBusy = false;
async function startRun() {
  if (runBusy) { setStatus("المهمة قيد التنفيذ بالفعل — سأُبقيك على اطلاع", true); return; }
  if (!state.insurer || !state.card) return;
  runBusy = true;
  $("printBtn").disabled = true;
  printBtnBusy("Working in background…");
  $("steps").textContent = "";
  const serviceType = $("typeSel")?.value || null;
  log("info", "popup: startRun", `insurer=${state.insurer.id} type=${serviceType}`);

  const st = await getSettings();
  const c = (st.insurers || {})[state.insurer.id] || {};
  const needsCreds = state.insurer.id !== "gig" && state.insurer.id !== "oqic";
  if (needsCreds && (!c.user || !c.pass)) {
    setStatus("Warning: login credentials incomplete (Settings) — works only with a live session");
    addStep("Credentials incomplete (Settings)", "err");
  } else {
    setStatus("Running in the background — the print dialog will open automatically…");
  }

  const res = await send({
    type: "insurer:run",
    insurerId: state.insurer.id,
    card: state.card,
    serviceType,
  });
  if (!res.ok) {
    setStatus(`Could not start: ${res.reason}`, true);
    printBtnReset();
    return;
  }
  state.tabId = res.tabId;
  addStep("Running insurer site in background", "run");
  if (!res.ok) runBusy = false;
}

async function probe() {
  let sent = 0;
  for (const ins of INSURERS) {
    const tabs = await chrome.tabs.query({ url: ins.urlPatterns });
    for (const t of tabs) {
      await chrome.tabs.sendMessage(t.id, { type: "insurer:probe" }).catch(() => {});
      sent += 1;
    }
  }
  setStatus(sent ? `Probe + recording active in ${sent} tab(s) — do the flow manually (2 min)` : "Open the insurer site tab first", true);
}

async function copyLog() {
  try {
    const res = await send({ type: "diag:get" });
    if (!res?.ok) throw new Error("no log");
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(res.text);
    } else {
      const ta = document.createElement("textarea");
      ta.value = res.text;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      ta.remove();
    }
    setStatus("Log copied — send it to the developer");
  } catch (e) {
    setStatus("Copy failed — open the popup console", true);
  }
}

$("printBtn").addEventListener("click", async () => {
  const kind = state.insurer?.kind;
  if (kind === "static-pdf") await runStaticPdf();
  else if (state.insurer?.id === "gig" && kind === "html-form") await runGigForm();
  else if (kind === "excel" || kind === "html-form") await runHtmlForm();
  else await startRun();
});
$("copyLog").addEventListener("click", (e) => { e.preventDefault(); copyLog(); });
$("manualGo").addEventListener("click", () => {
  const id = $("manualId").value.trim();
  if (/^\d{1,10}$/.test(id)) loadPatient({ patId: id });
});
$("manualId").addEventListener("keydown", (e) => {
  if (e.key === "Enter") $("manualGo").click();
});
$("openOptions").addEventListener("click", (e) => {
  e.preventDefault();
  chrome.runtime.openOptionsPage();
});
$("probeLink").addEventListener("click", (e) => {
  e.preventDefault();
  probe();
});

(async () => {
  log("info", "popup opened");
  send({ type: "ui:popup" }).catch(() => {});
  checkUpdate();
  await refreshPatient();
})();

// إعادة قراءة صفحة المشفى من جديد — بلا إعادة تحميل الصفحة
async function refreshPatient() {
  setStatus("Reading patient…");
  $("patientEmpty").textContent = "Reading patient from the HIS page…";
  $("patientEmpty").style.display = "";
  $("patientBody").style.display = "none";
  const det = await send({ type: "his:detect" });
  if (det.ok) loadPatient(det);
  else {
    log("warn", "popup: detect failed", det.reason);
    setStatus(ERR_TEXT[det.reason] || "", true);
    showManual();
  }
}

$("refreshBtn").addEventListener("click", (e) => {
  e.preventDefault();
  log("info", "popup: refresh clicked");
  refreshPatient();
});

// ---- update check: compare with version.json on the server ----
const FALLBACK_BASES = ["http://maxcall.local:3001", "http://192.168.1.32:3001"];

async function checkUpdate() {
  try {
    const st = await getSettings();
    const bases = [...new Set(
      [(st.serverUrl || "").replace(/\/+$/, ""), ...FALLBACK_BASES].filter(Boolean)
    )];
    const cur = chrome.runtime.getManifest().version;
    for (const base of bases) {
      try {
        const res = await fetch(`${base}/ext/version.json`, { cache: "no-store" });
        if (!res.ok) {
          log("warn", "update check http", `${base} -> ${res.status}`);
          continue;
        }
        const data = await res.json();
        log("info", "update check ok", `${base} -> ${data.version} vs ${cur}`);
        if (data.version && data.version !== cur) {
          $("updText").textContent = `New version available (${data.version}) — click to update:`;
          $("updBar").classList.add("show");
        }
        return;
      } catch (e) {
        log("warn", "update check failed", `${base} -> ${String(e).slice(0, 120)}`);
      }
    }
  } catch (e) {
    log("warn", "update check skipped", String(e).slice(0, 120));
  }
}

$("updBtn").addEventListener("click", () => {
  log("info", "popup: update clicked — trying native host");
  $("updBtn").disabled = true;
  $("updBtn").textContent = "Updating…";
  $("updText").textContent = "Updating — the popup will refresh automatically…";
  try {
    chrome.runtime.sendNativeMessage("com.maxcare.updater", { cmd: "update" }, (res) => {
      const err = chrome.runtime.lastError;
      if (err || !res?.ok) {
        const why = err?.message || res?.error || "unknown";
        log("error", "self-update failed", why);
        $("updBtn").disabled = false;
        $("updBtn").textContent = "Update";
        $("updText").textContent =
          `Self-update failed (${why}) — run setup-updater.ps1 once with: -ExtensionID ${chrome.runtime.id}`;
        return;
      }
      log("info", "self-update extracted", `v${res.version} — reloading`);
      chrome.runtime.reload();
    });
  } catch (e) {
    log("error", "sendNativeMessage threw", String(e));
    $("updBtn").disabled = false;
  }
});
