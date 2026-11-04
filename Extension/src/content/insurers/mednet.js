(() => {
  // MedNet — Oracle ADF (WebLogic).
  //
  // التدفق: دخول → MainOnline → شاشة Member Eligibility → رقم العضوية →
  // Eligibility Check → Print → التقاط رابط PDF → طباعة في صفحة الموظف.
  //
  // مبادئ التصميم:
  // • لا أعلام "تم النقر" — الحالة تُقرأ من الـDOM في كل مرة (idempotent)،
  //   لذلك إعادة الإرسال بعد كل تنقل تُكمل من حيث توقفت بدل أن تتخطى مرحلة.
  // • لا انتظار ثابت — كل خطوة تنتظر ظهور عنصرها فعلياً.
  // • الطباعة ليست مسؤولية المحوّل: bridge.printReport يعترض الرابط في عالم
  //   الصفحة والخلفية تجلبه وتطبعه في تبويب الموظف (بلا نافذة منبثقة).

  const ID = {
    user: ["it1"],
    pass: ["it2"],
    loginBtn: ["cb1"],
    member: ["it5"],
    checkBtn: ["cb1"],
    printBtn: ["cb2"],
  };

  const REPORT_RE_SRC = "pdfdisplayservlet|ReportsViewer|\\.pdf(\\?|$)";

  const isLoginPage = () => !!document.querySelector('input[type="password"]');

  // نص أي عنصر تحكم (زر/وصلة) — ADF يضع النص داخل span أو img[alt]
  function controlText(el) {
    if (!el) return "";
    const img = el.querySelector && el.querySelector("img[alt]");
    return [
      el.value,
      el.textContent,
      el.getAttribute && el.getAttribute("aria-label"),
      el.getAttribute && el.getAttribute("title"),
      el.getAttribute && el.getAttribute("alt"),
      img ? img.getAttribute("alt") : "",
    ]
      .filter(Boolean)
      .join(" ")
      .replace(/\s+/g, " ")
      .trim();
  }

  function byIdInput(suffixes) {
    for (const s of suffixes) {
      const el = document.querySelector(
        `input[id$="${s}::content"], input[id$=":${s}"]`
      );
      if (el) return el;
    }
    return null;
  }

  function visibleInputs(bridge) {
    return [...document.querySelectorAll('input[type="text"], input:not([type])')].filter(
      (el) => bridge.isVisible(el) && !el.readOnly && !el.disabled
    );
  }

  function labelText(el) {
    const holder = el.closest("td, tr, div, span") || el.parentElement;
    return (holder ? holder.textContent : "").replace(/\s+/g, " ").slice(0, 80);
  }

  // شاشة البحث عن العضوية — الحقل it5 هو المعرّف الثابت، والنص بديل احتياطي
  function findMemberInput(bridge) {
    const direct = byIdInput(ID.member);
    if (direct && bridge.isVisible(direct) && !direct.readOnly) return direct;
    return (
      visibleInputs(bridge).find((el) =>
        /member|card|certif|policy|insur/i.test(
          `${el.id} ${el.name || ""} ${el.placeholder || ""} ${labelText(el)}`
        )
      ) || null
    );
  }

  // رابط/تبويب "Check Member Eligibility" في لوحة التنقل
  function findNavLink(bridge) {
    const links = [...document.querySelectorAll("a")].filter((a) => bridge.isVisible(a));
    const byText = links.find((a) =>
      /member\s*eligib|check\s*eligib|eligibility\s*check/i.test(controlText(a))
    );
    if (byText) return byText;
    // الصفحة الوسيطة (welcome/task-flow): cil1 ينقل إلى MainOnline
    const byId = document.querySelector('a[id$="cil1"]');
    if (
      byId &&
      bridge.isVisible(byId) &&
      /main|online|eligib|member|home|next|start/i.test(controlText(byId))
    ) {
      return byId;
    }
    return null;
  }

  // عناصر الأوامر فقط (زر/إدخال/وصلة أمر ADF بمعرّف cb*) — لا روابط القوائم (cil*)
  function commandControl(suffixes) {
    for (const s of suffixes) {
      const el = document.querySelector(
        `button[id$="${s}"], input[id$="${s}"], a[id$="${s}"]`
      );
      if (el) return el;
    }
    return null;
  }

  function buttonControls(bridge) {
    return [
      ...document.querySelectorAll('button, input[type="submit"], input[type="button"]'),
    ].filter((el) => bridge.isVisible(el) && !el.disabled);
  }

  // زر الفحص (cb1)
  function findCheckButton(bridge) {
    const byIdEl = commandControl(ID.checkBtn);
    if (byIdEl && bridge.isVisible(byIdEl)) return byIdEl;
    return (
      buttonControls(bridge).find((el) =>
        /check|eligib|search|submit/i.test(controlText(el))
      ) || null
    );
  }

  // زر الطباعة (cb2) — يظهر مع نتيجة الفحص
  function findPrintControl(bridge) {
    const byIdEl = commandControl(ID.printBtn);
    if (byIdEl && bridge.isVisible(byIdEl)) return byIdEl;
    return (
      buttonControls(bridge).find((el) => /^\s*print\b/i.test(controlText(el))) || null
    );
  }

  // نتيجة التحقق: ADF يعرض حقل "Eligibility Number" (it3) بقيمة بعد نجاح الفحص.
  // هذه هي الإشارة القاطعة على وصول النتيجة — قبلها لا يوجد سوى حقلين،
  // والنقر على الطباعة مبكراً يعطي مستنداً فارغاً.
  function eligibilityNumber() {
    const el = document.querySelector('input[id$="it3::content"], input[id$=":it3"]');
    if (!el) return null;
    const v = String(el.value || "").trim();
    return v && v !== "0" ? v : null;
  }

  // رسالة الفحص الفاشل: MedNet يرد أحياناً "Member is Invalid." في عنصر نص ADF (ot*)
  function invalidNotice() {
    const nodes = document.querySelectorAll(
      '[id*=":ot"], [id*=":msg"], [class*="AFMessage"]'
    );
    for (const el of nodes) {
      if (!el.getClientRects().length) continue;
      const t = String(el.textContent || "").replace(/\s+/g, " ").trim();
      if (/^member\s+is\s+invalid\.?$/i.test(t)) return el;
    }
    return null;
  }

  // انتظر نتيجة جولة فحص واحدة: نجاح (رقم أهلية) أو فشل (Member is Invalid.).
  // prevNotice يمنع احتساب الرسالة القديمة — نعتبر الفشل جديداً فقط عند استبدال
  // العنصر بعنصر آخر (أي أن الرد الجديد من السيرفر وصل فعلاً).
  async function awaitVerdict(bridge, { timeout = 45000, prevNotice = null } = {}) {
    const deadline = Date.now() + timeout;
    let roundSeen = false;
    while (Date.now() < deadline) {
      if (eligibilityNumber()) return "ok";
      const notice = invalidNotice();
      if (notice && notice !== prevNotice) return "invalid";
      // ADF يغطي الصفحة أثناء الطلب — ظهوره ثم اختفاؤه يعني انتهاء جولة كاملة.
      if (document.querySelector(".AFMaskingPane")) {
        roundSeen = true;
      } else if (roundSeen) {
        await bridge.sleep(300);
        return eligibilityNumber() ? "ok" : "invalid";
      }
      await bridge.sleep(250);
    }
    return eligibilityNumber() ? "ok" : "timeout";
  }

  function stateLabel(bridge) {
    const field = findMemberInput(bridge);
    return [
      `url=${location.pathname}${location.search}`.slice(0, 90),
      `member=${field ? 1 : 0}`,
      `mv=${field ? `"${String(field.value || "").trim()}"` : "-"}`,
      `nav=${findNavLink(bridge) ? 1 : 0}`,
      `check=${findCheckButton(bridge) ? 1 : 0}`,
      `result=${eligibilityNumber() ? 1 : 0}`,
      `invalid=${invalidNotice() ? 1 : 0}`,
      `print=${findPrintControl(bridge) ? 1 : 0}`,
    ].join(" ");
  }

  async function adfIdle(bridge) {
    await bridge.waitFor(() => !document.querySelector(".AFMaskingPane"), {
      timeout: 15000,
      poll: 250,
    });
    await bridge.sleep(300);
  }

  async function doLogin(creds, bridge) {
    bridge.overlay.show("MedNet");
    if (!creds.user || !creds.pass) {
      bridge.progress("login", "error", "no_credentials");
      bridge.overlay.error("MedNet credentials missing — open extension settings");
      bridge.fail("no_credentials");
    }
    bridge.overlay.step("Signing in…", "run");
    bridge.progress("login", "run");
    await adfIdle(bridge);

    const user = byIdInput(ID.user);
    const pass = byIdInput(ID.pass);
    if (!user || !pass) {
      bridge.progress("login", "error", "login_fields_not_found");
      bridge.overlay.error("Login fields not found — showing candidates…");
      bridge.probe();
      bridge.fail("login_fields_not_found");
    }
    bridge.setNativeValue(user, creds.user);
    bridge.setNativeValue(pass, creds.pass);

    const submit =
      commandControl(ID.loginBtn) ||
      buttonControls(bridge).find((el) => /sign\s*in|log\s*in|login|submit/i.test(controlText(el)));
    if (!submit) {
      bridge.progress("login", "error", "submit_not_found");
      bridge.overlay.error("Login button not found — showing candidates…");
      bridge.probe();
      bridge.fail("submit_not_found");
    }
    submit.click();
    await adfIdle(bridge);
    bridge.progress("login", "ok");
  }

  // بعد التحقق: انقر الطباعة، ثم اترك الالتقاط والطباعة للخلفية
  async function printDocument(printCtl, bridge) {
    bridge.overlay.step("Capturing the report…", "run");
    bridge.progress("print", "run");
    const r = await bridge.printReport({
      // أعِد إيجاد الزر عند النقر (ADF قد يعيد بناء الصف) مع الاحتفاظ بالمرجع كاحتياط
      click: () => findPrintControl(bridge) || printCtl,
      urlRe: REPORT_RE_SRC,
      timeout: 25000,
    });
    if (!r.ok) {
      bridge.progress("print", "error", r.error || "print_failed");
      bridge.overlay.error("Could not click Print — showing candidates…");
      bridge.probe();
      bridge.fail(r.error || "print_failed");
    }
    bridge.overlay.step("Report captured — printing in your window", "ok");
  }

  // إعادة مزامنة الجلسة: MedNet يقبل العضوية بعد أن يُنقر Print مرة واحدة — المستند
  // الناتج فارغ فيُهمَل ولا يُطبع. هذا نفس المسار اليدوي المُثبت، لكن بلا نافذة
  // وبلا مستند (وضع discard في bridge.printReport).
  async function resetWithPrint(bridge) {
    return bridge.printReport({
      click: () => findPrintControl(bridge),
      urlRe: REPORT_RE_SRC,
      timeout: 12000,
      discard: true,
    });
  }

  window.__InsurerAdapter = {
    id: "mednet",

    async run(msg, bridge) {
      const { card, creds, stage } = msg;
      if (stage === "error" || stage === "done") return;

      // ---- 1) تسجيل الدخول ----
      if (isLoginPage()) {
        await doLogin(creds, bridge);
        return; // تنقل — الخلفية تعيد الإرسال
      }

      bridge.overlay.show("MedNet");

      // ---- 2) وصلنا مرحلة النتيجة مسبقاً؟ لا تعِد الإرسال، انتظر النتيجة ----
      if (stage === "awaiting_result" && !window.__mcrMednetRetried) {
        const verdict = await awaitVerdict(bridge, { timeout: 20000 });
        if (verdict === "ok") {
          const ready = await bridge.waitFor(() => findPrintControl(bridge), {
            timeout: 15000,
            poll: 300,
          });
          if (ready) {
            await printDocument(ready, bridge);
            return;
          }
        }
        // الصفحة أُعيد تحميلها أو رفضت العضوية — أعد الفحص مرة واحدة
        window.__mcrMednetRetried = true;
        bridge.progress("eligibility", "run", `retry after reload | ${stateLabel(bridge)}`);
      }

      // ---- 3) شاشة البحث: الموجودة فعلاً، أو انتقل إليها ----
      let inp = findMemberInput(bridge);
      if (!inp) {
        const nav = findNavLink(bridge);
        if (nav) {
          bridge.overlay.step("Opening Member Eligibility…", "run");
          bridge.progress("navigate", "run", `click=${controlText(nav).slice(0, 40)}`);
          nav.click();
          inp = await bridge.waitFor(() => findMemberInput(bridge), {
            timeout: 25000,
            poll: 300,
          });
        } else {
          inp = await bridge.waitFor(() => findMemberInput(bridge), {
            timeout: 12000,
            poll: 300,
          });
        }
      }

      if (!inp) {
        bridge.progress("eligibility", "error", `member_screen_not_found | ${stateLabel(bridge)}`);
        bridge.overlay.error("Member eligibility screen not found — showing candidates…");
        bridge.probe();
        bridge.fail("member_screen_not_found");
        return;
      }

      // ---- 4) رقم العضوية ----
      const memberNo = String(card.insurance.card_no || "").trim();
      bridge.progress("eligibility", "run", stateLabel(bridge));
      if (String(inp.value || "").trim() !== memberNo) {
        bridge.overlay.step(`Entering member no ${memberNo}…`, "run");
        inp.focus();
        bridge.setNativeValue(inp, memberNo);
        await adfIdle(bridge);
      } else {
        bridge.overlay.step("Member number already filled", "ok");
      }

      // ---- 5) Eligibility Check ----
      const checkBtn = findCheckButton(bridge);
      if (!checkBtn) {
        bridge.progress("eligibility", "error", `check_button_not_found | ${stateLabel(bridge)}`);
        bridge.overlay.error("Eligibility Check button not found — showing candidates…");
        bridge.probe();
        bridge.fail("check_button_not_found");
        return;
      }

      // ---- 6) التحقق: MedNet قد يرد "Member is Invalid." رغم صحة العضوية.
      // العلاج المُثبت يدوياً: نقر Print (مستند فارغ يُهمَل) ثم إعادة التحقق،
      // وتتكرر الدورة حتى تظهر النتيجة الناجحة.
      const MAX_ATTEMPTS = 8;
      let notice = invalidNotice();
      let eligNo = null;

      for (let attempt = 1; attempt <= MAX_ATTEMPTS; attempt++) {
        // ADF قد يُفرغ حقل العضوية — أعِد كتابته قبل كل محاولة
        const field = findMemberInput(bridge);
        if (field && String(field.value || "").trim() !== memberNo) {
          field.focus();
          bridge.setNativeValue(field, memberNo);
          await adfIdle(bridge);
        }
        const btn = findCheckButton(bridge);
        if (!btn) break;
        if (attempt === 1) {
          bridge.overlay.step("Checking eligibility…", "run");
          bridge.progress("submitted", "ok");
        } else {
          bridge.overlay.step(`Retrying eligibility check (${attempt}/${MAX_ATTEMPTS})…`, "run");
          bridge.progress(
            "eligibility",
            "run",
            `retry ${attempt} | member=${field ? `"${field.value}"` : "none"} | ${stateLabel(bridge)}`
          );
        }
        btn.click();
        const verdict = await awaitVerdict(bridge, { timeout: 45000, prevNotice: notice });
        notice = invalidNotice();
        bridge.progress(
          "eligibility",
          verdict === "ok" ? "ok" : "run",
          `verdict=${verdict} attempt=${attempt} btn=${btn.id || btn.tagName} notice=${notice ? notice.id : "-"}`
        );
        if (verdict === "ok") {
          eligNo = eligibilityNumber();
          break;
        }
        if (verdict === "timeout") break;

        // رفض → أعد مزامنة الجلسة بنقرة طباعة مُهمَلة، ثم أعِد التحقق
        bridge.overlay.step("Re-syncing with the insurer…", "run");
        const reset = await resetWithPrint(bridge);
        notice = invalidNotice();
        bridge.progress("eligibility", "run", `reset=${reset.ok ? 1 : 0} | ${stateLabel(bridge)}`);
        if (!reset.ok && attempt === MAX_ATTEMPTS) break;
      }

      if (!eligNo) {
        bridge.progress("eligibility", "error", `no_result | ${stateLabel(bridge)}`);
        bridge.overlay.error("No eligibility result appeared — showing candidates…");
        bridge.probe();
        bridge.fail("eligibility_result_missing");
        return;
      }
      bridge.overlay.step("Eligibility result received", "ok");
      bridge.progress("eligibility", "ok", `result=1 | ${stateLabel(bridge)}`);

      // ---- 7) زر الطباعة (موجود الآن، والنتيجة جاهزة) ----
      const printCtl = await bridge.waitFor(() => findPrintControl(bridge), {
        timeout: 15000,
        poll: 300,
      });
      if (!printCtl) {
        bridge.progress("eligibility", "error", `result_not_ready | ${stateLabel(bridge)}`);
        bridge.overlay.error("No Print button appeared — showing candidates…");
        bridge.probe();
        bridge.fail("result_not_ready");
        return;
      }

      // ---- 8) الطباعة (تُنفَّذ في صفحة الموظف) ----
      await printDocument(printCtl, bridge);
    },
  };
})();
