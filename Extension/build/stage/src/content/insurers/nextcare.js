(() => {
  // NextCare PULSE (ASP.NET WebForms + chosen.js dropdowns)
  const SEL = {
    loginForm: "#frmLogin",
    user: "#txtUserName",
    pass: "#txtPassword",
    token: "#token",
    error: "#lblerrLogin",
    v3Btn: "#btnSlider1",
    memberInput: '[id$="_txtCardNumber"]',
    typeSelect: 'select[id$="_cmbType"]',
    nextBtn: '#btnCheckEligibilityorSearchbyPolicy, [id$="_btnCheckEligibilityorSearchbyPolicy"]',
    printBtn: '#btnPrintVerification, [id$="_btnPrintVerification"]',
  };
  const HOST = "https://pulse-uae.nextcarehealth.com";
  const ELIG = HOST + "/EligibilityChecking2.aspx";

  const isLoginPage = () =>
    !!(document.querySelector(SEL.user) && document.querySelector(SEL.loginForm));
  const isEligibilityPage = () => /eligibilitychecking/i.test(location.pathname);

  function visible(bridge, sel) {
    const el = document.querySelector(sel);
    return el && bridge.isVisible(el) ? el : null;
  }

  function loginError(bridge) {
    const err = document.querySelector(SEL.error);
    if (err && err.offsetParent !== null && err.textContent.trim()) {
      bridge.progress("login", "error", err.textContent.trim());
      bridge.overlay.error(`Login failed: ${err.textContent.trim()}`);
      bridge.fail("login_failed: " + err.textContent.trim());
    }
  }

  async function doLogin(creds, bridge) {
    bridge.overlay.show("NextCare");
    if (!creds.user || !creds.pass) {
      bridge.progress("login", "error", "no_credentials");
      bridge.overlay.error("NextCare credentials missing — open extension settings");
      bridge.fail("no_credentials");
    }
    bridge.overlay.step("Signing in…", "run");
    bridge.progress("login", "run");
    bridge.setNativeValue(document.querySelector(SEL.user), creds.user);
    bridge.setNativeValue(document.querySelector(SEL.pass), creds.pass);

    const token = (document.querySelector(SEL.token) || {}).value || "";
    await new Promise((resolve) => {
      let done = false;
      const finish = () => { if (!done) { done = true; resolve(); } };
      try {
        if (window.Login2 && window.Login2.Login) {
          window.Login2.Login(creds.user, creds.pass, token, "1", finish);
          setTimeout(finish, 20000);
        } else {
          const btn = document.querySelector("#btnLogin");
          if (btn) btn.click();
          setTimeout(finish, 3000);
        }
      } catch (e) {
        finish();
      }
    });

    if (document.querySelector(SEL.v3Btn)) {
      const v3 = document.querySelector(SEL.v3Btn);
      if (v3.offsetParent !== null) v3.click();
    }

    await bridge.sleep(1500);
    loginError(bridge);
    bridge.progress("login", "ok");
    bridge.overlay.step("Signed in — opening eligibility…", "ok");
  }

  function findMemberInput(bridge) {
    return (
      visible(bridge, SEL.memberInput) ||
      [...document.querySelectorAll("input")].find(
        (el) =>
          bridge.isVisible(el) &&
          (el.type === "text" || !el.type) &&
          !el.readOnly && !el.disabled &&
          /member|card|certif|insur/i.test(
            `${el.id} ${el.name} ${el.placeholder || ""}`
          )
      ) ||
      null
    );
  }

  function findTypeSelect() {
    // قائمة النوع مبنية بـchosen.js — الـselect الأصلي مخفي لكنه يبقى في الـDOM
    return (
      document.querySelector(SEL.typeSelect) ||
      document.querySelector('select[id*="cmbType"]') ||
      null
    );
  }

  function setTypeValue(sel, value, bridge) {
    const want = String(value).trim().toLowerCase();
    const opt = [...sel.options].find(
      (o) => o.text.trim().toLowerCase() === want || o.value.trim().toLowerCase() === want
    );
    if (!opt) return false;
    bridge.setNativeValue(sel, opt.value);
    updateChosenDisplay(sel, opt.text.trim());
    return sel.value === opt.value;
  }

  function updateChosenDisplay(sel, text) {
    // chosen.js يعيش في عالم الصفحة — حدّث واجهته عبر DOM المشترك مباشرة
    const chosen = document.querySelector(`#${CSS.escape(sel.id)}_chosen`) ||
      document.querySelector('[id$="_cmbType_chosen"]');
    if (!chosen) return;
    const span = chosen.querySelector(".chosen-single span");
    if (span) span.textContent = text;
    const want = text.toLowerCase();
    chosen.querySelectorAll(".chosen-results .active-result").forEach((li) => {
      li.classList.toggle("result-selected", li.textContent.trim().toLowerCase() === want);
    });
  }

  function findNextButton(bridge) {
    return (
      visible(bridge, SEL.nextBtn) ||
      [...document.querySelectorAll("a,button,input[type=button],input[type=submit],span[onclick]")].find(
        (el) =>
          bridge.isVisible(el) &&
          /check\s*eligib|next|continue|proceed|search|submit|التالي|متابعة|تحقق/i.test(
            `${el.id} ${el.name || ""} ${el.value || ""} ${el.textContent || ""} ${el.getAttribute("onclick") || ""}`
          )
      ) ||
      null
    );
  }

  function findPrintButton(bridge) {
    return visible(bridge, SEL.printBtn);
  }

  function readVisibleError() {
    // رسائل التحقق (swal / validation) على صفحة الاستعلام
    const candidates = document.querySelectorAll(
      ".swal2-html-container, .swal2-title, .field-validation-error, .validation-summary-errors, [class*='error']"
    );
    for (const el of candidates) {
      const t = (el.textContent || "").trim();
      if (t && t.length < 200 && el.offsetParent !== null) return t;
    }
    return null;
  }

  // الطباعة: انقر Print Form فقط — الخلفية تلتقط رابط التقرير من حدث التنزيل،
  // تجلب الـPDF بجلسة هذه الصفحة، وتفتح مربع الطباعة في نافذة الموظف الأساسية.
  async function printDocument(bridge) {
    bridge.progress("print", "run");
    await bridge.sleep(300);
    try {
      document.querySelector(SEL.printBtn).click();
    } catch (e) { /* ignore */ }
  }

  window.__InsurerAdapter = {
    id: "nextcare",
    async showDocument(msg, bridge) {
      documentReady(bridge, msg.autoPrint);
    },
    async login(msg, bridge) {
      await doLogin(msg.creds, bridge);
    },
    async showDocument(msg, bridge) {
      documentReady(bridge, msg.autoPrint);
    },
    async run(msg, bridge) {
      const { card, creds, stage, autoPrint, serviceType } = msg;

      // الدخول أولاً دائماً — قبل فرع الوكيل وإلا حلقة تنقل لا تنتهي
      if (isLoginPage()) {
        await doLogin(creds, bridge);
        return; // إعادة توجيه بعد النجاح — الخلفية تعيد الإرسال
      }

      // صفحة النتيجة: زر Print Form ظاهر = المستند جاهز (في أي مرحلة)
      if (findPrintButton(bridge)) {
        return documentReady(bridge, autoPrint);
      }

      if (stage === "awaiting_result") {
        // الخلفية تستقصي النتيجة — إن وصلنا هنا والزر ظاهر فلنعرض فوراً، وإلا اصمت
        if (findPrintButton(bridge)) return documentReady(bridge, autoPrint);
        bridge.progress("await", "run");
        return;
      }

      if (isEligibilityPage()) {
        await doEligibility(card, msg, bridge);
        return;
      }

      // صفحة مصادَقة أخرى → انتقل للاستعلام
      bridge.overlay.show("NextCare");
      bridge.overlay.step("Opening eligibility page…", "run");
      bridge.progress("navigate", "run", ELIG);
      location.href = ELIG;
    },
  };

  window.__InsurerAdapterShowDocument = null; // placeholder for structure clarity

  function documentReady(bridge, autoPrint) {
    bridge.progress("document", "ok");
    const printFn = () => printDocument(bridge);
    window.__MCR_PRINT = printFn;
    bridge.overlay.result({
      title: "NextCare — document ready",
      autoPrint: false, // الطباعة هنا عبر اعتراض PDF — تُدار من printFn
      onPrint: (btn) => {
        btn.textContent = "Print";
        btn.onclick = () => printFn();
      },
    });
    if (autoPrint) {
      bridge.sleep(150).then(() => printFn());
    }
  }

  async function doEligibility(card, msg, bridge) {
    const { serviceType } = msg;
    bridge.overlay.show("NextCare");
    bridge.progress("eligibility", "run");

    const inp = findMemberInput(bridge);
    if (!inp) {
      bridge.progress("eligibility", "error", "member_input_not_found");
      bridge.overlay.error("Member number field not found on this page");
      bridge.probe();
      bridge.fail("member_input_not_found");
      return;
    }
    bridge.overlay.step(`Entering member no ${card.insurance.card_no}…`, "run");
    if (!inp.value) {
      inp.focus();
      bridge.setNativeValue(inp, card.insurance.card_no);
    }

    const sel = findTypeSelect();
    const nxt = findNextButton(bridge);

    if (sel && serviceType) {
      const ok = setTypeValue(sel, serviceType, bridge);
      const chosenText =
        document.querySelector('[id$="_cmbType_chosen"] .chosen-single span')?.textContent?.trim() || "";
      bridge.progress(
        "eligibility", "run",
        `typeSet=${ok} chosen="${chosenText}" selectValue="${sel.value}"`
      );
      if (!ok) {
        bridge.overlay.note(`Value "${serviceType}" not in the list — choose it manually then:`);
        const go = document.createElement("button");
        go.className = "mcr-ov-btn primary";
        go.textContent = "Continue";
        go.addEventListener("click", () => {
          go.disabled = true;
          submit(nxt, bridge);
        });
        bridge.overlay.body().appendChild(go);
        return;
      }
      bridge.overlay.step(`Type: ${serviceType}`, "ok");
    } else if (sel && !sel.value) {
      bridge.overlay.note("Type not set on the site — choose it then:");
      const go = document.createElement("button");
      go.className = "mcr-ov-btn primary";
      go.textContent = "Continue";
      go.addEventListener("click", () => {
        go.disabled = true;
        submit(nxt, bridge);
      });
      bridge.overlay.body().appendChild(go);
      return;
    }

    // متابعة تلقائية مع مهلة إلغاء
    const noteEl = bridge.overlay.note("Auto-continue in 1s…");
    let left = 1;
    const t = setInterval(() => {
      left -= 1;
      if (left <= 0) {
        clearInterval(t);
        noteEl.remove();
        submit(nxt, bridge);
      } else {
        noteEl.textContent = `Auto-continue in ${left}s… (close the panel to cancel)`;
      }
    }, 1000);
  }

  function submit(nxt, bridge) {
    if (!nxt) {
      bridge.progress("eligibility", "error", "next_button_not_found");
      bridge.overlay.error("Continue button not found — showing candidate controls…");
      bridge.probe();
      bridge.fail("next_button_not_found");
      return;
    }
    bridge.progress("submitted", "ok");
    nxt.click();
    // انتظار النتيجة يجري الآن في الخلفية (background polling) — لا مؤقتات هنا
  }
})();
