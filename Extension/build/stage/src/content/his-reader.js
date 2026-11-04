(() => {
  // يقرأ بيانات المريض/التأمين من صفحة المشفى (Angular) عند الطلب فقط.
  // الحقول مثبتة بمعرفات مستقرة من صفحة الإنتاج (PatientRegistration) مع بدائل زنة.
  const F = {
    cardNo: "#txtINSCertificateNo",   // رقم بطاقة التأمين (Ph_CertificateNo)
    typeHint: "#txtOPSpeciality",     // التخصص/النوع (readonly) — يطابق أحد أنواع NextCare أو فارغ
    phone: "#txtContact_No",          // الهاتف (PAT_GSMNO)
    consultant: "#txtConsultant",     // اسم الطبيب (readonly)
    age: "#txtAge",                   // العمر (PAT_AGE)
    gender: "#ddlGender",             // الجنس (PAT_SEX) — select M/F/O/A/U
    firstName: "#txtFirst_Name",      // الاسم الأول (PAT_FNAME)
    middleName: "#txtMiddle_Name",   // الاسم (PAT_MNAME)
    lastName: "#txtLast_Name",        // الاسم (PAT_LNAME)
    policyNo: "#txtINSPolicyNo",      // رقم الوثيقة (Ph_PolicyNo)
    employeeNo: "#txtCREmpId",        // رقم الموظف (CusEmp_FileNo) — اختياري
    maritalStatus: "#ddlMeritalStatus", // الحالة الاجتماعية (PAT_MSTATUS) — Relation
    expDate: "#txtINSExpdate",        // تاريخ انتهاء التأمين (Ph_ExpDate)
  };

  const URL_PATTERNS = [
    /[?&#]pat(?:ient)?[_-]?id=([0-9]{1,10})/i,
    /PatientRegistration\/([0-9]{1,10})/i,
    /PatientSearch\/([0-9]{1,10})/i,
  ];
  const LABEL_RE = /patient\s*id|file\s*no|mrn|رقم\s*الملف|رقم\s*المريض/i;
  const CARDNO_RE = /^[A-Za-z0-9][A-Za-z0-9\/\-]{6,24}$/;
  const PAYER_NAME_RE =
    /NEXTCARE|MEDNET|MED\s*GULF|MEDGULF|TAKAFUL|DAMAN|AXA|BUPA|DHOFAR\s*INS|INSURANCE|تأمين/i;
  // اسم شركة معروف — يُفضَّل على المطابقة العامة ("…INSURANCE…")
  const KNOWN_PAYER_RE =
    /LIVA|NLIC|MEDNET|MED\s*GULF|NEXTCARE|VIPUL|TAKAFUL|DAMAN|AXA|BUPA/i;

  function val(sel) {
    return (document.querySelector(sel)?.value || "").trim();
  }

  function readGender() {
    const sel = document.querySelector(F.gender);
    if (!sel) return "";
    const opt = sel.selectedOptions && sel.selectedOptions[0];
    return (opt ? opt.textContent : sel.value || "").trim();
  }

  function readSelectText(sel) {
    const el = document.querySelector(sel);
    if (!el) return "";
    const opt = el.selectedOptions && el.selectedOptions[0];
    return (opt ? opt.textContent : el.value || "").trim();
  }

  function readConsultant() {
    // صف Consultant: [autocomplete (الرمز: 194)] [readonly (الاسم 또는 التخصص)].
    // الاسم real: "Dr.Rahul", "Dr. S. Upadhyaya" — يحتوي "Dr" أو نقطة/مسافة.
    // الرمز/التخصص: "194", "ENT", "GYNAECOLOGY", "OPHTHALMOLOGY" — لا يحتوي "Dr".
    const candidates = [];
    // readonly input (name="txtOPDr") — usually shows the doctor name OR speciality
    const ro = document.querySelector('input[name="txtOPDr"]');
    if (ro) { const v = (ro.value || "").trim(); if (v) candidates.push(v); }
    // all txtConsultant inputs
    for (const el of document.querySelectorAll('input#txtConsultant')) {
      const v = (el.value || "").trim();
      if (v) candidates.push(v);
    }
    // readonly with title Consultant
    for (const el of document.querySelectorAll("input[readonly]")) {
      if (/consultant/i.test(el.title || "") || /consultant/i.test(el.name || "")) {
        const v = (el.value || "").trim();
        if (v) candidates.push(v);
      }
    }

    const nameLike = (v) =>
      /dr\b/i.test(v) ||           // Dr.Rahul, Dr. S. Upadhyaya, DR. K. SELVAGANESAN
      /\./.test(v) ||               // contains period (Dr.Rahul)
      (/\s/.test(v) && /[a-z]/i.test(v) && !/^[A-Z\s]+$/.test(v)); // has space + mixed case (not all-caps speciality)
    const picked = candidates.find(nameLike) || "";
    return picked;
  }

  function readInsurerName() {
    // الحقل readonly (wd-67) يحمل اسم التأمين — عادة "VIPUL-..." (مع أو بلا مسافات)
    for (const el of document.querySelectorAll("app-root input[readonly]")) {
      const v = (el.value || "").trim();
      if (v && /vipul/i.test(v)) return v;
    }
    // بديل: أي readonly input whose value matches a known insurer keyword
    for (const el of document.querySelectorAll("app-root input[readonly]")) {
      const v = (el.value || "").trim();
      if (v && /insurance|assurance|takaful|vipul/i.test(v)) return v;
    }
    return null;
  }

  function readCardNo() {
    const v = val(F.cardNo);
    if (v) return v;
    // بدائل قديمة (صفحات أخرى قد لا تحمل نفس المعرفات)
    for (const el of document.querySelectorAll("[id$='-panel'] .wd-27, div.wd-27")) {
      const t = (el.textContent || "").trim();
      if (CARDNO_RE.test(t) && /\d/.test(t) && t.length >= 8) return t;
    }
    return null;
  }

  function readPayer() {
    // بديل: أي input/select في التطبيق قيمته تحمل اسم تأمين معروف
    const fields = document.querySelectorAll("app-root input, app-root select");
    const values = [];
    for (const el of fields) {
      const v = el.tagName === "SELECT"
        ? (el.selectedOptions[0]?.text || "").trim()
        : (el.value || "").trim();
      if (v && !/<|http/i.test(v)) values.push(v);
    }
    // اسم شركة معروف أولاً (LIVA INSURANCE …) ثم أي اسم تأمين
    return (
      values.find((v) => KNOWN_PAYER_RE.test(v)) ||
      values.find((v) => PAYER_NAME_RE.test(v)) ||
      null
    );
  }

  function fromUrl() {
    const s = location.href;
    for (const re of URL_PATTERNS) {
      const m = s.match(re);
      if (m) return m[1];
    }
    return null;
  }

  function fromLabeledInputs() {
    let best = null;
    for (const el of document.querySelectorAll('input[type="text"], input:not([type])')) {
      const v = (el.value || "").trim();
      if (!/^[0-9]{4,10}$/.test(v)) continue;
      if (el.getBoundingClientRect().width <= 0) continue;
      const holder = el.closest("mat-form-field, .form-group, td, div") || el.parentElement;
      if (LABEL_RE.test(`${el.id} ${el.name} ${holder ? holder.textContent : ""}`)) best = v;
    }
    return best;
  }

  function diagnostics() {
    const ids = {};
    for (const [k, sel] of Object.entries(F)) {
      const el = document.querySelector(sel);
      ids[k] = el ? (el.value !== undefined ? String(el.value).slice(0, 30) : "(no value)") : "(absent)";
    }
    return { page: location.pathname + location.hash, fields: ids };
  }

  function readActivePatient() {
    const patId = fromUrl() || fromLabeledInputs() || null;
    const cardNo = readCardNo();
    const payerText = readPayer();
    if (!patId && !cardNo && !payerText) {
      return { diag: diagnostics() };
    }
    const first = val(F.firstName);
    const mid = val(F.middleName);
    const last = val(F.lastName);
    const fullName = [first, mid, last]
      .map((x) => (x || "").trim())
      .filter((x) => x && x !== "." && x !== "-" && x !== "N/A")
      .join(" ")
      .trim();
    return {
      patId,
      cardNo,
      payerText,
      typeHint: val(F.typeHint) || null,
      policyNo: val(F.policyNo) || null,
      employeeNo: val(F.employeeNo) || null,
      maritalStatus: readSelectText(F.maritalStatus) || null,
      expDate: val(F.expDate) || null,
      insurerName: readInsurerName(),
      phone: val(F.phone) || null,
      consultant: readConsultant() || null,
      age: val(F.age) || null,
      sex: readGender() || null,
      firstName: first || null,
      middleName: mid || null,
      lastName: last || null,
      fullName: fullName || null,
      source: "his_page",
      page: location.pathname + location.hash,
    };
  }

  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg?.type === "his:readPatient") {
      sendResponse(readActivePatient());
    }
    if (msg?.type === "ui:progress") {
      showProgress(msg);
    }
  });

  // ------------------------------------------------------------------
  // شريط تقدم صغير ثابت أعلى صفحة المشفى — لا يعتمد على الـpopup إطلاقاً
  // ------------------------------------------------------------------
  const STEPS = {
    login: "Signing in to the insurer…",
    navigate: "Opening the insurer page…",
    dashboard: "Opening the insurer dashboard…",
    eligibility: "Running eligibility check…",
    submitted: "Checking eligibility…",
    await: "Waiting for the result…",
    captcha: "Solve the captcha in this page…",
    document: "Document ready ✓",
    print: "Preparing the document…",
    probe: "Inspecting…",
  };
  let bar = null, hideTimer = null;

  function showProgress(msg) {
    if (!bar) {
      bar = document.createElement("div");
      bar.style.cssText =
        "position:fixed;top:0;left:50%;transform:translateX(-50%);z-index:2147483647;" +
        "background:#0f172af2;color:#e2e8f0;font:600 13px 'Segoe UI',Tahoma,sans-serif;" +
        "padding:9px 18px;border-radius:0 0 12px 12px;box-shadow:0 6px 18px #0005;" +
        "display:flex;align-items:center;gap:9px;direction:ltr;";
      const dot = document.createElement("span");
      dot.style.cssText =
        "width:12px;height:12px;border:2px solid #38bdf8;border-top-color:transparent;" +
        "border-radius:50%;animation:mcrsp .7s linear infinite;";
      dot.className = "mcr-dot";
      const st = document.createElement("style");
      st.textContent = "@keyframes mcrsp{to{transform:rotate(360deg)}}";
      document.documentElement.appendChild(st);
      bar.appendChild(dot);
      const label = document.createElement("span");
      bar.appendChild(label);
      bar._label = label;
      bar._dot = dot;
      document.documentElement.appendChild(bar);
    }
    clearTimeout(hideTimer);
    bar._dot.style.display = msg.status === "error" ? "none" : "";
    bar._dot.style.borderColor = msg.status === "error" ? "#f87171" : "#38bdf8";
    bar._dot.style.borderTopColor = msg.status === "error" ? "#f87171" : "transparent";
    if (msg.step === "print" && msg.status === "ok") {
      clearTimeout(hideTimer);
      bar._dot.style.display = "none";
      bar._label.textContent = "Done — print dialog opened ✓";
      bar._label.style.color = "#6ee7b7";
      hideTimer = setTimeout(() => { bar && bar.remove(); bar = null; }, 6000);
      return;
    }
    const step = STEPS[msg.step] || msg.step;
    bar._label.textContent = msg.status === "error" ? `Error: ${msg.detail || step}` : step;
    bar._label.style.color = msg.status === "error" ? "#fca5a5" : "#e2e8f0";
    if (msg.step === "document" || msg.status === "error") {
      hideTimer = setTimeout(() => { bar && bar.remove(); bar = null; }, 8000);
    }
  }
})();
